import os
import sys
import time
import logging
from pathlib import Path
from dotenv import load_dotenv
import requests

# ============================================================================
# CONFIGURATION - Edit these values
# ============================================================================
IMAGE_ASSET_ID = "f9ee171e-c671-439a-95d3-a0bc1e320c27"  # Your Hedra avatar asset ID
AUDIO_FILE_PATH = "/Users/tom/Downloads/test.mp3"   # Path to your audio file. butterscotch_16
TEXT_PROMPT = "professional interview conversation"  # Description for the video. "professional business conversation with natural expressions". "attentive listening and speaking in a professional interview"
ASPECT_RATIO = "16:9"  # Options: "16:9", "9:16", "1:1"
RESOLUTION = "720p"    # Options: "540p", "720p"
OUTPUT_FILENAME = "generated_video.mp4"  # Output filename
# Optional parameters (set to None to not use them)
DURATION_SECONDS = None  # Duration in seconds (float), or None
SEED = None  # Seed for reproducibility (int), or None
# ============================================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Character-3 model ID
CHARACTER_3_MODEL_ID = "d1dd37a3-e39a-4854-a298-6510289f9cf2"


def load_api_key():
    """Load HEDRA_API_KEY from .env file."""
    # Load .env from the expert-avatar-backend directory
    backend_dir = Path(__file__).parent.parent
    dotenv_path = backend_dir / ".env.local"
    
    if not dotenv_path.exists():
        logger.error(f"❌ Error: .env file not found at {dotenv_path}")
        logger.error(f"   Please create a .env file with your HEDRA_API_KEY")
        sys.exit(1)
    
    load_dotenv(dotenv_path)
    api_key = os.getenv("HEDRA_API_KEY")
    
    if not api_key:
        logger.error(f"❌ Error: HEDRA_API_KEY not found in {dotenv_path}")
        logger.error(f"   Please add HEDRA_API_KEY=your_key_here to your .env file")
        sys.exit(1)
    
    logger.info(f"✓ Loaded HEDRA_API_KEY from {dotenv_path}")
    return api_key


class HedraSession(requests.Session):
    """Custom session class that prepends the base URL to all requests"""
    
    def __init__(self, api_key: str):
        super().__init__()
        self.base_url = "https://api.hedra.com/web-app/public"
        self.headers["x-api-key"] = api_key

    def request(self, method, url, *args, **kwargs):
        full_url = f"{self.base_url}{url}"
        return super().request(method, full_url, *args, **kwargs)


def upload_audio_file(session: HedraSession, audio_path: str) -> str:
    """Upload an audio file to Hedra and return the asset ID"""
    logger.info(f"Uploading audio file: {audio_path}")
    
    # Create the audio asset
    create_response = session.post(
        "/assets",
        json={"name": os.path.basename(audio_path), "type": "audio"}
    )
    
    if not create_response.ok:
        logger.error(
            f"Error creating audio asset: {create_response.status_code} {create_response.text}"
        )
        raise Exception(f"Failed to create audio asset: {create_response.text}")
    
    audio_id = create_response.json()["id"]
    logger.info(f"Created audio asset with ID: {audio_id}")
    
    # Upload the audio file
    with open(audio_path, "rb") as f:
        upload_response = session.post(
            f"/assets/{audio_id}/upload",
            files={"file": f}
        )
    
    if not upload_response.ok:
        logger.error(
            f"Error uploading audio file: {upload_response.status_code} {upload_response.text}"
        )
        raise Exception(f"Failed to upload audio file: {upload_response.text}")
    
    logger.info(f"Successfully uploaded audio file with ID: {audio_id}")
    return audio_id


