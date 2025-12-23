import os
import logging
import threading
import uuid
from functools import wraps

from flask import Flask, jsonify, request
from dotenv import load_dotenv
from supabase import create_client, Client

from services.hedra_avatar_id_generator import HedraAvatarGenerator

# Load environment variables: prefer .env.local, fallback to default .env if present
_loaded = load_dotenv(".env.local")
if not _loaded:
	load_dotenv()

app = Flask(__name__)

# API key for authenticating internal service calls (agent → backend)
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY")
FLASK_ENV = os.getenv("FLASK_ENV", "production")

def require_api_key(f):
	"""Decorator to require API key authentication for endpoints."""
	@wraps(f)
	def decorated(*args, **kwargs):
		if not BACKEND_API_KEY:
			if FLASK_ENV == "development":
				# Allow unauthenticated requests in local development
				logging.warning("BACKEND_API_KEY not configured - allowing request (development mode)")
				return f(*args, **kwargs)
			else:
				# Reject in production if API key is not configured
				logging.error("BACKEND_API_KEY not configured - rejecting request")
				return jsonify({"error": "Server misconfigured"}), 500
		
		api_key = request.headers.get('X-API-Key')
		if not api_key or api_key != BACKEND_API_KEY:
			return jsonify({"error": "Unauthorized"}), 401
		return f(*args, **kwargs)
	return decorated

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
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY")

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

if not CARTESIA_API_KEY:
	logger.warning("Cartesia API key not found - voice cloning will be unavailable")

# Initialize services
hedra_avatar_generator = HedraAvatarGenerator(supabase, HEDRA_API_KEY)


@app.route("/")
def index():
	"""Health check endpoint"""
	return jsonify({"status": "ok", "message": "Backend is running"})


@app.route("/health")
def health():
	"""Health check endpoint"""
	return jsonify({"status": "healthy"})


@app.route("/post-call-orchestrator", methods=["POST"])
@require_api_key
def post_call_orchestrator_endpoint():
	"""
	Orchestrate all post-call processing after a call completes.
	Called by the LiveKit agent after saving the transcript to Supabase.
	
	Instead of processing immediately, this enqueues a job in the post_call_jobs table.
	A background worker will pick up and process the job asynchronously.
	"""
	try:
		# Get session_id from request
		data = request.get_json()
		session_id = data.get("session_id")
		
		if not session_id:
			return jsonify({"error": "session_id is required"}), 400
		
		# Enqueue job in Supabase
		job_data = {
			"session_id": session_id,
			"status": "pending",
			"attempts": 0,
			"max_attempts": 3
		}
		
		response = supabase.table("post_call_jobs").insert(job_data).execute()
		
		if not response.data:
			raise Exception("Failed to enqueue job")
		
		job_id = response.data[0].get('id')
		
		logger.info(f"Post-call job enqueued for session {session_id} (job_id: {job_id})")
		
		# Return immediately with job ID
		return jsonify({
			"status": "queued",
			"message": "Post-call orchestration job enqueued",
			"session_id": session_id,
			"job_id": job_id
		}), 202
		
	except Exception as e:
		# Handle unexpected errors
		logger.error(f"Error enqueuing post-call orchestration: {e}", exc_info=True)
		return jsonify({"error": str(e)}), 500


@app.route("/jobs/<job_id>", methods=["GET"])
@require_api_key
def get_job_status(job_id: str):
	"""
	Get the status of a background job.
	
	Args:
		job_id: UUID of the job
		
	Returns:
		Job status information including current state, attempts, and result/error
	"""
	try:
		response = supabase.table("post_call_jobs")\
			.select("*")\
			.eq("id", job_id)\
			.limit(1)\
			.execute()
		
		if not response.data or len(response.data) == 0:
			return jsonify({"error": "Job not found"}), 404
		
		job = response.data[0]
		
		return jsonify({
			"job_id": job['id'],
			"session_id": job['session_id'],
			"status": job['status'],
			"attempts": job['attempts'],
			"max_attempts": job['max_attempts'],
			"error": job.get('error'),
			"result": job.get('result'),
			"created_at": job['created_at'],
			"started_at": job.get('started_at'),
			"completed_at": job.get('completed_at')
		}), 200
		
	except Exception as e:
		logger.error(f"Error fetching job status: {e}", exc_info=True)
		return jsonify({"error": str(e)}), 500


