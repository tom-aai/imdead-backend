"""
Sandbox script for testing post-call orchestrator and individual tasks.
Use this to test the full orchestration flow or individual processing tasks from the CLI.
"""

import os
import sys
import json
import asyncio
import logging
import argparse
from dotenv import load_dotenv
from supabase import create_client

# Add parent directory to path to import services
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.post_call_orchestrator import PostCallOrchestrator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(".env.local")


def setup_clients():
    """Initialize and validate all required clients."""
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    
    if not all([SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY]):
        logger.error("Missing required environment variables")
        if not SUPABASE_URL:
            logger.error("  - SUPABASE_URL not set")
        if not SUPABASE_SERVICE_KEY:
            logger.error("  - SUPABASE_SERVICE_KEY not set")
        if not ANTHROPIC_API_KEY:
            logger.error("  - ANTHROPIC_API_KEY not set")
        sys.exit(1)
    
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return supabase, ANTHROPIC_API_KEY


def _get_user_id_from_session(supabase, session_id: str) -> str:
    """
    Get the user_id associated with a session.
    
    Args:
        supabase: Supabase client
        session_id: The LiveKit session ID
        
    Returns:
        str: The user_id, or None if not found
    """
    # Try interview_call_analysis first (most recent table)
    response = supabase.table("interview_call_analysis")\
        .select("user_id")\
        .eq("session_id", session_id)\
        .limit(1)\
        .execute()
    
    if response.data and len(response.data) > 0:
        return response.data[0].get('user_id')
    
    # Fallback to interview_calls table
    response = supabase.table("interview_calls")\
        .select("user_id")\
        .eq("session_id", session_id)\
        .limit(1)\
        .execute()
    
    if response.data and len(response.data) > 0:
        return response.data[0].get('user_id')
    
    return None


def cleanup_existing_records(supabase, session_id: str, task_name: str = None):
    """
    Delete existing records for the session to allow clean testing.
    
    Args:
        supabase: Supabase client
        session_id: The session ID to clean up
        task_name: Specific task to clean (None = clean all tasks)
    """
    logger.info(f"Cleaning up records for session {session_id}" + (f" (task: {task_name})" if task_name else " (all tasks)"))
    
    try:
        # Clean up transcript processing results (always in interview_call_analysis)
        if not task_name or task_name == "transcript_processing":
            response = supabase.table("interview_call_analysis").delete().eq("session_id", session_id).execute()
            if response.data:
                logger.debug(f"Deleted {len(response.data)} record(s) from interview_call_analysis")
        
		# Profile aggregation cleanup - check if profile exists and delete from user_profiles by user_id
		if not task_name or task_name == "profile_aggregation":
			# First, get the user_id from the session
			user_id = _get_user_id_from_session(supabase, session_id)
			if user_id:
				# Check if profile exists before attempting deletion
				existing = supabase.table("user_profiles")\
					.select("id")\
					.eq("user_id", user_id)\
					.execute()
				
				if existing.data and len(existing.data) > 0:
					# Profile exists, delete it
					response = supabase.table("user_profiles").delete().eq("user_id", user_id).execute()
					if response.data:
						logger.debug(f"Deleted {len(response.data)} user profile(s)")
        
        # Add cleanup for future tasks here as they're implemented
        # Example:
        # if not task_name or task_name == "voice_cloning":
        #     response = supabase.table("voice_clones").delete().eq("session_id", session_id).execute()
        
        logger.debug("Cleanup complete")
        
    except Exception as e:
        logger.warning(f"Cleanup failed (this may be okay if no records exist): {e}")


async def run_full_orchestration(orchestrator: PostCallOrchestrator, session_id: str, supabase):
    """Run the complete post-call orchestration flow."""
    logger.info(f"Running full orchestration for session {session_id}")
    
    # Clean up all existing records for this session
    cleanup_existing_records(supabase, session_id)
    
    result = await orchestrator.process_call_completion(session_id)
    return result


async def run_single_task(orchestrator: PostCallOrchestrator, session_id: str, task_name: str, supabase):
    """Run a single task from the orchestrator."""
    logger.info(f"Running single task: {task_name} for session {session_id}")
    
    # Clean up only the records for this specific task
    cleanup_existing_records(supabase, session_id, task_name)
    
    if task_name == "transcript_processing":
        result = await orchestrator._run_with_retry(
            task_name="transcript_processing",
            task_func=orchestrator._process_transcript,
            session_id=session_id,
            max_retries=3
        )
        return {
            "session_id": session_id,
            "status": "success",
            "tasks": {
                "transcript_processing": result
            }
        }
    elif task_name == "profile_aggregation":
        result = await orchestrator._run_with_retry(
            task_name="profile_aggregation",
            task_func=orchestrator._aggregate_profile,
            session_id=session_id,
            max_retries=3
        )
        return {
            "session_id": session_id,
            "status": "success",
            "tasks": {
                "profile_aggregation": result
            }
        }
    else:
        logger.error(f"Unknown task: {task_name}")
        logger.info("Available tasks:")
        logger.info("  - transcript_processing")
        logger.info("  - profile_aggregation")
        logger.info("  (more tasks will be added as they're implemented)")
        sys.exit(1)


def print_results(results: dict):
    """Pretty print the orchestration results."""
    print("\n" + "=" * 80)
    print("ORCHESTRATION RESULTS")
    print("=" * 80)
    print(json.dumps(results, indent=2, default=str))
    print("=" * 80 + "\n")


async def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description="Sandbox orchestrator for testing post-call processing tasks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all tasks for a session
  python sandbox_orchestrator.py --session-id RM_7Vn7ZtZWQxZX
  
  # Run only transcript processing
  python sandbox_orchestrator.py --session-id RM_7Vn7ZtZWQxZX --task transcript_processing
  
  # Run only profile aggregation
  python sandbox_orchestrator.py --session-id RM_7Vn7ZtZWQxZX --task profile_aggregation
  
  # Run with specific task (explicitly)
  python sandbox_orchestrator.py -s RM_7Vn7ZtZWQxZX -t profile_aggregation
        """
    )
    
    parser.add_argument(
        '--session-id', '-s',
        required=True,
        help='LiveKit session ID to process'
    )
    
    parser.add_argument(
        '--task', '-t',
        required=False,
        help='Run a specific task only (e.g., transcript_processing). If omitted, runs all tasks.'
    )
    
    args = parser.parse_args()
    
    try:
        # Setup clients
        logger.info("Initializing clients...")
        supabase, anthropic_key = setup_clients()
        
        # Initialize orchestrator with production services
        orchestrator = PostCallOrchestrator(supabase, anthropic_key)
        
        # Run orchestration based on arguments
        if args.task:
            results = await run_single_task(orchestrator, args.session_id, args.task, supabase)
        else:
            results = await run_full_orchestration(orchestrator, args.session_id, supabase)
        
        # Display results
        print_results(results)
        
        # Exit with appropriate status code
        if results.get("status") == "success":
            logger.info("Orchestration completed successfully")
            sys.exit(0)
        else:
            logger.error("Orchestration completed with errors")
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

