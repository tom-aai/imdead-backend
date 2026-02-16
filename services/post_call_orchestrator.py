"""
Post-call orchestrator service.
Coordinates all post-call processing tasks with retry logic.
"""

import asyncio
import time
from typing import Dict, Any, Optional

from services.transcript_processor import TranscriptProcessor
from services.profile_aggregator import ProfileAggregator
from services.cartoon_generator import CartoonGenerator
from services.hedra_avatar_id_generator import HedraAvatarGenerator
from services.voice_cloner import VoiceCloningService
from services.logging_config import get_logger

logger = get_logger(__name__)


class PostCallOrchestrator:
    """
    Orchestrates all post-call processing tasks.
    Handles task execution, retries, and error management.
    """
    
    def __init__(
        self,
        supabase_client,
        anthropic_api_key: str = None,
        hedra_api_key: str = None,
        cartesia_api_key: str = None,
        google_api_key: str = None,
        inworld_api_key: str = None,
        inworld_workspace_id: str = None,
        voice_provider: str = "inworld"
    ):
        """
        Initialize the post-call orchestrator.
        
        Args:
            supabase_client: Initialized Supabase client
            anthropic_api_key: Anthropic API key (optional, will use env var if not provided)
            hedra_api_key: Hedra API key (optional, will use env var if not provided)
            cartesia_api_key: Cartesia API key (optional, will use env var if not provided)
            google_api_key: Google API key (optional, will use env var if not provided)
            inworld_api_key: Inworld API key (optional, will use env var if not provided)
            inworld_workspace_id: Inworld workspace ID (optional, will use env var if not provided)
            voice_provider: Voice cloning provider ("inworld" or "cartesia", default: "inworld")
        """
        self.supabase = supabase_client
        self.transcript_processor = TranscriptProcessor(supabase_client, anthropic_api_key)
        self.profile_aggregator = ProfileAggregator(supabase_client, anthropic_api_key)
        self.cartoon_generator = CartoonGenerator(supabase_client, google_api_key)
        self.hedra_avatar_generator = HedraAvatarGenerator(supabase_client, hedra_api_key)
        self.voice_cloning_service = VoiceCloningService(
            supabase_client,
            voice_provider=voice_provider,
            cartesia_api_key=cartesia_api_key,
            inworld_api_key=inworld_api_key,
            inworld_workspace_id=inworld_workspace_id
        )
    
    async def process_call_completion(self, session_id: str) -> Dict[str, Any]:
        """
        Orchestrate all post-call processing tasks for a completed call.
        
        Tasks:
        - Transcript processing and analysis
        - User profile aggregation
        - Image cartoonization (transform to editorial illustration)
        - Hedra avatar generation from cartoonized image
        - Voice recording processing for voice cloning
        
        Args:
            session_id: The LiveKit session ID
            
        Returns:
            dict: Processing results with status and data
        """
        logger.info(f"Starting post-call orchestration for session {session_id}")
        
        # Early check: verify the conversation has meaningful user content
        try:
            response = self.supabase.table("interview_calls").select("conversation, usage").eq("session_id", session_id).execute()
            if response.data:
                call = response.data[0]
                conversation = call.get("conversation", {})
                usage = call.get("usage", {})
                has_user_speech = usage.get("stt_audio_duration", 0) > 0
                has_user_messages = any(
                    item.get("role") == "user" for item in conversation.get("items", [])
                )
                
                # Format transcript to check length (simple inline formatting)
                transcript_lines = []
                for item in conversation.get("items", []):
                    role = item.get("role", "").lower()
                    content = item.get("content", "")
                    messages = []
                    if isinstance(content, list):
                        for msg in content:
                            if isinstance(msg, dict):
                                text = msg.get("text", msg.get("transcript", ""))
                                if text:
                                    messages.append(text)
                            elif isinstance(msg, str):
                                messages.append(msg)
                    elif isinstance(content, str) and content:
                        messages.append(content)
                    for msg in messages:
                        transcript_lines.append(f"[{role.upper()}]: {msg}")
                transcript = "\n".join(transcript_lines)
                transcript_length = len(transcript) if transcript else 0
                
                # Skip if no user speech/messages OR transcript is too short to be meaningful (< 50 chars)
                if (not has_user_speech and not has_user_messages) or transcript_length < 50:
                    reason = "no_user_speech" if not has_user_speech and not has_user_messages else "transcript_too_short"
                    logger.info(f"Skipping processing for session {session_id} - {reason} (transcript_length={transcript_length})")
                    # Delete the orphaned interview_calls record
                    self.supabase.table("interview_calls").delete().eq("session_id", session_id).execute()
                    return {"session_id": session_id, "status": "skipped", "reason": reason}
        except Exception as e:
            logger.warning(f"Error checking for user speech in session {session_id}: {e}. Continuing with processing.")
        
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
            
            # Check if transcript processing succeeded - downstream tasks depend on interview_call_analysis existing
            transcript_succeeded = transcript_result.get("status") == "success"
            
            if not transcript_succeeded:
                logger.warning(f"Transcript processing failed for session {session_id}. Skipping tasks that depend on interview_call_analysis.")
            
            # Task 2: Aggregate user profile data (depends on interview_call_analysis)
            if transcript_succeeded:
                profile_result = await self._run_with_retry(
                    task_name="profile_aggregation",
                    task_func=self._aggregate_profile,
                    session_id=session_id,
                    max_retries=3
                )
                results["tasks"]["profile_aggregation"] = profile_result
            else:
                results["tasks"]["profile_aggregation"] = {
                    "status": "skipped",
                    "reason": "transcript_processing_failed"
                }
            
            # Task 3: Cartoonize user image (transform to editorial illustration)
            # This doesn't depend on interview_call_analysis, so we can still do it
            cartoonize_result = await self._run_with_retry(
                task_name="image_cartoonization",
                task_func=self._cartoonize_image,
                session_id=session_id,
                max_retries=3
            )
            results["tasks"]["image_cartoonization"] = cartoonize_result
            
            # Task 4: Generate Hedra avatar from cartoonized image
            # This DEPENDS on interview_call_analysis existing (needs to update it)
            if transcript_succeeded:
                hedra_avatar_result = await self._run_with_retry(
                    task_name="hedra_avatar_generation",
                    task_func=self._generate_hedra_avatar,
                    session_id=session_id,
                    max_retries=3,
                    cartoonized_picture_id=cartoonize_result.get("result", {}).get("cartoonized_picture_id") if cartoonize_result.get("status") == "success" else None
                )
                results["tasks"]["hedra_avatar_generation"] = hedra_avatar_result
            else:
                logger.info(f"Skipping hedra_avatar_generation for session {session_id} - interview_call_analysis row does not exist")
                results["tasks"]["hedra_avatar_generation"] = {
                    "status": "skipped",
                    "reason": "transcript_processing_failed"
                }
            
            # Task 5: Process voice recording for cloning
            # This doesn't depend on interview_call_analysis, so we can still do it
            voice_cloning_result = await self._run_with_retry(
                task_name="voice_cloning",
                task_func=self._process_voice_recording,
                session_id=session_id,
                max_retries=3
            )
            results["tasks"]["voice_cloning"] = voice_cloning_result
            
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
        task_start = time.time()
        
        for attempt in range(max_retries):
            try:
                logger.info(
                    f"Task started: {task_name}",
                    task=task_name,
                    attempt=attempt + 1,
                    max_attempts=max_retries
                )
                
                # Run the task (handle both sync and async functions)
                if asyncio.iscoroutinefunction(task_func):
                    result = await task_func(*args, **kwargs)
                else:
                    # Run sync function in thread pool to avoid blocking
                    result = await asyncio.to_thread(task_func, *args, **kwargs)
                
                duration_ms = int((time.time() - task_start) * 1000)
                logger.info(
                    f"Task completed: {task_name}",
                    task=task_name,
                    attempt=attempt + 1,
                    duration_ms=duration_ms,
                    status="success"
                )
                return {
                    "status": "success",
                    "result": result,
                    "attempts": attempt + 1
                }
                
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Task failed: {task_name} - {e}",
                    task=task_name,
                    attempt=attempt + 1,
                    max_attempts=max_retries,
                    error=str(e)
                )
                
                if attempt < max_retries - 1:
                    # Exponential backoff: 2^attempt seconds
                    backoff_time = 2 ** attempt
                    logger.info(
                        f"Task retry scheduled: {task_name}",
                        task=task_name,
                        retry_in_seconds=backoff_time
                    )
                    await asyncio.sleep(backoff_time)
                else:
                    duration_ms = int((time.time() - task_start) * 1000)
                    logger.error(
                        f"Task failed permanently: {task_name} - {e}",
                        task=task_name,
                        attempts=max_retries,
                        duration_ms=duration_ms,
                        error=str(e)
                    )
        
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
    
    def _cartoonize_image(self, session_id: str) -> dict:
        """
        Transform user's image into editorial illustration style.
        
        Args:
            session_id: The LiveKit session ID
            
        Returns:
            dict: Image cartoonization result with new picture_id
        """
        # Look up the picture_id and user_id from the session
        picture_id, user_id = self._get_picture_and_user_from_session(session_id)
        
        if not picture_id:
            raise ValueError(f"No picture_id found for session {session_id}")
        
        if not user_id:
            raise ValueError(f"No user_id found for session {session_id}")
        
        # Cartoonize the image
        return self.cartoon_generator.cartoonize_image(
            user_id=user_id,
            picture_id=picture_id,
            session_id=session_id
        )
    
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
    
    def _get_cartoon_id_from_session(self, session_id: str) -> Optional[str]:
        """
        Look up the cartoon_id associated with a session.
        
        Args:
            session_id: The LiveKit session ID
            
        Returns:
            str: cartoon_id or None if not found
        """
        try:
            response = self.supabase.table("interview_calls")\
                .select("cartoon_id")\
                .eq("session_id", session_id)\
                .limit(1)\
                .execute()
            
            if response.data and len(response.data) > 0:
                return response.data[0].get('cartoon_id')
            
            logger.warning(f"No cartoon_id found for session {session_id}")
            return None
            
        except Exception as e:
            logger.error(f"Failed to fetch cartoon_id for session {session_id}: {e}")
            return None
    
    def _generate_hedra_avatar(self, session_id: str, cartoonized_picture_id: str = None) -> dict:
        """
        Generate Hedra avatar from cartoonized image using Hedra API.
        
        The HedraAvatarGenerator will automatically update interview_call_analysis.
        
        Args:
            session_id: The LiveKit session ID
            cartoonized_picture_id: The cartoonized picture ID (from image_cartoonization task, optional for backward compatibility)
            
        Returns:
            dict: Hedra avatar generation result
        """
        # Look up the original picture_id and user_id from the session
        picture_id, user_id = self._get_picture_and_user_from_session(session_id)
        
        if not user_id:
            raise ValueError(f"No user_id found for session {session_id}")
        
        if not picture_id:
            raise ValueError(f"No picture_id found for session {session_id}")
        
        # Get cartoon_id from interview_calls to verify it exists
        cartoon_id = self._get_cartoon_id_from_session(session_id)
        
        if not cartoon_id:
            raise ValueError(f"No cartoon_id found for session {session_id}")
        
        logger.info(f"Using cartoonized image for Hedra avatar: {cartoon_id} (from original: {picture_id})")
        
        # Generate the Hedra avatar using the ORIGINAL picture_id
        # HedraAvatarGenerator will look up the cartoon_id from user_avatar_images
        return self.hedra_avatar_generator.generate_avatar(
            user_id=user_id,
            picture_id=picture_id,  # Pass original picture_id, not cartoon_id
            session_id=session_id
        )
    
    def _process_voice_recording(self, session_id: str) -> dict:
        """
        Process voice recording for voice cloning.
        
        Downloads the recording from Supabase storage, processes it using VAD
        to remove silence, and saves the processed audio file.
        
        Args:
            session_id: The LiveKit session ID
            
        Returns:
            dict: Voice processing result
        """
        return self.voice_cloning_service.process_voice_recording(session_id)
    
    async def process_avatar_update(self, session_id: str, user_id: str, picture_id: str) -> Dict[str, Any]:
        """
        Process an avatar update for an existing session.
        
        This is called when a user wants to change their avatar image.
        It runs cartoonization and Hedra avatar generation.
        
        Args:
            session_id: The LiveKit session ID
            user_id: The user's ID
            picture_id: The new picture ID (full path in storage: {user_id}/{uuid}.png)
            
        Returns:
            dict: Processing results with status and data
        """
        logger.info(f"Starting avatar update for session {session_id}, picture {picture_id}")
        
        results = {
            "session_id": session_id,
            "status": "success",
            "tasks": {}
        }
        
        try:
            # Task 1: Cartoonize the new image
            cartoonize_result = await self._run_with_retry(
                task_name="image_cartoonization",
                task_func=self._cartoonize_avatar_update,
                user_id=user_id,
                picture_id=picture_id,
                session_id=session_id,
                max_retries=3
            )
            results["tasks"]["image_cartoonization"] = cartoonize_result
            
            if cartoonize_result.get("status") != "success":
                raise Exception(f"Cartoonization failed: {cartoonize_result.get('error')}")
            
            # Task 2: Generate Hedra avatar from cartoonized image
            hedra_result = await self._run_with_retry(
                task_name="hedra_avatar_generation",
                task_func=self._generate_hedra_avatar_update,
                user_id=user_id,
                picture_id=picture_id,
                session_id=session_id,
                max_retries=3
            )
            results["tasks"]["hedra_avatar_generation"] = hedra_result
            
            logger.info(f"Avatar update completed for session {session_id}")
            
        except Exception as e:
            logger.error(f"Avatar update failed for session {session_id}: {e}")
            results["status"] = "error"
            results["error"] = str(e)
        
        return results
    
    def _cartoonize_avatar_update(self, user_id: str, picture_id: str, session_id: str) -> dict:
        """
        Cartoonize image for an avatar update.
        
        Args:
            user_id: The user's ID
            picture_id: The picture ID (full path: {user_id}/{uuid}.png)
            session_id: The session ID
            
        Returns:
            dict: Cartoonization result
        """
        return self.cartoon_generator.cartoonize_image(
            user_id=user_id,
            picture_id=picture_id,
            session_id=session_id
        )
    
    def _generate_hedra_avatar_update(self, user_id: str, picture_id: str, session_id: str) -> dict:
        """
        Generate Hedra avatar for an avatar update.
        
        Args:
            user_id: The user's ID
            picture_id: The original picture ID
            session_id: The session ID
            
        Returns:
            dict: Hedra avatar generation result
        """
        return self.hedra_avatar_generator.generate_avatar(
            user_id=user_id,
            picture_id=picture_id,
            session_id=session_id
        )

