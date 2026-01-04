"""
Background worker for processing jobs from the post_call_jobs queue.

This worker continuously polls the post_call_jobs table in Supabase,
claims pending jobs, and processes them based on job_type:
- 'post_call': Full post-call orchestration (transcript, profile, avatar, voice)
- 'hot_take': Hot take video generation
- 'avatar_update': Avatar update (cartoonize image + generate Hedra avatar)

Run this as a separate service (e.g., Render Background Worker):
    python worker.py
"""

import os
import sys
import time
import logging
import asyncio
from datetime import datetime, UTC
from dotenv import load_dotenv
from supabase import create_client, Client

# Import services
from services.post_call_orchestrator import PostCallOrchestrator
from services.hot_take_video_generator import HotTakeVideoGenerationService

# Load environment variables: prefer .env.local, fallback to default .env if present
_loaded = load_dotenv(".env.local")
if not _loaded:
    load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")  # Use service key for worker
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
HEDRA_API_KEY = os.getenv("HEDRA_API_KEY")
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
INWORLD_API_KEY = os.getenv("INWORLD_API_KEY")
INWORLD_WORKSPACE_ID = os.getenv("INWORLD_WORKSPACE_ID")

POLL_INTERVAL = int(os.getenv("WORKER_POLL_INTERVAL", "5"))  # seconds
STUCK_JOB_TIMEOUT = int(os.getenv("STUCK_JOB_TIMEOUT", "30"))  # minutes

# Validate required environment variables
if not all([SUPABASE_URL, SUPABASE_KEY]):
    logger.error("Missing required environment variables: SUPABASE_URL, SUPABASE_SERVICE_KEY")
    sys.exit(1)


class JobWorker:
    """Worker for processing background jobs from Supabase queue."""
    
    def __init__(self):
        """Initialize the worker with Supabase client and services."""
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.orchestrator = PostCallOrchestrator(
            supabase_client=self.supabase,
            anthropic_api_key=ANTHROPIC_API_KEY,
            hedra_api_key=HEDRA_API_KEY,
            cartesia_api_key=CARTESIA_API_KEY,
            google_api_key=GOOGLE_API_KEY,
            inworld_api_key=INWORLD_API_KEY,
            inworld_workspace_id=INWORLD_WORKSPACE_ID
        )
        self.hot_take_service = HotTakeVideoGenerationService(
            supabase_client=self.supabase,
            cartesia_api_key=CARTESIA_API_KEY,
            hedra_api_key=HEDRA_API_KEY
        )
        self.worker_id = os.getenv('RENDER_INSTANCE_ID', f'worker-{os.getpid()}')
        logger.info(f"Worker initialized with ID: {self.worker_id}")
    
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
        
        logger.info(f"Processing {job_type} job {job_id} for session {session_id} (attempt {attempts + 1})")
        
        try:
            # Route based on job_type
            if job_type == 'hot_take':
                # Hot take video generation
                result = self.hot_take_service.generate_hot_take_video(
                    session_id=session_id
                )
            elif job_type == 'avatar_update':
                # Avatar update (cartoonize + Hedra generation)
                user_id = job_data.get('user_id')
                picture_id = job_data.get('picture_id')
                
                if not user_id or not picture_id:
                    raise ValueError("avatar_update job requires user_id and picture_id in job_data")
                
                result = asyncio.run(self.orchestrator.process_avatar_update(
                    session_id=session_id,
                    user_id=user_id,
                    picture_id=picture_id
                ))
            else:
                # Default: post-call orchestration
                result = asyncio.run(self.orchestrator.process_call_completion(session_id))
            
            # Mark job as completed
            self.supabase.table('post_call_jobs')\
                .update({
                    'status': 'completed',
                    'completed_at': datetime.now(UTC).isoformat(),
                    'result': result
                })\
                .eq('id', job_id)\
                .execute()
            
            logger.info(f"Job {job_id} ({job_type}) completed successfully")
            
        except Exception as e:
            logger.error(f"Job {job_id} ({job_type}) failed with error: {e}", exc_info=True)
            
            # Increment attempts
            new_attempts = attempts + 1
            max_attempts = job['max_attempts']
            
            if new_attempts >= max_attempts:
                # Max retries reached, mark as permanently failed
                logger.error(f"Job {job_id} failed permanently after {new_attempts} attempts")
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
                logger.warning(f"Job {job_id} will be retried (attempt {new_attempts}/{max_attempts})")
                self.supabase.table('post_call_jobs')\
                    .update({
                        'status': 'pending',
                        'attempts': new_attempts,
                        'error': str(e),
                        'started_at': None  # Clear started_at so it can be retried
                    })\
                    .eq('id', job_id)\
                    .execute()
    
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
                logger.debug(f"Claimed {job_type} job {job['id']} for session {job['session_id']}")
                return job
            
            return None
            
        except Exception as e:
            logger.error(f"Error claiming job: {e}")
            return None
    
    def reset_stuck_jobs(self) -> None:
        """Reset jobs that have been stuck in 'processing' state for too long."""
        try:
            response = self.supabase.rpc('reset_stuck_jobs', {
                'timeout_minutes': STUCK_JOB_TIMEOUT
            }).execute()
            
            if response.data and response.data > 0:
                logger.warning(f"Reset {response.data} stuck job(s)")
                
        except Exception as e:
            logger.error(f"Error resetting stuck jobs: {e}")
    
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
