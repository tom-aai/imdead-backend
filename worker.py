"""
Background worker for processing jobs from the post_call_jobs queue.

This worker continuously polls the post_call_jobs table in Supabase,
claims pending jobs, and processes them based on job_type:
- 'post_call': Full post-call orchestration (transcript, profile, avatar, voice)
- 'hot_take': Hot take video generation
- 'avatar_update': Avatar update (cartoonize uploaded image)

Run this as a separate service (e.g., Render Background Worker):
    python worker.py
"""

import gc
import os
import sys
import time
import asyncio
from datetime import datetime, UTC
from dotenv import load_dotenv
from supabase import create_client, Client

# Service imports are deferred to reduce baseline memory on constrained instances.
# Each service (and its heavy dependencies like cartesia, google.genai, PIL, anthropic)
# is imported lazily when the corresponding job type is first processed.
from services.logging_config import (
    configure_logging,
    get_logger,
    set_job_context,
    clear_context
)

# Load environment variables: prefer .env.local, fallback to default .env if present
_loaded = load_dotenv(".env.local")
if not _loaded:
    load_dotenv()

# Configure structured logging
configure_logging()
logger = get_logger(__name__)

# Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")  # Use service key for worker
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
HEDRA_API_KEY = os.getenv("HEDRA_API_KEY")
WAVESPEED_API_KEY = os.getenv("WAVESPEED_API_KEY")
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
INWORLD_API_KEY = os.getenv("INWORLD_API_KEY")
INWORLD_WORKSPACE_ID = os.getenv("INWORLD_WORKSPACE_ID")
VOICE_PROVIDER = os.getenv("VOICE_PROVIDER", "inworld")
RESEND_API_KEY = os.getenv("RESEND_API_KEY")

POLL_INTERVAL = int(os.getenv("WORKER_POLL_INTERVAL", "5"))  # seconds
STUCK_JOB_TIMEOUT = int(os.getenv("STUCK_JOB_TIMEOUT", "5"))  # minutes (short so OOM-crashed jobs recover quickly)

# Validate required environment variables
if not all([SUPABASE_URL, SUPABASE_KEY]):
    logger.error("Missing required environment variables: SUPABASE_URL, SUPABASE_SERVICE_KEY")
    sys.exit(1)