def create_video_generation(
    session: HedraSession,
    image_asset_id: str,
    audio_asset_id: str,
    text_prompt: str,
    aspect_ratio: str,
    resolution: str,
    duration_seconds: float | None = None,
    seed: int | None = None,
) -> str:
    """Create a video generation request and return the generation ID"""
    logger.info("Creating video generation request")
    
    generation_data = {
        "type": "video",
        "ai_model_id": CHARACTER_3_MODEL_ID,
        "start_keyframe_id": image_asset_id,
        "audio_id": audio_asset_id,
        "generated_video_inputs": {
            "text_prompt": text_prompt,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
        },
    }
    
    # Add optional parameters
    if duration_seconds is not None:
        generation_data["generated_video_inputs"]["duration_ms"] = int(duration_seconds * 1000)
    
    if seed is not None:
        generation_data["generated_video_inputs"]["seed"] = seed
    
    response = session.post("/generations", json=generation_data)
    
    if not response.ok:
        logger.error(
            f"Error creating generation: {response.status_code} {response.text}"
        )
        raise Exception(f"Failed to create generation: {response.text}")
    
    result = response.json()
    generation_id = result["id"]
    logger.info(f"Created generation with ID: {generation_id}")
    logger.info(f"Generation response: {result}")
    
    return generation_id


def poll_generation_status(session: HedraSession, generation_id: str) -> dict:
    """Poll the generation status until it completes or errors"""
    logger.info(f"Polling status for generation: {generation_id}")
    
    while True:
        response = session.get(f"/generations/{generation_id}/status")
        
        if not response.ok:
            logger.error(
                f"Error getting status: {response.status_code} {response.text}"
            )
            raise Exception(f"Failed to get generation status: {response.text}")
        
        status_data = response.json()
        status = status_data["status"]
        logger.info(f"Generation status: {status}")
        
        if status == "complete":
            logger.info("Generation complete!")
            return status_data
        elif status == "error":
            error_message = status_data.get("error_message", "Unknown error")
            logger.error(f"Generation failed: {error_message}")
            raise Exception(f"Video generation failed: {error_message}")
        
        # Still processing, wait and check again
        time.sleep(5)


def download_video(download_url: str, output_path: str) -> None:
    """Download the generated video from the provided URL"""
    logger.info(f"Downloading video from {download_url}")
    logger.info(f"Saving to: {output_path}")
    
    try:
        with requests.get(download_url, stream=True) as r:
            r.raise_for_status()
            with open(output_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        
        logger.info(f"Successfully downloaded video to {output_path}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to download video: {e}")
        raise
    except IOError as e:
        logger.error(f"Failed to save video file: {e}")
        raise


def main():
    # Load API key from .env
    api_key = load_api_key()
    
    # Validate configuration
    if IMAGE_ASSET_ID == "your-image-asset-id-here":
        logger.error("Please set IMAGE_ASSET_ID in the script configuration")
        return
    
    if not os.path.exists(AUDIO_FILE_PATH):
        logger.error(f"Audio file not found: {AUDIO_FILE_PATH}")
        return
    
    try:
        # Initialize session
        session = HedraSession(api_key=api_key)
        
        # Step 1: Upload audio file
        audio_id = upload_audio_file(session, AUDIO_FILE_PATH)
        
        # Step 2: Create video generation
        generation_id = create_video_generation(
            session=session,
            image_asset_id=IMAGE_ASSET_ID,
            audio_asset_id=audio_id,
            text_prompt=TEXT_PROMPT,
            aspect_ratio=ASPECT_RATIO,
            resolution=RESOLUTION,
            duration_seconds=DURATION_SECONDS,
            seed=SEED,
        )
        
        # Step 3: Poll for completion
        status_data = poll_generation_status(session, generation_id)
        
        # Step 4: Download the video
        if status_data.get("download_url"):
            download_video(status_data["download_url"], OUTPUT_FILENAME)
            logger.info("=" * 60)
            logger.info(f"SUCCESS! Video saved to: {OUTPUT_FILENAME}")
            logger.info("=" * 60)
        else:
            logger.error("No download URL found in the response")
    
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        raise


if __name__ == "__main__":
    main()

