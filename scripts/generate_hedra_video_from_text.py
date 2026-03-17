"""
Generate a Hedra avatar video from a text string.

Takes text input, generates TTS audio (Inworld or Cartesia),
then sends it to Hedra to produce an avatar video. Saves the
raw Hedra output locally — no cropping or captions.

Usage:
    python scripts/generate_hedra_video_from_text.py "Your text here" --avatar-id <hedra_asset_id> --voice-id <voice_id>

    # Use Cartesia TTS instead of Inworld (default):
    python scripts/generate_hedra_video_from_text.py "Your text here" --avatar-id <id> --voice-id <id> --tts-provider cartesia

    # Custom output path:
    python scripts/generate_hedra_video_from_text.py "Your text here" --avatar-id <id> --voice-id <id> --output my_video.mp4
"""

import argparse
import base64
import json
import logging
import os
import sys
import time
import wave
from pathlib import Path

import requests
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

AI_MODEL_ID = "26f0fc66-152b-40ab-abed-76c43df99bc8"


class HedraSession(requests.Session):
    """Custom session class that prepends the base URL to all requests."""

    def __init__(self, api_key: str):
        super().__init__()
        self.base_url = "https://api.hedra.com/web-app/public"
        self.headers["x-api-key"] = api_key

    def request(self, method, url, *args, **kwargs):
        full_url = f"{self.base_url}{url}"
        return super().request(method, full_url, *args, **kwargs)


def load_env():
    backend_dir = Path(__file__).parent.parent
    dotenv_path = backend_dir / ".env.local"

    if not dotenv_path.exists():
        logger.error(f"Error: .env.local not found at {dotenv_path}")
        sys.exit(1)

    load_dotenv(dotenv_path)
    logger.info(f"Loaded environment variables from {dotenv_path}")


# ---------------------------------------------------------------------------
# TTS — Inworld  (matches service code in hot_take_video_generator.py)
# ---------------------------------------------------------------------------

def generate_audio_with_inworld(text: str, voice_id: str, api_key: str) -> str:
    """Generate TTS audio via Inworld and return the path to the WAV file."""
    tts_start = time.time()

    url = "https://api.inworld.ai/tts/v1/voice"
    headers = {
        "Authorization": f"Basic {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "text": text,
        "voiceId": voice_id,
        "modelId": "inworld-tts-1-max",
        "audioConfig": {
            "audioEncoding": "LINEAR16",
            "sampleRateHertz": 44100,
            "speakingRate": 0.85,
        },
        "temperature": 1.1,
        "timestampType": "WORD",
    }

    logger.info(f"Inworld TTS: generating audio ({len(text)} chars)")
    response = requests.post(url, headers=headers, json=payload, timeout=300)

    if not response.ok:
        raise Exception(f"Inworld API error: {response.status_code} {response.text}")

    result = response.json()

    audio_content_b64 = result.get("audioContent")
    if not audio_content_b64:
        raise Exception("No audio content in Inworld response")

    audio_bytes = base64.b64decode(audio_content_b64)

    output_path = os.path.join(os.getcwd(), "tts_audio.wav")
    with open(output_path, "wb") as f:
        f.write(audio_bytes)

    duration_ms = int((time.time() - tts_start) * 1000)
    logger.info(f"Inworld TTS complete: {len(audio_bytes)} bytes, {duration_ms}ms")
    return output_path


# ---------------------------------------------------------------------------
# TTS — Cartesia  (matches service code in hot_take_video_generator.py)
# ---------------------------------------------------------------------------

