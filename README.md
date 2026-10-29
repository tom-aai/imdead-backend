# imdead-backend

Local backend for the "I'm Dead" Halloween demo. Runs on the laptop, calls out
to external APIs. No Supabase, no auth, no job queue — state is the local
filesystem under `./data/<session_id>/`.

## What it does

- `POST /consent` — record the legal consent (who, when, scope). JSON `{session_id, name}` → `{status:"recorded"}`. Writes `data/<session_id>/consent.json` with a server-stamped UTC `consented_at`.
- `POST /clone-voice` — clone the voice with AssemblyAI (AssemblyAI-TTS-1). multipart `audio` + `session_id` → `{voice_id}`. Creates then pairs the voice (blocks until ready). Persists `data/<session_id>/voice.json`.
- `POST /upload-image` — store the webcam photo and ghost it with Gemini inline. multipart `file` + `session_id` → `{status:"ready"}`. Writes `original.png` and `ghost.png`.
- `GET /ghost-image/<session_id>` — the ghost PNG bytes (the agent fetches this to drive LemonSlice).
- `GET /status/<session_id>` — `{voice: bool, ghost: bool}` for the frontend's "please wait" spinner.
- `GET /health` — liveness.

The slow work (clone + pairing ~30-40s, ghost a few seconds) runs synchronously
behind the frontend's "please wait" screen, which polls `/status`.

## Setup & run

```bash
cp .env.example .env.local   # then fill in ASSEMBLYAI_API_KEY and GOOGLE_API_KEY
uv sync
uv run python app.py         # serves on http://localhost:5001
```

The WebM→WAV fallback in voice cloning needs `ffmpeg` on PATH (`brew install ffmpeg`);
it's only used if AssemblyAI rejects the raw WebM clip.
