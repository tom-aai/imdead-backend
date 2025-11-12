import os
import logging
import asyncio
import threading

from flask import Flask, jsonify, request
from dotenv import load_dotenv
from supabase import create_client, Client

from services.post_call_orchestrator import PostCallOrchestrator

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


if __name__ == "__main__":
	app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5001)), debug=True)
