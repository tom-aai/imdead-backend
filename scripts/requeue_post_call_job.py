"""
Script to requeue a post_call job for a given session_id.

Usage:
    uv run python scripts/requeue_post_call_job.py <session_id>

Example:
    uv run python scripts/requeue_post_call_job.py RM_f7Df5xY9Css9
"""

import sys
import os
from dotenv import load_dotenv
from supabase import create_client

# Load environment variables
_loaded = load_dotenv(".env.local")
if not _loaded:
    load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY]):
    print("ERROR: Missing required environment variables: SUPABASE_URL, SUPABASE_SERVICE_KEY")
    sys.exit(1)


def requeue_job(session_id: str) -> None:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Check for an existing completed/failed job for this session
    existing = (
        supabase.table("post_call_jobs")
        .select("id, status, result")
        .eq("session_id", session_id)
        .eq("job_type", "post_call")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )

    if existing.data:
        job = existing.data[0]
        print(f"Found existing job {job['id']} with status '{job['status']}'")

    # Insert a fresh pending job
    response = (
        supabase.table("post_call_jobs")
        .insert({
            "session_id": session_id,
            "job_type": "post_call",
            "status": "pending",
            "attempts": 0,
            "max_attempts": 3,
        })
        .execute()
    )

    if response.data:
        new_job = response.data[0]
        print(f"✅ Requeued job successfully!")
        print(f"   Job ID:     {new_job['id']}")
        print(f"   Session ID: {new_job['session_id']}")
        print(f"   Status:     {new_job['status']}")
        print(f"\nThe worker will pick this up automatically on its next poll.")
    else:
        print("ERROR: Failed to insert new job.")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    session_id = sys.argv[1]
    print(f"Requeueing post_call job for session: {session_id}")
    requeue_job(session_id)
