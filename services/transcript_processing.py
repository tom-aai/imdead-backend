"""
Transcript processing service.
Handles post-call transcript analysis and processing.
"""

import logging

logger = logging.getLogger(__name__)


class TranscriptProcessor:
	"""Service for processing call transcripts."""
	
	def __init__(self, supabase_client):
		"""
		Initialize the transcript processor.
		
		Args:
			supabase_client: Initialized Supabase client
		"""
		self.supabase = supabase_client
	
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
		
		logger.info("=" * 60)
		logger.info("✅ TRANSCRIPT PROCESSING COMPLETE (stub)")
		logger.info("=" * 60)
		
		# TODO: Add LLM processing here
		# analysis = self._process_with_llm(conversation)
		# self.supabase.table("call_analysis").insert({
		#     "session_id": session_id,
		#     "analysis": analysis
		# }).execute()
		
		return {
			"status": "success",
			"message": "Transcript processed successfully",
			"session_id": session_id,
			"call_data": call_data
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
	
	def _process_with_llm(self, conversation: dict) -> dict:
		"""
		Process conversation with LLM (to be implemented).
		
		Args:
			conversation: The conversation data
			
		Returns:
			dict: Structured analysis from LLM
		"""
		# TODO: Implement LLM processing
		# Example:
		# - Extract key points
		# - Sentiment analysis
		# - Action items
		# - Summary
		pass

