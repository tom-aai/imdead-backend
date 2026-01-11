"""
Chat message extractor service.
Extracts messages intended for the expert from chat session transcripts using LLM analysis.
"""

import json
import logging
import os
from typing import List, Dict, Any
from anthropic import Anthropic

logger = logging.getLogger(__name__)


class ChatMessageExtractor:
    """Service for extracting and processing messages from chat sessions."""
    
    def __init__(self, supabase_client, anthropic_api_key: str = None):
        """
        Initialize the chat message extractor.
        
        Args:
            supabase_client: Initialized Supabase client
            anthropic_api_key: Anthropic API key (optional, will use env var if not provided)
        """
        self.supabase = supabase_client
        self.anthropic = Anthropic(api_key=anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"))
    
    def process_chat_session(self, chat_session_id: str) -> Dict[str, Any]:
        """
        Process a chat session to extract messages for the expert.
        
        This method:
        1. Fetches the chat session from the database
        2. Uses LLM to extract messages from the conversation transcript
        3. Inserts messages into student_messages table
        
        Args:
            chat_session_id: The chat session ID to process
            
        Returns:
            dict: Processing result with status and extracted messages
        """
        if not chat_session_id:
            raise ValueError("chat_session_id is required")
        
        if not self.supabase:
            raise Exception("Supabase client not initialized")
        
        logger.info(f"Processing chat session for messages: {chat_session_id}")
        
        # Fetch the chat session
        response = self.supabase.table("chat_sessions").select("*").eq("session_id", chat_session_id).execute()
        
        if not response.data or len(response.data) == 0:
            logger.error(f"No chat session found for session_id: {chat_session_id}")
            raise ValueError(f"Chat session not found: {chat_session_id}")
        
        chat_session = response.data[0]
        source_session_id = chat_session.get("source_session_id")
        expert_user_id = chat_session.get("expert_user_id")
        student_user_id = chat_session.get("student_user_id")
        
        # Extract messages from conversation via LLM
        conversation = chat_session.get("conversation", {})
        messages = self._extract_messages_with_llm(conversation)
        
        if not messages:
            logger.info(f"No messages to extract from chat session {chat_session_id}")
            return {
                "status": "success",
                "message": "No messages found in chat session",
                "chat_session_id": chat_session_id,
                "messages_extracted": 0,
                "messages_inserted": 0
            }
        
        # Insert messages into student_messages table
        inserted_count = self._insert_messages(
            messages=messages,
            source_session_id=source_session_id,
            chat_session_id=chat_session_id,
            expert_user_id=expert_user_id,
            student_user_id=student_user_id
        )
        
        logger.info(f"Extracted and inserted {inserted_count} messages")
        
        return {
            "status": "success",
            "message": f"Extracted and inserted {inserted_count} messages",
            "chat_session_id": chat_session_id,
            "messages_extracted": len(messages),
            "messages_inserted": inserted_count
        }
    
    def _extract_messages_with_llm(self, conversation: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract messages intended for the expert using LLM analysis.
        
        Args:
            conversation: The conversation data from the chat session
            
        Returns:
            List of message dictionaries
        """
        if not conversation or not conversation.get("items"):
            return []
        
        # Format conversation for LLM
        transcript_lines = []
        for item in conversation.get("items", []):
            role = item.get("role", "").upper()
            content = item.get("content", "")
            
            if isinstance(content, list):
                for msg in content:
                    if isinstance(msg, dict):
                        text = msg.get("text", msg.get("transcript", ""))
                        if text:
                            transcript_lines.append(f"[{role}]: {text}")
                    elif isinstance(msg, str):
                        transcript_lines.append(f"[{role}]: {msg}")
            elif isinstance(content, str) and content:
                transcript_lines.append(f"[{role}]: {content}")
        
        if not transcript_lines:
            return []
        
        transcript = "\n".join(transcript_lines)
        
        prompt = f"""Analyze this conversation between a student (USER) and an expert's avatar (ASSISTANT).

Extract any messages that the student explicitly asked to pass along to the real expert.

Look for:
1. Questions the student wants the real expert to answer (when the avatar didn't know)
2. Introduction requests (student wants to meet/connect with the real expert)

For each message found, extract:
- message_content: The actual message or question
- message_type: Either "question" or "introduction_request"
- student_name: The student's name (if mentioned)
- student_company: Their company (if mentioned)
- student_role: Their role/title (if mentioned)

IMPORTANT: Only include messages that the student explicitly agreed to send to the expert.
Do NOT include regular conversation or questions the avatar answered successfully.

Return a JSON array of messages. If no messages to pass along, return an empty array [].

Conversation:
{transcript}

Response (JSON array only):"""

        try:
            response = self.anthropic.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}]
            )
            
            response_text = response.content[0].text.strip()
            
            # Parse JSON from response
            if "```json" in response_text:
                json_start = response_text.find("```json") + 7
                json_end = response_text.find("```", json_start)
                json_text = response_text[json_start:json_end].strip()
            elif "```" in response_text:
                json_start = response_text.find("```") + 3
                json_end = response_text.find("```", json_start)
                json_text = response_text[json_start:json_end].strip()
            else:
                json_text = response_text
            
            messages = json.loads(json_text)
            return messages if isinstance(messages, list) else []
            
        except Exception as e:
            logger.error(f"Error extracting messages with LLM: {e}")
            return []
    
    def _insert_messages(
        self,
        messages: List[Dict[str, Any]],
        source_session_id: str,
        chat_session_id: str,
        expert_user_id: str,
        student_user_id: str
    ) -> int:
        """
        Insert messages into the student_messages table.
        
        Args:
            messages: List of messages to insert
            source_session_id: The source interview session ID
            chat_session_id: The chat session ID
            expert_user_id: The expert's user ID
            student_user_id: The student's user ID
            
        Returns:
            Number of messages inserted
        """
        inserted = 0
        
        for msg in messages:
            try:
                data = {
                    "source_session_id": source_session_id,
                    "chat_session_id": chat_session_id,
                    "expert_user_id": expert_user_id,
                    "student_user_id": student_user_id,
                    "student_name": msg.get("student_name", "Unknown"),
                    "student_company": msg.get("student_company"),
                    "student_role": msg.get("student_role"),
                    "message_content": msg.get("message_content", ""),
                    "message_type": msg.get("message_type", "question"),
                    "status": "pending"
                }
                
                self.supabase.table("student_messages").insert(data).execute()
                inserted += 1
                
            except Exception as e:
                logger.error(f"Error inserting message: {e}")
        
        return inserted
