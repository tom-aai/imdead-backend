"""
Email service using Resend for transactional emails.
Handles expert notifications, student notifications, and introduction emails.
"""

import logging
import os
from typing import Optional, Dict, Any
from datetime import datetime, UTC

import resend

logger = logging.getLogger(__name__)


class EmailService:
    """Service for sending emails via Resend."""
    
    def __init__(self, supabase_client, resend_api_key: str = None, from_email: str = None):
        """
        Initialize the email service.
        
        Args:
            supabase_client: Initialized Supabase client
            resend_api_key: Resend API key (optional, will use env var if not provided)
            from_email: From email address (optional, will use env var if not provided)
        """
        self.supabase = supabase_client
        self.resend_api_key = resend_api_key or os.getenv("RESEND_API_KEY")
        self.from_email = from_email or os.getenv("RESEND_FROM_EMAIL", "noreply@expertavatar.com")
        self.app_url = os.getenv("APP_URL", "https://expertavatar.com")
        
        if self.resend_api_key:
            resend.api_key = self.resend_api_key
        else:
            logger.warning("RESEND_API_KEY not configured - emails will not be sent")
    
    def send_expert_notification(
        self,
        expert_user_id: str,
        message_count: int,
        session_id: str
    ) -> Dict[str, Any]:
        """
        Send notification to expert about new messages from students.
        
        Args:
            expert_user_id: The expert's user ID
            message_count: Number of new messages
            session_id: The source session ID
            
        Returns:
            dict: Result with status and details
        """
        if not self.resend_api_key:
            logger.warning("Cannot send email - Resend API key not configured")
            return {"status": "skipped", "reason": "No API key"}
        
        # Check if expert has opted out of emails
        if not self._is_email_enabled(expert_user_id):
            logger.info(f"Expert {expert_user_id} has opted out of email notifications")
            return {"status": "skipped", "reason": "User opted out"}
        
        # Get expert's email
        expert_info = self._get_user_info(expert_user_id)
        if not expert_info or not expert_info.get("email"):
            logger.warning(f"Could not find email for expert {expert_user_id}")
            return {"status": "error", "error": "Expert email not found"}
        
        expert_email = expert_info["email"]
        expert_name = expert_info.get("name", "there")
        
        # Get session info for context
        session_info = self._get_session_info(session_id)
        topic = session_info.get("main_topic", "your expertise") if session_info else "your expertise"
        
        subject = f"You have {message_count} new message{'s' if message_count > 1 else ''} for your expert avatar."
        
        html_content = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1a1a1a;">Hi {expert_name},</h2>
            
            <p style="color: #333; font-size: 16px; line-height: 1.6;">
                You have <strong>{message_count} new message{'s' if message_count > 1 else ''}</strong> from people 
                who chatted with your expert avatar about <strong>{topic}</strong>.
            </p>
            
            <p style="color: #333; font-size: 16px; line-height: 1.6;">
                Resume your session to review and respond to these messages.
            </p>
            
            <a href="{self.app_url}/dashboard" 
               style="display: inline-block; background-color: #000; color: #fff; padding: 12px 24px; 
                      text-decoration: none; border-radius: 6px; font-weight: 600; margin: 20px 0;">
                View Messages
            </a>
            
            <p style="color: #666; font-size: 14px; margin-top: 30px;">
                <a href="{self.app_url}/settings" style="color: #666;">Manage notification preferences</a>
            </p>
        </div>
        """
        
        try:
            # Use idempotency key to prevent duplicate emails
            idempotency_key = f"expert-notification-{expert_user_id}-{session_id}-{datetime.now(UTC).strftime('%Y%m%d%H')}"
            
            result = resend.Emails.send({
                "from": self.from_email,
                "to": [expert_email],
                "subject": subject,
                "html": html_content,
                "headers": {
                    "X-Idempotency-Key": idempotency_key
                }
            })
            
            logger.info(f"Sent expert notification to {expert_email}")
            return {"status": "sent", "email_id": result.get("id")}
            
        except Exception as e:
            logger.error(f"Error sending expert notification: {e}")
            return {"status": "error", "error": str(e)}
    
    def send_student_chat_updated(
        self,
        student_user_id: str,
        expert_name: str,
        share_token: str
    ) -> Dict[str, Any]:
        """
        Send notification to student that the chat agent has been updated with new answers.
        
        Args:
            student_user_id: The student's user ID
            expert_name: The expert's name
            share_token: The share token for the chat link
            
        Returns:
            dict: Result with status and details
        """
        if not self.resend_api_key:
            logger.warning("Cannot send email - Resend API key not configured")
            return {"status": "skipped", "reason": "No API key"}
        
        # Get student's email
        student_info = self._get_user_info(student_user_id)
        if not student_info or not student_info.get("email"):
            logger.warning(f"Could not find email for student {student_user_id}")
            return {"status": "error", "error": "Student email not found"}
        
        student_email = student_info["email"]
        student_name = student_info.get("name", "there")
        
        chat_url = f"{self.app_url}/chat/{share_token}"
        
        subject = f"{expert_name}'s avatar has new answers for you"
        
        html_content = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1a1a1a;">Hi {student_name},</h2>
            
            <p style="color: #333; font-size: 16px; line-height: 1.6;">
                Great news! <strong>{expert_name}</strong> has responded to your questions.
                Their expert avatar has been updated with new knowledge.
            </p>
            
            <p style="color: #333; font-size: 16px; line-height: 1.6;">
                Chat with the avatar again to get your answers.
            </p>
            
            <a href="{chat_url}" 
               style="display: inline-block; background-color: #000; color: #fff; padding: 12px 24px; 
                      text-decoration: none; border-radius: 6px; font-weight: 600; margin: 20px 0;">
                Chat with {expert_name}'s Expert Avatar
            </a>
        </div>
        """
        
        try:
            result = resend.Emails.send({
                "from": self.from_email,
                "to": [student_email],
                "subject": subject,
                "html": html_content
            })
            
            logger.info(f"Sent chat update notification to {student_email}")
            return {"status": "sent", "email_id": result.get("id")}
            
        except Exception as e:
            logger.error(f"Error sending student notification: {e}")
            return {"status": "error", "error": str(e)}
    
    def send_introduction_email(
        self,
        expert_user_id: str,
        student_user_id: str,
        student_name: str,
        student_company: str,
        student_role: str,
        student_message: str
    ) -> Dict[str, Any]:
        """
        Send introduction email connecting expert and student.
        
        Args:
            expert_user_id: The expert's user ID
            student_user_id: The student's user ID
            student_name: The student's name
            student_company: The student's company
            student_role: The student's role
            student_message: The student's introduction message
            
        Returns:
            dict: Result with status and details
        """
        if not self.resend_api_key:
            logger.warning("Cannot send email - Resend API key not configured")
            return {"status": "skipped", "reason": "No API key"}
        
        # Get both users' info
        expert_info = self._get_user_info(expert_user_id)
        student_info = self._get_user_info(student_user_id)
        
        if not expert_info or not expert_info.get("email"):
            return {"status": "error", "error": "Expert email not found"}
        if not student_info or not student_info.get("email"):
            return {"status": "error", "error": "Student email not found"}
        
        expert_email = expert_info["email"]
        expert_name = expert_info.get("name", "Expert")
        student_email = student_info["email"]
        
        # Build student context
        student_context = student_name
        if student_role and student_company:
            student_context = f"{student_name}, {student_role} at {student_company}"
        elif student_role:
            student_context = f"{student_name}, {student_role}"
        elif student_company:
            student_context = f"{student_name} from {student_company}"
        
        subject = f"Introduction: {student_name} would like to connect"
        
        html_content = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1a1a1a;">Introduction</h2>
            
            <p style="color: #333; font-size: 16px; line-height: 1.6;">
                Hi {expert_name} and {student_name},
            </p>
            
            <p style="color: #333; font-size: 16px; line-height: 1.6;">
                I'm connecting you two! {student_name} chatted with {expert_name}'s avatar and 
                requested an introduction.
            </p>
            
            <div style="background-color: #f5f5f5; padding: 20px; border-radius: 8px; margin: 20px 0;">
                <p style="color: #333; font-size: 14px; margin: 0 0 10px 0;">
                    <strong>About {student_name}:</strong>
                </p>
                <p style="color: #333; font-size: 14px; margin: 0 0 10px 0;">
                    {student_context}
                </p>
                <p style="color: #333; font-size: 14px; margin: 0;">
                    <strong>Their message:</strong> {student_message}
                </p>
            </div>
            
            <p style="color: #333; font-size: 16px; line-height: 1.6;">
                Feel free to take the conversation from here!
            </p>
            
            <p style="color: #666; font-size: 14px; margin-top: 30px;">
                This introduction was made via Expert Avatar.
            </p>
        </div>
        """
        
        try:
            result = resend.Emails.send({
                "from": self.from_email,
                "to": [expert_email, student_email],
                "subject": subject,
                "html": html_content,
                "reply_to": [expert_email, student_email]
            })
            
            logger.info(f"Sent introduction email connecting {expert_email} and {student_email}")
            return {"status": "sent", "email_id": result.get("id")}
            
        except Exception as e:
            logger.error(f"Error sending introduction email: {e}")
            return {"status": "error", "error": str(e)}
    
    def _is_email_enabled(self, user_id: str) -> bool:
        """Check if user has email notifications enabled."""
        try:
            response = self.supabase.table("user_profiles").select(
                "email_notifications_enabled"
            ).eq("user_id", user_id).limit(1).execute()
            
            if response.data and len(response.data) > 0:
                # Default to True if not set
                return response.data[0].get("email_notifications_enabled", True) is not False
            return True  # Default to enabled if no profile
            
        except Exception as e:
            logger.warning(f"Could not check email preference: {e}")
            return True  # Default to enabled on error
    
    def _get_user_info(self, user_id: str) -> Optional[Dict[str, str]]:
        """Get user's email and name from auth.users."""
        try:
            # Use Supabase admin API to get user
            response = self.supabase.auth.admin.get_user_by_id(user_id)
            
            if response and response.user:
                user = response.user
                name = (
                    user.user_metadata.get("given_name") or 
                    user.user_metadata.get("name") or 
                    user.user_metadata.get("full_name") or
                    user.email.split("@")[0] if user.email else "User"
                )
                return {
                    "email": user.email,
                    "name": name
                }
            return None
            
        except Exception as e:
            logger.warning(f"Could not get user info for {user_id}: {e}")
            return None
    
    def _get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session info for context."""
        try:
            response = self.supabase.table("interview_call_analysis").select(
                "main_topic"
            ).eq("session_id", session_id).limit(1).execute()
            
            if response.data and len(response.data) > 0:
                return response.data[0]
            return None
            
        except Exception as e:
            logger.warning(f"Could not get session info: {e}")
            return None

