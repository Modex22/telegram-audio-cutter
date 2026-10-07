import asyncio
import os
from dotenv import load_dotenv

load_dotenv()
import secrets
import shutil
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

BASE_DIR = Path(__file__).resolve().parent
MEDIA_DIR = BASE_DIR / "media"
OUTPUT_DIR = BASE_DIR / "output"
WEBAPP_DIR = BASE_DIR / "webapp"
MEDIA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEB_APP_URL = os.getenv("WEBAPP_URL", "http://localhost:8080")
PORT = int(os.getenv("PORT", "8080"))
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "50"))

if not BOT_TOKEN:
    raise RuntimeError("Set BOT_TOKEN in your environment before starting the bot.")

# Short-lived in-memory sessions: token -> audio metadata.
SESSIONS = {}
BOT_APP = None

flask_app = Flask(__name__, static_folder=str(WEBAPP_DIR), static_url_path="")


def clean_name(name: str) -> str:
    keep = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    cleaned = "".join(c if c in keep else "_" for c in name)
    return cleaned[:100] or "audio"


def get_session(token):
    session = SESSIONS.get(token)
    if not session:
        return None
    return session


@flask_app.get("/")
def index():
    return send_from_directory(WEBAPP_DIR, "index.html")


@flask_app.get("/<path:path>")
def static_files(path):
    return send_from_directory(WEBAPP_DIR, path)


@flask_app.get("/api/info")
def api_info():
    token = request.args.get("token", "")
    session = get_session(token)
    if not session:
        return jsonify({"error": "Invalid or expired audio session."}), 404
    return jsonify({
        "filename": session["filename"],
        "duration": session["duration"],
        "size": session["size"],
        "media_url": f"/media/{token}",
    })


@flask_app.get("/media/<token>")
def media(token):
    session = get_session(token)
    if not session:
        return jsonify({"error": "Invalid or expired audio session."}), 404
    path = Path(session["path"])
    if not path.exists():
        return jsonify({"error": "Audio file no longer exists."}), 404
    return send_from_directory(path.parent, path.name, as_attachment=False)


@flask_app.post("/api/cut")
def cut_audio():
    token = request.form.get("token", "")
    session = get_session(token)
    if not session:
        return jsonify({"error": "Invalid or expired audio session."}), 404

    try:
        start = float(request.form.get("start", "0"))
        end = float(request.form.get("end", "0"))
    except ValueError:
        return jsonify({"error": "Invalid start or end time."}), 400

    duration = float(session["duration"])
    start = max(0.0, min(start, duration))
    end = max(0.0, min(end, duration))
    if end <= start:
        return jsonify({"error": "End time must be greater than start time."}), 400
    if end - start < 0.1:
        return jsonify({"error": "Selection is too short."}), 400

    fmt = request.form.get("format", "mp3").lower()
    if fmt not in {"mp3", "m4a", "wav"}:
        fmt = "mp3"

    input_path = Path(session["path"])
    stem = Path(session["filename"]).stem
    output_name = f"{clean_name(stem)}_cut_{uuid.uuid4().hex[:6]}.{fmt}"
    output_path = OUTPUT_DIR / output_name

    # FFmpeg command is intentionally assembled as a list to avoid shell injection.
    if fmt == "mp3":
        codec_args = ["-c:a", "libmp3lame", "-b:a", "192k"]
    elif fmt == "m4a":
        codec_args = ["-c:a", "aac", "-b:a", "192k"]
    else:
        codec_args = ["-c:a", "pcm_s16le"]

    import subprocess
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", str(start), "-to", str(end), "-i", str(input_path),
        *codec_args, str(output_path),
    ]

    try:
        subprocess.run(cmd, check=True, timeout=300)
    except FileNotFoundError:
        return jsonify({"error": "FFmpeg is not installed on the server."}), 500
    except subprocess.CalledProcessError:
        return jsonify({"error": "FFmpeg could not process this audio file."}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Processing timed out."}), 504

    session["last_output"] = str(output_path)

    # Send the finished audio back into the Telegram chat.
    if BOT_APP is not None:
        asyncio.run_coroutine_threadsafe(
            send_result(session["chat_id"], output_path, start, end),
            BOT_APP.bot_data["loop"],
        )

    return jsonify({
        "success": True,
        "filename": output_name,
        "duration": round(end - start, 2),
        "download_url": f"/download/{token}",
    })


@flask_app.get("/download/<token>")
def download(token):
    session = get_session(token)
    if not session or not session.get("last_output"):
        return jsonify({"error": "No finished audio available."}), 404
    path = Path(session["last_output"])
    if not path.exists():
        return jsonify({"error": "Output file no longer exists."}), 404
    return send_from_directory(path.parent, path.name, as_attachment=True)


async def send_result(chat_id, output_path, start, end):
    try:
        with open(output_path, "rb") as f:
            if Path(output_path).suffix.lower() in {".mp3", ".m4a"}:
                await BOT_APP.bot.send_audio(
                    chat_id=chat_id,
                    audio=f,
                    caption=f"✂️ Done! Cut from {format_time(start)} to {format_time(end)}.",
                )
            else:
                await BOT_APP.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    caption=f"✂️ Done! Cut from {format_time(start)} to {format_time(end)}.",
                )
    except Exception as exc:
        print("Failed to send result:", exc)


