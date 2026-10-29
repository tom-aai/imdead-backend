"""
Local filesystem storage for the "I'm Dead" demo.

Replaces every Supabase storage/DB call from the old backend. Everything a guest
produces lives under ./data/<session_id>/:

    original.png   the webcam capture, as uploaded
    ghost.png      the Gemini-ghosted image the agent drives LemonSlice with
    voice.json     {"voice_id": "<assemblyai-voice-uuid>"}
    consent.json   {"name", "consented", "consented_at", "scope": [...]}  (legal record)

No auth, no job queue, no database. One directory per guest, keyed by the opaque
session_id the frontend mints.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from services.logging_config import get_logger

logger = get_logger(__name__)

# Root for all per-session data. Override with DATA_DIR for a different location.
DATA_ROOT = Path(os.getenv("DATA_DIR", "./data")).resolve()

# session_id comes from the client, so it names a directory under DATA_ROOT and
# must never be allowed to traverse out of it. The frontend mints a UUID; accept
# that shape (hex + hyphens) and nothing else.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

ORIGINAL_NAME = "original.png"
GHOST_NAME = "ghost.png"
VOICE_NAME = "voice.json"
CONSENT_NAME = "consent.json"


class InvalidSessionId(ValueError):
    """Raised when a session_id is missing or not a safe directory name."""


def validate_session_id(session_id: str) -> str:
    """Return session_id if it is a safe single path segment, else raise."""
    if not session_id or not isinstance(session_id, str):
        raise InvalidSessionId("session_id is required")
    if not _SESSION_ID_RE.match(session_id) or "/" in session_id or "\\" in session_id:
        raise InvalidSessionId(f"invalid session_id: {session_id!r}")
    return session_id


def session_dir(session_id: str, create: bool = True) -> Path:
    """Resolve (and by default create) the data directory for a session."""
    validate_session_id(session_id)
    path = (DATA_ROOT / session_id).resolve()
    # Defense in depth: the resolved path must stay inside DATA_ROOT.
    if path.parent != DATA_ROOT:
        raise InvalidSessionId(f"invalid session_id: {session_id!r}")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


# --- images ----------------------------------------------------------------


def original_path(session_id: str) -> Path:
    return session_dir(session_id) / ORIGINAL_NAME


def ghost_path(session_id: str) -> Path:
    return session_dir(session_id) / GHOST_NAME


def save_original(session_id: str, data: bytes) -> Path:
    path = original_path(session_id)
    path.write_bytes(data)
    logger.info(f"Saved original image for session {session_id} ({len(data)} bytes)")
    return path


def has_ghost(session_id: str) -> bool:
    try:
        return ghost_path(session_id).exists()
    except InvalidSessionId:
        return False


def read_ghost(session_id: str) -> bytes:
    return ghost_path(session_id).read_bytes()


# --- voice.json ------------------------------------------------------------


def save_voice_id(session_id: str, voice_id: str) -> Path:
    path = session_dir(session_id) / VOICE_NAME
    path.write_text(json.dumps({"voice_id": voice_id}))
    logger.info(f"Saved voice_id for session {session_id}")
    return path


def read_voice_id(session_id: str) -> str | None:
    path = session_dir(session_id, create=False) / VOICE_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text()).get("voice_id")
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not read {VOICE_NAME} for session {session_id}: {e}")
        return None


def has_voice(session_id: str) -> bool:
    return bool(read_voice_id(session_id))


# --- consent.json (the legal record) ---------------------------------------


def save_consent(session_id: str, name: str) -> dict:
    """Write the legal consent record with a server-stamped UTC timestamp."""
    record = {
        "name": name,
        "consented": True,
        # Server-stamped; never trust a client clock for the legal record.
        "consented_at": datetime.now(timezone.utc).isoformat(),
        "scope": ["voice", "likeness"],
    }
    path = session_dir(session_id) / CONSENT_NAME
    path.write_text(json.dumps(record, indent=2))
    logger.info(f"Recorded consent for session {session_id}")
    return record


def read_consent(session_id: str) -> dict | None:
    path = session_dir(session_id, create=False) / CONSENT_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not read {CONSENT_NAME} for session {session_id}: {e}")
        return None


# --- status ----------------------------------------------------------------


def status(session_id: str) -> dict:
    """Readiness for the frontend spinner: are the voice and ghost both ready?"""
    return {"voice": has_voice(session_id), "ghost": has_ghost(session_id)}
