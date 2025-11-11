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


def process_with_claude(metadata: str, transcript: str) -> dict:
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
    
    return json.loads(json_text)


def upload_to_supabase(analysis_data: dict) -> dict:
    """
    Upload analysis data to interview_call_analysis table.
    Includes all fields from transcript analysis.
    
    Args:
        analysis_data: Dict containing the full analysis from Claude
        
    Returns:
        dict: The inserted record from Supabase
    """
    # Prepare data for upload
    upload_data = {
        "session_id": analysis_data.get("session_id"),
        "user_id": analysis_data.get("user_id"),
        "user_firstname": analysis_data.get("user_firstname"),
        "user_lastname": analysis_data.get("user_lastname"),
        "created_at": analysis_data.get("created_at"),
        "main_topic": analysis_data.get("main_topic"),
        "conversation_narrative": analysis_data.get("conversation_narrative"),
        "myers_briggs_code": analysis_data.get("myers_briggs_code"),
        "personality": analysis_data.get("personality"),
        "expertise_description": analysis_data.get("expertise_description"),
        "hot_take_1": analysis_data.get("hot_take_1"),
        "hot_take_2": analysis_data.get("hot_take_2"),
        "social_post_draft": analysis_data.get("social_post_draft"),
        # Nullable fields (will be filled later)
        "avatar_chat_prompt": None,
        "hot_take_video_1": None,
        "hot_take_video_2": None
    }
    
    logger.info(f"Uploading analysis to interview_call_analysis table...")
    
    # Insert into Supabase
    response = supabase.table("interview_call_analysis").upsert(upload_data).execute()
    
    if response.data:
        logger.info(f"✅ Successfully uploaded analysis for session: {upload_data['session_id']}")
        return response.data[0]
    else:
        raise Exception("Failed to upload analysis to Supabase")


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
        analysis = process_with_claude(metadata, transcript)
        
        # Output result
        print("\n" + "="*60)
        print("TRANSCRIPT ANALYSIS RESULT")
        print("="*60)
        print(json.dumps(analysis, indent=2))
        print("="*60 + "\n")
        
        # Upload to Supabase
        uploaded_record = upload_to_supabase(analysis)
        
        print("\n" + "="*60)
        print("UPLOAD CONFIRMATION")
        print("="*60)
        print(f"✅ Analysis uploaded successfully!")
        print(f"Record ID: {uploaded_record.get('id')}")
        print(f"Session ID: {uploaded_record.get('session_id')}")
        print("="*60 + "\n")
        
    except ValueError as e:
        logger.error(f"Validation error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