def _run_avatar_generation_in_background(user_id: str, picture_id: str, session_id: str):
	"""
	Run avatar generation in a background thread.
	This allows the endpoint to return immediately while processing continues.
	
	Args:
		user_id: The user's ID (required)
		picture_id: The picture ID from storage (required)
		session_id: The LiveKit session ID (required)
	"""
	try:
		result = hedra_avatar_generator.generate_avatar(
			user_id=user_id,
			picture_id=picture_id,
			session_id=session_id
		)
		if result.get("status") == "error":
			logger.error(f"Background avatar generation failed for user {user_id}, picture {picture_id}: {result.get('error')}")
		else:
			logger.info(f"Avatar generation completed for user {user_id} (hedra_avatar_id: {result.get('hedra_avatar_id')})")
	except Exception as e:
		logger.error(f"Error in background avatar generation for user {user_id}, picture {picture_id}: {e}", exc_info=True)


@app.route("/upload-avatar-image", methods=["POST"])
@require_api_key
def upload_avatar_image_endpoint():
	"""
	Upload an avatar image for a user and automatically generate Hedra avatar.
	This is the complete user upload flow - handles everything in the background.
	
	Required fields:
	- user_id: The user's ID
	- session_id: The LiveKit session ID (needed to update interview_call_analysis)
	- file: The image file (multipart/form-data)
	
	Process:
	1. Upload image to Supabase storage
	2. Create initial database record in user_avatar_images (without session_id for user uploads)
	3. Trigger Hedra avatar generation in background
	4. Update interview_call_analysis with hedra_avatar_id when complete
	5. Return immediately
	
	Note: For user uploads, session_id is NOT stored in user_avatar_images table,
	      so you can identify which images came from the interview call flow.
	
	Returns:
	- status: "processing"
	- picture_id: The ID of the uploaded picture in avatar-images-bucket
	- record_id: The ID of the created record in user_avatar_images table
	"""
	try:
		# Get user_id and session_id from form data
		user_id = request.form.get("user_id")
		session_id = request.form.get("session_id")
		
		if not user_id:
			return jsonify({"error": "user_id is required"}), 400
		
		if not session_id:
			return jsonify({"error": "session_id is required"}), 400
		
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
		
		# Generate a unique picture_id (flat structure, no subdirectories)
		picture_id = f"{uuid.uuid4()}.{file_ext}"
		
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
			"picture_id": picture_id,
			"picture_source": "user_upload"
		}
		
		response = supabase.table("user_avatar_images").insert(upload_data).execute()
		
		if not response.data:
			raise Exception("Failed to create record in user_avatar_images")
		
		record_id = response.data[0].get('id')
		
		logger.info(f"Created user_avatar_images record (id: {record_id}) for user {user_id}")
		
		# Start avatar generation in background thread
		thread = threading.Thread(
			target=_run_avatar_generation_in_background,
			args=(user_id, picture_id, session_id),
			daemon=True
		)
		thread.start()
		
		logger.info(f"Avatar generation started in background for user {user_id}, picture {picture_id}, session {session_id}")
		
		# Return immediately
		return jsonify({
			"status": "processing",
			"message": "Image uploaded successfully, avatar generation started in background",
			"user_id": user_id,
			"session_id": session_id,
			"picture_id": picture_id,
			"record_id": record_id
		}), 200
		
	except Exception as e:
		logger.error(f"Error uploading avatar image: {e}", exc_info=True)
		return jsonify({"error": str(e)}), 500


@app.route("/generate-avatar", methods=["POST"])
@require_api_key
def generate_avatar_endpoint():
	"""
	Regenerate a Hedra avatar for an existing picture.
	This endpoint is for manual retries or regenerating avatars, not the primary upload flow.
	
	For uploading new images, use /upload-avatar-image instead (which handles everything).
	
	Required fields:
	- user_id: The user's ID
	- picture_id: The picture ID from avatar-images-bucket in Supabase storage
	- session_id: The LiveKit session ID
	
	Note: This will UPDATE the existing rows in user_avatar_images and interview_call_analysis.
	"""
	try:
		# Get user_id, picture_id, and session_id from request
		data = request.get_json()
		user_id = data.get("user_id")
		picture_id = data.get("picture_id")
		session_id = data.get("session_id")
		
		if not user_id:
			return jsonify({"error": "user_id is required"}), 400
		
		if not picture_id:
			return jsonify({"error": "picture_id is required"}), 400
		
		if not session_id:
			return jsonify({"error": "session_id is required"}), 400
		
		# Start avatar generation in background thread
		thread = threading.Thread(
			target=_run_avatar_generation_in_background,
			args=(user_id, picture_id, session_id),
			daemon=True
		)
		thread.start()
		
		logger.info(f"Avatar regeneration started for user {user_id}, picture {picture_id}, session {session_id}")
		
		# Return immediately
		return jsonify({
			"status": "processing",
			"message": "Avatar generation started in background",
			"user_id": user_id,
			"picture_id": picture_id,
			"session_id": session_id
		}), 200
		
	except Exception as e:
		# Handle unexpected errors
		logger.error(f"Error starting avatar generation: {e}", exc_info=True)
		return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
	app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5001)), debug=True)
