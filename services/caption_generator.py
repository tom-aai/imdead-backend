"""
Caption generation service using ASS subtitles with karaoke effects.

This service takes a video, audio file, and Cartesia word timestamps
and generates captions with word-by-word highlighting using ASS subtitle format.
"""

import os
import logging
import json
import subprocess
import tempfile
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)


class CaptionGenerator:
    """Service for adding captions to videos using ffmpeg."""
    
    def __init__(self):
        """Initialize the caption generator."""
        pass
    
    def _get_font_path(self) -> str:
        """
        Get the path to a suitable font for captions.
        
        On macOS, tries to find Arial Bold or falls back to other available fonts.
        
        Returns:
            str: Path to a font file that can be used with ffmpeg drawtext
        """
        # List of font paths to try (in order of preference)
        font_paths = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Supplemental/Helvetica.ttc",
            "/System/Library/Fonts/SFNS.ttf",
        ]
        
        for font_path in font_paths:
            if os.path.exists(font_path):
                logger.info(f"Using font: {font_path}")
                return font_path
        
        # If no fonts found, raise an error
        raise FileNotFoundError(
            "Could not find a suitable font file. Please ensure Arial Bold or Helvetica is installed."
        )
    
    def _group_words_into_phrases(
        self,
        word_timestamps: List[Dict],
        words_per_phrase: int = 2
    ) -> List[List[Dict]]:
        """
        Group words into phrases of 2-3 words for caption display.
        
        Args:
            word_timestamps: List of word timestamp dicts
            words_per_phrase: Number of words per phrase (default 2)
            
        Returns:
            List of phrases, where each phrase is a list of word dicts
        """
        phrases = []
        for i in range(0, len(word_timestamps), words_per_phrase):
            phrase = word_timestamps[i:i + words_per_phrase]
            phrases.append(phrase)
        
        return phrases
    
    def _escape_text_for_ass(self, text: str) -> str:
        """
        Escape text for ASS subtitle format.
        
        Args:
            text: Text to escape
            
        Returns:
            str: Escaped text
        """
        # Escape special characters for ASS format
        # ASS uses { } for override codes, so we need to escape braces
        text = text.replace("\\", "\\\\")  # Backslash first
        text = text.replace("{", "\\{")
        text = text.replace("}", "\\}")
        # Newlines in ASS use \N
        text = text.replace("\n", "\\N")
        return text
    
    def _format_ass_time(self, seconds: float) -> str:
        """
        Format time in seconds to ASS timestamp format (H:MM:SS.CC).
        
        Args:
            seconds: Time in seconds
            
        Returns:
            str: ASS formatted timestamp
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        centisecs = int((seconds % 1) * 100)
        
        return f"{hours}:{minutes:02d}:{secs:02d}.{centisecs:02d}"
    
    def _generate_ass_subtitles(
        self,
        word_timestamps: List[Dict],
        video_width: int = 1280,
        video_height: int = 720,
        video_duration: float = None
    ) -> str:
        r"""
        Generate ASS subtitle content with word-by-word highlighting.
        
        Creates individual subtitle lines for each word to ensure precise timing.
        Each word is highlighted in yellow for its duration.
        
        Args:
            word_timestamps: List of word timestamp dicts from Cartesia
            video_width: Width of the video
            video_height: Height of the video
            video_duration: Duration of the video in seconds (optional, used to extend last caption)
            
        Returns:
            str: Complete ASS subtitle file content
        """
        logger.info(f"🎬 Generating ASS subtitles for {len(word_timestamps)} individual words")
        logger.info(f"📐 Video dimensions: {video_width}x{video_height}")
        
        # Log first few word timestamps
        logger.info(f"🔍 First 5 word timestamps:")
        for i, wt in enumerate(word_timestamps[:5]):
            logger.info(f"   {i + 1}. '{wt.get('word')}' - start={wt.get('start'):.3f}s, end={wt.get('end'):.3f}s, duration={(wt.get('end') - wt.get('start')):.3f}s")
        
        # Get font path (just the filename for ASS)
        font_path = self._get_font_path()
        font_name = os.path.basename(font_path).replace('.ttf', '').replace('.ttc', '')
        logger.info(f"🔤 Using font: {font_name}")
        
        # ASS color format is &H00BBGGRR (BGR, not RGB)
        # White: &H00FFFFFF
        # Yellow: &H0000FFFF
        # Black: &H00000000
        
        # Build ASS file content with TWO styles:
        # 1. White text style (for base/static text)
        # 2. Yellow text style (for highlighted word overlay)
        # Use smaller font (36 instead of 48) to fit more text on screen
        ass_content = [
            "[Script Info]",
            "ScriptType: v4.00+",
            "PlayResX: " + str(video_width),
            "PlayResY: " + str(video_height),
            "WrapStyle: 0",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            # White style for base text (smaller font)
            f"Style: White,{font_name},36,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,2,10,10,80,1",
            # Yellow style for highlighted word overlay (same size)
            f"Style: Yellow,{font_name},36,&H0000FFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,2,10,10,80,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
        ]
        
        logger.info(f"\n📝 Creating karaoke effect with 2 layers:")
        logger.info(f"   Layer 0: Static white text (chunks of ~6 words)")
        logger.info(f"   Layer 1: Yellow word overlays (move through chunk)\n")
        
        # Group words into chunks of ~6 words (like TikTok/Instagram/LinkedIn)
        words_per_chunk = 6
        chunks = self._group_words_into_phrases(word_timestamps, words_per_phrase=words_per_chunk)
        
        logger.info(f"📊 Split {len(word_timestamps)} words into {len(chunks)} chunks of ~{words_per_chunk} words each\n")
        
        # LAYER 0: Create base subtitles for each chunk showing text in white
        logger.info(f"✅ Layer 0 (Base text chunks):")
        for chunk_idx, chunk_words in enumerate(chunks):
            chunk_text = " ".join([w['word'] for w in chunk_words])
            chunk_start = chunk_words[0]['start']
            chunk_end = chunk_words[-1]['end']
            
            # For the last chunk, extend to video duration if provided
            is_last_chunk = (chunk_idx == len(chunks) - 1)
            if is_last_chunk and video_duration and video_duration > chunk_end:
                logger.info(f"   📏 Extending last chunk from {chunk_end:.2f}s to video duration {video_duration:.2f}s")
                chunk_end = video_duration
            
            start_time = self._format_ass_time(chunk_start)
            end_time = self._format_ass_time(chunk_end)
            
            # Create the base subtitle on Layer 0
            base_subtitle = f"Dialogue: 0,{start_time},{end_time},White,,0,0,0,,{self._escape_text_for_ass(chunk_text)}"
            ass_content.append(base_subtitle)
            
            logger.info(f"   Chunk {chunk_idx + 1}/{len(chunks)}: {start_time} → {end_time}")
            logger.info(f"      Text: {chunk_text}")
        
        # LAYER 1: Create individual yellow word overlays
        logger.info(f"\n📝 Layer 1 (Yellow highlights):")
        
        # Minimum duration for a word highlight to be visible (in seconds)
        # Too short and it won't render or be perceptible
        MIN_HIGHLIGHT_DURATION = 0.15  # 150ms minimum
        
        for chunk_idx, chunk_words in enumerate(chunks):
            logger.info(f"\n   Chunk {chunk_idx + 1}/{len(chunks)} highlights:")
            
            for word_idx_in_chunk, word_data in enumerate(chunk_words):
                word = word_data['word']
                word_start = word_data['start']
                word_end = word_data['end']
                
                # Ensure minimum duration for visibility
                original_duration = word_end - word_start
                if original_duration < MIN_HIGHLIGHT_DURATION:
                    # Extend to minimum duration
                    desired_end = word_start + MIN_HIGHLIGHT_DURATION
                    
                    # But don't overlap with the next word - cap at next word's start time
                    # Check if there's a next word in the current chunk
                    if word_idx_in_chunk < len(chunk_words) - 1:
                        next_word_start = chunk_words[word_idx_in_chunk + 1]['start']
                        word_end = min(desired_end, next_word_start)
                    # If this is the last word in the chunk, check if there's a next chunk
                    elif chunk_idx < len(chunks) - 1:
                        next_chunk_first_word_start = chunks[chunk_idx + 1][0]['start']
                        word_end = min(desired_end, next_chunk_first_word_start)
                    else:
                        # Last word overall, can extend freely
                        word_end = desired_end
                    
                    logger.info(f"      Word {word_idx_in_chunk + 1}/{len(chunk_words)}: '{word}' ({word_start:.3f}s → {word_end:.3f}s) [extended from {original_duration:.3f}s]")
                else:
                    logger.info(f"      Word {word_idx_in_chunk + 1}/{len(chunk_words)}: '{word}' ({word_start:.3f}s → {word_end:.3f}s)")
                
                # Find global word index
                global_word_idx = word_timestamps.index(word_data)
                
                # Build text with only current word in yellow, rest invisible
                # Only include words from the current chunk
                text_parts = []
                for idx_in_chunk, wt in enumerate(chunk_words):
                    if idx_in_chunk == word_idx_in_chunk:
                        text_parts.append(f"{{\\rYellow}}{self._escape_text_for_ass(wt['word'])}")
                    else:
                        text_parts.append(f"{{\\alpha&HFF&}}{self._escape_text_for_ass(wt['word'])}")  # Invisible
                
                overlay_text = " ".join(text_parts)
                
                # Format timestamps
                start_time = self._format_ass_time(word_start)
                end_time = self._format_ass_time(word_end)
                
                # Create dialogue line on Layer 1 (overlays on top of Layer 0)
                dialogue_line = f"Dialogue: 1,{start_time},{end_time},Yellow,,0,0,0,,{overlay_text}"
                
                ass_content.append(dialogue_line)
        
        logger.info(f"\n✅ Generated {len(word_timestamps)} dialogue lines (one per word)")
        
        # Create a visual timeline showing what text is visible when
        logger.info(f"\n{'='*80}")
        logger.info(f"📊 VISUAL TIMELINE (what you should see on screen)")
        logger.info(f"{'='*80}")
        
        # Create timeline showing chunks with moving highlight
        if word_timestamps and chunks:
            start_time = word_timestamps[0]['start']
            end_time = word_timestamps[-1]['end']
            
            # If video duration is provided and extends beyond last word, use it
            if video_duration and video_duration > end_time:
                timeline_end = video_duration
            else:
                timeline_end = end_time
            
            time_step = 0.2  # 200ms increments for readability
            
            current_time = start_time
            
            while current_time <= timeline_end + 0.1:
                # Find which chunk and word is active at this time
                active_chunk_idx = None
                highlighted_word_idx_in_chunk = None
                
                for chunk_idx, chunk_words in enumerate(chunks):
                    chunk_start = chunk_words[0]['start']
                    chunk_end = chunk_words[-1]['end']
                    
                    if chunk_start <= current_time <= chunk_end:
                        active_chunk_idx = chunk_idx
                        
                        # Find highlighted word in this chunk
                        for word_idx, wt in enumerate(chunk_words):
                            if wt['start'] <= current_time < wt['end']:
                                highlighted_word_idx_in_chunk = word_idx
                                break
                        break
                
                if active_chunk_idx is not None:
                    chunk_words = chunks[active_chunk_idx]
                    
                    # Build the visual representation
                    visual_parts = []
                    for idx, word_data in enumerate(chunk_words):
                        word = word_data['word']
                        if idx == highlighted_word_idx_in_chunk:
                            visual_parts.append(f"[{word}]")  # Highlighted
                        else:
                            visual_parts.append(word)  # Not highlighted
                    
                    visual_text = " ".join(visual_parts)
                    logger.info(f"   {current_time:05.2f}s: {visual_text}")
                elif current_time > word_timestamps[-1]['end']:
                    # After all words are done, show last chunk without highlight (extended to video end)
                    last_chunk = chunks[-1]
                    chunk_text = " ".join([w['word'] for w in last_chunk])
                    logger.info(f"   {current_time:05.2f}s: {chunk_text} (extended)")
                
                current_time += time_step
        
        logger.info(f"{'='*80}")
        logger.info(f"Legend: [word] = highlighted in yellow")
        logger.info(f"        word = visible in white")
        logger.info(f"~6 words per chunk (like TikTok/Instagram)")
        logger.info(f"Each chunk stays on screen - only the highlight moves!")
        logger.info(f"{'='*80}\n")
        
        return "\n".join(ass_content)
    
    def add_captions_to_video(
        self,
        video_path: str,
        audio_path: str,
        timestamps_path: str,
        output_path: str = None
    ) -> str:
        """
        Add word-highlighted captions to a video using ASS subtitles with karaoke effects.
        
        Args:
            video_path: Path to the input video file
            audio_path: Path to the audio file (for reference, not used directly)
            timestamps_path: Path to the JSON file with word timestamps
            output_path: Path for the output video (optional, auto-generated if not provided)
            
        Returns:
            str: Path to the captioned video file
            
        Raises:
            Exception: If caption generation fails
        """
        ass_file = None
        try:
            logger.info(f"\n{'='*80}")
            logger.info(f"🎬 STARTING CAPTION GENERATION")
            logger.info(f"{'='*80}")
            logger.info(f"📹 Video: {video_path}")
            logger.info(f"🎵 Audio: {audio_path}")
            logger.info(f"⏱️  Timestamps: {timestamps_path}")
            
            # Validate inputs
            if not os.path.exists(video_path):
                raise FileNotFoundError(f"Video file not found: {video_path}")
            
            if not os.path.exists(timestamps_path):
                raise FileNotFoundError(f"Timestamps file not found: {timestamps_path}")
            
            # Load word timestamps
            with open(timestamps_path, 'r') as f:
                word_timestamps = json.load(f)
            
            logger.info(f"\n📝 Loaded {len(word_timestamps)} word timestamps from file")
            
            # Generate output path if not provided
            if output_path is None:
                base_name = os.path.splitext(video_path)[0]
                output_path = f"{base_name}_captioned.mp4"
            
            # Get video dimensions and duration using ffprobe
            probe_cmd = [
                'ffprobe',
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height:format=duration',
                '-of', 'json',
                video_path
            ]
            
            probe_result = subprocess.run(
                probe_cmd,
                capture_output=True,
                text=True
            )
            
            if probe_result.returncode != 0:
                logger.warning(f"Could not probe video information, using defaults")
                video_width = 1280
                video_height = 720
                video_duration = None
            else:
                probe_data = json.loads(probe_result.stdout)
                video_width = probe_data['streams'][0]['width']
                video_height = probe_data['streams'][0]['height']
                video_duration = float(probe_data['format']['duration']) if 'format' in probe_data and 'duration' in probe_data['format'] else None
                logger.info(f"Video dimensions: {video_width}x{video_height}")
                if video_duration:
                    logger.info(f"Video duration: {video_duration:.2f}s")
            
            # Generate ASS subtitle content
            logger.info(f"\n{'='*80}")
            logger.info(f"📝 GENERATING ASS SUBTITLES")
            logger.info(f"{'='*80}")
            
            ass_content = self._generate_ass_subtitles(
                word_timestamps,
                video_width,
                video_height,
                video_duration
            )
            
            # Save ASS file to temporary location
            ass_file = tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.ass',
                delete=False,
                encoding='utf-8'
            )
            ass_file.write(ass_content)
            ass_file.close()
            
            logger.info(f"\n{'='*80}")
            logger.info(f"📄 Created ASS subtitle file: {ass_file.name}")
            logger.info(f"📏 ASS file size: {len(ass_content)} characters")
            logger.info(f"\n📜 ASS File Content:")
            logger.info(f"{'='*80}")
            # Log the full ASS content for debugging
            for line_num, line in enumerate(ass_content.split('\n'), 1):
                logger.info(f"{line_num:3d}: {line}")
            logger.info(f"{'='*80}\n")
            
            # Build ffmpeg command using subtitles filter
            # The subtitles filter burns ASS subtitles into the video
            cmd = [
                'ffmpeg',
                '-i', video_path,
                '-vf', f"subtitles={ass_file.name}",
                '-codec:a', 'copy',  # Copy audio stream as-is
                '-codec:v', 'libx264',
                '-crf', '18',  # High quality
                '-preset', 'medium',
                '-y',  # Overwrite output file
                output_path
            ]
            
            logger.info(f"\n{'='*80}")
            logger.info(f"🎬 RUNNING FFMPEG")
            logger.info(f"{'='*80}")
            logger.info(f"Command: {' '.join(cmd)}")
            logger.info(f"Output: {output_path}")
            logger.info(f"⏳ Processing... (this may take a few minutes)")
            
            # Run ffmpeg
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0:
                logger.error(f"\n{'='*80}")
                logger.error(f"❌ FFMPEG FAILED")
                logger.error(f"{'='*80}")
                logger.error(f"Return code: {result.returncode}")
                logger.error(f"\nSTDERR:")
                logger.error(result.stderr)
                logger.error(f"\nSTDOUT:")
                logger.error(result.stdout)
                logger.error(f"{'='*80}")
                raise Exception(f"ffmpeg failed: {result.stderr}")
            
            logger.info(f"\n✅ ffmpeg completed successfully")
            logger.info(f"✅ Captioned video created: {output_path}")
            
            # Verify output file exists and has content
            if not os.path.exists(output_path):
                raise Exception("Output file was not created")
            
            file_size = os.path.getsize(output_path)
            logger.info(f"📁 Output file size: {file_size:,} bytes ({file_size / 1024 / 1024:.2f} MB)")
            
            return output_path
            
        except Exception as e:
            logger.error(f"Failed to add captions to video: {e}")
            raise Exception(f"Caption generation failed: {e}")
        finally:
            # Clean up temporary ASS file
            if ass_file and os.path.exists(ass_file.name):
                try:
                    os.unlink(ass_file.name)
                    logger.info(f"🧹 Cleaned up temporary ASS file")
                except Exception as e:
                    logger.warning(f"Failed to clean up ASS file: {e}")
    
    def add_simple_captions_to_video(
        self,
        video_path: str,
        timestamps_path: str,
        output_path: str = None,
        words_per_line: int = 3
    ) -> str:
        """
        Add simple (non-highlighted) captions to a video using ASS subtitles.
        
        This is a simpler alternative that shows N words at a time without highlighting.
        
        Args:
            video_path: Path to the input video file
            timestamps_path: Path to the JSON file with word timestamps
            output_path: Path for the output video (optional, auto-generated if not provided)
            words_per_line: Number of words to show per caption line (default: 3)
            
        Returns:
            str: Path to the captioned video file
            
        Raises:
            Exception: If caption generation fails
        """
        ass_file = None
        try:
            logger.info(f"🎬 Adding simple captions to video: {video_path}")
            
            # Validate inputs
            if not os.path.exists(video_path):
                raise FileNotFoundError(f"Video file not found: {video_path}")
            
            if not os.path.exists(timestamps_path):
                raise FileNotFoundError(f"Timestamps file not found: {timestamps_path}")
            
            # Load word timestamps
            with open(timestamps_path, 'r') as f:
                word_timestamps = json.load(f)
            
            logger.info(f"📝 Loaded {len(word_timestamps)} word timestamps")
            
            # Generate output path if not provided
            if output_path is None:
                base_name = os.path.splitext(video_path)[0]
                output_path = f"{base_name}_captioned_simple.mp4"
            
            # Get video dimensions
            probe_cmd = [
                'ffprobe',
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height',
                '-of', 'json',
                video_path
            ]
            
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
            
            if probe_result.returncode != 0:
                video_width = 1280
                video_height = 720
            else:
                probe_data = json.loads(probe_result.stdout)
                video_width = probe_data['streams'][0]['width']
                video_height = probe_data['streams'][0]['height']
            
            # Group words into phrases
            phrases = self._group_words_into_phrases(word_timestamps, words_per_line)
            
            # Generate simple ASS content (no karaoke effects)
            font_path = self._get_font_path()
            font_name = os.path.basename(font_path).replace('.ttf', '').replace('.ttc', '')
            
            ass_content = [
                "[Script Info]",
                "ScriptType: v4.00+",
                f"PlayResX: {video_width}",
                f"PlayResY: {video_height}",
                "WrapStyle: 0",
                "",
                "[V4+ Styles]",
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
                f"Style: Default,{font_name},48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,2,10,10,80,1",
                "",
                "[Events]",
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
            ]
            
            # Add dialogue lines
            for phrase_words in phrases:
                phrase_start = phrase_words[0]['start']
                phrase_end = phrase_words[-1]['end']
                phrase_text = ' '.join([w['word'] for w in phrase_words])
                escaped_text = self._escape_text_for_ass(phrase_text)
                
                start_time = self._format_ass_time(phrase_start)
                end_time = self._format_ass_time(phrase_end)
                
                dialogue_line = f"Dialogue: 0,{start_time},{end_time},Default,,0,0,0,,{escaped_text}"
                ass_content.append(dialogue_line)
            
            # Save ASS file
            ass_file = tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.ass',
                delete=False,
                encoding='utf-8'
            )
            ass_file.write('\n'.join(ass_content))
            ass_file.close()
            
            logger.info(f"📄 Created simple ASS subtitle file: {ass_file.name}")
            
            # Build and run ffmpeg command
            cmd = [
                'ffmpeg',
                '-i', video_path,
                '-vf', f"subtitles={ass_file.name}",
                '-codec:a', 'copy',
                '-codec:v', 'libx264',
                '-crf', '18',
                '-preset', 'medium',
                '-y',
                output_path
            ]
            
            logger.info(f"🎬 Running ffmpeg to generate captioned video")
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"ffmpeg failed with return code {result.returncode}")
                logger.error(f"stderr: {result.stderr}")
                raise Exception(f"ffmpeg failed: {result.stderr}")
            
            logger.info(f"✅ Captioned video created: {output_path}")
            
            file_size = os.path.getsize(output_path)
            logger.info(f"📁 Output file size: {file_size:,} bytes ({file_size / 1024 / 1024:.2f} MB)")
            
            return output_path
            
        except Exception as e:
            logger.error(f"Failed to add captions to video: {e}")
            raise Exception(f"Caption generation failed: {e}")
        finally:
            # Clean up temporary ASS file
            if ass_file and os.path.exists(ass_file.name):
                try:
                    os.unlink(ass_file.name)
                except Exception as e:
                    logger.warning(f"Failed to clean up ASS file: {e}")

