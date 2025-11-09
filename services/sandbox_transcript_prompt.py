"""
Sandbox script for iterating on transcript processing with Claude.
Use this to test different prompts and processing approaches.
"""

import os
import json
import logging
from dotenv import load_dotenv
from supabase import create_client
from anthropic import Anthropic

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
    response = supabase.table("calls").select("*").eq("session_id", session_id).execute()
    
    if not response.data or len(response.data) == 0:
        raise ValueError(f"No call found for session_id: {session_id}")
    
    return response.data[0]


def extract_user_statements(conversation: dict) -> list[str]:
    """
    Extract all user statements from the conversation.
    
    Args:
        conversation: The conversation JSONB data
        
    Returns:
        list: List of user statement strings
    """
    user_statements = []
    items = conversation.get('items', [])
    
    for item in items:
        role = item.get('role', '').lower()
        if role == 'user':
            content = item.get('content', '')
            
            # Handle different content formats
            if isinstance(content, list):
                for msg in content:
                    if isinstance(msg, dict):
                        text = msg.get('text', msg.get('transcript', ''))
                        if text:
                            user_statements.append(text)
                    elif isinstance(msg, str):
                        user_statements.append(msg)
            elif isinstance(content, str) and content:
                user_statements.append(content)
    
    return user_statements


def process_with_claude(user_statements: list[str]) -> str:
    """
    Process user statements with Claude.
    
    Args:
        user_statements: List of user statement strings
        
    Returns:
        str: Claude's response
    """
    # Combine all user statements
    all_statements = "\n".join([f"- {stmt}" for stmt in user_statements])
    
    # ========================================
    # PROMPT - Modify this to iterate on your processing
    # ========================================
    prompt = f"""Here are statements from a user during a conversation:

{all_statements}

Please summarize all of the user's statements into one concise sentence that captures the main points they expressed."""
    
    logger.info(f"Processing {len(user_statements)} user statements with Claude")
    
    # Call Claude API
    message = anthropic.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
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
        
        # Extract user statements
        user_statements = extract_user_statements(conversation)
        
        if not user_statements:
            logger.warning("No user statements found in conversation")
            return
        
        # Process with Claude
        result = process_with_claude(user_statements)
        
        # Output result
        print("\n" + result + "\n")
        
    except ValueError as e:
        logger.error(f"Validation error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

