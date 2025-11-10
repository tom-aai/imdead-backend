"""
Sandbox script for iterating on transcript processing with Claude.
Use this to test different prompts and processing approaches.
"""

import os
import sys
import json
import logging
from dotenv import load_dotenv
from supabase import create_client
from anthropic import Anthropic

# Add parent directory to path to import utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_prompt

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(".env.local")

# ========================================
# CONFIGURATION - Change this session_id to test different calls
# ========================================
SESSION_ID = "RM_7Vn7ZtZWQxZX"  # Replace with actual session_id from your calls table

# ========================================
# Initialize clients
# ========================================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

if not all([SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY]):
    logger.error("Missing required environment variables")
    if not SUPABASE_URL:
        logger.error("SUPABASE_URL not set")
    if not SUPABASE_SERVICE_KEY:
        logger.error("SUPABASE_SERVICE_KEY not set")
    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set")
    exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
anthropic = Anthropic(api_key=ANTHROPIC_API_KEY)


def fetch_call_data(session_id: str) -> dict:
    """
    Fetch call data from Supabase.
    
    Args:
        session_id: The LiveKit session ID
        
    Returns:
        dict: Call data including conversation
    """
    logger.info(f"Fetching call data for session: {session_id}")
    response = supabase.table("interview_calls").select("*").eq("session_id", session_id).execute()
    
    if not response.data or len(response.data) == 0:
        raise ValueError(f"No call found for session_id: {session_id}")
    
    return response.data[0]


def format_transcript(conversation: dict) -> str:
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


def format_metadata(call_data: dict) -> str:
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


def process_with_claude(metadata: str, transcript: str) -> str:
    """
    Process transcript with Claude using the transcript analysis prompt.
    
    Args:
        metadata: Formatted metadata string
        transcript: Formatted transcript string
        
    Returns:
        str: Claude's response (JSON-formatted analysis)
    """
    # Load prompt template from file
    prompt_template = load_prompt('transcript_analysis_prompt.txt')
    
    # Replace placeholders with actual data
    prompt = prompt_template.replace('{metadata}', metadata).replace('{transcript}', transcript)
    
    logger.info(f"Processing transcript with Claude (transcript length: {len(transcript)} chars)")
    
    # Call Claude API
    message = anthropic.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,  # Increased for structured JSON response
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    )
    
    return message.content[0].text


def main():
    """Main execution function."""
    try:
        # Fetch call data
        call_data = fetch_call_data(SESSION_ID)
        logger.info(f"Call found for user: {call_data.get('user_firstname', '')} {call_data.get('user_lastname', '')}")
        
        # Extract conversation
        conversation = call_data.get('conversation', {})
        if not conversation or not conversation.get('items'):
            logger.error("No conversation data found")
            return
        
        # Format metadata and transcript
        metadata = format_metadata(call_data)
        transcript = format_transcript(conversation)
        
        if not transcript:
            logger.warning("No conversation content found in transcript")
            return
        
        logger.info(f"Formatted transcript with {len(transcript.splitlines())} lines")
        
        # Process with Claude
        result = process_with_claude(metadata, transcript)
        
        # Output result
        print("\n" + "="*60)
        print("TRANSCRIPT ANALYSIS RESULT")
        print("="*60)
        print(result)
        print("="*60 + "\n")
        
    except ValueError as e:
        logger.error(f"Validation error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

