# Lexia Read Mode

## What it is

Lexia Read Mode is the reading-assistant mode of OnRecord, built Oct 2026 for Bay-Valley Hacks. OnRecord is the glasses project for conversations. Lexia Read Mode adds reading help to the same glasses: point the glasses at text, or upload an image of text, and get that text back in short chunks that are easier to read, with optional read-aloud.

It runs as a standalone Python service on port 8100.

## Origin

Lexia is prior work by the same builder. The earlier Lexia (2026) was a reading assistant for people with dyslexia, built on a Raspberry Pi Zero 2W with Tesseract OCR and offline TTS. Devpost: https://devpost.com/software/lexia-iw5qzn

This is a new implementation as a mode inside OnRecord, not the old code. The goal is the same, helping people read printed and on-screen text, but the code here was written fresh for OnRecord in Oct 2026.

## How it works

1. A still image of text comes in, from the glasses or from an upload.
2. The service sends the image to the OCR layer, which extracts the text.
3. The text is cleaned up and split into short reading-friendly chunks.
4. An optional rule-based simplification pass can run on the chunks.
5. Chunks can be read aloud via local TTS.
6. A small unstyled web preview shows the chunked reader view.

Swift sources for the glasses bridge live in `lexia/swift/` (uncompiled, see Honest stub list below).

## Layout

- `lexia/ocr/` - OCR interface, Tesseract implementation, and a test double.
- `lexia/reading/` - Text cleanup, chunking, simplification, and sessions.
- `lexia/tts/` - TTS interface, local engine, and a test double.
- `lexia/api/` - FastAPI service, with a stdlib fallback server.
- `lexia/web/` - Preview page for the chunked reader view.
- `lexia/swift/` - Bridge sources for the glasses.
- `lexia/tests/` - Tests.

## Run it

From the repo root:

```bash
python3 -m venv lexia/.venv && lexia/.venv/bin/pip install -r lexia/requirements.txt
```

Then start the service:

```bash
lexia/.venv/bin/python -m uvicorn lexia.api.app:app --port 8100
```

Or use the stdlib fallback server:

```bash
lexia/.venv/bin/python -m lexia.api.server
```

The service listens on port 8100.

## API

- `GET /health` - Health check.
- `POST /read` - Submit an image of text and start a reading session.
- `GET /read/{id}` - Get a reading session and its chunks.
- `POST /read/{id}/speak` - Read a session's chunks aloud via TTS.
- `GET /audio/{file}` - Fetch a generated audio file.

## Configuration

Set these environment variables to change how the service runs:

- `LEXIA_OCR_ENGINE` - `tesseract` or `fake`.
- `LEXIA_TTS_ENGINE` - `local` or `fake`.
- `LEXIA_SIMPLIFY` - `rule` or `off`.
- `LEXIA_DATA_DIR` - Directory for session data and generated audio.

## Tests

From the repo root:

```bash
lexia/.venv/bin/python -m pytest lexia/tests -q
```

## Honest stub list

This section is frank about what is not done yet:

- There is no tesseract binary on the dev VM, so the real OCR path is written but has not yet been run against the binary here. Tests use the fake OCR engine.
- pyttsx3 needs espeak on Linux, which is absent here, so local TTS raises unavailable in this environment. Tests use the fake engine, which writes silent WAV files of the right duration.
- Simplification is rule-based only. There is no LLM in the simplification pass.
- The Swift sources in `lexia/swift/` are uncompiled and have not been tested on a device.
- The web preview is intentionally unstyled. Design comes later.

## License note

Built for Bay-Valley Hacks, Oct 2026.
