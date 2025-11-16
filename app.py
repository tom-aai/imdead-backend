import os
import logging
import asyncio
import threading
import uuid

from flask import Flask, jsonify, request
from dotenv import load_dotenv
from supabase import create_client, Client

from services.post_call_orchestrator import PostCallOrchestrator
from services.hedra_avatar_generation import HedraAvatarGenerator

# Load environment variables: prefer .env.local, fallback to default .env if present
_loaded = load_dotenv(".env.local")
if not _loaded:
	load_dotenv()

app = Flask(__name__)

# Configure logging
logging.basicConfig(
	level=logging.INFO,
	format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
HEDRA_API_KEY = os.getenv("HEDRA_API_KEY")

supabase: Client = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
	supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
	logger.debug("Supabase client initialized")
else:
	logger.error("Supabase credentials not found")

if not ANTHROPIC_API_KEY:
	logger.error("Anthropic API key not found")

if not HEDRA_API_KEY:
	logger.warning("Hedra API key not found - avatar generation will be unavailable")

# Initialize services
post_call_orchestrator = PostCallOrchestrator(supabase, ANTHROPIC_API_KEY, HEDRA_API_KEY)
hedra_avatar_generator = HedraAvatarGenerator(supabase, HEDRA_API_KEY)


@app.route("/")
def index():
	"""Health check endpoint"""
	return jsonify({"status": "ok", "message": "Backend is running"})


@app.route("/health")
def health():
	"""Health check endpoint"""
	return jsonify({"status": "healthy"})


def _run_orchestrator_in_background(session_id: str):
	"""
	Run the orchestrator in a background thread.
	This allows the endpoint to return immediately while processing continues.
	"""
	try:
		result = asyncio.run(post_call_orchestrator.process_call_completion(session_id))
		if result["status"] == "error":
			logger.error(f"Background orchestration failed for {session_id}: {result.get('error')}")
		else:
			logger.info(f"Post-call orchestration completed for {session_id}")
	except Exception as e:
		logger.error(f"Error in background orchestration for {session_id}: {e}", exc_info=True)


@app.route("/post-call-orchestrator", methods=["POST"])
def post_call_orchestrator_endpoint():
	"""
	Orchestrate all post-call processing after a call completes.
	Called by the LiveKit agent after saving the transcript to Supabase.
	Returns immediately and runs orchestration in the background.
	"""
	try:
		# Get session_id from request
		data = request.get_json()
		session_id = data.get("session_id")
		
		if not session_id:
			return jsonify({"error": "session_id is required"}), 400
		
		# Start orchestrator in background thread
		thread = threading.Thread(
			target=_run_orchestrator_in_background,
			args=(session_id,),
			daemon=True
		)
		thread.start()
		
		logger.info(f"Post-call orchestrator started for session {session_id}")
		
		# Return immediately
		return jsonify({
			"status": "processing",
			"message": "Post-call orchestration started in background",
			"session_id": session_id
		}), 200
		
	except Exception as e:
		# Handle unexpected errors
		logger.error(f"Error starting post-call orchestration: {e}", exc_info=True)
		return jsonify({"error": str(e)}), 500


def _run_avatar_generation_in_background(user_id: str, picture_id: str):
	"""
	Run avatar generation in a background thread.
	This allows the endpoint to return immediately while processing continues.
	"""
	try:
		result = hedra_avatar_generator.generate_avatar(
			user_id=user_id,
			picture_id=picture_id
		)
		if result.get("status") == "error":
			logger.error(f"Background avatar generation failed for user {user_id}, picture {picture_id}: {result.get('error')}")
		else:
			logger.info(f"Avatar generation completed for user {user_id} (hedra_avatar_id: {result.get('hedra_avatar_id')})")
	except Exception as e:
		logger.error(f"Error in background avatar generation for user {user_id}, picture {picture_id}: {e}", exc_info=True)


@app.route("/upload-avatar-image", methods=["POST"])
def upload_avatar_image_endpoint():
	"""
	Upload an avatar image for a user and automatically generate Hedra avatar.
	This is the complete user upload flow - handles everything in the background.
	
	Required fields:
	- user_id: The user's ID
	- file: The image file (multipart/form-data)
	
	Process:
	1. Upload image to Supabase storage
	2. Create initial database record in user_avatar_images
	3. Trigger Hedra avatar generation in background
	4. Return immediately
	
	Returns:
	- status: "processing"
	- picture_id: The ID of the uploaded picture in avatar-images-bucket
	- record_id: The ID of the created record in user_avatar_images table
	"""
	try:
		# Get user_id from form data
		user_id = request.form.get("user_id")
		
		if not user_id:
			return jsonify({"error": "user_id is required"}), 400
		
		# Get the uploaded file
		if 'file' not in request.files:
			return jsonify({"error": "file is required"}), 400
		
		file = request.files['file']
		
		if file.filename == '':
			return jsonify({"error": "No file selected"}), 400
		
		# Validate file type (basic check)
		allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
		file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
		
		if file_ext not in allowed_extensions:
			return jsonify({"error": f"File type not allowed. Allowed types: {', '.join(allowed_extensions)}"}), 400
		
		# Generate a unique picture_id
		picture_id = f"{user_id}/{uuid.uuid4()}.{file_ext}"
		
		# Upload to Supabase storage
		file_data = file.read()
		supabase.storage.from_("avatar-images-bucket").upload(
			picture_id,
			file_data,
			file_options={"content-type": file.content_type}
		)
		
		logger.info(f"Uploaded image to avatar-images-bucket: {picture_id}")
		
		# Create initial record in user_avatar_images table (without hedra_avatar_id)
		upload_data = {
			"user_id": user_id,
			"picture_id": picture_id
		}
		
		response = supabase.table("user_avatar_images").insert(upload_data).execute()
		
		if not response.data:
			raise Exception("Failed to create record in user_avatar_images")
		
		record_id = response.data[0].get('id')
		
		logger.info(f"Created user_avatar_images record (id: {record_id}) for user {user_id}")
		
		# Start avatar generation in background thread
		thread = threading.Thread(
			target=_run_avatar_generation_in_background,
			args=(user_id, picture_id),
			daemon=True
		)
		thread.start()
		
		logger.info(f"Avatar generation started in background for user {user_id}, picture {picture_id}")
		
		# Return immediately
		return jsonify({
			"status": "processing",
			"message": "Image uploaded successfully, avatar generation started in background",
			"user_id": user_id,
			"picture_id": picture_id,
			"record_id": record_id
		}), 200
		
	except Exception as e:
		logger.error(f"Error uploading avatar image: {e}", exc_info=True)
		return jsonify({"error": str(e)}), 500


@app.route("/generate-avatar", methods=["POST"])
def generate_avatar_endpoint():
	"""
	Regenerate a Hedra avatar for an existing picture.
	This endpoint is for manual retries or regenerating avatars, not the primary upload flow.
	
	For uploading new images, use /upload-avatar-image instead (which handles everything).
	
	Required fields:
	- user_id: The user's ID
	- picture_id: The picture ID from avatar-images-bucket in Supabase storage
	
	Note: This will UPDATE the existing row in user_avatar_images (not create a new one).
	"""
	try:
		# Get user_id and picture_id from request
		data = request.get_json()
		user_id = data.get("user_id")
		picture_id = data.get("picture_id")
		
		if not user_id:
			return jsonify({"error": "user_id is required"}), 400
		
		if not picture_id:
			return jsonify({"error": "picture_id is required"}), 400
		
		# Start avatar generation in background thread
		thread = threading.Thread(
			target=_run_avatar_generation_in_background,
			args=(user_id, picture_id),
			daemon=True
		)
		thread.start()
		
		logger.info(f"Avatar regeneration started for user {user_id}, picture {picture_id}")
		
		# Return immediately
		return jsonify({
			"status": "processing",
			"message": "Avatar generation started in background",
			"user_id": user_id,
			"picture_id": picture_id
		}), 200
		
	except Exception as e:
		# Handle unexpected errors
		logger.error(f"Error starting avatar generation: {e}", exc_info=True)
		return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
	app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5001)), debug=True)
