# Telegram Audio Cutter Bot

A Telegram bot with a mobile-friendly Telegram Mini App for trimming audio with a waveform UI.

## Features

- `/start` button opens the Audio Cutter Mini App
- Accepts Telegram audio, voice messages, and audio documents
- Waveform preview in the Mini App
- Drag start/end handles
- Exact timestamp inputs
- ±10 second controls
- Play/pause preview
- MP3, M4A, and WAV output
- FFmpeg processing
- Sends the finished audio back to the Telegram chat
- Download button in the Mini App
- Docker setup with FFmpeg included

## 1. Create the Telegram bot

Open **@BotFather** in Telegram and create a bot with `/newbot`.
Copy the bot token.

## 2. Local setup

Install FFmpeg and Python 3.11+.

```bash
pip install -r requirements.txt
```

Set environment variables:

```bash
export BOT_TOKEN="YOUR_BOT_TOKEN"
export WEB_APP_URL="http://localhost:8080"
```

Run:

```bash
python bot.py
```

For a real Telegram Mini App, Telegram normally needs an HTTPS URL. Use a tunnel such as Cloudflare Tunnel or deploy the app to a host with HTTPS.

## 3. Docker

```bash
docker build -t telegram-audio-cutter .
docker run --rm -p 8080:8080 \
  -e BOT_TOKEN="YOUR_BOT_TOKEN" \
  -e WEB_APP_URL="https://your-domain.example.com" \
  telegram-audio-cutter
```

## 4. Deployment

The included Dockerfile works well on hosts that support Docker containers. Set:

- `BOT_TOKEN` = your BotFather token
- `WEB_APP_URL` = the public HTTPS URL of this service
- `PORT` = the port supplied by the hosting provider, if required

The app currently stores uploaded and processed files on local disk. For production at scale, add persistent/object storage and cleanup jobs.

## Important Telegram limit

Telegram Bot API file limits and hosting limits can affect the maximum upload size. This project defaults to 50 MB for normal audio messages. Adjust `MAX_FILE_MB` if your deployment and Telegram setup support a different limit.

## Security notes

- Session tokens are random and stored in memory.
- Do not expose `BOT_TOKEN` in frontend code.
- Use HTTPS in production.
- Add authentication based on Telegram Web App `initData` before using this as a public production service.
- Add automatic cleanup for old files before running at scale.
