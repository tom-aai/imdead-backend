"""
Post-call orchestrator service.
Coordinates all post-call processing tasks with retry logic.
"""

import asyncio
import logging
from typing import Dict, Any, Optional

from services.transcript_processing import TranscriptProcessor
from services.profile_aggregation import ProfileAggregator
from services.hedra_avatar_generation import HedraAvatarGenerator

logger = logging.getLogger(__name__)


class PostCallOrchestrator:
	"""
	Orchestrates all post-call processing tasks.
	Handles task execution, retries, and error management.
	"""
	
	def __init__(self, supabase_client, anthropic_api_key: str = None, hedra_api_key: str = None):
		"""
		Initialize the post-call orchestrator.
		
		Args:
			supabase_client: Initialized Supabase client
			anthropic_api_key: Anthropic API key (optional, will use env var if not provided)
			hedra_api_key: Hedra API key (optional, will use env var if not provided)
		"""
		self.supabase = supabase_client
		self.transcript_processor = TranscriptProcessor(supabase_client, anthropic_api_key)
		self.profile_aggregator = ProfileAggregator(supabase_client, anthropic_api_key)
		self.hedra_avatar_generator = HedraAvatarGenerator(supabase_client, hedra_api_key)
	
	async def process_call_completion(self, session_id: str) -> Dict[str, Any]:
		"""
		Orchestrate all post-call processing tasks for a completed call.
		
		Currently runs:
		- Transcript processing and analysis
		- User profile aggregation
		- Hedra avatar generation from user image
		
		Future tasks can be added here:
		- Voice cloning (for first-time users)
		- Hot take video generation
		
		Args:
			session_id: The LiveKit session ID
			
		Returns:
			dict: Processing results with status and data
		"""
		logger.info(f"Starting post-call orchestration for session {session_id}")
		
		results = {
			"session_id": session_id,
			"status": "success",
			"tasks": {}
		}
		
		try:
			# Task 1: Process transcript with retry
			transcript_result = await self._run_with_retry(
				task_name="transcript_processing",
				task_func=self._process_transcript,
				session_id=session_id,
				max_retries=3
			)
			results["tasks"]["transcript_processing"] = transcript_result
			
			# Task 2: Aggregate user profile data
			profile_result = await self._run_with_retry(
				task_name="profile_aggregation",
				task_func=self._aggregate_profile,
				session_id=session_id,
				max_retries=3
			)
			results["tasks"]["profile_aggregation"] = profile_result
			
			# Task 3: Generate Hedra avatar from user image
			hedra_avatar_result = await self._run_with_retry(
				task_name="hedra_avatar_generation",
				task_func=self._generate_hedra_avatar,
				session_id=session_id,
				max_retries=3
			)
			results["tasks"]["hedra_avatar_generation"] = hedra_avatar_result
			
			# Future tasks can be added here
			# Example:
			# if await self._is_first_call(session_id):
			#     voice_result = await self._run_with_retry(
			#         task_name="voice_cloning",
			#         task_func=self._clone_voice,
			#         session_id=session_id
			#     )
			#     results["tasks"]["voice_cloning"] = voice_result
			
			logger.info(f"Post-call orchestration completed for session {session_id} ({len(results['tasks'])} tasks)")
			
		except Exception as e:
			logger.error(f"Post-call orchestration failed for session {session_id}: {e}")
			results["status"] = "error"
			results["error"] = str(e)
		
		return results
	
	async def _run_with_retry(
		self,
		task_name: str,
		task_func,
		*args,
		max_retries: int = 3,
		**kwargs
	) -> Dict[str, Any]:
		"""
		Run a task with exponential backoff retry logic.
		
		Args:
			task_name: Name of the task for logging
			task_func: The function to execute
			*args: Positional arguments for the task function
			max_retries: Maximum number of retry attempts
			**kwargs: Keyword arguments for the task function
			
		Returns:
			dict: Task result with status and data
		"""
		last_error = None
		
		for attempt in range(max_retries):
			try:
				logger.debug(f"Running task {task_name} (attempt {attempt + 1}/{max_retries})")
				
				# Run the task (handle both sync and async functions)
				if asyncio.iscoroutinefunction(task_func):
					result = await task_func(*args, **kwargs)
				else:
					# Run sync function in thread pool to avoid blocking
					result = await asyncio.to_thread(task_func, *args, **kwargs)
				
				if attempt > 0:
					logger.info(f"Task {task_name} succeeded on attempt {attempt + 1}")
				return {
					"status": "success",
					"result": result,
					"attempts": attempt + 1
				}
				
			except Exception as e:
				last_error = e
				logger.warning(f"Task {task_name} failed (attempt {attempt + 1}/{max_retries}): {e}")
				
				if attempt < max_retries - 1:
					# Exponential backoff: 2^attempt seconds
					backoff_time = 2 ** attempt
					logger.debug(f"Retrying in {backoff_time} seconds...")
					await asyncio.sleep(backoff_time)
				else:
					logger.error(f"Task {task_name} failed after {max_retries} attempts: {e}")
		
		# All retries exhausted
		return {
			"status": "error",
			"error": str(last_error),
			"attempts": max_retries
		}
	
	def _process_transcript(self, session_id: str) -> dict:
		"""
		Process and analyze the call transcript.
		
		Args:
			session_id: The LiveKit session ID
			
		Returns:
			dict: Transcript processing result
		"""
		return self.transcript_processor.process_transcript(session_id)
	
	def _aggregate_profile(self, session_id: str) -> dict:
		"""
		Aggregate user profile data from all past call analyses.
		
		Args:
			session_id: The LiveKit session ID
			
		Returns:
			dict: Profile aggregation result
		"""
		return self.profile_aggregator.aggregate_user_profile(session_id)
	
	def _get_picture_and_user_from_session(self, session_id: str) -> tuple[Optional[str], Optional[str]]:
		"""
		Look up the picture_id and user_id associated with a session.
		
		Args:
			session_id: The LiveKit session ID
			
		Returns:
			tuple: (picture_id, user_id) or (None, None) if not found
		"""
		try:
			response = self.supabase.table("interview_calls")\
				.select("picture_id, user_id")\
				.eq("session_id", session_id)\
				.limit(1)\
				.execute()
			
			if response.data and len(response.data) > 0:
				picture_id = response.data[0].get('picture_id')
				user_id = response.data[0].get('user_id')
				return (picture_id, user_id)
			
			logger.warning(f"No picture_id/user_id found for session {session_id}")
			return (None, None)
			
		except Exception as e:
			logger.error(f"Failed to fetch picture_id/user_id for session {session_id}: {e}")
			return (None, None)
	
	def _generate_hedra_avatar(self, session_id: str) -> dict:
		"""
		Generate Hedra avatar from user's uploaded image using Hedra API.
		
		Args:
			session_id: The LiveKit session ID
			
		Returns:
			dict: Hedra avatar generation result
		"""
		# Look up the picture_id and user_id from the session
		picture_id, user_id = self._get_picture_and_user_from_session(session_id)
		
		if not picture_id:
			raise ValueError(f"No picture_id found for session {session_id}")
		
		if not user_id:
			raise ValueError(f"No user_id found for session {session_id}")
		
		return self.hedra_avatar_generator.generate_avatar(
			user_id=user_id,
			picture_id=picture_id,
			session_id=session_id
		)
	
	# Future task methods can be added below:
	
	# async def _is_first_call(self, session_id: str) -> bool:
	# 	"""Check if this is the user's first call"""
	# 	# Implementation here
	# 	pass
	
	# async def _clone_voice(self, session_id: str) -> dict:
	# 	"""Clone user's voice from the call audio"""
	# 	# Implementation here
	# 	pass
	


