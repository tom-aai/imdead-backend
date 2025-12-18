"""
Cartoon Generator service.
Transforms user portrait images into editorial illustration style using Google Gemini.
"""

import os
import logging
import tempfile
from pathlib import Path
from google import genai
from google.genai import types
from PIL import Image

logger = logging.getLogger(__name__)


class CartoonGenerator:
	"""Service for transforming user images into editorial illustrations using Google Gemini."""
	
	# Editorial illustration prompt
	EDITORIAL_PROMPT = """Transform the uploaded portrait into a modern editorial illustration bust.

Depict the person from the chest up, centered, with a calm, thoughtful expression and a neutral, authoritative presence. The person must be facing forward because the image will be used to generate a talking avatar. Use a clean, professional editorial illustration style inspired by high-end magazine opinion art.

Simplify facial features slightly while preserving identity. Avoid photorealism. Use confident line work, subtle cross-hatching or soft shading, and restrained detail. Skin texture should be smooth and illustrated, not realistic.

The pose should feel composed and timeless, as if the subject is an expert explaining a complex idea. No exaggerated emotion. No stylized "cool" effects.

Use a limited, muted color palette with one or two accent tones. Background should be minimal and uncluttered, with very faint abstract shapes or intellectual motifs that suggest thinking and expertise rather than decoration.

Lighting should be even and soft, with gentle contrast, similar to an editorial illustration rather than a studio photograph.

Overall mood: credible, intelligent, approachable, and timeless — a visual representation of expertise rather than a realistic face."""
	
	def __init__(self, supabase_client, google_api_key: str = None):
		"""
		Initialize the cartoon generator.
		
		Args:
			supabase_client: Initialized Supabase client
			google_api_key: Google API key (optional, will use env var if not provided)
		"""
		self.supabase = supabase_client
		self.google_api_key = google_api_key or os.getenv("GOOGLE_API_KEY")
		
		if not self.google_api_key:
			raise Exception("GOOGLE_API_KEY not configured")
		else:
			# Initialize Google Genai client
			self.client = genai.Client(api_key=self.google_api_key)
	
	def cartoonize_image(self, user_id: str, picture_id: str, session_id: str) -> dict:
		"""
		Transform a user's image into an editorial illustration style.
		
		Process:
		1. Download original image from Supabase avatar-images-bucket
		2. Send to Google Gemini for transformation
		3. Upload transformed image back to Supabase storage
		4. Update interview_calls.cartoon_id with cartoonized picture_id
		5. Return new picture_id for use by Hedra
		
		Args:
			user_id: The user's ID
			picture_id: The original picture ID from avatar-images-bucket
			session_id: The LiveKit session ID
			
		Returns:
			dict: Processing result with status and new picture_id
			
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
		
		if not self.google_api_key:
			raise Exception("GOOGLE_API_KEY not configured")
		
		logger.info(f"Cartoonizing image for user {user_id}, picture {picture_id}")
		
		# Step 1: Download original image from Supabase storage
		local_image_path = self._download_image_from_storage(picture_id)
		cartoonized_image_path = None  # Initialize to avoid cleanup errors
		
		try:
			# Step 2: Transform image using Google Gemini
			cartoonized_image_path = self._transform_with_gemini(local_image_path)
			
			# Step 3: Upload transformed image to Supabase storage
			new_picture_id = self._upload_to_storage(
				cartoonized_image_path,
				original_picture_id=picture_id
			)
			
			# Step 4: Update interview_calls.cartoon_id with cartoonized picture_id
			self._update_interview_calls(
				session_id=session_id,
				cartoon_id=new_picture_id
			)
			
			logger.info(f"Image cartoonization completed: {picture_id} -> {new_picture_id}")
			
			return {
				"status": "success",
				"message": "Image transformed to editorial illustration",
				"session_id": session_id,
				"user_id": user_id,
				"original_picture_id": picture_id,
				"cartoonized_picture_id": new_picture_id
			}
			
		finally:
			# Clean up temporary files
			for path in [local_image_path, cartoonized_image_path]:
				if path and os.path.exists(path):
					try:
						os.unlink(path)
						logger.debug(f"Cleaned up temporary file: {path}")
					except Exception as e:
						logger.warning(f"Failed to clean up temporary file {path}: {e}")
	
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
	
	def _transform_with_gemini(self, image_path: str) -> str:
		"""
		Transform image using Google Gemini API.
		
		Args:
			image_path: Path to the original image file
			
		Returns:
			str: Path to the transformed image file
			
		Raises:
			Exception: If transformation fails
		"""
		try:
			logger.debug(f"Transforming image with Google Gemini")
			
			# Load and preprocess the image
			image = Image.open(image_path)
			
			# Convert to RGB if necessary (remove alpha channel)
			if image.mode in ('RGBA', 'LA', 'P'):
				# Create a white background
				background = Image.new('RGB', image.size, (255, 255, 255))
				if image.mode == 'P':
					image = image.convert('RGBA')
				background.paste(image, mask=image.split()[-1] if image.mode in ('RGBA', 'LA') else None)
				image = background
			elif image.mode != 'RGB':
				image = image.convert('RGB')
			
			# Resize if image is too large (max 4096 on longest side)
			max_size = 4096
			if max(image.size) > max_size:
				ratio = max_size / max(image.size)
				new_size = tuple(int(dim * ratio) for dim in image.size)
				image = image.resize(new_size, Image.Resampling.LANCZOS)
				logger.debug(f"Resized image to {new_size}")
			
			logger.debug(f"Image prepared: mode={image.mode}, size={image.size}")
			
			# Call Gemini API with image generation model
			# Use gemini-2.5-flash-image for image generation
			response = self.client.models.generate_content(
				model="gemini-2.5-flash-image",
				contents=[self.EDITORIAL_PROMPT, image],
				config=types.GenerateContentConfig(
					response_modalities=['TEXT', 'IMAGE']
				)
			)
			
			# Extract the generated image from response using SDK's as_image() method
			generated_image = None
			for part in response.parts:
				if part.inline_data:
					generated_image = part.as_image()
					logger.debug(f"Successfully extracted image using part.as_image()")
					break
			
			if not generated_image:
				# Log the response for debugging
				logger.error(f"No image found in response. Response text: {response.text if hasattr(response, 'text') else 'N/A'}")
				raise Exception("No image returned from Gemini API")
			
			# Save generated image to temporary file
			temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
			temp_file.close()
			generated_image.save(temp_file.name)
			
			logger.debug(f"Image transformation completed successfully")
			return temp_file.name
			
		except Exception as e:
			logger.error(f"Failed to transform image with Gemini: {e}")
			raise Exception(f"Gemini transformation failed: {e}")
	
	def _upload_to_storage(self, image_path: str, original_picture_id: str) -> str:
		"""
		Upload transformed image to Supabase storage.
		
		Args:
			image_path: Path to the transformed image file
			original_picture_id: Original picture ID (for naming)
			
		Returns:
			str: New picture_id in storage
			
		Raises:
			Exception: If upload fails
		"""
		try:
			# Generate new filename based on original
			original_path = Path(original_picture_id)
			original_name = original_path.stem
			new_picture_id = f"{original_name}_cartoon.png"
			
			logger.debug(f"Uploading cartoonized image as {new_picture_id}")
			
			# Read image file
			with open(image_path, 'rb') as f:
				image_data = f.read()
			
			# Upload to Supabase storage (overwrite if exists)
			self.supabase.storage.from_("avatar-images-bucket").upload(
				path=new_picture_id,
				file=image_data,
				file_options={"content-type": "image/png", "upsert": "true"}
			)
			
			logger.debug(f"Successfully uploaded cartoonized image to storage")
			return new_picture_id
			
		except Exception as e:
			logger.error(f"Failed to upload cartoonized image to storage: {e}")
			raise Exception(f"Storage upload failed: {e}")
	
	def _update_interview_calls(
		self,
		session_id: str,
		cartoon_id: str
	) -> dict:
		"""
		Update interview_calls table with cartoon_id.
		
		Args:
			session_id: The LiveKit session ID
			cartoon_id: The cartoonized picture ID
			
		Returns:
			dict: The updated record from Supabase
			
		Raises:
			Exception: If update fails
		"""
		try:
			response = self.supabase.table("interview_calls")\
				.update({"cartoon_id": cartoon_id})\
				.eq("session_id", session_id)\
				.execute()
			
			if response.data:
				logger.debug(f"Updated interview_calls.cartoon_id for session {session_id}")
				return response.data[0]
			else:
				raise Exception(f"Failed to update interview_calls for session {session_id}")
				
		except Exception as e:
			logger.error(f"Failed to update interview_calls: {e}")
			raise Exception(f"Database update failed: {e}")
