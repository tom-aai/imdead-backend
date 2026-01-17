"""
Message orchestrator service.
Handles message notification processing and mail session post-processing.
"""

import json
import logging
from datetime import datetime, UTC
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class MessageOrchestrator:
    """Service for orchestrating message-related post-processing tasks."""
    
    def __init__(self, supabase_client, email_service, anthropic_api_key: str = None):
        """
        Initialize the message orchestrator.
        
        Args:
            supabase_client: Initialized Supabase client
            email_service: Initialized EmailService instance
            anthropic_api_key: Anthropic API key (no longer used, kept for API compatibility)
        """
        self.supabase = supabase_client
        self.email_service = email_service
    
    def process_message_notifications(self) -> Dict[str, Any]:
        """
        Process message notifications - send emails to experts about new messages.
        
        Queries for pending messages that haven't been notified yet,
        groups them by expert, and sends notification emails.
        
        Returns:
            dict: Result with counts of notifications sent
        """
        logger.info("Processing message notifications...")
        
        # Find experts with pending messages that haven't been notified
        response = self.supabase.table("seeker_messages")\
            .select("expert_user_id, source_session_id")\
            .eq("status", "pending")\
            .is_("notification_sent_at", "null")\
            .execute()
        
        if not response.data:
            return {"status": "success", "notifications_sent": 0, "message": "No pending notifications"}
        
        # Group by expert and source session
        notifications = {}
        for row in response.data:
            key = (row["expert_user_id"], row["source_session_id"])
            notifications[key] = notifications.get(key, 0) + 1
        
        sent_count = 0
        for (expert_user_id, source_session_id), message_count in notifications.items():
            try:
                result = self.email_service.send_expert_notification(
                    expert_user_id=expert_user_id,
                    message_count=message_count,
                    session_id=source_session_id
                )
                
                if result.get("status") == "sent":
                    # Mark messages as notified
                    self.supabase.table("seeker_messages")\
                        .update({"notification_sent_at": datetime.now(UTC).isoformat()})\
                        .eq("expert_user_id", expert_user_id)\
                        .eq("source_session_id", source_session_id)\
                        .eq("status", "pending")\
                        .is_("notification_sent_at", "null")\
                        .execute()
                    sent_count += 1
                    
            except Exception as e:
                logger.error(f"Error sending notification to expert {expert_user_id}: {e}")
        
        return {"status": "success", "notifications_sent": sent_count}
    
    def process_mail_session(self, session_id: str, job_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a mail agent session after it ends.
        
        Queries seeker_messages by status to find messages that were answered
        or had introductions approved by the MailAgent, then processes them
        (appends Q&A to transcript, sends emails, updates status).
        
        Args:
            session_id: The mail session ID
            job_data: Additional job data including original_session_id
            
        Returns:
            dict: Result with processing details
        """
        original_session_id = job_data.get("original_session_id")
        
        if not original_session_id:
            raise ValueError("mail_session job requires original_session_id in job_data")
        
        logger.info(f"Processing mail session {session_id} for source {original_session_id}")
        
        # Query for messages needing backend processing
        # These were updated by the MailAgent's handle_message tool
        messages = self.supabase.table("seeker_messages")\
            .select("*")\
            .eq("source_session_id", original_session_id)\
            .in_("status", ["answered", "intro_approved"])\
            .execute()
        
        answered_count = 0
        intros_count = 0
        
        for msg in messages.data or []:
            if msg["status"] == "answered":
                self._process_answered_message(msg, original_session_id)
                answered_count += 1
            elif msg["status"] == "intro_approved":
                self._process_approved_intro(msg)
                intros_count += 1
        
        return {
            "status": "success",
            "answered_processed": answered_count,
            "intros_processed": intros_count
        }
    
    def _process_answered_message(self, msg: Dict[str, Any], original_session_id: str) -> None:
        """Process an answered message - append to transcript, notify seeker, update status."""
        try:
            self._append_qa_to_transcript(
                source_session_id=original_session_id,
                question=msg.get("message_content"),
                answer=msg.get("expert_response"),
                seeker_name=msg.get("seeker_name")
            )
            self._notify_seeker_of_answer(msg, original_session_id)
            
            # Update status to answer_delivered
            self.supabase.table("seeker_messages")\
                .update({"status": "answer_delivered"})\
                .eq("id", msg["id"])\
                .execute()
                
        except Exception as e:
            logger.error(f"Error processing answered message {msg.get('id')}: {e}")
    
    def _process_approved_intro(self, msg: Dict[str, Any]) -> None:
        """Process an approved intro - send email, update status."""
        try:
            self.email_service.send_introduction_email(
                expert_user_id=msg.get("expert_user_id"),
                seeker_user_id=msg.get("seeker_user_id"),
                seeker_name=msg.get("seeker_name"),
                seeker_company=msg.get("seeker_company"),
                seeker_role=msg.get("seeker_role"),
                seeker_message=msg.get("message_content")
            )
            
            # Update status to intro_sent
            self.supabase.table("seeker_messages")\
                .update({"status": "intro_sent"})\
                .eq("id", msg["id"])\
                .execute()
                
        except Exception as e:
            logger.error(f"Error processing approved intro {msg.get('id')}: {e}")
    
    def _append_qa_to_transcript(
        self, 
        source_session_id: str, 
        question: str, 
        answer: str,
        seeker_name: str
    ) -> None:
        """Append a Q&A pair to the session transcript."""
        try:
            # Get existing transcript
            response = self.supabase.table("interview_call_analysis")\
                .select("transcript")\
                .eq("session_id", source_session_id)\
                .execute()
            
            if not response.data:
                return
            
            transcript_str = response.data[0].get("transcript", "{}")
            transcript = json.loads(transcript_str) if isinstance(transcript_str, str) else transcript_str
            
            if not transcript:
                transcript = {"items": []}
            
            # Add Q&A as new items
            transcript["items"].append({
                "role": "user",
                "content": [{"text": f"[Question from {seeker_name}]: {question}"}]
            })
            transcript["items"].append({
                "role": "assistant", 
                "content": [{"text": answer}]
            })
            
            # Update transcript
            self.supabase.table("interview_call_analysis")\
                .update({"transcript": json.dumps(transcript)})\
                .eq("session_id", source_session_id)\
                .execute()
                
        except Exception as e:
            logger.error(f"Error appending Q&A to transcript: {e}")
    
    def _notify_seeker_of_answer(self, message: Dict[str, Any], source_session_id: str) -> None:
        """Send email to seeker that their question was answered."""
        try:
            # Get share token for the source session
            response = self.supabase.table("avatar_share_tokens")\
                .select("share_token")\
                .eq("session_id", source_session_id)\
                .eq("is_active", True)\
                .limit(1)\
                .execute()
            
            if not response.data:
                logger.warning(f"No active share token found for session {source_session_id}")
                return
            
            share_token = response.data[0].get("share_token")
            
            # Get expert name
            expert_response = self.supabase.table("interview_call_analysis")\
                .select("user_firstname, user_lastname")\
                .eq("session_id", source_session_id)\
                .execute()
            
            expert_name = "the expert"
            if expert_response.data:
                first = expert_response.data[0].get("user_firstname", "")
                last = expert_response.data[0].get("user_lastname", "")
                expert_name = f"{first} {last}".strip() or "the expert"
            
            self.email_service.send_seeker_chat_updated(
                seeker_user_id=message.get("seeker_user_id"),
                expert_name=expert_name,
                share_token=share_token
            )
            
        except Exception as e:
            logger.error(f"Error notifying seeker: {e}")

