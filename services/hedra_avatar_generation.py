"""
Hedra avatar generation service.
Handles post-call avatar creation using Hedra API.
"""

import json
import logging
import os
import tempfile
import requests
from pathlib import Path
import sys

logger = logging.getLogger(__name__)


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
	
	def generate_avatar(self, session_id: str) -> dict:
		"""
		Generate a Hedra avatar for a user based on their uploaded image.
		
		Process:
		1. Look up picture_id from interview_calls table
		2. Download image from Supabase avatar-images-bucket
		3. Upload image to Hedra API
		4. Store hedra_avatar_id in user_avatar_images table
		
		Args:
			session_id: The LiveKit session ID
			
		Returns:
			dict: Processing result with status and avatar data
			
		Raises:
			ValueError: If session_id is invalid or required data not found
			Exception: If processing fails
		"""
		if not session_id:
			raise ValueError("session_id is required")
		
		if not self.supabase:
			raise Exception("Supabase client not initialized")
		
		if not self.hedra_api_key:
			raise Exception("HEDRA_API_KEY not configured")
		
		logger.info(f"Generating Hedra avatar for session {session_id}")
		
		# Step 1: Fetch call data and picture_id from Supabase
		call_data = self._get_call_data(session_id)
		picture_id = call_data.get('picture_id')
		user_id = call_data.get('user_id')
		user_firstname = call_data.get('user_firstname', '')
		user_lastname = call_data.get('user_lastname', '')
		
		if not picture_id:
			logger.error(f"No picture_id found for session {session_id}")
			raise ValueError(f"No picture_id found for session {session_id}")
		
		if not user_id:
			logger.error(f"No user_id found for session {session_id}")
			raise ValueError(f"No user_id found for session {session_id}")
		
		logger.debug(f"Found picture_id: {picture_id} for user {user_id}")
		
		# Step 2: Download image from Supabase storage
		local_image_path = self._download_image_from_storage(picture_id)
		
		try:
			# Step 3: Upload image to Hedra and get hedra_avatar_id
			hedra_avatar_id = self._upload_to_hedra(local_image_path, user_firstname, user_lastname)
			
			# Step 4: Store hedra_avatar_id in user_avatar_images table
			uploaded_record = self._upload_to_supabase(
				user_id=user_id,
				session_id=session_id,
				picture_id=picture_id,
				hedra_avatar_id=hedra_avatar_id,
				user_firstname=user_firstname,
				user_lastname=user_lastname
			)
			
			logger.info(f"Hedra avatar generation completed for session {session_id} (hedra_avatar_id: {hedra_avatar_id})")
			
			return {
				"status": "success",
				"message": "Hedra avatar generated and uploaded successfully",
				"session_id": session_id,
				"user_id": user_id,
				"picture_id": picture_id,
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
	
	def _get_call_data(self, session_id: str) -> dict:
		"""
		Fetch call data from interview_calls table.
		
		Args:
			session_id: The LiveKit session ID
			
		Returns:
			dict: Call data record
			
		Raises:
			ValueError: If call not found
		"""
		response = self.supabase.table("interview_calls")\
			.select("session_id, user_id, picture_id, user_firstname, user_lastname, created_at")\
			.eq("session_id", session_id)\
			.execute()
		
		if not response.data or len(response.data) == 0:
			logger.error(f"No call found for session_id: {session_id}")
			raise ValueError(f"Call not found for session_id: {session_id}")
		
		return response.data[0]
	
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
				raise Exception(f"Failed to download image {picture_id} from storage")
			
			# Save to temporary file
			# Determine file extension from picture_id
			file_ext = Path(picture_id).suffix or '.jpg'
			temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=file_ext)
			temp_file.write(response)
			temp_file.close()
			
			logger.debug(f"Image downloaded to temporary file: {temp_file.name}")
			return temp_file.name
			
		except Exception as e:
			logger.error(f"Failed to download image from storage: {e}")
			raise Exception(f"Image download failed: {e}")
	
	def _upload_to_hedra(self, image_path: str, user_firstname: str, user_lastname: str) -> str:
		"""
		Upload image to Hedra API and return hedra_avatar_id.
		
		The Hedra API requires two steps:
		1. Create an asset to get an asset ID
		2. Upload the image file to that asset
		
		Args:
			image_path: Path to the image file
			user_firstname: User's first name for naming the avatar
			user_lastname: User's last name for naming the avatar
			
		Returns:
			str: The Hedra avatar_id (asset ID)
			
		Raises:
			Exception: If upload fails
		"""
		hedra_base_url = "https://api.hedra.com/web-app/public/assets"
		headers = {
			"X-API-Key": self.hedra_api_key
		}
		
		try:
			# Step 1: Create asset
			avatar_name = f"{user_firstname}_{user_lastname}_avatar".strip("_")
			if not avatar_name:
				avatar_name = "user_avatar"
			
			logger.debug(f"Creating Hedra asset with name: {avatar_name}")
			
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
				logger.error(f"Hedra asset creation failed: {create_response.status_code} - {create_response.text}")
				raise Exception(f"Failed to create Hedra asset: {create_response.status_code}")
			
			asset_data = create_response.json()
			asset_id = asset_data.get("id")
			
			if not asset_id:
				raise Exception("No asset ID returned from Hedra API")
			
			logger.debug(f"Created Hedra asset with ID: {asset_id}")
			
			# Step 2: Upload image to the asset
			upload_url = f"{hedra_base_url}/{asset_id}/upload"
			
			logger.debug(f"Uploading image to Hedra asset {asset_id}")
			
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
				logger.error(f"Hedra image upload failed: {upload_response.status_code} - {upload_response.text}")
				raise Exception(f"Failed to upload image to Hedra: {upload_response.status_code}")
			
			logger.debug(f"Successfully uploaded image to Hedra (asset_id: {asset_id})")
			
			return asset_id
			
		except requests.exceptions.RequestException as e:
			logger.error(f"Hedra API request failed: {e}")
			raise Exception(f"Hedra API error: {e}")
		except Exception as e:
			logger.error(f"Failed to upload to Hedra: {e}")
			raise
	
	def _upload_to_supabase(
		self,
		user_id: str,
		session_id: str,
		picture_id: str,
		hedra_avatar_id: str,
		user_firstname: str,
		user_lastname: str
	) -> dict:
		"""
		Upload avatar data to user_avatar_images table.
		
		Args:
			user_id: The user's ID
			session_id: The LiveKit session ID
			picture_id: The picture ID from storage
			hedra_avatar_id: The Hedra avatar ID
			user_firstname: User's first name
			user_lastname: User's last name
			
		Returns:
			dict: The inserted record from Supabase
			
		Raises:
			Exception: If upload fails
		"""
		upload_data = {
			"user_id": user_id,
			"session_id": session_id,
			"picture_id": picture_id,
			"hedra_avatar_id": hedra_avatar_id,
			"user_firstname": user_firstname,
			"user_lastname": user_lastname
		}
		
		# Insert into Supabase
		response = self.supabase.table("user_avatar_images").insert(upload_data).execute()
		
		if response.data:
			logger.debug(f"Uploaded avatar data to database (user: {user_id}, hedra_avatar_id: {hedra_avatar_id})")
			return response.data[0]
		else:
			raise Exception("Failed to upload avatar data to Supabase")

