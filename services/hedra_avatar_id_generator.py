"""
Hedra avatar generation service.
Handles post-call avatar creation using Hedra API.
"""

import json
import os
import time
import tempfile
import requests
from pathlib import Path
import sys

from services.logging_config import get_logger

logger = get_logger(__name__)


class HedraAvatarGenerator:
    """Service for generating avatars from user images using Hedra API."""
    
    def __init__(self, supabase_client, hedra_api_key: str = None):
        """
        Initialize the Hedra avatar generator.
        
        Args:
            supabase_client: Initialized Supabase client
            hedra_api_key: Hedra API key (optional, will use env var if not provided)
        """
        self.supabase = supabase_client
        self.hedra_api_key = hedra_api_key or os.getenv("HEDRA_API_KEY")
        
        if not self.hedra_api_key:
            logger.warning("HEDRA_API_KEY not set - avatar generation will fail")
    
    def generate_avatar(self, user_id: str, picture_id: str, session_id: str) -> dict:
        """
        Generate a Hedra avatar for a user based on their cartoonized image.
        
        Process:
        1. Look up cartoon_id from user_avatar_images using the original picture_id
        2. Download cartoon image from Supabase avatar-images-bucket
        3. Upload image to Hedra API
        4. Update user_avatar_images table with hedra_avatar_id
        5. Update interview_call_analysis table with hedra_avatar_id
        
        Args:
            user_id: The user's ID
            picture_id: The ORIGINAL picture ID (not the cartoon_id)
            session_id: The LiveKit session ID
            
        Returns:
            dict: Processing result with status and avatar data
            
        Raises:
            ValueError: If required parameters are missing
            Exception: If processing fails
        """
        if not user_id:
            raise ValueError("user_id is required")
        
        if not picture_id:
            raise ValueError("picture_id is required")
        
        if not session_id:
            raise ValueError("session_id is required")
        
        if not self.supabase:
            raise Exception("Supabase client not initialized")
        
        if not self.hedra_api_key:
            raise Exception("HEDRA_API_KEY not configured. Please set HEDRA_API_KEY in environment variables.")
        
        logger.info(f"Generating Hedra avatar for user {user_id}, picture {picture_id}")
        
        # Step 1: Get cartoon_id from user_avatar_images
        cartoon_id = self._get_cartoon_id(user_id, picture_id)
        
        if not cartoon_id:
            raise ValueError(f"No cartoon_id found for user {user_id}, picture {picture_id}")
        
        logger.info(f"Using cartoon image: {cartoon_id}")
        
        # Step 2: Download cartoon image from Supabase storage
        local_image_path = self._download_image_from_storage(cartoon_id)
        
        try:
            # Step 3: Upload image to Hedra and get hedra_avatar_id
            hedra_avatar_id = self._upload_to_hedra(local_image_path)
            
            # Step 4: Update user_avatar_images table with hedra_avatar_id
            uploaded_record = self._update_user_avatar_images(
                user_id=user_id,
                session_id=session_id,
                picture_id=picture_id,  # Use original picture_id to find the record
                hedra_avatar_id=hedra_avatar_id
            )
            
            # Step 5: Update interview_call_analysis table with hedra_avatar_id and cartoon_id
            self._update_interview_analysis(
                session_id=session_id,
                hedra_avatar_id=hedra_avatar_id,
                cartoon_id=cartoon_id
            )
            
            logger.info(f"Hedra avatar generation completed for picture {picture_id}, cartoon {cartoon_id} (hedra_avatar_id: {hedra_avatar_id})")
            
            return {
                "status": "success",
                "message": "Hedra avatar generated and uploaded successfully",
                "session_id": session_id,
                "user_id": user_id,
                "picture_id": picture_id,
                "cartoon_id": cartoon_id,
                "hedra_avatar_id": hedra_avatar_id,
                "record_id": uploaded_record.get('id')
            }
            
        finally:
            # Clean up temporary file
            if local_image_path and os.path.exists(local_image_path):
                try:
                    os.unlink(local_image_path)
                    logger.debug(f"Cleaned up temporary file: {local_image_path}")
                except Exception as e:
                    logger.warning(f"Failed to clean up temporary file {local_image_path}: {e}")
    
    def _get_cartoon_id(self, user_id: str, picture_id: str) -> str:
        """
        Get the cartoon_id from user_avatar_images table.
        
        Args:
            user_id: The user's ID
            picture_id: The original picture ID
            
        Returns:
            str: The cartoon_id from the record
            
        Raises:
            Exception: If lookup fails or cartoon_id not found
        """
        try:
            response = self.supabase.table("user_avatar_images")\
                .select("cartoon_id")\
                .eq("user_id", user_id)\
                .eq("picture_id", picture_id)\
                .execute()
            
            if not response.data:
                raise Exception(f"No record found in user_avatar_images for user {user_id}, picture {picture_id}")
            
            cartoon_id = response.data[0].get("cartoon_id")
            
            if not cartoon_id:
                raise Exception(f"No cartoon_id found in user_avatar_images for user {user_id}, picture {picture_id}")
            
            return cartoon_id
            
        except Exception as e:
            logger.error(f"Failed to get cartoon_id: {e}")
            raise
    
    def _download_image_from_storage(self, picture_id: str) -> str:
        """
        Download image from Supabase storage bucket.
        
        Args:
            picture_id: The picture filename/path in the storage bucket
            
        Returns:
            str: Path to downloaded temporary file
            
        Raises:
            Exception: If download fails
        """
        try:
            logger.debug(f"Downloading image {picture_id} from avatar-images-bucket")
            
            # Download from Supabase storage
            response = self.supabase.storage.from_("avatar-images-bucket").download(picture_id)
            
            if not response:
                raise Exception(f"Empty response downloading image {picture_id}")
            
            # Save to temporary file
            file_ext = Path(picture_id).suffix or '.jpg'
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=file_ext)
            temp_file.write(response)
            temp_file.close()
            
            logger.debug(f"Image downloaded successfully ({len(response)} bytes)")
            return temp_file.name
            
        except Exception as e:
            logger.error(f"Failed to download image '{picture_id}' from storage: {e}")
            raise Exception(f"Image download failed for '{picture_id}': {e}")
    
    def _upload_to_hedra(self, image_path: str) -> str:
        """
        Upload image to Hedra API and return hedra_avatar_id.
        
        The Hedra API requires two steps:
        1. Create an asset to get an asset ID
        2. Upload the image file to that asset
        
        Args:
            image_path: Path to the image file
            
        Returns:
            str: The Hedra avatar_id (asset ID)
            
        Raises:
            Exception: If upload fails
        """
        hedra_base_url = "https://api.hedra.com/web-app/public/assets"
        headers = {
            "X-API-Key": self.hedra_api_key
        }
        
        upload_start_time = time.time()
        
        try:
            # Step 1: Create asset with generic name
            avatar_name = "user_avatar"
            
            logger.info(
                "Hedra asset creation started",
                service="Hedra",
                operation="create_asset"
            )
            
            create_response = requests.post(
                hedra_base_url,
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "type": "image",
                    "name": avatar_name
                },
                timeout=30
            )
            
            if create_response.status_code != 200:
                logger.error(
                    f"Hedra asset creation failed: {create_response.status_code}",
                    service="Hedra",
                    operation="create_asset",
                    status_code=create_response.status_code
                )
                raise Exception(f"Failed to create Hedra asset: {create_response.status_code}")
            
            asset_data = create_response.json()
            asset_id = asset_data.get("id")
            
            if not asset_id:
                raise Exception("No asset ID returned from Hedra API")
            
            logger.info(
                "Hedra asset created",
                service="Hedra",
                operation="create_asset",
                asset_id=asset_id
            )
            
            # Step 2: Upload image to the asset
            upload_url = f"{hedra_base_url}/{asset_id}/upload"
            
            logger.info(
                "Hedra image upload started",
                service="Hedra",
                operation="upload_image",
                asset_id=asset_id
            )
            
            with open(image_path, 'rb') as image_file:
                files = {
                    'file': (os.path.basename(image_path), image_file, 'image/jpeg')
                }
                upload_response = requests.post(
                    upload_url,
                    headers={"X-API-Key": self.hedra_api_key},  # No Content-Type for multipart
                    files=files,
                    timeout=60
                )
            
            if upload_response.status_code != 200:
                logger.error(
                    f"Hedra image upload failed: {upload_response.status_code}",
                    service="Hedra",
                    operation="upload_image",
                    status_code=upload_response.status_code
                )
                raise Exception(f"Failed to upload image to Hedra: {upload_response.status_code}")
            
            duration_ms = int((time.time() - upload_start_time) * 1000)
            logger.info(
                "Hedra image upload completed",
                service="Hedra",
                operation="upload_image",
                asset_id=asset_id,
                duration_ms=duration_ms,
                status="success"
            )
            
            return asset_id
            
        except requests.exceptions.RequestException as e:
            duration_ms = int((time.time() - upload_start_time) * 1000)
            logger.error(
                f"Hedra API request failed: {e}",
                service="Hedra",
                duration_ms=duration_ms,
                error=str(e)
            )
            raise Exception(f"Hedra API error: {e}")
        except Exception as e:
            duration_ms = int((time.time() - upload_start_time) * 1000)
            logger.error(
                f"Failed to upload to Hedra: {e}",
                service="Hedra",
                duration_ms=duration_ms,
                error=str(e)
            )
            raise
    
    def _update_user_avatar_images(
        self,
        user_id: str,
        session_id: str,
        picture_id: str,
        hedra_avatar_id: str
    ) -> dict:
        """
        Update user_avatar_images table with hedra_avatar_id.
        
        Handles both post-call and user upload flows by updating the existing record.
        Only sets session_id for images from interview calls (picture_source == "from_interview_session").
        
        Args:
            user_id: The user's ID
            session_id: The LiveKit session ID
            picture_id: The original picture ID (used to find the record)
            hedra_avatar_id: The Hedra avatar ID
            
        Returns:
            dict: The updated record from Supabase
            
        Raises:
            Exception: If update fails
        """
        # First, get the existing record to check the picture_source
        existing_record_response = self.supabase.table("user_avatar_images")\
            .select("picture_source")\
            .eq("user_id", user_id)\
            .eq("picture_id", picture_id)\
            .execute()
        
        if not existing_record_response.data:
            raise Exception(f"No existing record found in user_avatar_images for user {user_id}, picture {picture_id}")
        
        picture_source = existing_record_response.data[0].get("picture_source")
        
        # Prepare update data with hedra_avatar_id
        update_data = {
            "hedra_avatar_id": hedra_avatar_id
        }
        
        # Only include session_id if this image came from the interview call flow
        if picture_source and picture_source == "from_interview_session":
            update_data["session_id"] = session_id
            logger.debug(f"Image from interview call - will set session_id in user_avatar_images")
        else:
            logger.debug(f"Image from user upload - will NOT set session_id in user_avatar_images")
        
        # Update existing row (find by user_id and picture_id)
        response = self.supabase.table("user_avatar_images")\
            .update(update_data)\
            .eq("user_id", user_id)\
            .eq("picture_id", picture_id)\
            .execute()
        
        if response.data:
            session_info = f", session: {session_id}" if (picture_source and picture_source == "from_interview_session") else " (no session_id - user upload or unknown source)"
            logger.debug(f"Updated user_avatar_images table (user: {user_id}{session_info}, hedra_avatar_id: {hedra_avatar_id})")
            return response.data[0]
        else:
            raise Exception(f"Failed to update user_avatar_images for user {user_id}, picture {picture_id}. Row may not exist.")
    
    def _update_interview_analysis(self, session_id: str, hedra_avatar_id: str, cartoon_id: str) -> dict:
        """
        Update interview_call_analysis table with hedra_avatar_id and cartoon_id.
        
        This makes interview_call_analysis the single source of truth for:
        - Which Hedra avatar to use for the chat agent (hedra_avatar_id)
        - Which cartoon image to display in the dashboard (cartoon_id)
        
        Args:
            session_id: The LiveKit session ID
            hedra_avatar_id: The Hedra avatar ID
            cartoon_id: The cartoon image filename in storage
            
        Returns:
            dict: The updated record from Supabase
            
        Raises:
            Exception: If update fails
        """
        update_data = {
            "hedra_avatar_id": hedra_avatar_id,
            "cartoon_id": cartoon_id
        }
        
        response = self.supabase.table("interview_call_analysis")\
            .update(update_data)\
            .eq("session_id", session_id)\
            .execute()
        
        if response.data:
            logger.debug(f"Updated interview_call_analysis table with hedra_avatar_id: {hedra_avatar_id}, cartoon_id: {cartoon_id} for session {session_id}")
            return response.data[0]
        else:
            raise Exception(f"Failed to update interview_call_analysis for session {session_id}. Row may not exist.")

