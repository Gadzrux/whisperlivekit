# WhisperLive + LiveKit + FastAPI POC

A proof-of-concept that wires together:

| Component | Role |
|-----------|------|
| **[WhisperLive](https://github.com/collabora/WhisperLive)** (external) | Whisper-based transcription server over WebSocket |
| **LiveKit agent worker** | Joins LiveKit rooms, runs Silero VAD, forwards speech segments to WhisperLive |
| **FastAPI** | REST API: `/health`, `/config`, `POST /token`, and a tiny browser test UI at `/` |

```
Client ──(join room)──► LiveKit Cloud/self-hosted
                              │  audio tracks
                              ▼
                       LiveKit Agent Worker
                              │  VAD-segmented frames
                              ▼
                    WhisperLiveSTT adapter
                              │  f32le 16 kHz WebSocket
                              ▼
                    WhisperLive Server :9090
                              │  JSON segments
                              └──► logged / published back
```

## Prerequisites

- **Python >= 3.11** and **[uv](https://docs.astral.sh/uv/)** installed
- A **LiveKit** server (cloud or self-hosted) with API credentials
- A running **WhisperLive** server (see below)

## Install

```bash
git clone <this-repo>
cd whisperlivekit
cp .env.example .env   # fill in your LiveKit credentials
uv sync                # installs everything into .venv
```

## Run with Docker Compose

This repo includes a `docker-compose.yml` that starts:

- `api` - FastAPI server and static test UI on `http://localhost:8080/`
- `worker` - LiveKit agent worker
- `whisperlive` - WhisperLive GPU container on port `9090`

Prerequisites:

- Docker Engine with Compose support
- NVIDIA Container Toolkit installed on the host GPU VM

Start everything:

```bash
cp .env.example .env
docker compose up --build
```

Then open `http://localhost:8080/`.

Notes:

- The Compose stack overrides the worker's `WHISPERLIVE_WS_URL` to `ws://whisperlive:9090`, so the worker talks to the WhisperLive container over the internal Docker network.
- The `whisperlive` service uses `ghcr.io/collabora/whisperlive-gpu:latest`, which is intended for GPU-backed deployments.
- Model downloads/cache are persisted in the named Docker volume `whisperlive-cache`.

## Start WhisperLive (separate terminal / process)

WhisperLive runs independently. The quickest way is via its own install:

```bash
# In a separate directory / venv - NOT inside this project
pip install whisper-live
python -m whisper_live.run_server --port 9090 --backend faster_whisper
```

Or using the repo directly:

```bash
git clone https://github.com/collabora/WhisperLive
cd WhisperLive
pip install -r requirements/server.txt
python run_server.py --port 9090 --backend faster_whisper
```

Once running you should see `Listening on port 9090`.

> **Tip:** set `WHISPERLIVE_MODEL=base.en` in `.env` for better accuracy on
> English-only audio, at the cost of slightly higher latency.

## Run the FastAPI server

```bash
uv run uvicorn app.api:app --host 0.0.0.0 --port 8080 --reload
```

Test it:

```bash
curl http://localhost:8080/health
# {"status":"ok"}

curl -X POST http://localhost:8080/token \
  -H 'Content-Type: application/json' \
  -d '{"room":"my-room","identity":"alice"}'
# {"token":"eyJ..."}
```

## Run the agent worker

```bash
# Development mode (auto-reconnects, verbose logging):
uv run python -m app.worker dev

# Production mode:
uv run python -m app.worker start
```

Or via the installed script:

```bash
uv run wlk-worker dev
```

The worker will connect to LiveKit, and whenever a participant publishes an
audio track the agent will:

1. Run **Silero VAD** to detect speech boundaries.
2. Resample each utterance to 16 kHz f32le.
3. Open a WebSocket to WhisperLive and stream the audio.
4. Log the returned transcript to stdout.
5. Publish the final transcript back into the LiveKit room as a data packet.

## Open the test UI

Open `http://localhost:8080/` in your browser.

1. Confirm the `LiveKit URL` field is correct.
2. Pick a room name and identity.
3. Click `Connect and Start Mic`.
4. Speak into your microphone.
5. Watch final transcripts appear in the page and in the worker logs.

The page uses the same `POST /token` endpoint as before, but hides the manual
token copy/paste step. If you still want to use another LiveKit-compatible
client, that also continues to work.

## Configuration (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `LIVEKIT_URL` | - | `wss://` URL of your LiveKit server |
| `LIVEKIT_API_KEY` | - | LiveKit API key |
| `LIVEKIT_API_SECRET` | - | LiveKit API secret |
| `WHISPERLIVE_WS_URL` | `ws://localhost:9090` | WhisperLive WebSocket URL |
| `WHISPERLIVE_LANGUAGE` | `en` | Language code passed to Whisper |
| `WHISPERLIVE_MODEL` | `tiny` | Whisper model name (must be loaded by the server) |
| `WHISPERLIVE_READY_TIMEOUT` | `60` | Seconds to wait for WhisperLive to send `SERVER_READY` while loading the model |
| `WHISPERLIVE_RECV_TIMEOUT` | `15` | Seconds to wait for transcript responses after audio has been streamed |

If you run larger models or the first request has to cold-load the model into GPU
memory, increase `WHISPERLIVE_READY_TIMEOUT`. A timeout during `SERVER_READY`
usually means WhisperLive is still loading the model rather than that the socket
URL is wrong.

## Project layout

```
app/
  api.py            FastAPI app (/health, /config, /token, /)
  static/
    index.html      Minimal browser UI for transcription testing
  worker.py         LiveKit agent worker entrypoint
  stt/
    whisperlive.py  Async WhisperLive STT adapter (core logic)
    __init__.py
pyproject.toml      uv project & dependencies
.dockerignore       Docker build exclusions
Dockerfile.app      Application image for API and worker
docker-compose.yml  Full stack: API, worker, WhisperLive GPU
.env.example        Environment variable template
```

## Extending

- **Publish transcripts to the room**: use `ctx.room.local_participant.publish_data()` inside `on_transcript`.
- **Multiple languages**: set `WHISPERLIVE_LANGUAGE` per deployment, or pass `language` per `recognize()` call.
- **Diarization / translation**: configure the WhisperLive server and pass `task="translate"` in `WhisperLiveSTT`.
