import os
import logging
import threading
import uuid
from functools import wraps

from flask import Flask, jsonify, request
from dotenv import load_dotenv
from supabase import create_client, Client

from services.hedra_avatar_id_generator import HedraAvatarGenerator
from services.voice_cloner import VoiceCloningService

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
INWORLD_API_KEY = os.getenv("INWORLD_API_KEY")
INWORLD_WORKSPACE_ID = os.getenv("INWORLD_WORKSPACE_ID")
VOICE_PROVIDER = os.getenv("VOICE_PROVIDER", "inworld")

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
voice_cloning_service = VoiceCloningService(
	supabase,
	voice_provider=VOICE_PROVIDER,
	cartesia_api_key=CARTESIA_API_KEY,
	inworld_api_key=INWORLD_API_KEY,
	inworld_workspace_id=INWORLD_WORKSPACE_ID
)


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
		
		# Enqueue job in Supabase with job_type
		job_data = {
			"session_id": session_id,
			"job_type": "post_call",
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


@app.route("/resume-session-orchestrator", methods=["POST"])
@require_api_key
def resume_session_orchestrator_endpoint():
	"""
	Process a resumed session by appending the new conversation to the original session.
	Called by the LiveKit agent after saving the transcript for a resumed session.
	
	This enqueues a job in the post_call_jobs table with job_type='resume_session'.
	A background worker will pick up and process the job asynchronously.
	
	Required fields:
	- session_id: The new LiveKit session ID (where the resumed conversation is stored)
	- original_session_id: The original session ID to append the conversation to
	"""
	try:
		# Get data from request
		data = request.get_json()
		session_id = data.get("session_id")
		original_session_id = data.get("original_session_id")
		
		if not session_id:
			return jsonify({"error": "session_id is required"}), 400
		
		if not original_session_id:
			return jsonify({"error": "original_session_id is required"}), 400
		
		# Enqueue job in Supabase with job_type='resume_session'
		job_data = {
			"session_id": session_id,
			"job_type": "resume_session",
			"job_data": {
				"original_session_id": original_session_id
			},
			"status": "pending",
			"attempts": 0,
			"max_attempts": 3
		}
		
		response = supabase.table("post_call_jobs").insert(job_data).execute()
		
		if not response.data:
			raise Exception("Failed to enqueue resume session job")
		
		job_id = response.data[0].get('id')
		
		logger.info(f"Resume session job enqueued: {session_id} -> {original_session_id} (job_id: {job_id})")
		
		# Return immediately with job ID
		return jsonify({
			"status": "queued",
			"message": "Resume session job enqueued",
			"session_id": session_id,
			"original_session_id": original_session_id,
			"job_id": job_id
		}), 202
		
	except Exception as e:
		# Handle unexpected errors
		logger.error(f"Error enqueuing resume session job: {e}", exc_info=True)
		return jsonify({"error": str(e)}), 500


@app.route("/chat-session-orchestrator", methods=["POST"])
@require_api_key
def chat_session_orchestrator_endpoint():
	"""
	Called by the chat agent after saving the chat session transcript to Supabase.
	
	Messages are created directly by the chat agent during the conversation via
	the create_message tool (inserted into seeker_messages with status='pending').
	
	This endpoint enqueues a message_notification job to notify the expert
	about any new pending messages from this session.
	
	Required fields:
	- session_id: The chat session ID (from chat_sessions table)
	"""
	try:
		# Get data from request
		data = request.get_json()
		session_id = data.get("session_id")
		
		if not session_id:
			return jsonify({"error": "session_id is required"}), 400
		
		# Enqueue message_notification job to notify expert of new messages
		job_data = {
			"session_id": session_id,
			"job_type": "message_notification",
			"status": "pending",
			"attempts": 0,
			"max_attempts": 3
		}
		
		response = supabase.table("post_call_jobs").insert(job_data).execute()
		
		if not response.data:
			raise Exception("Failed to enqueue message notification job")
		
		job_id = response.data[0].get('id')
		
		logger.info(f"Message notification job enqueued for chat session {session_id} (job_id: {job_id})")
		
		return jsonify({
			"status": "queued",
			"message": "Message notification job enqueued",
			"session_id": session_id,
			"job_id": job_id
		}), 202
		
	except Exception as e:
		logger.error(f"Error enqueuing message notification job: {e}", exc_info=True)
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


def _verify_supabase_user(auth_header: str) -> dict | None:
	"""
	Verify a Supabase JWT token and return the user info.
	
	Args:
		auth_header: The Authorization header value (Bearer <token>)
		
	Returns:
		dict: User info if valid, None otherwise
	"""
	if not auth_header or not auth_header.startswith('Bearer '):
		return None
	
	token = auth_header.replace('Bearer ', '')
	
	try:
		# Use Supabase to verify the token and get user info
		user_response = supabase.auth.get_user(token)
		if user_response and user_response.user:
			return {
				"id": user_response.user.id,
				"email": user_response.user.email
			}
		return None
	except Exception as e:
		logger.warning(f"Failed to verify Supabase token: {e}")
		return None


@app.route("/generate-hot-take-video", methods=["POST"])
def generate_hot_take_video_endpoint():
	"""
	Generate a hot take video for a session. Called from the frontend dashboard.
	
	This endpoint is authenticated via Supabase JWT token (user auth).
	It enqueues a job in the post_call_jobs table with job_type='hot_take' for async processing.
	
	Required fields:
	- session_id: The LiveKit session ID
	- hot_take_number: Which hot take (1 or 2)
	- hot_take_text: The (possibly edited) hot take text
	
	Returns:
	- job_id: UUID of the enqueued job for status polling
	"""
	try:
		# Verify user authentication via Supabase JWT
		auth_header = request.headers.get('Authorization')
		user = _verify_supabase_user(auth_header)
		
		if not user:
			return jsonify({"error": "Unauthorized"}), 401
		
		user_id = user["id"]
		
		# Get request data
		data = request.get_json()
		session_id = data.get("session_id")
		hot_take_text = data.get("hot_take_text")
		
		if not session_id:
			return jsonify({"error": "session_id is required"}), 400
		
		if not hot_take_text or not hot_take_text.strip():
			return jsonify({"error": "hot_take_text is required"}), 400
		
		# Verify the session belongs to this user
		session_response = supabase.table("interview_call_analysis")\
			.select("user_id")\
			.eq("session_id", session_id)\
			.limit(1)\
			.execute()
		
		if not session_response.data or len(session_response.data) == 0:
			return jsonify({"error": "Session not found"}), 404
		
		session_user_id = session_response.data[0].get("user_id")
		if session_user_id != user_id:
			return jsonify({"error": "Unauthorized - session belongs to another user"}), 403
		
		# Update the hot_take text in interview_call_analysis (in case it was edited)
		supabase.table("interview_call_analysis")\
			.update({"hot_take_1": hot_take_text.strip()})\
			.eq("session_id", session_id)\
			.execute()
		
		logger.info(f"Updated hot_take_1 for session {session_id}")
		
		# Enqueue hot take video generation job in post_call_jobs with job_type='hot_take'
		job_record = {
			"session_id": session_id,
			"job_type": "hot_take",
			"job_data": {"user_id": user_id},
			"status": "pending",
			"attempts": 0,
			"max_attempts": 3
		}
		
		job_response = supabase.table("post_call_jobs").insert(job_record).execute()
		
		if not job_response.data:
			raise Exception("Failed to enqueue hot take job")
		
		job_id = job_response.data[0].get('id')
		
		logger.info(f"Hot take video job enqueued for session {session_id} (job_id: {job_id})")
		
		return jsonify({
			"status": "queued",
			"message": "Hot take video generation job enqueued",
			"job_id": job_id,
			"session_id": session_id
		}), 202
		
	except Exception as e:
		logger.error(f"Error enqueuing hot take video generation: {e}", exc_info=True)
		return jsonify({"error": str(e)}), 500


@app.route("/hot-take-jobs/<job_id>", methods=["GET"])
def get_hot_take_job_status(job_id: str):
	"""
	Get the status of a hot take video generation job.
	
	Authenticated via Supabase JWT token.
	Queries the post_call_jobs table for jobs with job_type='hot_take'.
	
	Args:
		job_id: UUID of the job
		
	Returns:
		Job status information including current state, attempts, and result/error
	"""
	try:
		# Verify user authentication via Supabase JWT
		auth_header = request.headers.get('Authorization')
		user = _verify_supabase_user(auth_header)
		
		if not user:
			return jsonify({"error": "Unauthorized"}), 401
		
		user_id = user["id"]
		
		# Fetch the job from post_call_jobs
		response = supabase.table("post_call_jobs")\
			.select("*")\
			.eq("id", job_id)\
			.eq("job_type", "hot_take")\
			.limit(1)\
			.execute()
		
		if not response.data or len(response.data) == 0:
			return jsonify({"error": "Job not found"}), 404
		
		job = response.data[0]
		job_data = job.get("job_data") or {}
		
		# Verify job belongs to this user (user_id is stored in job_data for hot_take jobs)
		job_user_id = job_data.get("user_id")
		if job_user_id != user_id:
			return jsonify({"error": "Unauthorized - job belongs to another user"}), 403
		
		return jsonify({
			"job_id": job['id'],
			"session_id": job['session_id'],
			"job_type": job.get('job_type'),
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
		logger.error(f"Error fetching hot take job status: {e}", exc_info=True)
		return jsonify({"error": str(e)}), 500


@app.route("/reclone-voice", methods=["POST"])
def reclone_voice_endpoint():
	"""
	Re-clone a user's voice from a new audio recording.
	
	This endpoint is authenticated via Supabase JWT token (user auth).
	It accepts an audio file (WAV format), uploads it to Supabase storage,
	and enqueues a job for async processing by the worker.
	
	Request:
		- Content-Type: multipart/form-data
		- audio: The audio file (WAV format, ~10 seconds)
	
	Returns:
		- status: "queued"
		- job_id: UUID of the enqueued job for status polling
	"""
	try:
		# Verify user authentication via Supabase JWT
		auth_header = request.headers.get('Authorization')
		user = _verify_supabase_user(auth_header)
		
		if not user:
			return jsonify({"error": "Unauthorized"}), 401
		
		user_id = user["id"]
		
		# Get the audio file from the request
		if 'audio' not in request.files:
			return jsonify({"error": "audio file is required"}), 400
		
		audio_file = request.files['audio']
		
		if audio_file.filename == '':
			return jsonify({"error": "No audio file selected"}), 400
		
		# Read audio data
		audio_data = audio_file.read()
		
		if len(audio_data) == 0:
			return jsonify({"error": "Audio file is empty"}), 400
		
		logger.info(f"Received voice re-clone request from user {user_id} ({len(audio_data):,} bytes)")
		
		# Upload audio to Supabase storage
		audio_id = str(uuid.uuid4())
		storage_path = f"{user_id}/{audio_id}.wav"
		
		supabase.storage.from_("voice-clone-audio").upload(
			storage_path,
			audio_data,
			file_options={"content-type": "audio/wav"}
		)
		
		logger.info(f"Uploaded voice clone audio to storage: {storage_path}")
		
		# Enqueue voice_reclone job
		job_record = {
			"session_id": audio_id,  # Use audio_id as session_id for this job type
			"job_type": "voice_reclone",
			"job_data": {
				"user_id": user_id,
				"audio_path": storage_path
			},
			"status": "pending",
			"attempts": 0,
			"max_attempts": 3
		}
		
		job_response = supabase.table("post_call_jobs").insert(job_record).execute()
		
		if not job_response.data:
			raise Exception("Failed to enqueue voice reclone job")
		
		job_id = job_response.data[0].get('id')
		
		logger.info(f"Voice reclone job enqueued for user {user_id} (job_id: {job_id})")
		
		return jsonify({
			"status": "queued",
			"message": "Voice re-clone job enqueued",
			"job_id": job_id
		}), 202
		
	except ValueError as e:
		logger.warning(f"Invalid request for voice re-clone: {e}")
		return jsonify({"error": str(e)}), 400
		
	except Exception as e:
		logger.error(f"Error enqueuing voice re-clone: {e}", exc_info=True)
		return jsonify({"error": str(e)}), 500


@app.route("/reclone-voice-jobs/<job_id>", methods=["GET"])
def get_reclone_voice_job_status(job_id: str):
	"""
	Get the status of a voice re-clone job.
	
	Authenticated via Supabase JWT token.
	
	Args:
		job_id: UUID of the job
		
	Returns:
		Job status information including current state and result/error
	"""
	try:
		# Verify user authentication via Supabase JWT
		auth_header = request.headers.get('Authorization')
		user = _verify_supabase_user(auth_header)
		
		if not user:
			return jsonify({"error": "Unauthorized"}), 401
		
		user_id = user["id"]
		
		# Fetch the job
		response = supabase.table("post_call_jobs")\
			.select("*")\
			.eq("id", job_id)\
			.eq("job_type", "voice_reclone")\
			.limit(1)\
			.execute()
		
		if not response.data or len(response.data) == 0:
			return jsonify({"error": "Job not found"}), 404
		
		job = response.data[0]
		job_data = job.get("job_data") or {}
		
		# Verify job belongs to this user
		job_user_id = job_data.get("user_id")
		if job_user_id != user_id:
			return jsonify({"error": "Unauthorized - job belongs to another user"}), 403
		
		return jsonify({
			"job_id": job['id'],
			"job_type": job.get('job_type'),
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
		logger.error(f"Error fetching voice reclone job status: {e}", exc_info=True)
		return jsonify({"error": str(e)}), 500


@app.route("/sessions/<session_id>", methods=["PATCH"])
def update_session_endpoint(session_id: str):
	"""
	Update a session's metadata (e.g., main_topic/title).
	
	Authenticated via Supabase JWT token (user auth).
	
	Args:
		session_id: The session ID to update
	
	Request body:
		- main_topic: The new title for the session (required, non-empty string)
	
	Returns:
		- success: True if updated
		- main_topic: The updated main_topic value
	"""
	try:
		# Verify user authentication via Supabase JWT
		auth_header = request.headers.get('Authorization')
		user = _verify_supabase_user(auth_header)
		
		if not user:
			return jsonify({"error": "Unauthorized"}), 401
		
		user_id = user["id"]
		
		# Get request data (silent=True to handle missing/invalid JSON gracefully)
		data = request.get_json(silent=True)
		
		if not data:
			return jsonify({"error": "Request body is required"}), 400
		
		main_topic = data.get("main_topic")
		
		# Validate input
		if main_topic is None:
			return jsonify({"error": "main_topic is required"}), 400
		
		if not isinstance(main_topic, str) or not main_topic.strip():
			return jsonify({"error": "main_topic must be a non-empty string"}), 400
		
		trimmed_topic = main_topic.strip()
		
		# Verify the session exists and belongs to this user
		session_response = supabase.table("interview_call_analysis")\
			.select("user_id")\
			.eq("session_id", session_id)\
			.limit(1)\
			.execute()
		
		if not session_response.data or len(session_response.data) == 0:
			return jsonify({"error": "Session not found"}), 404
		
		session_user_id = session_response.data[0].get("user_id")
		if session_user_id != user_id:
			return jsonify({"error": "Forbidden - session belongs to another user"}), 403
		
		# Update the main_topic in interview_call_analysis
		update_response = supabase.table("interview_call_analysis")\
			.update({"main_topic": trimmed_topic})\
			.eq("session_id", session_id)\
			.execute()
		
		if not update_response.data:
			logger.error(f"Failed to update session {session_id}")
			return jsonify({"error": "Failed to update session"}), 500
		
		logger.info(f"Updated main_topic for session {session_id}")
		
		return jsonify({
			"success": True,
			"main_topic": trimmed_topic
		}), 200
		
	except Exception as e:
		logger.error(f"Error updating session: {e}", exc_info=True)
		return jsonify({"error": str(e)}), 500


@app.route("/update-session-avatar", methods=["POST"])
@require_api_key
def update_session_avatar_endpoint():
	"""
	Update the avatar for an existing session.
	
	This endpoint is called from the frontend when a user wants to change their
	avatar image for a session. It:
	1. Creates a new record in user_avatar_images table
	2. Enqueues an avatar_update job for async processing (cartoonize + Hedra)
	
	Required fields:
	- user_id: The user's ID
	- session_id: The session to update
	- picture_id: The picture ID from Supabase storage (already uploaded by frontend)
	
	Returns:
	- job_id: UUID of the enqueued job for status polling
	"""
	try:
		# Get request data
		data = request.get_json()
		user_id = data.get("user_id")
		session_id = data.get("session_id")
		picture_id = data.get("picture_id")
		
		if not user_id:
			return jsonify({"error": "user_id is required"}), 400
		
		if not session_id:
			return jsonify({"error": "session_id is required"}), 400
		
		if not picture_id:
			return jsonify({"error": "picture_id is required"}), 400
		
		# Create record in user_avatar_images table
		# The picture_id from frontend is in format: {user_id}/{uuid}.png
		# Use "user_upload" as the picture_source since this is a user-initiated upload
		avatar_record = {
			"user_id": user_id,
			"picture_id": picture_id,
			"session_id": session_id,
			"picture_source": "user_upload"
		}
		
		response = supabase.table("user_avatar_images").insert(avatar_record).execute()
		
		if not response.data:
			raise Exception("Failed to create record in user_avatar_images")
		
		record_id = response.data[0].get('id')
		logger.info(f"Created user_avatar_images record (id: {record_id}) for avatar update")
		
		# Update interview_calls.picture_id to point to the new picture
		supabase.table("interview_calls")\
			.update({"picture_id": picture_id})\
			.eq("session_id", session_id)\
			.execute()
		
		logger.info(f"Updated interview_calls.picture_id for session {session_id}")
		
		# Enqueue avatar_update job in post_call_jobs
		job_record = {
			"session_id": session_id,
			"job_type": "avatar_update",
			"job_data": {
				"user_id": user_id,
				"picture_id": picture_id
			},
			"status": "pending",
			"attempts": 0,
			"max_attempts": 3
		}
		
		job_response = supabase.table("post_call_jobs").insert(job_record).execute()
		
		if not job_response.data:
			raise Exception("Failed to enqueue avatar update job")
		
		job_id = job_response.data[0].get('id')
		
		logger.info(f"Avatar update job enqueued for session {session_id} (job_id: {job_id})")
		
		return jsonify({
			"status": "queued",
			"message": "Avatar update job enqueued",
			"job_id": job_id,
			"session_id": session_id,
			"record_id": record_id
		}), 202
		
	except Exception as e:
		logger.error(f"Error enqueuing avatar update: {e}", exc_info=True)
		return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
	app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5001)), debug=True)
