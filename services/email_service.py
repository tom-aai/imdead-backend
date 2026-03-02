"""
Email service using Resend for transactional emails.
Handles expert notifications, student notifications, and introduction emails.
"""

import os
import time
from typing import Optional, Dict, Any
from datetime import datetime, UTC

import resend

from services.logging_config import get_logger

logger = get_logger(__name__)


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
        default_from = "Expert Avatar <hello@notifications.melochora.com>"
        self.from_email = from_email or os.getenv("RESEND_FROM_EMAIL", default_from)
        self.reply_to_email = os.getenv("SUPPORT_EMAIL", "support@melochora.com")
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
        
        subject = f"You have {message_count} new message{'s' if message_count > 1 else ''} for your Expert Avatar."
        
        html_content = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1a1a1a;">Hi {expert_name},</h2>
            
            <p style="color: #333; font-size: 16px; line-height: 1.6;">
                You have {message_count} new message{'s' if message_count > 1 else ''} from people 
                who chatted with your Expert Avatar about {topic}.
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
            
            logger.info(
                "Resend email started: expert_notification",
                service="Resend",
                operation="send_expert_notification",
                email_type="expert_notification",
                recipient_domain=expert_email.split('@')[1] if '@' in expert_email else 'unknown'
            )
            
            send_start = time.time()
            result = resend.Emails.send({
                "from": self.from_email,
                "to": [expert_email],
                "subject": subject,
                "html": html_content,
                "reply_to": self.reply_to_email,
                "headers": {
                    "X-Idempotency-Key": idempotency_key
                }
            })
            
            duration_ms = int((time.time() - send_start) * 1000)
            logger.info(
                "Resend email completed: expert_notification",
                service="Resend",
                operation="send_expert_notification",
                email_type="expert_notification",
                duration_ms=duration_ms,
                status="sent"
            )
            return {"status": "sent", "email_id": result.get("id")}
            
        except Exception as e:
            logger.error(
                f"Resend email failed: expert_notification - {e}",
                service="Resend",
                operation="send_expert_notification",
                email_type="expert_notification",
                error=str(e)
            )
            return {"status": "error", "error": str(e)}
    
    def send_seeker_chat_updated(
        self,
        seeker_user_id: str,
        expert_name: str,
        share_token: str
    ) -> Dict[str, Any]:
        """
        Send notification to seeker that the chat agent has been updated with new answers.
        
        Args:
            seeker_user_id: The seeker's user ID
            expert_name: The expert's name
            share_token: The share token for the chat link
            
        Returns:
            dict: Result with status and details
        """
        if not self.resend_api_key:
            logger.warning("Cannot send email - Resend API key not configured")
            return {"status": "skipped", "reason": "No API key"}
        
        # Get seeker's email
        seeker_info = self._get_user_info(seeker_user_id)
        if not seeker_info or not seeker_info.get("email"):
            logger.warning(f"Could not find email for seeker {seeker_user_id}")
            return {"status": "error", "error": "Seeker email not found"}
        
        seeker_email = seeker_info["email"]
        seeker_name = seeker_info.get("name", "there")
        
        chat_url = f"{self.app_url}/chat/{share_token}"
        
        subject = f"{expert_name}'s Expert Avatar has new answers for you"
        
        html_content = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1a1a1a;">Hi {seeker_name},</h2>
            
            <p style="color: #333; font-size: 16px; line-height: 1.6;">
                {expert_name} has responded to your questions.
                Their Expert Avatar has been updated with new knowledge.
            </p>
            
            <p style="color: #333; font-size: 16px; line-height: 1.6;">
                Chat with {expert_name}'s Expert Avatar again to get your answers.
            </p>
            
            <a href="{chat_url}" 
               style="display: inline-block; background-color: #000; color: #fff; padding: 12px 24px; 
                      text-decoration: none; border-radius: 6px; font-weight: 600; margin: 20px 0;">
                Chat with {expert_name}'s Expert Avatar
            </a>
        </div>
        """
        
        try:
            logger.info(
                "Resend email started: seeker_chat_updated",
                service="Resend",
                operation="send_seeker_chat_updated",
                email_type="seeker_chat_updated"
            )
            
            send_start = time.time()
            result = resend.Emails.send({
                "from": self.from_email,
                "to": [seeker_email],
                "subject": subject,
                "html": html_content
            })
            
            duration_ms = int((time.time() - send_start) * 1000)
            logger.info(
                "Resend email completed: seeker_chat_updated",
                service="Resend",
                operation="send_seeker_chat_updated",
                email_type="seeker_chat_updated",
                duration_ms=duration_ms,
                status="sent"
            )
            return {"status": "sent", "email_id": result.get("id")}
            
        except Exception as e:
            logger.error(
                f"Resend email failed: seeker_chat_updated - {e}",
                service="Resend",
                operation="send_seeker_chat_updated",
                email_type="seeker_chat_updated",
                error=str(e)
            )
            return {"status": "error", "error": str(e)}
    
    def send_introduction_email(
        self,
        expert_user_id: str,
        seeker_user_id: str,
        seeker_name: str,
        seeker_company: str,
        seeker_role: str,
        seeker_message: str
    ) -> Dict[str, Any]:
        """
        Send introduction email connecting expert and seeker.
        
        Args:
            expert_user_id: The expert's user ID
            seeker_user_id: The seeker's user ID
            seeker_name: The seeker's name
            seeker_company: The seeker's company
            seeker_role: The seeker's role
            seeker_message: The seeker's introduction message
            
        Returns:
            dict: Result with status and details
        """
        if not self.resend_api_key:
            logger.warning("Cannot send email - Resend API key not configured")
            return {"status": "skipped", "reason": "No API key"}
        
        # Get both users' info
        expert_info = self._get_user_info(expert_user_id)
        seeker_info = self._get_user_info(seeker_user_id)
        
        if not expert_info or not expert_info.get("email"):
            return {"status": "error", "error": "Expert email not found"}
        if not seeker_info or not seeker_info.get("email"):
            return {"status": "error", "error": "Seeker email not found"}
        
        expert_email = expert_info["email"]
        expert_name = expert_info.get("name", "Expert")
        seeker_email = seeker_info["email"]
        
        # Build seeker context
        seeker_context = seeker_name
        if seeker_role and seeker_company:
            seeker_context = f"{seeker_name}, {seeker_role} at {seeker_company}"
        elif seeker_role:
            seeker_context = f"{seeker_name}, {seeker_role}"
        elif seeker_company:
            seeker_context = f"{seeker_name} from {seeker_company}"
        
        subject = f"Expert Avatar Introduction: {expert_name} meet {seeker_name}"
        
        html_content = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1a1a1a;">Introduction</h2>
            
            <p style="color: #333; font-size: 16px; line-height: 1.6;">
                Hi {expert_name},
            </p>
            
            <p style="color: #333; font-size: 16px; line-height: 1.6;">
                I told you about {seeker_name} (cc'ed). You agreed to connect with them so I'm putting you two in contact.
            </p>
            
            <div style="background-color: #f5f5f5; padding: 20px; border-radius: 8px; margin: 20px 0;">
                <p style="color: #333; font-size: 14px; margin: 0 0 10px 0;">
                    <strong>About {seeker_name}:</strong>
                </p>
                <p style="color: #333; font-size: 14px; margin: 0 0 10px 0;">
                    {seeker_context}
                </p>
                <p style="color: #333; font-size: 14px; margin: 0;">
                    <strong>Their message:</strong> {seeker_message}
                </p>
            </div>
            
            <p style="color: #333; font-size: 16px; line-height: 1.6;">
                I'll let you take it from here.
            </p>
            
        </div>
        """
        
        try:
            logger.info(
                "Resend email started: introduction",
                service="Resend",
                operation="send_introduction_email",
                email_type="introduction"
            )
            
            send_start = time.time()
            result = resend.Emails.send({
                "from": self.from_email,
                "to": [expert_email],
                "cc": [seeker_email],
                "bcc": self.reply_to_email,
                "subject": subject,
                "html": html_content,
                "reply_to": [expert_email, seeker_email]
            })
            
            duration_ms = int((time.time() - send_start) * 1000)
            logger.info(
                "Resend email completed: introduction",
                service="Resend",
                operation="send_introduction_email",
                email_type="introduction",
                duration_ms=duration_ms,
                status="sent"
            )
            return {"status": "sent", "email_id": result.get("id")}
            
        except Exception as e:
            logger.error(
                f"Resend email failed: introduction - {e}",
                service="Resend",
                operation="send_introduction_email",
                email_type="introduction",
                error=str(e)
            )
            return {"status": "error", "error": str(e)}
    
    def send_usage_warning_email(
        self,
        expert_user_id: str,
        used_minutes: float,
        total_minutes: float,
        percentage: float
    ) -> Dict[str, Any]:
        """
        Send warning email when user reaches 80% of free tier usage.
        
        Args:
            expert_user_id: The expert's user ID
            used_minutes: Minutes used so far
            total_minutes: Total free tier minutes
            percentage: Usage percentage (0-100)
            
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
        
        remaining_minutes = round(total_minutes - used_minutes, 1)
        
        subject = f"You've used {int(percentage)}% of your free Expert Avatar minutes"
        
        html_content = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1a1a1a;">Hi {expert_name},</h2>
            
            <p style="color: #333; font-size: 16px; line-height: 1.6;">
                You've used <strong>{round(used_minutes, 1)} minutes</strong> of your 
                <strong>{int(total_minutes)} free minutes</strong> this month.
            </p>
            
            <p style="color: #333; font-size: 16px; line-height: 1.6;">
                You have approximately <strong>{remaining_minutes} minutes</strong> remaining 
                before your Expert Avatar will pause conversations and not start new ones.
            </p>
            
            <p style="color: #333; font-size: 16px; line-height: 1.6;">
                Upgrade to a paid plan to ensure uninterrupted access to your Expert Avatar.
            </p>
            
            <a href="{self.app_url}/settings" 
               style="display: inline-block; background-color: #000; color: #fff; padding: 12px 24px; 
                      text-decoration: none; border-radius: 6px; font-weight: 600; margin: 20px 0;">
                Upgrade Now
            </a>
            
            <p style="color: #666; font-size: 14px; margin-top: 30px;">
                <a href="{self.app_url}/settings" style="color: #666;">Manage notification preferences</a>
            </p>
        </div>
        """
        
        try:
            logger.info(
                "Resend email started: usage_warning",
                service="Resend",
                operation="send_usage_warning_email",
                email_type="usage_warning"
            )
            
            send_start = time.time()
            result = resend.Emails.send({
                "from": self.from_email,
                "to": [expert_email],
                "subject": subject,
                "html": html_content,
                "reply_to": self.reply_to_email
            })
            
            duration_ms = int((time.time() - send_start) * 1000)
            logger.info(
                "Resend email completed: usage_warning",
                service="Resend",
                operation="send_usage_warning_email",
                email_type="usage_warning",
                duration_ms=duration_ms,
                status="sent"
            )
            return {"status": "sent", "email_id": result.get("id")}
            
        except Exception as e:
            logger.error(
                f"Resend email failed: usage_warning - {e}",
                service="Resend",
                operation="send_usage_warning_email",
                email_type="usage_warning",
                error=str(e)
            )
            return {"status": "error", "error": str(e)}
    
    def send_out_of_credits_email(
        self,
        expert_user_id: str,
        seeker_name: str = "Someone"
    ) -> Dict[str, Any]:
        """
        Send notification to expert when someone tried to chat but credits are exhausted.
        
        Args:
            expert_user_id: The expert's user ID
            seeker_name: Name of the person who tried to chat
            
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
        
        subject = f"You're out of free credits. Users can no longer chat with your Expert Avatars."
        
        html_content = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1a1a1a;">Hi {expert_name},</h2>
            
            <p style="color: #333; font-size: 16px; line-height: 1.6;">
                Someone just tried to chat with one of your Expert Avatars, 
                but your free minutes for this month have been used up.
            </p>
            
            <p style="color: #333; font-size: 16px; line-height: 1.6;">
                Upgrade to a paid plan so people can continue learning from your expertise.
            </p>
            
            <a href="{self.app_url}/settings" 
               style="display: inline-block; background-color: #000; color: #fff; padding: 12px 24px; 
                      text-decoration: none; border-radius: 6px; font-weight: 600; margin: 20px 0;">
                Upgrade Now
            </a>
            
            <p style="color: #666; font-size: 14px; margin-top: 30px;">
                <a href="{self.app_url}/settings" style="color: #666;">Manage notification preferences</a>
            </p>
        </div>
        """
        
        try:
            # Use idempotency key to prevent duplicate emails within the same hour
            idempotency_key = f"out-of-credits-{expert_user_id}-{datetime.now(UTC).strftime('%Y%m%d%H')}"
            
            logger.info(
                "Resend email started: out_of_credits",
                service="Resend",
                operation="send_out_of_credits_email",
                email_type="out_of_credits"
            )
            
            send_start = time.time()
            result = resend.Emails.send({
                "from": self.from_email,
                "to": [expert_email],
                "subject": subject,
                "html": html_content,
                "reply_to": self.reply_to_email,
                "headers": {
                    "X-Idempotency-Key": idempotency_key
                }
            })
            
            duration_ms = int((time.time() - send_start) * 1000)
            logger.info(
                "Resend email completed: out_of_credits",
                service="Resend",
                operation="send_out_of_credits_email",
                email_type="out_of_credits",
                duration_ms=duration_ms,
                status="sent"
            )
            return {"status": "sent", "email_id": result.get("id")}
            
        except Exception as e:
            logger.error(
                f"Resend email failed: out_of_credits - {e}",
                service="Resend",
                operation="send_out_of_credits_email",
                email_type="out_of_credits",
                error=str(e)
            )
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

