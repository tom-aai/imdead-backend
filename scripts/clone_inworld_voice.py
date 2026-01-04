#!/usr/bin/env python3
"""
Standalone script to clone a voice using the Inworld API.

This script:
1. Reads the INWORLD_API_KEY from a .env file
2. Uploads an audio file to Inworld for voice cloning
3. Prints the voice ID for use in speech synthesis

Usage:
    1. Set the AUDIO_PATH variable below to your audio file path
    2. Set the WORKSPACE_ID variable to your Inworld workspace ID
    3. Set the VOICE_NAME variable to a name for your cloned voice
    4. Run: python clone_inworld_voice.py
"""

import os
import sys
import base64
import requests
from pathlib import Path
from dotenv import load_dotenv

# ============================================
# CONFIGURATION - Set your values here
# ============================================
AUDIO_PATH = "/Users/tom/Downloads/tom_clone_audio_input.wav"  # Path to audio file (WAV or MP3, 10-15 sec recommended)
WORKSPACE_ID = ""  # Your Inworld workspace ID
VOICE_NAME = "tom"  # Display name for the cloned voice
LANG_CODE = "EN_US"  # Language code (EN_US, ZH_CN, KO_KR, JA_JP, RU_RU, AUTO, etc.)
DESCRIPTION = ""  # Optional description for the voice
REMOVE_BACKGROUND_NOISE = False  # Set to True if audio has background noise
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


def validate_audio_path(audio_path: str):
    """Validate that the audio path exists and is a file."""
    if not audio_path or audio_path == "/path/to/your/audio.wav":
        print(f"❌ Error: Please set AUDIO_PATH in the script to your actual audio file path")
        sys.exit(1)
    
    path = Path(audio_path)
    if not path.exists():
        print(f"❌ Error: Audio file not found: {audio_path}")
        sys.exit(1)
    
    if not path.is_file():
        print(f"❌ Error: Path is not a file: {audio_path}")
        sys.exit(1)
    
    # Check if it's an audio file (basic check)
    valid_extensions = {'.wav', '.mp3'}
    if path.suffix.lower() not in valid_extensions:
        print(f"⚠️  Warning: File extension {path.suffix} may not be a valid audio format")
        print(f"   Valid extensions: {', '.join(valid_extensions)}")
    
    print(f"✓ Audio file found: {audio_path}")
    return str(path.resolve())


def validate_workspace_id(workspace_id: str):
    """Validate that the workspace ID is set."""
    if not workspace_id or workspace_id == "your-workspace-id":
        print(f"❌ Error: Please set WORKSPACE_ID in the script to your Inworld workspace ID")
        sys.exit(1)
    
    print(f"✓ Using workspace ID: {workspace_id}")
    return workspace_id


def read_audio_as_base64(audio_path: str) -> str:
    """Read audio file and return as base64-encoded string."""
    print(f"\n📖 Reading audio file: {audio_path}")
    
    with open(audio_path, 'rb') as f:
        audio_data = f.read()
    
    encoded = base64.b64encode(audio_data).decode('utf-8')
    
    file_size = len(audio_data)
    print(f"✓ Read {file_size:,} bytes ({file_size / 1024:.1f} KB)")
    
    return encoded


def clone_voice(
    api_key: str,
    workspace_id: str,
    voice_name: str,
    audio_base64: str,
    lang_code: str = "EN_US",
    description: str = "",
    remove_background_noise: bool = False
) -> dict:
    """
    Clone a voice using the Inworld API.
    
    Args:
        api_key: Inworld API key (base64-encoded credential)
        workspace_id: Inworld workspace ID
        voice_name: Display name for the cloned voice
        audio_base64: Base64-encoded audio data
        lang_code: Language code for the voice
        description: Optional description for the voice
        remove_background_noise: Whether to remove background noise
        
    Returns:
        dict: API response containing voice information
    """
    # Format workspace path
    if not workspace_id.startswith("workspaces/"):
        workspace_path = f"workspaces/{workspace_id}"
    else:
        workspace_path = workspace_id
    
    url = f"https://api.inworld.ai/voices/v1/{workspace_path}/voices:clone"
    
    headers = {
        "Authorization": f"Basic {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "displayName": voice_name,
        "langCode": lang_code,
        "voiceSamples": [
            {
                "audioData": audio_base64
            }
        ]
    }
    
    if description:
        payload["description"] = description
    
    if remove_background_noise:
        payload["audioProcessingConfig"] = {
            "removeBackgroundNoise": True
        }
    
    print(f"\n📤 Cloning voice with Inworld API")
    print(f"   Workspace: {workspace_path}")
    print(f"   Voice name: {voice_name}")
    print(f"   Language: {lang_code}")
    
    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=120  # Voice cloning can take a while
        )
        
        if response.status_code != 200:
            print(f"❌ Error: Failed to clone voice")
            print(f"   Status code: {response.status_code}")
            print(f"   Response: {response.text}")
            sys.exit(1)
        
        result = response.json()
        print(f"✓ Voice cloned successfully")
        
        return result
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error: Request to Inworld API failed: {e}")
        sys.exit(1)


def main():
    """Main function to clone voice with Inworld."""
    print("=" * 60)
    print("Inworld Voice Cloner")
    print("=" * 60)
    
    # Step 1: Load API key from .env
    api_key = load_api_key()
    
    # Step 2: Validate inputs
    audio_path = validate_audio_path(AUDIO_PATH)
    workspace_id = validate_workspace_id(WORKSPACE_ID)
    
    # Step 3: Read audio file as base64
    audio_base64 = read_audio_as_base64(audio_path)
    
    # Step 4: Clone the voice
    result = clone_voice(
        api_key=api_key,
        workspace_id=workspace_id,
        voice_name=VOICE_NAME,
        audio_base64=audio_base64,
        lang_code=LANG_CODE,
        description=DESCRIPTION,
        remove_background_noise=REMOVE_BACKGROUND_NOISE
    )
    
    # Step 5: Print success message with voice details
    print("\n" + "=" * 60)
    print("✅ SUCCESS!")
    print("=" * 60)
    
    voice = result.get("voice", {})
    voice_id = voice.get("voiceId", "N/A")
    voice_name = voice.get("displayName", "N/A")
    voice_resource_name = voice.get("name", "N/A")
    
    print(f"\nVoice Details:")
    print(f"   Display Name: {voice_name}")
    print(f"   Voice ID: {voice_id}")
    print(f"   Resource Name: {voice_resource_name}")
    
    # Print validation info if available
    samples_validated = result.get("audioSamplesValidated", [])
    if samples_validated:
        print(f"\nSample Validation:")
        for i, sample in enumerate(samples_validated):
            warnings = sample.get("warnings", [])
            errors = sample.get("errors", [])
            transcription = sample.get("transcription", "")
            
            if transcription:
                print(f"   Transcription: {transcription}")
            
            if warnings:
                print(f"   ⚠️  Warnings:")
                for warning in warnings:
                    print(f"      - {warning.get('text', 'Unknown warning')}")
            
            if errors:
                print(f"   ❌ Errors:")
                for error in errors:
                    print(f"      - {error.get('text', 'Unknown error')}")
    
    print(f"\n📋 Use this voice ID in your speech synthesis scripts:")
    print(f"\n    VOICE_ID = \"{voice_id}\"")
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()