class JobWorker:
    """Worker for processing background jobs from Supabase queue."""
    
    def __init__(self):
        """Initialize the worker with Supabase client.
        
        Services are initialized lazily on first use to reduce baseline memory.
        On a 512 MB Render instance, eager initialization of all services and their
        heavy dependencies (cartesia, google.genai, PIL, anthropic) leaves too little
        headroom for ffmpeg video processing.
        """
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self._services_cache = {}
        self.worker_id = os.getenv('RENDER_INSTANCE_ID', f'worker-{os.getpid()}')
        logger.info(f"Worker initialized with ID: {self.worker_id}")
    
    def _get_service(self, name: str):
        """Lazily initialize and cache a service by name.
        
        Imports are deferred so that only the libraries needed for the current
        job type are loaded into memory.
        """
        if name in self._services_cache:
            return self._services_cache[name]
        
        if name == 'orchestrator':
            from services.post_call_orchestrator import PostCallOrchestrator
            service = PostCallOrchestrator(
                supabase_client=self.supabase,
                anthropic_api_key=ANTHROPIC_API_KEY,
                hedra_api_key=HEDRA_API_KEY,
                cartesia_api_key=CARTESIA_API_KEY,
                google_api_key=GOOGLE_API_KEY,
                inworld_api_key=INWORLD_API_KEY,
                inworld_workspace_id=INWORLD_WORKSPACE_ID
            )
        elif name == 'hot_take_service':
            from services.hot_take_video_generator import HotTakeVideoGenerationService
            service = HotTakeVideoGenerationService(
                supabase_client=self.supabase,
                tts_provider=VOICE_PROVIDER,
                cartesia_api_key=CARTESIA_API_KEY,
                inworld_api_key=INWORLD_API_KEY,
                wavespeed_api_key=WAVESPEED_API_KEY
            )
        elif name == 'voice_cloning_service':
            from services.voice_cloner import VoiceCloningService
            service = VoiceCloningService(
                supabase_client=self.supabase,
                voice_provider=VOICE_PROVIDER,
                cartesia_api_key=CARTESIA_API_KEY,
                inworld_api_key=INWORLD_API_KEY,
                inworld_workspace_id=INWORLD_WORKSPACE_ID
            )
        elif name == 'transcript_processor':
            from services.transcript_processor import TranscriptProcessor
            service = TranscriptProcessor(
                supabase_client=self.supabase,
                anthropic_api_key=ANTHROPIC_API_KEY
            )
        elif name == 'email_service':
            from services.email_service import EmailService
            service = EmailService(
                supabase_client=self.supabase,
                resend_api_key=RESEND_API_KEY
            )
        elif name == 'message_orchestrator':
            from services.message_orchestrator import MessageOrchestrator
            email_svc = self._get_service('email_service')
            service = MessageOrchestrator(
                supabase_client=self.supabase,
                email_service=email_svc,
                anthropic_api_key=ANTHROPIC_API_KEY
            )
        else:
            raise ValueError(f"Unknown service: {name}")
        
        self._services_cache[name] = service
        return service
    
    def process_job(self, job: dict) -> None:
        """
        Process a single job based on its job_type.
        
        Args:
            job: Job record from post_call_jobs table
        """
        job_id = job['id']
        session_id = job['session_id']
        job_type = job.get('job_type', 'post_call')  # Default to 'post_call' for backward compatibility
        job_data = job.get('job_data') or {}
        attempts = job['attempts']
        user_id = job_data.get('user_id')
        
        # Set job context for all subsequent log messages
        set_job_context(
            job_id=str(job_id),
            user_id=user_id,
            session_id=session_id
        )
        
        start_time = time.time()
        logger.info(
            f"Job processing started: {job_type}",
            job_type=job_type,
            attempt=attempts + 1,
            max_attempts=job['max_attempts']
        )
        
        try:
            # Route based on job_type (services are lazily initialized on first use)
            if job_type == 'hot_take':
                # Hot take video generation
                result = self._get_service('hot_take_service').generate_hot_take_video(
                    session_id=session_id
                )
            elif job_type == 'avatar_update':
                # Avatar update (cartoonize the uploaded image)
                user_id = job_data.get('user_id')
                picture_id = job_data.get('picture_id')
                
                if not user_id or not picture_id:
                    raise ValueError("avatar_update job requires user_id and picture_id in job_data")
                
                result = asyncio.run(self._get_service('orchestrator').process_avatar_update(
                    session_id=session_id,
                    user_id=user_id,
                    picture_id=picture_id
                ))
            elif job_type == 'voice_reclone':
                # Voice re-clone from uploaded audio
                user_id = job_data.get('user_id')
                audio_path = job_data.get('audio_path')
                
                if not user_id or not audio_path:
                    raise ValueError("voice_reclone job requires user_id and audio_path in job_data")
                
                # Download audio from Supabase storage
                audio_data = self.supabase.storage.from_("voice-clone-audio").download(audio_path)
                
                if not audio_data:
                    raise Exception(f"Failed to download audio from {audio_path}")
                
                # Clone voice
                result = self._get_service('voice_cloning_service').clone_voice_from_audio_data(
                    user_id=user_id,
                    audio_data=audio_data
                )
                
                # Clean up: delete audio from storage after successful cloning
                try:
                    self.supabase.storage.from_("voice-clone-audio").remove([audio_path])
                    logger.debug(f"Cleaned up voice clone audio: {audio_path}")
                except Exception as cleanup_error:
                    logger.warning(f"Failed to clean up audio file {audio_path}: {cleanup_error}")
            elif job_type == 'resume_session':
                # Resume session - append new conversation to existing session
                original_session_id = job_data.get('original_session_id')
                
                if not original_session_id:
                    raise ValueError("resume_session job requires original_session_id in job_data")
                
                result = self._get_service('transcript_processor').append_to_transcript(
                    original_session_id=original_session_id,
                    new_session_id=session_id
                )
            elif job_type == 'message_notification':
                # Send notification emails to experts about new messages
                result = self._get_service('message_orchestrator').process_message_notifications()
            elif job_type == 'mail_session':
                # Process mail agent session - handle answered questions and intros
                result = self._get_service('message_orchestrator').process_mail_session(session_id, job_data)
            else:
                # Default: post-call orchestration
                result = asyncio.run(self._get_service('orchestrator').process_call_completion(session_id))
            
            # Mark job as completed
            duration_ms = int((time.time() - start_time) * 1000)
            self.supabase.table('post_call_jobs')\
                .update({
                    'status': 'completed',
                    'completed_at': datetime.now(UTC).isoformat(),
                    'result': result
                })\
                .eq('id', job_id)\
                .execute()
            
            logger.info(
                f"Job completed successfully: {job_type}",
                job_type=job_type,
                duration_ms=duration_ms,
                status="completed"
            )
            
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(
                f"Job failed: {job_type} - {e}",
                job_type=job_type,
                duration_ms=duration_ms,
                error=str(e),
                exc_info=True
            )
            
            # Increment attempts
            new_attempts = attempts + 1
            max_attempts = job['max_attempts']
            
            if new_attempts >= max_attempts:
                # Max retries reached, mark as permanently failed
                logger.error(
                    f"Job failed permanently after {new_attempts} attempts",
                    job_type=job_type,
                    attempts=new_attempts,
                    status="failed"
                )
                self.supabase.table('post_call_jobs')\
                    .update({
                        'status': 'failed',
                        'attempts': new_attempts,
                        'error': str(e),
                        'completed_at': datetime.now(UTC).isoformat()
                    })\
                    .eq('id', job_id)\
                    .execute()
            else:
                # Retry - mark back as pending with exponential backoff
                logger.warning(
                    f"Job will be retried",
                    job_type=job_type,
                    attempt=new_attempts,
                    max_attempts=max_attempts,
                    status="retry_pending"
                )
                self.supabase.table('post_call_jobs')\
                    .update({
                        'status': 'pending',
                        'attempts': new_attempts,
                        'error': str(e),
                        'started_at': None  # Clear started_at so it can be retried
                    })\
                    .eq('id', job_id)\
                    .execute()
        
        finally:
            # Clear job context after processing
            clear_context()
            # Free memory after each job to stay within instance limits
            gc.collect()
    
    def claim_job(self) -> dict | None:
        """
        Claim the next pending job from the queue.
        
        Returns:
            Job record if one was claimed, None otherwise
        """
        try:
            # Use the claim_pending_job function to atomically claim a job
            response = self.supabase.rpc('claim_pending_job').execute()
            
            if response.data and len(response.data) > 0:
                job = response.data[0]
                job_type = job.get('job_type', 'post_call')
                job_data = job.get('job_data') or {}
                logger.info(
                    f"Job claimed: {job_type}",
                    job_id=job['id'],
                    job_type=job_type,
                    session_id=job['session_id'],
                    user_id=job_data.get('user_id')
                )
                return job
            
            return None
            
        except Exception as e:
            logger.error(f"Error claiming job: {e}", error=str(e))
            return None
    
    def reset_stuck_jobs(self) -> None:
        """Reset jobs that have been stuck in 'processing' state for too long."""
        try:
            response = self.supabase.rpc('reset_stuck_jobs', {
                'timeout_minutes': STUCK_JOB_TIMEOUT
            }).execute()
            
            if response.data and response.data > 0:
                logger.warning(
                    f"Reset {response.data} stuck job(s)",
                    stuck_jobs_reset=response.data,
                    timeout_minutes=STUCK_JOB_TIMEOUT
                )
                
        except Exception as e:
            logger.error(f"Error resetting stuck jobs: {e}", error=str(e))
    
    def run(self) -> None:
        """Main worker loop - continuously processes jobs from the queue."""
        logger.info(f"Worker {self.worker_id} started (poll interval: {POLL_INTERVAL}s)")
        
        # Reset any stuck jobs on startup
        self.reset_stuck_jobs()
        
        consecutive_errors = 0
        max_consecutive_errors = 10
        
        while True:
            try:
                # Claim and process a job
                job = self.claim_job()
                
                if job:
                    self.process_job(job)
                    consecutive_errors = 0  # Reset error counter on success
                else:
                    # No jobs available, sleep for a bit
                    logger.debug(f"No pending jobs, sleeping for {POLL_INTERVAL}s")
                    time.sleep(POLL_INTERVAL)
                
            except KeyboardInterrupt:
                logger.info("Worker shutting down gracefully...")
                break
                
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Worker loop error ({consecutive_errors}/{max_consecutive_errors}): {e}", exc_info=True)
                
                if consecutive_errors >= max_consecutive_errors:
                    logger.critical("Too many consecutive errors, shutting down worker")
                    sys.exit(1)
                
                # Sleep longer on errors to avoid hammering the database
                time.sleep(POLL_INTERVAL * 2)
        
        logger.info("Worker stopped")


def main():
    """Entry point for the worker."""
    logger.info("Starting background job worker...")
    
    worker = JobWorker()
    worker.run()


if __name__ == '__main__':
    main()
