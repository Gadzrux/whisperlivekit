"""FastAPI app — browser mic → faster-whisper transcription."""
from __future__ import annotations

import asyncio
import io
import logging
import os
from pathlib import Path

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from faster_whisper import WhisperModel
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("whisperlivekit")

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "hi")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "int8")

app = FastAPI(title="WhisperLiveKit", version="0.3.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"

# Load model once at startup
logger.info("Loading faster-whisper model '%s' (device=%s, compute=%s)...", WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE)
model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
logger.info("Model loaded.")

TARGET_SR = 16000
# Buffer ~2s of audio before transcribing (16000 samples/s * 2s * 4 bytes/sample)
MIN_BUFFER_BYTES = TARGET_SR * 2 * 4


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/config")
async def config() -> dict:
    return {"language": WHISPER_LANGUAGE, "model": WHISPER_MODEL}


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def transcribe_audio(audio: np.ndarray, language: str) -> str:
    """Run faster-whisper on a numpy float32 array. Called in a thread."""
    segments, info = model.transcribe(
        audio,
        language=language,
        beam_size=5,
        vad_filter=True,
    )
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return text


@app.websocket("/ws/transcribe")
async def ws_transcribe(ws: WebSocket) -> None:
    """Receive f32le 16kHz mono audio from browser, transcribe, send text back."""
    await ws.accept()
    await ws.send_json({"type": "status", "status": "ready"})
    logger.info("Browser connected for transcription")

    audio_buffer = bytearray()
    chunks = 0
    disconnected = False
    loop = asyncio.get_running_loop()

    async def transcribe_and_send(pcm_data: np.ndarray) -> None:
        """Transcribe audio in a thread and send the result back over WS."""
        duration = pcm_data.size / TARGET_SR
        logger.info("Transcribing %.1fs of audio...", duration)
        text = await loop.run_in_executor(None, transcribe_audio, pcm_data, WHISPER_LANGUAGE)
        if text:
            logger.info("Transcript: %s", text)
            if not disconnected:
                try:
                    await ws.send_json({"type": "transcript", "text": text})
                except Exception:
                    logger.debug("Could not send transcript (client gone)")

    pending_tasks: list[asyncio.Task] = []

    try:
        while True:
            data = await ws.receive_bytes()
            audio_buffer.extend(data)
            chunks += 1

            if chunks == 1:
                logger.info("First audio chunk: %d bytes", len(data))

            # Transcribe when we have enough audio buffered
            if len(audio_buffer) >= MIN_BUFFER_BYTES:
                pcm = np.frombuffer(bytes(audio_buffer), dtype=np.float32)
                audio_buffer.clear()

                if pcm.size == 0:
                    continue

                # Fire off transcription concurrently — don't block receiving
                task = asyncio.create_task(transcribe_and_send(pcm))
                pending_tasks.append(task)

                # Clean up finished tasks
                pending_tasks = [t for t in pending_tasks if not t.done()]

    except WebSocketDisconnect:
        disconnected = True
        logger.info("Browser disconnected after %d chunks", chunks)
    except Exception:
        disconnected = True
        logger.exception("WebSocket error after %d chunks", chunks)
    finally:
        # Wait for any in-flight transcriptions to finish
        if pending_tasks:
            logger.info("Waiting for %d pending transcription(s)...", len(pending_tasks))
            await asyncio.gather(*pending_tasks, return_exceptions=True)

        # Transcribe leftover audio in buffer
        if len(audio_buffer) > 0:
            pcm = np.frombuffer(bytes(audio_buffer), dtype=np.float32)
            if pcm.size > 0:
                text = await loop.run_in_executor(None, transcribe_audio, pcm, WHISPER_LANGUAGE)
                if text:
                    logger.info("Final transcript (after disconnect): %s", text)