def format_time(seconds):
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def probe_duration(path):
    import subprocess
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True, timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🎵 Open Audio Cutter", web_app=WebAppInfo(url=WEB_APP_URL))]]
    await update.message.reply_text(
        "🎵 *Audio Cutter*\n\nSend me an audio file and I’ll let you trim it with a waveform interface.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send an audio file, then open the Audio Cutter interface. You can drag the start/end handles, preview the selection, choose an output format, and cut it."
    )


async def receive_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    tg_file = None
    filename = "audio.mp3"

    if message.audio:
        tg_file = await message.audio.get_file()
        filename = message.audio.file_name or filename
    elif message.voice:
        tg_file = await message.voice.get_file()
        filename = "voice.ogg"
    elif message.document and (message.document.mime_type or "").startswith("audio/"):
        tg_file = await message.document.get_file()
        filename = message.document.file_name or filename
    else:
        await message.reply_text("Please send an audio file (MP3, M4A, WAV, OGG, etc.).")
        return

    if message.audio and message.audio.file_size and message.audio.file_size > MAX_FILE_MB * 1024 * 1024:
        await message.reply_text(f"That file is too large. Maximum size is {MAX_FILE_MB} MB.")
        return

    safe = clean_name(filename)
    unique = f"{uuid.uuid4().hex}_{safe}"
    local_path = MEDIA_DIR / unique
    await tg_file.download_to_drive(custom_path=str(local_path))

    duration = probe_duration(local_path)
    token = secrets.token_urlsafe(18)
    SESSIONS[token] = {
        "chat_id": message.chat_id,
        "filename": filename,
        "path": str(local_path),
        "duration": duration,
        "size": local_path.stat().st_size,
    }

    url = f"{WEB_APP_URL.rstrip('/')}/?token={token}"
    keyboard = [[InlineKeyboardButton("✂️ Open Audio Cutter", web_app=WebAppInfo(url=url))]]
    await message.reply_text(
        f"🎵 *{filename}*\nDuration: `{format_time(duration)}`\n\nChoose the section you want to cut:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


async def main():
    global BOT_APP
    app = Application.builder().token(BOT_TOKEN).build()
    BOT_APP = app
    loop = asyncio.get_running_loop()
    app.bot_data["loop"] = loop

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.AUDIO | filters.VOICE | filters.Document.AUDIO, receive_audio))

    from threading import Thread
    Thread(target=run_flask, daemon=True).start()

    print(f"Web app running on port {PORT}")
    print("Bot polling started")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
