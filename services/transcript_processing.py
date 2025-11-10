"""
Transcript processing service.
Handles post-call transcript analysis and processing.
"""

import json
import logging
import os
from anthropic import Anthropic
import sys

# Add parent directory to path to import utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_prompt

logger = logging.getLogger(__name__)


class TranscriptProcessor:
	"""Service for processing call transcripts."""
	
	def __init__(self, supabase_client, anthropic_api_key: str = None):
		"""
		Initialize the transcript processor.
		
		Args:
			supabase_client: Initialized Supabase client
			anthropic_api_key: Anthropic API key (optional, will use env var if not provided)
		"""
		self.supabase = supabase_client
		self.anthropic = Anthropic(api_key=anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"))
	
	def process_transcript(self, session_id: str) -> dict:
		"""
		Process a transcript for a given session.
		
		Args:
			session_id: The LiveKit session ID
			
		Returns:
			dict: Processing result with status and data
			
		Raises:
			ValueError: If session_id is invalid or call not found
			Exception: If processing fails
		"""
		if not session_id:
			raise ValueError("session_id is required")
		
		if not self.supabase:
			raise Exception("Supabase client not initialized")
		
		logger.info("=" * 60)
		logger.info(f"📞 PROCESSING TRANSCRIPT FOR SESSION: {session_id}")
		logger.info("=" * 60)
		
		# Fetch the call data from Supabase
		response = self.supabase.table("interview_calls").select("*").eq("session_id", session_id).execute()
		
		if not response.data or len(response.data) == 0:
			logger.error(f"❌ No call found for session_id: {session_id}")
			raise ValueError(f"Call not found for session_id: {session_id}")
		
		call_data = response.data[0]
		
		# Log call metadata
		self._log_call_metadata(call_data)
		
		# Log conversation transcript
		conversation = call_data.get('conversation', {})
		self._log_conversation(conversation)
		
		# Log usage statistics
		self._log_usage_stats(call_data.get('usage', {}))
		
		# Format metadata and transcript for Claude processing
		metadata = self._format_metadata(call_data)
		transcript = self._format_transcript(conversation)
		
		if not transcript:
			logger.warning("No conversation content found in transcript")
			return {
				"status": "error",
				"message": "No conversation content to process",
				"session_id": session_id
			}
		
		# Process with Claude
		logger.info("Processing transcript with Claude...")
		analysis = self._process_with_llm(metadata, transcript)
		
		# Upload to interview_call_analysis table
		uploaded_record = self._upload_to_supabase(analysis)
		
		logger.info("=" * 60)
		logger.info("✅ TRANSCRIPT PROCESSING COMPLETE")
		logger.info(f"Analysis uploaded with ID: {uploaded_record.get('id')}")
		logger.info("=" * 60)
		
		return {
			"status": "success",
			"message": "Transcript processed and uploaded successfully",
			"session_id": session_id,
			"analysis_id": uploaded_record.get('id'),
			"analysis": analysis
		}
	
	def _log_call_metadata(self, call_data: dict):
		"""Log call metadata information."""
		logger.info(f"User ID: {call_data.get('user_id', 'N/A')}")
		logger.info(f"User Name: {call_data.get('user_firstname', '')} {call_data.get('user_lastname', '')}")
		logger.info(f"Picture ID: {call_data.get('picture_id', 'N/A')}")
		logger.info(f"Timestamp: {call_data.get('timestamp', 'N/A')}")
		logger.info(f"Job ID: {call_data.get('job_id', 'N/A')}")
	
	def _log_conversation(self, conversation: dict):
		"""Log the conversation transcript."""
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
	
	def _log_usage_stats(self, usage: dict):
		"""Log usage statistics."""
		if usage:
			logger.info(f"\n📊 USAGE STATISTICS:")
			logger.info(f"  LLM Prompt Tokens: {usage.get('llm_prompt_tokens', 0)}")
			logger.info(f"  LLM Completion Tokens: {usage.get('llm_completion_tokens', 0)}")
			logger.info(f"  TTS Characters: {usage.get('tts_characters_count', 0)}")
			logger.info(f"  TTS Audio Duration: {usage.get('tts_audio_duration', 0):.1f}s")
			logger.info(f"  STT Audio Duration: {usage.get('stt_audio_duration', 0):.1f}s")
	
	def _format_metadata(self, call_data: dict) -> str:
		"""
		Format call metadata for the prompt.
		
		Args:
			call_data: The call data from the database
			
		Returns:
			str: Formatted metadata string
		"""
		return f"""Session ID: {call_data.get('session_id', 'N/A')}
User ID: {call_data.get('user_id', 'N/A')}
User First Name: {call_data.get('user_firstname', 'N/A')}
User Last Name: {call_data.get('user_lastname', 'N/A')}
Created At: {call_data.get('created_at', 'N/A')}"""
	
	def _format_transcript(self, conversation: dict) -> str:
		"""
		Format the conversation into a transcript with both user and assistant messages.
		
		Args:
			conversation: The conversation JSONB data
			
		Returns:
			str: Formatted transcript string
		"""
		transcript_lines = []
		items = conversation.get('items', [])
		
		for item in items:
			role = item.get('role', '').lower()
			content = item.get('content', '')
			
			# Handle different content formats
			messages = []
			if isinstance(content, list):
				for msg in content:
					if isinstance(msg, dict):
						text = msg.get('text', msg.get('transcript', ''))
						if text:
							messages.append(text)
					elif isinstance(msg, str):
						messages.append(msg)
			elif isinstance(content, str) and content:
				messages.append(content)
			
			# Format each message with the role
			for msg in messages:
				transcript_lines.append(f"[{role.upper()}]: {msg}")
		
		return "\n".join(transcript_lines)
	
	def _process_with_llm(self, metadata: str, transcript: str) -> dict:
		"""
		Process transcript with Claude using the transcript analysis prompt.
		
		Args:
			metadata: Formatted metadata string
			transcript: Formatted transcript string
			
		Returns:
			dict: Parsed JSON analysis from Claude
		"""
		# Load prompt template from file
		prompt_template = load_prompt('transcript_analysis_prompt.txt')
		
		# Replace placeholders with actual data
		prompt = prompt_template.replace('{metadata}', metadata).replace('{transcript}', transcript)
		
		logger.info(f"Calling Claude API (transcript length: {len(transcript)} chars)")
		
		# Call Claude API
		message = self.anthropic.messages.create(
			model="claude-sonnet-4-20250514",
			max_tokens=4096,
			messages=[
				{
					"role": "user",
					"content": prompt
				}
			]
		)
		
		response_text = message.content[0].text
		
		# Parse JSON from response (handle potential markdown code blocks)
		if "```json" in response_text:
			# Extract JSON from markdown code block
			json_start = response_text.find("```json") + 7
			json_end = response_text.find("```", json_start)
			json_text = response_text[json_start:json_end].strip()
		elif "```" in response_text:
			# Extract from generic code block
			json_start = response_text.find("```") + 3
			json_end = response_text.find("```", json_start)
			json_text = response_text[json_start:json_end].strip()
		else:
			json_text = response_text.strip()
		
		return json.loads(json_text)
	
	def _upload_to_supabase(self, analysis_data: dict) -> dict:
		"""
		Upload analysis data to interview_call_analysis table.
		Excludes myers_briggs_code, personality, and expertise_description.
		
		Args:
			analysis_data: Dict containing the full analysis from Claude
			
		Returns:
			dict: The inserted record from Supabase
		"""
		# Prepare data for upload (exclude unwanted fields)
		upload_data = {
			"session_id": analysis_data.get("session_id"),
			"user_id": analysis_data.get("user_id"),
			"user_firstname": analysis_data.get("user_firstname"),
			"user_lastname": analysis_data.get("user_lastname"),
			"created_at": analysis_data.get("created_at"),
			"main_topic": analysis_data.get("main_topic"),
			"conversation_narrative": analysis_data.get("conversation_narrative"),
			"hot_take_1": analysis_data.get("hot_take_1"),
			"hot_take_2": analysis_data.get("hot_take_2"),
			"social_post_draft": analysis_data.get("social_post_draft"),
			# Nullable fields (will be filled later)
			"avatar_chat_prompt": None,
			"hot_take_video_1": None,
			"hot_take_video_2": None
		}
		
		logger.info(f"Uploading analysis to interview_call_analysis table...")
		
		# Insert into Supabase (upsert to handle re-processing)
		response = self.supabase.table("interview_call_analysis").upsert(upload_data).execute()
		
		if response.data:
			logger.info(f"✅ Successfully uploaded analysis for session: {upload_data['session_id']}")
			return response.data[0]
		else:
			raise Exception("Failed to upload analysis to Supabase")

