"""
Voice cloning via the AssemblyAI Voices API (AssemblyAI-TTS-1).

This is net-new: the old backend cloned with Inworld/Cartesia. The clone is a
two-call sequence against the US data-zone host, with the raw API key in the
Authorization header:

  1. POST /v1/voices            (multipart) -> 201 {"id": "<uuid>"}
  2. POST /v1/voices/{id}/pairings  (JSON)  -> voice is ready to speak

A voice MUST be paired before it can speak; an unpaired voice fails every TTS
turn with 3014 voice_not_paired, so this blocks until pairing is ready.

Account prerequisites (one-time): the account must have accepted the biometric
data terms (dashboard -> data controls) and have a positive balance.

The frontend records ~10s of 48 kHz mono WebM/Opus, which the Voices API accepts
directly, so we send the WebM straight through. The pydub -> WAV conversion is
kept only as a fallback for a clip the API rejects.
"""

import os
import tempfile
import uuid

import requests

from services.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_TTS_HOST = "https://tts.us.assemblyai.com"
PAIRING_MODEL = "assemblyai-tts-1"

# Pairing usually takes a few seconds, up to ~30. Give the blocking call headroom.
PAIRING_TIMEOUT_S = 35
CREATE_TIMEOUT_S = 60


class VoiceCloneError(Exception):
    """Raised when creating or pairing a cloned voice fails."""


class AssemblyAIVoiceCloner:
    def __init__(self, api_key: str | None = None, tts_host: str | None = None):
        self.api_key = api_key or os.getenv("ASSEMBLYAI_API_KEY")
        if not self.api_key:
            raise VoiceCloneError(
                "ASSEMBLYAI_API_KEY not configured. Set it in the environment."
            )
        self.tts_host = (tts_host or os.getenv("ASSEMBLYAI_TTS_HOST") or DEFAULT_TTS_HOST).rstrip("/")

    @property
    def _auth_headers(self) -> dict:
        return {"Authorization": self.api_key}

    def clone_voice(self, audio_data: bytes, filename: str = "voice-recording.webm") -> str:
        """
        Clone a voice from a recorded clip and return its paired voice_id.

        Sends the clip as-is first (WebM/Opus is accepted). If the API rejects
        the clip, converts it to WAV with pydub and retries once.
        """
        voice_id = self._create_voice(audio_data, filename)
        self._pair_voice(voice_id)
        logger.info(f"Voice cloned and paired: {voice_id}")
        return voice_id

    # --- step 1: create --------------------------------------------------

    def _create_voice(self, audio_data: bytes, filename: str) -> str:
        """POST /v1/voices. Returns the new voice's id."""
        try:
            return self._post_create(audio_data, filename)
        except VoiceCloneError as first_error:
            # Fallback: a clip the API rejected may convert and succeed as WAV.
            logger.warning(
                f"Create voice failed ({first_error}); retrying with WAV conversion"
            )
            wav_data = self._convert_to_wav(audio_data)
            if wav_data is None:
                raise
            return self._post_create(wav_data, "voice-recording.wav")

    def _post_create(self, audio_data: bytes, filename: str) -> str:
        url = f"{self.tts_host}/v1/voices"
        # Unique per account; the API requires a unique name.
        name = f"imdead-{uuid.uuid4().hex[:12]}"
        headers = {**self._auth_headers, "Idempotency-Key": str(uuid.uuid4())}
        files = {"file": (filename, audio_data)}
        data = {
            "name": name,
            "language": "english",
            "consent_attested": "true",
        }

        logger.info(f"Creating AssemblyAI voice {name} ({len(audio_data)} bytes)")
        try:
            resp = requests.post(
                url, headers=headers, files=files, data=data, timeout=CREATE_TIMEOUT_S
            )
        except requests.RequestException as e:
            raise VoiceCloneError(f"Create voice request failed: {e}") from e

        if resp.status_code not in (200, 201):
            raise VoiceCloneError(
                f"Create voice returned {resp.status_code}: {_err_text(resp)}"
            )

        voice_id = (resp.json() or {}).get("id")
        if not voice_id:
            raise VoiceCloneError(f"Create voice response had no id: {resp.text[:500]}")
        logger.info(f"Created voice {voice_id}")
        return voice_id

    # --- step 2: pair ----------------------------------------------------

    def _pair_voice(self, voice_id: str) -> None:
        """POST /v1/voices/{id}/pairings. Blocks until the voice is ready."""
        url = f"{self.tts_host}/v1/voices/{voice_id}/pairings"
        logger.info(f"Pairing voice {voice_id} with {PAIRING_MODEL}")
        try:
            resp = requests.post(
                url,
                headers=self._auth_headers,
                json={"model": PAIRING_MODEL},
                timeout=PAIRING_TIMEOUT_S,
            )
        except requests.RequestException as e:
            raise VoiceCloneError(f"Pair voice request failed: {e}") from e

        if resp.status_code not in (200, 201, 202, 204):
            raise VoiceCloneError(
                f"Pair voice returned {resp.status_code}: {_err_text(resp)}"
            )
        logger.info(f"Paired voice {voice_id}")

    # --- fallback: webm -> wav -------------------------------------------

    @staticmethod
    def _convert_to_wav(audio_data: bytes) -> bytes | None:
        """
        Convert a recorded clip to mono 48 kHz 16-bit WAV with pydub.

        Fallback only — the API accepts the WebM directly. Returns None if the
        clip can't be decoded.
        """
        try:
            from pydub import AudioSegment
        except ImportError:
            logger.warning("pydub not available; cannot convert clip to WAV")
            return None

        is_webm = audio_data[:4] == b"\x1a\x45\xdf\xa3"  # WebM/Matroska magic bytes
        suffix = ".webm" if is_webm else ".bin"
        src = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        out_path = None
        try:
            src.write(audio_data)
            src.close()
            audio = AudioSegment.from_file(src.name, format="webm" if is_webm else None)
            audio = audio.set_channels(1).set_frame_rate(48000).set_sample_width(2)
            out_path = tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name
            audio.export(out_path, format="wav")
            with open(out_path, "rb") as f:
                return f.read()
        except Exception as e:
            logger.warning(f"WAV conversion failed: {e}")
            return None
        finally:
            for p in (src.name, out_path):
                if p and os.path.exists(p):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass


def _err_text(resp: requests.Response) -> str:
    """Best-effort, concise error text from an AssemblyAI response."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:500]
    if isinstance(body, dict):
        return str(body.get("error") or body.get("detail") or body)[:500]
    return str(body)[:500]
