#!/usr/bin/env python3
"""
Standalone script to generate speech using Inworld TTS API.

This script:
1. Reads the INWORLD_API_KEY from a .env file
2. Synthesizes speech from text using a specified voice
3. Saves the audio output to a file

Usage:
    1. Set the TEXT variable below to the text you want to synthesize
    2. Set the VOICE_ID variable to your cloned voice ID
    3. Run: python generate_inworld_speech.py
"""

import os
import sys
import base64
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

# ============================================
# CONFIGURATION - Set your values here
# ============================================
TEXT = """Hi I"m Tom. I'm an expert in voice agents. I work for Live Kit. I am building an inference gateway. It's an exciting space to work in!"""

VOICE_ID = ""  # Voice ID from clone_inworld_voice.py output
OUTPUT_PATH = "/Users/tom/Downloads/tom_clone_audio_output.mp3"  # Where to save the audio file

# Audio configuration
MODEL_ID = "inworld-tts-1.5-max"  # Options: "inworld-tts-1" or "inworld-tts-1-max"
AUDIO_ENCODING = "MP3"  # Options: LINEAR16, MP3, OGG_OPUS, ALAW, MULAW, FLAC
SAMPLE_RATE_HZ = 48000  # Options: 8000, 16000, 22050, 24000, 32000, 44100, 48000
SPEAKING_RATE = 1.0  # Range: 0.5 to 1.5 (1.0 is normal speed)
TEMPERATURE = 1.1  # Range: 0 to 2 (higher = more expressive/random)
# ============================================


def load_api_key():
    """Load INWORLD_API_KEY from .env file."""
    # Load .env from the expert-avatar-backend directory
    backend_dir = Path(__file__).parent.parent
    dotenv_path = backend_dir / ".env.local"
    
    if not dotenv_path.exists():
        print(f"❌ Error: .env file not found at {dotenv_path}")
        print(f"   Please create a .env.local file with your INWORLD_API_KEY")
        sys.exit(1)
    
    load_dotenv(dotenv_path)
    api_key = os.getenv("INWORLD_API_KEY")
    
    if not api_key:
        print(f"❌ Error: INWORLD_API_KEY not found in {dotenv_path}")
        print(f"   Please add INWORLD_API_KEY=your_key_here to your .env.local file")
        print(f"   The key should be a Base64-encoded credential for Basic auth")
        sys.exit(1)
    
    print(f"✓ Loaded INWORLD_API_KEY from {dotenv_path}")
    return api_key


def validate_voice_id(voice_id: str):
    """Validate that the voice ID is set."""
    if not voice_id or voice_id == "your-voice-id":
        print(f"❌ Error: Please set VOICE_ID in the script")
        print(f"   Run clone_inworld_voice.py first to get a voice ID")
        sys.exit(1)
    
    print(f"✓ Using voice ID: {voice_id}")
    return voice_id


def synthesize_speech(
    api_key: str,
    text: str,
    voice_id: str,
    model_id: str = "inworld-tts-1",
    audio_encoding: str = "MP3",
    sample_rate_hz: int = 48000,
    speaking_rate: float = 1.0,
    temperature: float = 1.1
) -> bytes:
    """
    Synthesize speech using the Inworld TTS API (streaming endpoint).
    
    Args:
        api_key: Inworld API key (base64-encoded credential)
        text: Text to synthesize (max 2000 characters)
        voice_id: Voice ID to use
        model_id: TTS model ID
        audio_encoding: Output audio format
        sample_rate_hz: Sample rate in Hz
        speaking_rate: Speaking rate (0.5 to 1.5)
        temperature: Randomness/expressiveness (0 to 2)
        
    Returns:
        bytes: Audio data
    """
    url = "https://api.inworld.ai/tts/v1/voice:stream"
    
    headers = {
        "Authorization": f"Basic {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "text": text,
        "voiceId": voice_id,
        "modelId": model_id,
        "temperature": temperature,
        "audioConfig": {
            "audioEncoding": audio_encoding,
            "sampleRateHertz": sample_rate_hz,
            "speakingRate": speaking_rate
        }
    }
    
    print(f"\n📤 Synthesizing speech with Inworld API")
    print(f"   Voice ID: {voice_id}")
    print(f"   Model: {model_id}")
    print(f"   Text length: {len(text)} characters")
    
    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=120,
            stream=True
        )
        
        if response.status_code != 200:
            print(f"❌ Error: Failed to synthesize speech")
            print(f"   Status code: {response.status_code}")
            print(f"   Response: {response.text}")
            sys.exit(1)
        
        # Process streaming response - collect all audio chunks
        audio_chunks = []
        
        for line in response.iter_lines():
            if line:
                try:
                    chunk_data = json.loads(line)
                    
                    # Check for errors in stream
                    if "error" in chunk_data:
                        error = chunk_data["error"]
                        print(f"❌ Stream error: {error.get('message', 'Unknown error')}")
                        sys.exit(1)
                    
                    # Get audio content from result
                    if "result" in chunk_data:
                        result = chunk_data["result"]
                        audio_content = result.get("audioContent")
                        if audio_content:
                            audio_bytes = base64.b64decode(audio_content)
                            audio_chunks.append(audio_bytes)
                            
                except json.JSONDecodeError:
                    continue
        
        if not audio_chunks:
            print(f"❌ Error: No audio data received from API")
            sys.exit(1)
        
        # Combine all audio chunks
        full_audio = b''.join(audio_chunks)
        print(f"✓ Received {len(full_audio):,} bytes of audio data")
        
        return full_audio
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error: Request to Inworld API failed: {e}")
        sys.exit(1)


def save_audio(audio_data: bytes, output_path: str):
    """Save audio data to file."""
    path = Path(output_path)
    
    # Create parent directories if needed
    path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(path, 'wb') as f:
        f.write(audio_data)
    
    print(f"✓ Saved audio to: {path.resolve()}")


def main():
    """Main function to generate speech with Inworld."""
    print("=" * 60)
    print("Inworld Speech Generator")
    print("=" * 60)
    
    # Step 1: Load API key from .env
    api_key = load_api_key()
    
    # Step 2: Validate inputs
    voice_id = validate_voice_id(VOICE_ID)
    
    if len(TEXT) > 2000:
        print(f"⚠️  Warning: Text is {len(TEXT)} characters, but API max is 2000")
        print(f"   Text will be truncated to 2000 characters")
    
    text = TEXT[:2000]
    
    print(f"\n📝 Text to synthesize:")
    print(f"   \"{text[:100]}{'...' if len(text) > 100 else ''}\"")
    
    # Step 3: Synthesize speech
    audio_data = synthesize_speech(
        api_key=api_key,
        text=text,
        voice_id=voice_id,
        model_id=MODEL_ID,
        audio_encoding=AUDIO_ENCODING,
        sample_rate_hz=SAMPLE_RATE_HZ,
        speaking_rate=SPEAKING_RATE,
        temperature=TEMPERATURE
    )
    
    # Step 4: Save audio file
    save_audio(audio_data, OUTPUT_PATH)
    
    # Step 5: Print success message
    print("\n" + "=" * 60)
    print("✅ SUCCESS!")
    print("=" * 60)
    print(f"\nAudio saved to: {Path(OUTPUT_PATH).resolve()}")
    print(f"Format: {AUDIO_ENCODING}")
    print(f"Sample rate: {SAMPLE_RATE_HZ} Hz")
    print("=" * 60)


if __name__ == "__main__":
    main()

