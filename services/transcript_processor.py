"""
Transcript processing service.
Handles post-call transcript analysis and processing.
"""

import json
import os
import time
from anthropic import Anthropic
import sys

# Add parent directory to path to import utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_prompt
from services.logging_config import get_logger

logger = get_logger(__name__)


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
		
		logger.info(f"Processing transcript for session {session_id}")
		
		# Fetch the call data from Supabase
		response = self.supabase.table("interview_calls").select("*").eq("session_id", session_id).execute()
		
		if not response.data or len(response.data) == 0:
			logger.error(f"No call found for session_id: {session_id}")
			raise ValueError(f"Call not found for session_id: {session_id}")
		
		call_data = response.data[0]
		conversation = call_data.get('conversation', {})
		
		# Format metadata and transcript for Claude processing
		metadata = self._format_metadata(call_data)
		transcript = self._format_transcript(conversation)
		
		if not transcript:
			logger.warning(f"No conversation content found for session {session_id}")
			return {
				"status": "error",
				"message": "No conversation content to process",
				"session_id": session_id
			}
		
		# Process with Claude
		logger.debug(f"Analyzing transcript with LLM (length: {len(transcript)} chars)")
		analysis = self._process_with_llm(metadata, transcript)
		
		# Upload to interview_call_analysis table 
		uploaded_record = self._upload_to_supabase(analysis, call_data)
		
		logger.info(f"Transcript processing completed for session {session_id}")
		
		return {
			"status": "success",
			"message": "Transcript processed and uploaded successfully",
			"session_id": session_id,
			"analysis_id": uploaded_record.get('id'),
			"analysis": analysis
		}
	
	
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
	
	def _filter_conversation_items(self, conversation: dict) -> dict:
		"""
		Filter conversation items to only include essential fields.
		
		Keeps: role, content, interrupted, transcript_confidence (if available)
		Removes: id, type, extra, metrics, etc.
		
		Args:
			conversation: The raw conversation JSONB data
			
		Returns:
			dict: Filtered conversation with only essential fields
		"""
		filtered_items = []
		for item in conversation.get("items", []):
			# Skip non-message items (like agent_handoff)
			if item.get("type") != "message":
				continue
			
			filtered_item = {
				"role": item.get("role"),
				"content": item.get("content"),
			}
			
			# Include optional fields if present
			if "interrupted" in item:
				filtered_item["interrupted"] = item["interrupted"]
			if "transcript_confidence" in item:
				filtered_item["transcript_confidence"] = item["transcript_confidence"]
			
			filtered_items.append(filtered_item)
		
		return {"items": filtered_items}
	
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
		
		logger.info(
			"Anthropic LLM call started: transcript_analysis",
			service="Anthropic",
			operation="transcript_analysis",
			model="claude-haiku-4-5",
			transcript_length=len(transcript)
		)
		
		llm_start = time.time()
		
		# Call Claude API
		message = self.anthropic.messages.create(
			model="claude-haiku-4-5",
			max_tokens=4096,
			messages=[
				{
					"role": "user",
					"content": prompt
				}
			]
		)
		
		duration_ms = int((time.time() - llm_start) * 1000)
		logger.info(
			"Anthropic LLM call completed: transcript_analysis",
			service="Anthropic",
			operation="transcript_analysis",
			duration_ms=duration_ms,
			input_tokens=message.usage.input_tokens if hasattr(message, 'usage') else None,
			output_tokens=message.usage.output_tokens if hasattr(message, 'usage') else None
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
		
		# Validate JSON before parsing
		if not json_text:
			logger.error(f"Empty JSON response from LLM for session. Response text: {response_text[:200]}")
			raise ValueError("LLM returned empty response")
		
		try:
			return json.loads(json_text)
		except json.JSONDecodeError as e:
			logger.error(f"Failed to parse JSON from LLM response. Error: {e}. Response text (first 500 chars): {response_text[:500]}")
			raise ValueError(f"Invalid JSON from LLM: {str(e)}")
	
	def _upload_to_supabase(self, analysis_data: dict, call_data: dict) -> dict:
		"""
		Upload analysis data to interview_call_analysis table.
		
		Stores:
		- transcript: Filtered conversation JSON (for chat agent knowledge base)
		- conversation_summary: LLM-generated summary (for resumed session context)
		
		Args:
			analysis_data: Dict containing the full analysis from Claude (includes conversation_summary)
			call_data: Dict containing the original call data including conversation
			
		Returns:
			dict: The inserted record from Supabase
		"""
		# Filter conversation to essential fields and serialize as JSON string
		conversation = call_data.get("conversation", {})
		filtered_conversation = self._filter_conversation_items(conversation)
		
		# Prepare data for upload
		upload_data = {
			"session_id": analysis_data.get("session_id"),
			"user_id": analysis_data.get("user_id"),
			"user_firstname": analysis_data.get("user_firstname"),
			"user_lastname": analysis_data.get("user_lastname"),
			"created_at": analysis_data.get("created_at"),
			"main_topic": analysis_data.get("main_topic"),
			# transcript: filtered conversation JSON for chat agent knowledge base
			"transcript": json.dumps(filtered_conversation),
			# conversation_summary: LLM-generated summary for resumed session context
			"conversation_summary": analysis_data.get("conversation_summary"),
			"myers_briggs_code": analysis_data.get("myers_briggs_code"),
			"personality": analysis_data.get("personality"),
			"expertise_description": analysis_data.get("expertise_description"),
			"hot_take_1": analysis_data.get("hot_take_1"),
			# Nullable fields (will be populated by future tasks)
			"hedra_avatar_id": None,
			"hot_take_video_1": None
		}
		
		# Upsert into Supabase (allows re-processing of the same session)
		response = self.supabase.table("interview_call_analysis").upsert(upload_data).execute()
		
		if response.data:
			logger.debug(f"Uploaded analysis to database (session: {upload_data['session_id']})")
			return response.data[0]
		else:
			raise Exception("Failed to upload analysis to Supabase")
	
	def append_to_transcript(self, original_session_id: str, new_session_id: str) -> dict:
		"""
		Append new conversation to an existing session's transcript.
		Used for resume sessions where we want to add new conversation content
		to an existing session record.
		
		Note: This updates the transcript (filtered conversation JSON) but does NOT
		regenerate the conversation_summary. The summary will need to be
		regenerated separately if needed.
		
		Args:
			original_session_id: The original session ID to append to
			new_session_id: The new session ID containing the resumed conversation
			
		Returns:
			dict: Result with status and updated transcript
			
		Raises:
			ValueError: If either session is not found
			Exception: If processing fails
		"""
		logger.info(f"Appending conversation from {new_session_id} to {original_session_id}")
		
		# 1. Fetch the original session's transcript
		original_response = self.supabase.table("interview_call_analysis")\
			.select("session_id, transcript, user_id")\
			.eq("session_id", original_session_id)\
			.execute()
		
		if not original_response.data:
			raise ValueError(f"Original session not found: {original_session_id}")
		
		original_record = original_response.data[0]
		original_user_id = original_record.get("user_id")
		
		# Parse existing transcript
		existing_transcript_str = original_record.get("transcript", "{}")
		try:
			if existing_transcript_str is None:
				existing_transcript = {"items": []}
			elif isinstance(existing_transcript_str, str):
				existing_transcript = json.loads(existing_transcript_str)
			else:
				existing_transcript = existing_transcript_str
		except json.JSONDecodeError:
			existing_transcript = {"items": []}
		
		if existing_transcript is None:
			existing_transcript = {"items": []}
		
		existing_items = existing_transcript.get("items", [])
		logger.info(f"Original session has {len(existing_items)} conversation items")
		
		# 2. Fetch the new session's conversation from interview_calls
		new_response = self.supabase.table("interview_calls")\
			.select("session_id, user_id, conversation")\
			.eq("session_id", new_session_id)\
			.execute()
		
		if not new_response.data:
			raise ValueError(f"New session not found: {new_session_id}")
		
		new_call = new_response.data[0]
		new_user_id = new_call.get("user_id")
		
		# Security check: verify same user owns both sessions
		if original_user_id != new_user_id:
			raise ValueError("Session ownership mismatch - cannot append conversations from different users")
		
		new_conversation = new_call.get("conversation", {})
		
		# 3. Filter the new conversation items using the same logic as original processing
		filtered_new = self._filter_conversation_items(new_conversation)
		new_items = filtered_new.get("items", [])
		logger.info(f"New session has {len(new_items)} filtered conversation items")
		
		# 4. Append new items to existing items
		merged_items = existing_items + new_items
		merged_transcript = {"items": merged_items}
		
		# 5. Update the interview_call_analysis record with merged transcript and updated_at
		update_response = self.supabase.table("interview_call_analysis")\
			.update({
				"transcript": json.dumps(merged_transcript),
				"updated_at": "now()"  # Use database's current timestamp
			})\
			.eq("session_id", original_session_id)\
			.execute()
		
		if not update_response.data:
			raise Exception(f"Failed to update transcript for {original_session_id}")
		
		logger.info(f"✅ Appended {len(new_items)} items to session {original_session_id} (total: {len(merged_items)})")
		
		# 6. Optionally mark the new session as a "resume child" for tracking
		# This doesn't delete it but adds a reference
		try:
			self.supabase.table("interview_calls")\
				.update({
					"resumed_from": original_session_id
				})\
				.eq("session_id", new_session_id)\
				.execute()
		except Exception as e:
			# Don't fail if this column doesn't exist
			logger.debug(f"Could not set resumed_from reference: {e}")
		
		return {
			"status": "success",
			"original_session_id": original_session_id,
			"new_session_id": new_session_id,
			"items_appended": len(new_items),
			"total_items": len(merged_items)
		}

