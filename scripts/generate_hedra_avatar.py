#!/usr/bin/env python3
"""
Standalone script to generate a Hedra avatar from an image.

This script:
1. Reads the HEDRA_API_KEY from a .env file
2. Uploads an image to Hedra API
3. Prints the asset_id for use in your avatar configuration

Usage:
    1. Set the IMAGE_PATH variable below to your image file path
    2. Run: python generate_hedra_avatar.py
"""

import os
import sys
import requests
from pathlib import Path
from dotenv import load_dotenv

# ============================================
# CONFIGURATION - Set your image path here
# ============================================
IMAGE_PATH = "/Users/tom/repos/expertavatar/expertavatar-interview/expertavatar-interview-agent/assets/ashley.png"
# ============================================


def load_api_key():
    """Load HEDRA_API_KEY from .env file."""
    # Load .env from the expert-avatar-backend directory
    backend_dir = Path(__file__).parent.parent
    dotenv_path = backend_dir / ".env.local"
    
    if not dotenv_path.exists():
        print(f"❌ Error: .env file not found at {dotenv_path}")
        print(f"   Please create a .env file with your HEDRA_API_KEY")
        sys.exit(1)
    
    load_dotenv(dotenv_path)
    api_key = os.getenv("HEDRA_API_KEY")
    
    if not api_key:
        print(f"❌ Error: HEDRA_API_KEY not found in {dotenv_path}")
        print(f"   Please add HEDRA_API_KEY=your_key_here to your .env file")
        sys.exit(1)
    
    print(f"✓ Loaded HEDRA_API_KEY from {dotenv_path}")
    return api_key


def validate_image_path(image_path: str):
    """Validate that the image path exists and is a file."""
    if not image_path or image_path == "/path/to/your/image.jpg":
        print(f"❌ Error: Please set IMAGE_PATH in the script to your actual image path")
        sys.exit(1)
    
    path = Path(image_path)
    if not path.exists():
        print(f"❌ Error: Image file not found: {image_path}")
        sys.exit(1)
    
    if not path.is_file():
        print(f"❌ Error: Path is not a file: {image_path}")
        sys.exit(1)
    
    # Check if it's an image file (basic check)
    valid_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
    if path.suffix.lower() not in valid_extensions:
        print(f"⚠️  Warning: File extension {path.suffix} may not be a valid image format")
        print(f"   Valid extensions: {', '.join(valid_extensions)}")
    
    print(f"✓ Image file found: {image_path}")
    return str(path.resolve())


def create_hedra_asset(api_key: str, avatar_name: str = "user_avatar"):
    """
    Create a Hedra asset and return the asset ID.
    
    Args:
        api_key: Hedra API key
        avatar_name: Name for the avatar asset
        
    Returns:
        str: Asset ID
    """
    hedra_base_url = "https://api.hedra.com/web-app/public/assets"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    
    print(f"\n📝 Creating Hedra asset with name: {avatar_name}")
    
    try:
        response = requests.post(
            hedra_base_url,
            headers=headers,
            json={
                "type": "image",
                "name": avatar_name
            },
            timeout=30
        )
        
        if response.status_code != 200:
            print(f"❌ Error: Failed to create Hedra asset")
            print(f"   Status code: {response.status_code}")
            print(f"   Response: {response.text}")
            sys.exit(1)
        
        asset_data = response.json()
        asset_id = asset_data.get("id")
        
        if not asset_id:
            print(f"❌ Error: No asset ID returned from Hedra API")
            print(f"   Response: {asset_data}")
            sys.exit(1)
        
        print(f"✓ Created Hedra asset with ID: {asset_id}")
        return asset_id
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error: Request to Hedra API failed: {e}")
        sys.exit(1)


def upload_image_to_hedra(api_key: str, asset_id: str, image_path: str):
    """
    Upload an image to a Hedra asset.
    
    Args:
        api_key: Hedra API key
        asset_id: Asset ID from create_hedra_asset
        image_path: Path to the image file
    """
    upload_url = f"https://api.hedra.com/web-app/public/assets/{asset_id}/upload"
    headers = {
        "X-API-Key": api_key
    }
    
    print(f"\n📤 Uploading image to Hedra asset {asset_id}")
    
    try:
        with open(image_path, 'rb') as image_file:
            files = {
                'file': (os.path.basename(image_path), image_file, 'image/jpeg')
            }
            response = requests.post(
                upload_url,
                headers=headers,
                files=files,
                timeout=60
            )
        
        if response.status_code != 200:
            print(f"❌ Error: Failed to upload image to Hedra")
            print(f"   Status code: {response.status_code}")
            print(f"   Response: {response.text}")
            sys.exit(1)
        
        print(f"✓ Successfully uploaded image to Hedra")
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error: Request to Hedra API failed: {e}")
        sys.exit(1)


def main():
    """Main function to generate Hedra avatar."""
    print("=" * 60)
    print("Hedra Avatar Generator")
    print("=" * 60)
    
    # Step 1: Load API key from .env
    api_key = load_api_key()
    
    # Step 2: Validate image path
    image_path = validate_image_path(IMAGE_PATH)
    
    # Step 3: Create Hedra asset
    asset_id = create_hedra_asset(api_key)
    
    # Step 4: Upload image to asset
    upload_image_to_hedra(api_key, asset_id, image_path)
    
    # Step 5: Print success message with asset ID
    print("\n" + "=" * 60)
    print("✅ SUCCESS!")
    print("=" * 60)
    print(f"\nYour Hedra avatar asset ID is:")
    print(f"\n    {asset_id}")
    print(f"\nYou can use this asset_id in your avatar configuration.")
    print("=" * 60)


if __name__ == "__main__":
    main()

