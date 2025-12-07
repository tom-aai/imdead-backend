#!/usr/bin/env python3
"""
Script to add captions to a hot take video.

This script takes the outputs from hot_take_video_generation.py
(video, audio, and timestamps) and generates a captioned video.

Usage:
    python scripts/add_captions_to_video.py --video path/to/video.mp4 --timestamps path/to/timestamps.json --output path/to/output.mp4
    
    Or with auto-detection:
    python scripts/add_captions_to_video.py --base hot_take_1_session123
    
    Or simple captions (no highlighting):
    python scripts/add_captions_to_video.py --video path/to/video.mp4 --timestamps path/to/timestamps.json --simple
"""

import sys
import os
import argparse
import logging

# Add parent directory to path to import services
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.caption_generator import CaptionGenerator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description='Add captions to a hot take video',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Add captions with highlighting
  python scripts/add_captions_to_video.py --video video.mp4 --audio audio.wav --timestamps timestamps.json
  
  # Add captions using base filename (auto-detects files)
  python scripts/add_captions_to_video.py --base hot_take_1_session123
  
  # Add simple captions (no highlighting)
  python scripts/add_captions_to_video.py --base hot_take_1_session123 --simple
  
  # Specify custom output path
  python scripts/add_captions_to_video.py --base hot_take_1_session123 --output my_captioned_video.mp4
        """
    )
    
    # Input options
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        '--base',
        help='Base filename (without extensions) to auto-detect video, audio, and timestamps files'
    )
    input_group.add_argument(
        '--video',
        help='Path to video file'
    )
    
    # Additional inputs (only needed if not using --base)
    parser.add_argument(
        '--audio',
        help='Path to audio file (optional, for reference)'
    )
    parser.add_argument(
        '--timestamps',
        help='Path to timestamps JSON file'
    )
    
    # Output options
    parser.add_argument(
        '--output',
        help='Path for output video (optional, auto-generated if not provided)'
    )
    
    # Caption style
    parser.add_argument(
        '--simple',
        action='store_true',
        help='Use simple captions without word highlighting'
    )
    
    parser.add_argument(
        '--words-per-line',
        type=int,
        default=5,
        help='Number of words per caption line (default: 5, only used with --simple)'
    )
    
    args = parser.parse_args()
    
    # Determine input files
    if args.base:
        # Auto-detect files based on base filename
        base_dir = os.path.dirname(args.base) or '.'
        base_name = os.path.basename(args.base)
        
        video_path = os.path.join(base_dir, f"{base_name}.mp4")
        audio_path = os.path.join(base_dir, f"{base_name}_audio.wav")
        timestamps_path = os.path.join(base_dir, f"{base_name}_timestamps.json")
        
        logger.info(f"Auto-detected files:")
        logger.info(f"  Video: {video_path}")
        logger.info(f"  Audio: {audio_path}")
        logger.info(f"  Timestamps: {timestamps_path}")
        
        # Validate files exist
        if not os.path.exists(video_path):
            logger.error(f"Video file not found: {video_path}")
            return 1
        
        if not os.path.exists(timestamps_path):
            logger.error(f"Timestamps file not found: {timestamps_path}")
            return 1
            
    else:
        # Use explicitly provided paths
        video_path = args.video
        audio_path = args.audio
        timestamps_path = args.timestamps
        
        if not timestamps_path:
            logger.error("--timestamps is required when using --video")
            return 1
    
    # Validate required files
    if not os.path.exists(video_path):
        logger.error(f"Video file not found: {video_path}")
        return 1
    
    if not os.path.exists(timestamps_path):
        logger.error(f"Timestamps file not found: {timestamps_path}")
        return 1
    
    # Initialize caption generator
    generator = CaptionGenerator()
    
    try:
        # Generate captions
        if args.simple:
            logger.info(f"🎬 Generating simple captions (no highlighting)")
            output_path = generator.add_simple_captions_to_video(
                video_path=video_path,
                timestamps_path=timestamps_path,
                output_path=args.output,
                words_per_line=args.words_per_line
            )
        else:
            logger.info(f"🎬 Generating captions with word highlighting")
            output_path = generator.add_captions_to_video(
                video_path=video_path,
                audio_path=audio_path,
                timestamps_path=timestamps_path,
                output_path=args.output
            )
        
        logger.info(f"✅ Success! Captioned video saved to: {output_path}")
        return 0
        
    except Exception as e:
        logger.error(f"❌ Failed to generate captions: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