def generate_audio_with_cartesia(text: str, voice_id: str, api_key: str) -> str:
    """Generate TTS audio via Cartesia SSE and return the path to the WAV file."""
    tts_start = time.time()

    url = "https://api.cartesia.ai/tts/sse"
    headers = {
        "X-API-Key": api_key,
        "Cartesia-Version": "2024-06-10",
        "Content-Type": "application/json",
    }

    payload = {
        "model_id": "sonic-english",
        "transcript": text,
        "voice": {"mode": "id", "id": voice_id},
        "output_format": {
            "container": "raw",
            "encoding": "pcm_s16le",
            "sample_rate": 44100,
        },
        "language": "en",
        "add_timestamps": True,
        "generation_config": {
            "speed": 0.85,
        },
    }

    logger.info(f"Cartesia TTS: generating audio ({len(text)} chars)")
    response = requests.post(url, headers=headers, json=payload, stream=True)

    if not response.ok:
        raise Exception(f"Cartesia API error: {response.status_code} {response.text}")

    audio_chunks = []

    for line in response.iter_lines():
        if not line:
            continue
        line = line.decode("utf-8")
        if line.startswith("event:"):
            continue
        if line.startswith("data:"):
            data_str = line[5:].strip()
            try:
                data = json.loads(data_str)
                if data.get("type") == "chunk" and "data" in data:
                    audio_chunks.append(base64.b64decode(data["data"]))
            except json.JSONDecodeError:
                continue

    raw_audio = b"".join(audio_chunks)

    output_path = os.path.join(os.getcwd(), "tts_audio.wav")
    with wave.open(output_path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(44100)
        wav_file.writeframes(raw_audio)

    duration_ms = int((time.time() - tts_start) * 1000)
    logger.info(f"Cartesia TTS complete: {len(raw_audio)} bytes, {duration_ms}ms")
    return output_path


# ---------------------------------------------------------------------------
# Hedra video generation  (matches service code in hot_take_video_generator.py)
# ---------------------------------------------------------------------------

def upload_audio_file(session: HedraSession, audio_path: str) -> str:
    """Upload audio to Hedra and return the asset ID."""
    logger.info(f"Uploading audio to Hedra: {audio_path}")

    create_response = session.post(
        "/assets", json={"name": os.path.basename(audio_path), "type": "audio"}
    )
    if not create_response.ok:
        raise Exception(f"Failed to create audio asset: {create_response.text}")

    audio_id = create_response.json()["id"]
    logger.info(f"Created audio asset: {audio_id}")

    with open(audio_path, "rb") as f:
        upload_response = session.post(
            f"/assets/{audio_id}/upload", files={"file": f}
        )
    if not upload_response.ok:
        raise Exception(f"Failed to upload audio file: {upload_response.text}")

    logger.info(f"Audio uploaded: {audio_id}")
    return audio_id


def create_video_generation(
    session: HedraSession,
    image_asset_id: str,
    audio_asset_id: str,
    text_prompt: str,
    aspect_ratio: str,
    resolution: str,
) -> str:
    """Create a Hedra video generation and return the generation ID."""
    logger.info(
        f"Creating Hedra generation (model={AI_MODEL_ID}, "
        f"resolution={resolution}, aspect_ratio={aspect_ratio})"
    )

    generation_data = {
        "type": "video",
        "ai_model_id": AI_MODEL_ID,
        "start_keyframe_id": image_asset_id,
        "audio_id": audio_asset_id,
        "generated_video_inputs": {
            "text_prompt": text_prompt,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
        },
    }

    logger.info(f"Hedra generation payload: {json.dumps(generation_data, indent=2)}")

    response = session.post("/generations", json=generation_data)
    logger.info(f"Hedra generation response: {response.status_code} {response.text}")
    if not response.ok:
        raise Exception(f"Failed to create generation: {response.text}")

    result = response.json()
    generation_id = result["id"]
    logger.info(f"Generation created: {generation_id}")
    return generation_id


def poll_generation_status(session: HedraSession, generation_id: str) -> dict:
    """Poll until the generation completes or errors."""
    logger.info(f"Polling generation status: {generation_id}")
    poll_count = 0

    while True:
        response = session.get(f"/generations/{generation_id}/status")
        if not response.ok:
            raise Exception(f"Failed to get generation status: {response.text}")

        status_data = response.json()
        status = status_data["status"]
        poll_count += 1

        if poll_count % 6 == 0:
            logger.info(f"Still processing... (status: {status})")

        if status == "complete":
            logger.info("Generation complete!")
            return status_data
        elif status == "error":
            error_message = status_data.get("error_message", "Unknown error")
            raise Exception(f"Video generation failed: {error_message}")

        time.sleep(5)


def download_video(download_url: str, output_path: str) -> None:
    """Download the generated video."""
    logger.info(f"Downloading video to {output_path}")

    with requests.get(download_url, stream=True) as r:
        r.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

    file_size_mb = os.path.getsize(output_path) / 1024 / 1024
    logger.info(f"Video saved: {output_path} ({file_size_mb:.1f} MB)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate a Hedra avatar video from text input."
    )
    parser.add_argument("text", help="Text to speak")
    parser.add_argument("--avatar-id", required=True, help="Hedra image asset ID")
    parser.add_argument("--voice-id", required=True, help="TTS voice ID")
    parser.add_argument(
        "--tts-provider",
        choices=["inworld", "cartesia"],
        default="inworld",
        help="TTS provider (default: inworld)",
    )
    parser.add_argument(
        "--aspect-ratio",
        default="9:16",
        help="Video aspect ratio (default: 9:16)",
    )
    parser.add_argument(
        "--resolution",
        default="720p",
        choices=["540p", "720p"],
        help="Video resolution (default: 720p)",
    )
    parser.add_argument(
        "--text-prompt",
        default="professional interview conversation",
        help="Hedra text prompt describing the style",
    )
    parser.add_argument(
        "--output",
        default="hedra_output.mp4",
        help="Output video filename (default: hedra_output.mp4)",
    )
    args = parser.parse_args()

    load_env()

    hedra_api_key = os.getenv("HEDRA_API_KEY")
    if not hedra_api_key:
        logger.error("HEDRA_API_KEY not set in .env.local")
        sys.exit(1)

    # Generate TTS audio
    if args.tts_provider == "inworld":
        inworld_api_key = os.getenv("INWORLD_API_KEY")
        if not inworld_api_key:
            logger.error("INWORLD_API_KEY not set in .env.local")
            sys.exit(1)
        audio_path = generate_audio_with_inworld(args.text, args.voice_id, inworld_api_key)
    else:
        cartesia_api_key = os.getenv("CARTESIA_API_KEY")
        if not cartesia_api_key:
            logger.error("CARTESIA_API_KEY not set in .env.local")
            sys.exit(1)
        audio_path = generate_audio_with_cartesia(args.text, args.voice_id, cartesia_api_key)

    try:
        # Generate video with Hedra
        session = HedraSession(api_key=hedra_api_key)

        audio_id = upload_audio_file(session, audio_path)

        generation_id = create_video_generation(
            session=session,
            image_asset_id=args.avatar_id,
            audio_asset_id=audio_id,
            text_prompt=args.text_prompt,
            aspect_ratio=args.aspect_ratio,
            resolution=args.resolution,
        )

        status_data = poll_generation_status(session, generation_id)

        if not status_data.get("download_url"):
            raise Exception("No download URL in generation response")

        download_video(status_data["download_url"], args.output)

        logger.info("=" * 60)
        logger.info(f"SUCCESS! Video saved to: {args.output}")
        logger.info(f"TTS provider: {args.tts_provider}")
        logger.info(f"Avatar ID: {args.avatar_id}")
        logger.info(f"Text: {args.text[:100]}{'...' if len(args.text) > 100 else ''}")
        logger.info("=" * 60)

    finally:
        if os.path.exists(audio_path):
            os.unlink(audio_path)
            logger.info("Cleaned up temp audio file")


if __name__ == "__main__":
    main()
