import os
import logging

from flask import Flask, jsonify, request
from dotenv import load_dotenv
from supabase import create_client, Client

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

supabase: Client = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
	supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
	logger.info("✅ Supabase client initialized")
else:
	logger.warning("⚠️ Supabase credentials not found")


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
		
		if not session_id:
			logger.error("❌ No session_id provided in request")
			return jsonify({"error": "session_id is required"}), 400
		
		if not supabase:
			logger.error("❌ Supabase client not initialized")
			return jsonify({"error": "Supabase not configured"}), 500
		
		logger.info("=" * 60)
		logger.info(f"📞 PROCESSING TRANSCRIPT FOR SESSION: {session_id}")
		logger.info("=" * 60)
		
		# Fetch the call data from Supabase
		response = supabase.table("calls").select("*").eq("session_id", session_id).execute()
		
		if not response.data or len(response.data) == 0:
			logger.error(f"❌ No call found for session_id: {session_id}")
			return jsonify({"error": "Call not found"}), 404
		
		call_data = response.data[0]
		
		# Print call metadata
		logger.info(f"User ID: {call_data.get('user_id', 'N/A')}")
		logger.info(f"User Name: {call_data.get('user_firstname', '')} {call_data.get('user_lastname', '')}")
		logger.info(f"Picture ID: {call_data.get('picture_id', 'N/A')}")
		logger.info(f"Timestamp: {call_data.get('timestamp', 'N/A')}")
		logger.info(f"Job ID: {call_data.get('job_id', 'N/A')}")
		
		# Print conversation history
		conversation = call_data.get('conversation', {})
		items = conversation.get('items', [])
		logger.info(f"\n📝 CONVERSATION TRANSCRIPT ({len(items)} items):")
		logger.info("-" * 60)
		
		for i, item in enumerate(items, 1):
			role = item.get('role', 'unknown')
			content = item.get('content', '')
			
			# Handle different content formats
			if isinstance(content, list):
				# Content is a list of messages
				for msg in content:
					if isinstance(msg, dict):
						text = msg.get('text', msg.get('transcript', ''))
						if text:
							logger.info(f"{i}. [{role.upper()}]: {text}")
					else:
						logger.info(f"{i}. [{role.upper()}]: {msg}")
			elif isinstance(content, str):
				logger.info(f"{i}. [{role.upper()}]: {content}")
			else:
				logger.info(f"{i}. [{role.upper()}]: {content}")
		
		logger.info("-" * 60)
		
		# Print usage statistics
		usage = call_data.get('usage', {})
		if usage:
			logger.info(f"\n📊 USAGE STATISTICS:")
			logger.info(f"  LLM Prompt Tokens: {usage.get('llm_prompt_tokens', 0)}")
			logger.info(f"  LLM Completion Tokens: {usage.get('llm_completion_tokens', 0)}")
			logger.info(f"  TTS Characters: {usage.get('tts_characters_count', 0)}")
			logger.info(f"  TTS Audio Duration: {usage.get('tts_audio_duration', 0):.1f}s")
			logger.info(f"  STT Audio Duration: {usage.get('stt_audio_duration', 0):.1f}s")
		
		logger.info("=" * 60)
		logger.info("✅ TRANSCRIPT PROCESSING COMPLETE (stub)")
		logger.info("=" * 60)
		
		# TODO: Add LLM processing here
		# analysis = process_with_llm(conversation)
		# supabase.table("call_analysis").insert({"session_id": session_id, "analysis": analysis}).execute()
		
		return jsonify({
			"status": "success",
			"message": "Transcript processed successfully",
			"session_id": session_id
		}), 200
		
	except Exception as e:
		logger.error(f"❌ Error processing transcript: {e}", exc_info=True)
		return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
	app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5001)), debug=True)
