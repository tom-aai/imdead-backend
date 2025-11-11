import os
import logging
import asyncio

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
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

supabase: Client = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
	supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
	logger.info("✅ Supabase client initialized")
else:
	logger.warning("⚠️ Supabase credentials not found")

if not ANTHROPIC_API_KEY:
	logger.warning("⚠️ Anthropic API key not found")

# Initialize services
post_call_orchestrator = PostCallOrchestrator(supabase, ANTHROPIC_API_KEY)


@app.route("/")
def index():
	"""Health check endpoint"""
	return jsonify({"status": "ok", "message": "Backend is running"})


@app.route("/health")
def health():
	"""Health check endpoint"""
	return jsonify({"status": "healthy"})


@app.route("/post-call-orchestrator", methods=["POST"])
def post_call_orchestrator_endpoint():
	"""
	Orchestrate all post-call processing after a call completes.
	Called by the LiveKit agent after saving the transcript to Supabase.
	Uses the post-call orchestrator to coordinate all post-call tasks.
	"""
	try:
		# Get session_id from request
		data = request.get_json()
		session_id = data.get("session_id")
		
		if not session_id:
			return jsonify({"error": "session_id is required"}), 400
		
		# Run the orchestrator (async -> sync conversion for Flask)
		result = asyncio.run(post_call_orchestrator.process_call_completion(session_id))
		
		# Check if orchestration succeeded
		if result["status"] == "error":
			logger.error(f"❌ Post-call orchestration failed: {result.get('error')}")
			return jsonify({
				"status": "error",
				"message": result.get("error"),
				"session_id": session_id
			}), 500
		
		# Extract transcript processing result
		transcript_task = result.get("tasks", {}).get("transcript_processing", {})
		transcript_result = transcript_task.get("result", {})
		
		return jsonify({
			"status": "success",
			"message": "Post-call processing completed successfully",
			"session_id": session_id,
			"tasks_completed": len(result.get("tasks", {})),
			"transcript_analysis_id": transcript_result.get("analysis_id")
		}), 200
		
	except ValueError as e:
		# Handle validation errors (missing session_id, call not found, etc.)
		logger.error(f"❌ Validation error: {e}")
		return jsonify({"error": str(e)}), 400 if "required" in str(e) else 404
		
	except Exception as e:
		# Handle unexpected errors
		logger.error(f"❌ Error in post-call processing: {e}", exc_info=True)
		return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
	app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5001)), debug=True)
