"""
Script to generate a hot take video for a given session.

Usage:
    python scripts/generate_hot_take_video.py <session_id>
"""

import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# Add parent directory to path to import services
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.hot_take_video_generation import HotTakeVideoGenerationService
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
        logger.error("❌ Usage: python scripts/generate_hot_take_video.py <session_id>")
        sys.exit(1)
    
    session_id = sys.argv[1]
    
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
        supabase_client=supabase,
        output_dir=str(Path(__file__).parent.parent)  # Save to backend directory
    )
    
    try:
        logger.info(f"🎬 Starting hot take video generation for session: {session_id}")
        logger.info("=" * 60)
        
        result = service.generate_hot_take_video(
            session_id=session_id,
            text_prompt="professional interview conversation",
            aspect_ratio="16:9",
            resolution="720p"
        )
        
        logger.info("=" * 60)
        logger.info("🎉 SUCCESS!")
        logger.info(f"Session ID: {result['session_id']}")
        logger.info(f"User ID: {result['user_id']}")
        logger.info(f"Video saved to: {result['video_path']}")
        logger.info(f"Hot take: {result['hot_take_text'][:100]}...")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error("=" * 60)
        logger.error(f"❌ ERROR: {e}")
        logger.error("=" * 60)
        raise


if __name__ == "__main__":
    main()

