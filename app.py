import os
import logging

from flask import Flask, jsonify, request
from dotenv import load_dotenv
from supabase import create_client, Client

from services.transcript_processing import TranscriptProcessor

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
transcript_processor = TranscriptProcessor(supabase, ANTHROPIC_API_KEY)


@app.route("/")
def index():
	"""Health check endpoint"""
	return jsonify({"status": "ok", "message": "Backend is running"})


@app.route("/health")
def health():
	"""Health check endpoint"""
	return jsonify({"status": "healthy"})


@app.route("/process-transcript", methods=["POST"])
def process_transcript():
	"""
	Process transcript after a call completes.
	Called by the LiveKit agent after saving the transcript to Supabase.
	"""
	try:
		# Get session_id from request
		data = request.get_json()
		session_id = data.get("session_id")
		
		# Process the transcript using the service
		result = transcript_processor.process_transcript(session_id)
		
		return jsonify({
			"status": result["status"],
			"message": result["message"],
			"session_id": result["session_id"]
		}), 200
		
	except ValueError as e:
		# Handle validation errors (missing session_id, call not found, etc.)
		logger.error(f"❌ Validation error: {e}")
		return jsonify({"error": str(e)}), 400 if "required" in str(e) else 404
		
	except Exception as e:
		# Handle unexpected errors
		logger.error(f"❌ Error processing transcript: {e}", exc_info=True)
		return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
	app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5001)), debug=True)
