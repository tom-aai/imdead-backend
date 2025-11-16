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
from services.hedra_avatar_generation import HedraAvatarGenerator

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
    HEDRA_API_KEY = os.getenv("HEDRA_API_KEY")
    
    if not all([SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY]):
        logger.error("Missing required environment variables")
        if not SUPABASE_URL:
            logger.error("  - SUPABASE_URL not set")
        if not SUPABASE_SERVICE_KEY:
            logger.error("  - SUPABASE_SERVICE_KEY not set")
        if not ANTHROPIC_API_KEY:
            logger.error("  - ANTHROPIC_API_KEY not set")
        sys.exit(1)
    
    if not HEDRA_API_KEY:
        logger.warning("  - HEDRA_API_KEY not set (avatar generation will be unavailable)")
    
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return supabase, ANTHROPIC_API_KEY, HEDRA_API_KEY


def cleanup_existing_records(supabase, session_id: str, task_name: str = None):
    """
    Delete existing records for tasks that create new records (e.g., transcript_processing).
    Does NOT delete records for tasks that update existing records (e.g., profile_aggregation).
    
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
        
        # Profile aggregation cleanup - NO deletion needed
        # Profile aggregation updates existing records, so we don't delete them during cleanup
        if not task_name or task_name == "profile_aggregation":
            # Profile aggregation service handles upsert logic internally
            # No cleanup needed here since we want to test updating existing records
            logger.debug("Skipping user_profiles cleanup - profile_aggregation updates existing records")
        
        # Hedra avatar generation cleanup
        if not task_name or task_name == "hedra_avatar_generation":
            response = supabase.table("user_avatar_images").delete().eq("session_id", session_id).execute()
            if response.data:
                logger.debug(f"Deleted {len(response.data)} record(s) from user_avatar_images")
        
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
    elif task_name == "hedra_avatar_generation":
        result = await orchestrator._run_with_retry(
            task_name="hedra_avatar_generation",
            task_func=orchestrator._generate_hedra_avatar,
            session_id=session_id,
            max_retries=3
        )
        return {
            "session_id": session_id,
            "status": "success",
            "tasks": {
                "hedra_avatar_generation": result
            }
        }
    else:
        logger.error(f"Unknown task: {task_name}")
        logger.info("Available tasks:")
        logger.info("  - transcript_processing")
        logger.info("  - profile_aggregation")
        logger.info("  - hedra_avatar_generation")
        logger.info("  (more tasks will be added as they're implemented)")
        sys.exit(1)


async def run_direct_avatar_generation(supabase, hedra_key: str, user_id: str, picture_id: str, session_id: str):
    """Run direct avatar generation (e.g., user uploads avatar from profile page)."""
    logger.info(f"Running direct avatar generation for user {user_id}, picture {picture_id}, session {session_id}")
    
    # Initialize avatar generator
    avatar_generator = HedraAvatarGenerator(supabase, hedra_key)
    
    try:
        result = avatar_generator.generate_avatar(
            user_id=user_id,
            picture_id=picture_id,
            session_id=session_id
        )
        return {
            "user_id": user_id,
            "picture_id": picture_id,
            "session_id": session_id,
            "status": "success",
            "result": result
        }
    except Exception as e:
        logger.error(f"Direct avatar generation failed: {e}")
        return {
            "user_id": user_id,
            "picture_id": picture_id,
            "session_id": session_id,
            "status": "error",
            "error": str(e)
        }


def print_results(results: dict):
    """Pretty print the orchestration results in a concise format."""
    print("\n" + "=" * 80)
    
    # Check if this is a direct avatar generation result (no tasks field)
    if "tasks" not in results and "picture_id" in results:
        print("DIRECT AVATAR GENERATION RESULTS")
        print("=" * 80)
        
        summary = {
            "user_id": results.get("user_id"),
            "picture_id": results.get("picture_id"),
            "session_id": results.get("session_id"),
            "status": results.get("status")
        }
        
        if results.get("status") == "success" and "result" in results:
            result = results["result"]
            summary["hedra_avatar_id"] = result.get("hedra_avatar_id")
            summary["record_id"] = result.get("record_id")
        elif results.get("status") == "error":
            summary["error"] = results.get("error")
        
        print(json.dumps(summary, indent=2, default=str))
        print("=" * 80 + "\n")
        
        if results.get("status") == "success":
            print("✓ Avatar has been generated and saved to Supabase.")
            print()
        return
    
    # Regular orchestration results
    print("ORCHESTRATION RESULTS")
    print("=" * 80)
    
    # Create a summary version without verbose fields
    summary = {
        "session_id": results.get("session_id"),
        "status": results.get("status"),
        "tasks": {}
    }
    
    # Summarize each task
    for task_name, task_result in results.get("tasks", {}).items():
        task_summary = {
            "status": task_result.get("status"),
            "attempts": task_result.get("attempts")
        }
        
        # Add concise task-specific info
        if task_result.get("status") == "success" and "result" in task_result:
            result = task_result["result"]
            
            if task_name == "transcript_processing":
                task_summary["analysis_id"] = result.get("analysis_id")
                task_summary["main_topic"] = result.get("analysis", {}).get("main_topic")
                task_summary["myers_briggs"] = result.get("analysis", {}).get("myers_briggs_code")
                
            elif task_name == "profile_aggregation":
                task_summary["profile_id"] = result.get("profile_id")
                task_summary["sessions_analyzed"] = result.get("record_count")
                task_summary["consolidated_myers_briggs"] = result.get("synthesis", {}).get("consolidated_myers_briggs")
                
            elif task_name == "hedra_avatar_generation":
                task_summary["record_id"] = result.get("record_id")
                task_summary["hedra_avatar_id"] = result.get("hedra_avatar_id")
                task_summary["picture_id"] = result.get("picture_id")
                
        elif task_result.get("status") == "error":
            task_summary["error"] = task_result.get("error")
        
        summary["tasks"][task_name] = task_summary
    
    print(json.dumps(summary, indent=2, default=str))
    print("=" * 80 + "\n")
    
    # Print helpful note about full data
    if results.get("status") == "success":
        print("✓ All data has been saved to Supabase. Use the analysis_id or profile_id to retrieve full details.")
        print()


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
  
  # Run only Hedra avatar generation (from call)
  python sandbox_orchestrator.py --session-id RM_7Vn7ZtZWQxZX --task hedra_avatar_generation
  
  # Run direct avatar generation (e.g., user uploads from profile)
  python sandbox_orchestrator.py --user-id abc123 --picture-id profile-pic.jpg --session-id RM_7Vn7ZtZWQxZX
  
  # Run with specific task (explicitly)
  python sandbox_orchestrator.py -s RM_7Vn7ZtZWQxZX -t profile_aggregation
        """
    )
    
    parser.add_argument(
        '--session-id', '-s',
        required=False,
        help='LiveKit session ID (required for all operations)'
    )
    
    parser.add_argument(
        '--task', '-t',
        required=False,
        help='Run a specific task only (e.g., transcript_processing). If omitted, runs all tasks.'
    )
    
    parser.add_argument(
        '--user-id', '-u',
        required=False,
        help='User ID for direct avatar generation (use with --picture-id)'
    )
    
    parser.add_argument(
        '--picture-id', '-p',
        required=False,
        help='Picture ID for direct avatar generation (use with --user-id)'
    )
    
    args = parser.parse_args()
    
    try:
        # Setup clients
        logger.info("Initializing clients...")
        supabase, anthropic_key, hedra_key = setup_clients()
        
        # Check if this is a direct avatar generation request
        if args.user_id or args.picture_id:
            # Direct avatar generation mode
            if not args.user_id or not args.picture_id or not args.session_id:
                logger.error("All of --user-id, --picture-id, and --session-id are required for direct avatar generation")
                sys.exit(1)
            
            if args.task:
                logger.error("Cannot use --task with direct avatar generation (--user-id/--picture-id/--session-id)")
                sys.exit(1)
            
            results = await run_direct_avatar_generation(supabase, hedra_key, args.user_id, args.picture_id, args.session_id)
        else:
            # Session-based orchestration mode
            if not args.session_id:
                logger.error("--session-id is required for all operations")
                sys.exit(1)
            
            # Initialize orchestrator with production services
            orchestrator = PostCallOrchestrator(supabase, anthropic_key, hedra_key)
            
            # Run orchestration based on arguments
            if args.task:
                results = await run_single_task(orchestrator, args.session_id, args.task, supabase)
            else:
                results = await run_full_orchestration(orchestrator, args.session_id, supabase)
        
        # Display results
        print_results(results)
        
        # Exit with appropriate status code
        if results.get("status") == "success":
            logger.info("Operation completed successfully")
            sys.exit(0)
        else:
            logger.error("Operation completed with errors")
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

