"""
Audio processing script that converts MP4 to MP3, detects speech segments using Silero VAD,
and removes silence while preserving natural pauses between speech segments.
"""

import torch
from pydub import AudioSegment
import numpy as np
from pathlib import Path

# ============= CONFIGURATION =============
# Specify your input and output file paths here
INPUT_FILE_PATH = "/Users/tom/Downloads/voice_assistant_room_3111.mp4"  # Can be .mp4 or .mp3
OUTPUT_FILE_PATH = "/Users/tom/Downloads/voice_assistant_room_3111.mp3"

# Audio processing parameters
BUFFER_BETWEEN_SEGMENTS_MS = 250  # Pause duration between speech segments (in milliseconds)
SAMPLE_RATE = 16000  # Silero VAD works best at 16kHz
SILENCE_THRESHOLD_DB = -40  # dB threshold for silence detection
MIN_SILENCE_LENGTH_MS = 500  # Minimum silence length to be considered a gap
# =========================================


def convert_to_mp3(input_path: str) -> AudioSegment:
    """
    Load audio file and convert to MP3 format if needed.
    
    Args:
        input_path: Path to input audio file (MP4, MP3, WAV, etc.)
        
    Returns:
        AudioSegment object
    """
    print(f"Loading audio from: {input_path}")
    audio = AudioSegment.from_file(input_path)
    print(f"Audio loaded: {len(audio)}ms, {audio.frame_rate}Hz, {audio.channels} channel(s)")
    return audio


def get_speech_segments_silero(audio: AudioSegment) -> list[tuple[int, int]]:
    """
    Use Silero VAD to detect speech segments in the audio.
    
    Args:
        audio: AudioSegment object
        
    Returns:
        List of (start_ms, end_ms) tuples indicating speech segments
    """
    print("Detecting speech segments using Silero VAD...")
    
    # Load Silero VAD model
    model, utils = torch.hub.load(
        repo_or_dir='snakers4/silero-vad',
        model='silero_vad',
        force_reload=False,
        onnx=False
    )
    
    (get_speech_timestamps, _, read_audio, *_) = utils
    
    # Convert audio to the right format for Silero
    # Silero expects 16kHz mono audio
    audio_16k = audio.set_frame_rate(SAMPLE_RATE).set_channels(1)
    
    # Convert to numpy array
    samples = np.array(audio_16k.get_array_of_samples()).astype(np.float32)
    
    # Normalize to [-1, 1]
    if audio_16k.sample_width == 2:  # 16-bit audio
        samples = samples / 32768.0
    elif audio_16k.sample_width == 4:  # 32-bit audio
        samples = samples / 2147483648.0
    
    # Convert to torch tensor
    audio_tensor = torch.from_numpy(samples)
    
    # Get speech timestamps
    speech_timestamps = get_speech_timestamps(
        audio_tensor,
        model,
        sampling_rate=SAMPLE_RATE,
        threshold=0.5,  # Speech probability threshold
        min_speech_duration_ms=100,  # Minimum speech duration
        min_silence_duration_ms=MIN_SILENCE_LENGTH_MS,  # Minimum silence between speech
        window_size_samples=512,
    )
    
    # Convert sample indices to milliseconds (relative to original audio)
    segments = []
    original_rate = audio.frame_rate
    
    for timestamp in speech_timestamps:
        start_sample = timestamp['start']
        end_sample = timestamp['end']
        
        # Convert from 16kHz samples to milliseconds in original audio
        start_ms = int((start_sample / SAMPLE_RATE) * 1000)
        end_ms = int((end_sample / SAMPLE_RATE) * 1000)
        
        segments.append((start_ms, end_ms))
    
    print(f"Found {len(segments)} speech segments")
    return segments


def stitch_audio_segments(
    audio: AudioSegment,
    segments: list[tuple[int, int]],
    buffer_ms: int
) -> AudioSegment:
    """
    Extract speech segments and stitch them together with buffers.
    
    Args:
        audio: Original AudioSegment
        segments: List of (start_ms, end_ms) tuples
        buffer_ms: Buffer duration to add between segments
        
    Returns:
        Stitched AudioSegment
    """
    print(f"Stitching {len(segments)} segments with {buffer_ms}ms buffers...")
    
    if not segments:
        print("Warning: No speech segments found!")
        return audio
    
    # Create silence buffer
    silence_buffer = AudioSegment.silent(duration=buffer_ms, frame_rate=audio.frame_rate)
    
    # Extract and concatenate segments
    result = None
    
    for i, (start_ms, end_ms) in enumerate(segments):
        segment = audio[start_ms:end_ms]
        
        if result is None:
            result = segment
        else:
            # Add buffer between segments
            result = result + silence_buffer + segment
        
        duration = end_ms - start_ms
        print(f"  Segment {i+1}: {start_ms}ms - {end_ms}ms ({duration}ms)")
    
    return result


def process_audio(input_path: str, output_path: str, buffer_ms: int = 250):
    """
    Main processing function that orchestrates the entire workflow.
    
    Args:
        input_path: Path to input audio file
        output_path: Path to save processed audio
        buffer_ms: Buffer duration between speech segments
    """
    # Validate paths
    input_file = Path(input_path)
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Step 1: Load and convert audio
    audio = convert_to_mp3(input_path)
    
    # Step 2: Detect speech segments using Silero VAD
    speech_segments = get_speech_segments_silero(audio)
    
    # Step 3: Stitch segments together
    processed_audio = stitch_audio_segments(audio, speech_segments, buffer_ms)
    
    # Step 4: Export as MP3
    print(f"\nExporting to: {output_path}")
    processed_audio.export(
        output_path,
        format="mp3",
        bitrate="192k",
        parameters=["-q:a", "0"]  # Highest quality MP3
    )
    
    print("✓ Processing complete!")


if __name__ == "__main__":
    # Validate configuration
    if INPUT_FILE_PATH == "path/to/your/input.mp4":
        print("ERROR: Please set INPUT_FILE_PATH at the top of this script")
        exit(1)
    
    if OUTPUT_FILE_PATH == "path/to/your/output.mp3":
        print("ERROR: Please set OUTPUT_FILE_PATH at the top of this script")
        exit(1)
    
    # Run the processing
    try:
        process_audio(
            INPUT_FILE_PATH,
            OUTPUT_FILE_PATH,
            BUFFER_BETWEEN_SEGMENTS_MS
        )
    except Exception as e:
        print(f"\n❌ Error during processing: {e}")
        raise

