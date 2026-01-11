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
    
    def __init__(self, supabase_client, email_service):
        """
        Initialize the message orchestrator.
        
        Args:
            supabase_client: Initialized Supabase client
            email_service: Initialized EmailService instance
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
        response = self.supabase.table("student_messages")\
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
                    self.supabase.table("student_messages")\
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
        
        Extracts answered questions and approved introductions,
        updates the student_messages table, appends Q&A to transcript,
        and sends appropriate emails.
        
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
        
        # Fetch the mail session conversation
        response = self.supabase.table("interview_calls")\
            .select("conversation")\
            .eq("session_id", session_id)\
            .execute()
        
        if not response.data:
            raise ValueError(f"Mail session not found: {session_id}")
        
        conversation = response.data[0].get("conversation", {})
        
        # Extract answered questions and approved intros from conversation
        answered_questions = []
        approved_intros = []
        
        for item in conversation.get("items", []):
            tool_calls = item.get("tool_calls", [])
            for tool_call in tool_calls:
                name = tool_call.get("name", "")
                args = tool_call.get("arguments", {})
                
                if name == "answer_question":
                    answered_questions.append({
                        "message_id": args.get("message_id"),
                        "response": args.get("response")
                    })
                elif name == "approve_introduction":
                    approved_intros.append({
                        "message_id": args.get("message_id")
                    })
        
        results = {
            "answered_questions": len(answered_questions),
            "approved_intros": len(approved_intros)
        }
        
        # Process answered questions
        for aq in answered_questions:
            self._process_answered_question(aq, original_session_id)
        
        # Process approved introductions
        for intro in approved_intros:
            self._process_approved_intro(intro)
        
        return {"status": "success", **results}
    
    def _process_answered_question(self, aq: Dict[str, Any], original_session_id: str) -> None:
        """Process a single answered question."""
        try:
            message_id = aq.get("message_id")
            response_text = aq.get("response")
            
            if not message_id:
                return
            
            # Update message status
            self.supabase.table("student_messages")\
                .update({
                    "status": "answered",
                    "expert_response": response_text
                })\
                .eq("id", message_id)\
                .execute()
            
            # Get message details
            msg_response = self.supabase.table("student_messages")\
                .select("*")\
                .eq("id", message_id)\
                .execute()
            
            if msg_response.data:
                msg = msg_response.data[0]
                
                # Append Q&A to transcript
                self._append_qa_to_transcript(
                    source_session_id=original_session_id,
                    question=msg.get("message_content"),
                    answer=response_text,
                    student_name=msg.get("student_name")
                )
                
                # Send notification to student
                self._notify_student_of_answer(msg, original_session_id)
                
        except Exception as e:
            logger.error(f"Error processing answered question: {e}")
    
    def _process_approved_intro(self, intro: Dict[str, Any]) -> None:
        """Process a single approved introduction."""
        try:
            message_id = intro.get("message_id")
            
            if not message_id:
                return
            
            # Get message details
            msg_response = self.supabase.table("student_messages")\
                .select("*")\
                .eq("id", message_id)\
                .execute()
            
            if msg_response.data:
                msg = msg_response.data[0]
                
                # Send introduction email
                self.email_service.send_introduction_email(
                    expert_user_id=msg.get("expert_user_id"),
                    student_user_id=msg.get("student_user_id"),
                    student_name=msg.get("student_name"),
                    student_company=msg.get("student_company"),
                    student_role=msg.get("student_role"),
                    student_message=msg.get("message_content")
                )
                
                # Update message status
                self.supabase.table("student_messages")\
                    .update({"status": "intro_sent"})\
                    .eq("id", message_id)\
                    .execute()
                
        except Exception as e:
            logger.error(f"Error processing intro: {e}")
    
    def _append_qa_to_transcript(
        self, 
        source_session_id: str, 
        question: str, 
        answer: str,
        student_name: str
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
                "content": [{"text": f"[Question from {student_name}]: {question}"}]
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
    
    def _notify_student_of_answer(self, message: Dict[str, Any], source_session_id: str) -> None:
        """Send email to student that their question was answered."""
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
            
            self.email_service.send_student_chat_updated(
                student_user_id=message.get("student_user_id"),
                expert_name=expert_name,
                share_token=share_token
            )
            
        except Exception as e:
            logger.error(f"Error notifying student: {e}")

