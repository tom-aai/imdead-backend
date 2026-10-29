"""
I'm Dead — local backend.

Three jobs, all run on the laptop against external APIs:
  - record the guest's consent (the legal record),
  - clone their voice with AssemblyAI (AssemblyAI-TTS-1),
  - ghost their photo with Gemini.

No Supabase, no auth, no job queue, no billing. State is the local filesystem
under ./data/<session_id>/ (see services/storage.py). The slow work (clone,
ghost) runs synchronously behind the frontend's "please wait" screen, which
polls GET /status/<session_id>.
"""

import os

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from dotenv import load_dotenv

from services import storage
from services.storage import InvalidSessionId
from services.ghost_generator import GhostGenerator
from services.voice_cloner import AssemblyAIVoiceCloner, VoiceCloneError
from services.logging_config import configure_logging, get_logger

# Prefer .env.local, fall back to .env.
if not load_dotenv(".env.local"):
    load_dotenv()

configure_logging()
logger = get_logger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB (clip <=16 MiB + photo)

# Local demo: the frontend runs on another localhost port, so allow cross-origin.
CORS(app)

# Lazily-built singletons so the app boots (and /health works) even if a key is
# missing; the relevant endpoint then fails with a clear message.
_ghost_generator: GhostGenerator | None = None
_voice_cloner: AssemblyAIVoiceCloner | None = None


def ghost_generator() -> GhostGenerator:
    global _ghost_generator
    if _ghost_generator is None:
        _ghost_generator = GhostGenerator()
    return _ghost_generator


def voice_cloner() -> AssemblyAIVoiceCloner:
    global _voice_cloner
    if _voice_cloner is None:
        _voice_cloner = AssemblyAIVoiceCloner()
    return _voice_cloner


@app.route("/")
def index():
    return jsonify({"service": "imdead-backend", "status": "ok"})


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


@app.route("/consent", methods=["POST"])
def consent_endpoint():
    """Record the legal consent for a session. JSON: {session_id, name}."""
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    name = (data.get("name") or "").strip()

    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        storage.save_consent(session_id, name)
    except InvalidSessionId as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"status": "recorded"})


@app.route("/clone-voice", methods=["POST"])
def clone_voice_endpoint():
    """Clone a voice. multipart: audio (file) + session_id. Returns {voice_id}."""
    session_id = request.form.get("session_id")
    try:
        storage.validate_session_id(session_id)
    except InvalidSessionId as e:
        return jsonify({"error": str(e)}), 400

    audio_file = request.files.get("audio")
    if audio_file is None:
        return jsonify({"error": "audio file is required"}), 400

    audio_data = audio_file.read()
    if not audio_data:
        return jsonify({"error": "audio file is empty"}), 400

    try:
        voice_id = voice_cloner().clone_voice(
            audio_data, filename=audio_file.filename or "voice-recording.webm"
        )
    except VoiceCloneError as e:
        logger.error(f"Voice clone failed for session {session_id}: {e}")
        return jsonify({"error": f"voice clone failed: {e}"}), 502

    storage.save_voice_id(session_id, voice_id)
    return jsonify({"voice_id": voice_id})


@app.route("/upload-image", methods=["POST"])
def upload_image_endpoint():
    """Store the photo and ghost it inline. multipart: file + session_id."""
    session_id = request.form.get("session_id")
    try:
        storage.validate_session_id(session_id)
    except InvalidSessionId as e:
        return jsonify({"error": str(e)}), 400

    image_file = request.files.get("file")
    if image_file is None:
        return jsonify({"error": "file is required"}), 400

    image_data = image_file.read()
    if not image_data:
        return jsonify({"error": "file is empty"}), 400

    original = storage.save_original(session_id, image_data)
    ghost_out = storage.ghost_path(session_id)
    try:
        ghost_generator().ghost_image(str(original), str(ghost_out))
    except Exception as e:
        logger.error(f"Ghost generation failed for session {session_id}: {e}")
        return jsonify({"error": f"ghost generation failed: {e}"}), 502

    return jsonify({"status": "ready"})


@app.route("/ghost-image/<session_id>", methods=["GET"])
def ghost_image_endpoint(session_id: str):
    """Serve the ghost PNG. The agent fetches this to drive LemonSlice."""
    try:
        path = storage.ghost_path(session_id)
    except InvalidSessionId as e:
        return jsonify({"error": str(e)}), 400
    if not path.exists():
        return jsonify({"error": "ghost image not ready"}), 404
    return send_file(path, mimetype="image/png")


@app.route("/status/<session_id>", methods=["GET"])
def status_endpoint(session_id: str):
    """Readiness for the spinner: {voice: bool, ghost: bool}."""
    try:
        return jsonify(storage.status(session_id))
    except InvalidSessionId as e:
        return jsonify({"error": str(e)}), 400


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5001"))
    logger.info(f"Starting imdead-backend on port {port}; data dir {storage.DATA_ROOT}")
    app.run(host="0.0.0.0", port=port, debug=True)
