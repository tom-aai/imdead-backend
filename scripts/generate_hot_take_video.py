"""
Script to generate a hot take video for a given session.

This script generates a hot take video WITH captions by default. It saves:
- Video file (.mp4) with word-by-word highlighted captions
- Audio file (.wav)
- Timestamps file (.json)

Usage:
    python scripts/generate_hot_take_video.py <session_id> [hot_take_number] [output_dir] [--no-captions]
    
Arguments:
    session_id: The LiveKit session ID
    hot_take_number: Optional. Which hot take to generate (1 or 2). Default: 1
    output_dir: Optional. Directory to save outputs. Default: current directory
    --no-captions: Optional. Skip adding captions to the video
"""

import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# Add parent directory to path to import services
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.hot_take_video_generator import HotTakeVideoGenerationService
from supabase import create_client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_env():
    """Load environment variables from .env.local file."""
    backend_dir = Path(__file__).parent.parent
    dotenv_path = backend_dir / ".env.local"
    
    if not dotenv_path.exists():
        logger.error(f"❌ Error: .env.local file not found at {dotenv_path}")
        logger.error(f"   Please create a .env.local file with required API keys")
        sys.exit(1)
    
    load_dotenv(dotenv_path)
    logger.info(f"✓ Loaded environment variables from {dotenv_path}")


def main():
    if len(sys.argv) < 2:
        logger.error("❌ Usage: python scripts/generate_hot_take_video.py <session_id> [hot_take_number] [output_dir] [--no-captions]")
        logger.error("   hot_take_number: Optional. Which hot take to generate (1 or 2). Default: 1")
        logger.error("   output_dir: Optional. Directory to save outputs. Default: current directory")
        logger.error("   --no-captions: Optional. Skip adding captions to the video")
        sys.exit(1)
    
    session_id = sys.argv[1]
    
    # Check for --no-captions flag
    add_captions = True
    if '--no-captions' in sys.argv:
        add_captions = False
        sys.argv.remove('--no-captions')
    
    # Get hot_take_number from arguments (default to 1)
    hot_take_number = 1
    if len(sys.argv) >= 3:
        try:
            hot_take_number = int(sys.argv[2])
            if hot_take_number not in [1, 2]:
                logger.error("❌ hot_take_number must be 1 or 2")
                sys.exit(1)
        except ValueError:
            logger.error("❌ hot_take_number must be an integer (1 or 2)")
            sys.exit(1)
    
    # Get output_dir from arguments (default to None, which means current dir)
    output_dir = None
    if len(sys.argv) >= 4:
        output_dir = sys.argv[3]
    
    # Load environment variables
    load_env()
    
    # Initialize Supabase client
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")
    
    if not supabase_url or not supabase_key:
        logger.error("❌ SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env.local")
        sys.exit(1)
    
    supabase = create_client(supabase_url, supabase_key)
    
    # Initialize the service
    service = HotTakeVideoGenerationService(
        supabase_client=supabase
    )
    
    try:
        logger.info(f"🎬 Starting hot take #{hot_take_number} video generation for session: {session_id}")
        logger.info(f"   Captions: {'Enabled ✅' if add_captions else 'Disabled ❌'}")
        logger.info("=" * 60)
        
        result = service.generate_hot_take_video(
            session_id=session_id,
            text_prompt="professional interview conversation",
            aspect_ratio="16:9",
            resolution="720p",
            hot_take_number=hot_take_number,
            output_dir=output_dir,
            add_captions=add_captions
        )
        
        logger.info("=" * 60)
        logger.info("🎉 SUCCESS!")
        logger.info(f"Session ID: {result['session_id']}")
        logger.info(f"User ID: {result['user_id']}")
        logger.info(f"Hot take #{result['hot_take_number']}: {result['hot_take_text'][:100]}...")
        logger.info(f"Video path: {result['video_path']}")
        logger.info(f"Audio path: {result['audio_path']}")
        logger.info(f"Timestamps path: {result['timestamps_path']}")
        logger.info(f"Captions added: {'Yes ✅' if result.get('captions_added') else 'No ❌'}")
        
        if not result.get('captions_added'):
            logger.info("")
            logger.info("📝 To add captions manually, run:")
            logger.info(f"   python scripts/add_captions_to_video.py --video {result['video_path']} --audio {result['audio_path']} --timestamps {result['timestamps_path']}")
        
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error("=" * 60)
        logger.error(f"❌ ERROR: {e}")
        logger.error("=" * 60)
        raise


if __name__ == "__main__":
    main()

