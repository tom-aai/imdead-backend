#!/usr/bin/env python3
"""
List all Cartesia voices for debugging.
Usage: python scripts/list_cartesia_voices.py
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from cartesia import Cartesia

# Load environment variables from .env.local in the project root
# Get the directory where this script is located
script_dir = Path(__file__).parent
# Go up one level to the project root (expert-avatar-backend)
project_root = script_dir.parent
# Load .env.local from the project root
env_path = project_root / ".env.local"
load_dotenv(env_path)

def list_voices():
    """List all Cartesia voices."""
    api_key = os.getenv("CARTESIA_API_KEY")
    
    if not api_key:
        print("ERROR: CARTESIA_API_KEY not found in environment variables")
        return
    
    print("Connecting to Cartesia API...")
    client = Cartesia(api_key=api_key)
    
    print("\n" + "="*80)
    print("LISTING ALL CARTESIA VOICES")
    print("="*80 + "\n")
    
    try:
        # List all voices owned by the user (convert pager to list)
        voices = list(client.voices.list(is_owner=True))
        
        if not voices:
            print("No voices found in your account.")
            return
        
        print(f"Found {len(voices)} voice(s):\n")
        
        for i, voice in enumerate(voices, 1):
            print(f"Voice #{i}")
            print(f"  ID:          {voice.id}")
            print(f"  Name:        {voice.name}")
            print(f"  Language:    {voice.language}")
            print(f"  Description: {voice.description}")
            print(f"  Created:     {voice.created_at}")
            print(f"  Public:      {voice.is_public}")
            
            # Check if it has additional attributes
            if hasattr(voice, 'user_id'):
                print(f"  User ID:     {voice.user_id}")
            
            print()
        
        print("="*80)
        print(f"Total: {len(voices)} voice(s)")
        print("="*80)
        
    except Exception as e:
        print(f"ERROR: Failed to list voices: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    list_voices()

