"""
Billing service for Expert Avatar.
Handles usage calculation, Stripe integration, and billing status management.
"""

import os
from datetime import datetime, UTC
from typing import Optional, Dict, Any
from dataclasses import dataclass

import stripe

from services.logging_config import get_logger

logger = get_logger(__name__)

# Free tier: 1 hour (3600 seconds) per month
FREE_TIER_SECONDS = 3600
# Warning threshold: 80%
WARNING_THRESHOLD = 0.8


@dataclass
class UsageInfo:
    """Usage information for a user."""
    used_seconds: float
    free_tier_seconds: float
    remaining_seconds: float
    usage_percentage: float
    is_exceeded: bool
    is_warning: bool
    billing_status: str
    is_admin: bool
    is_paid: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "used_seconds": self.used_seconds,
            "used_minutes": round(self.used_seconds / 60, 1),
            "free_tier_seconds": self.free_tier_seconds,
            "free_tier_minutes": round(self.free_tier_seconds / 60, 1),
            "remaining_seconds": self.remaining_seconds,
            "remaining_minutes": round(self.remaining_seconds / 60, 1),
            "usage_percentage": round(self.usage_percentage * 100, 1),
            "is_exceeded": self.is_exceeded,
            "is_warning": self.is_warning,
            "billing_status": self.billing_status,
            "is_admin": self.is_admin,
            "is_paid": self.is_paid,
        }


class BillingService:
    """Service for managing billing and usage."""
    
    def __init__(self, supabase_client):
        """
        Initialize the billing service.
        
        Args:
            supabase_client: Initialized Supabase client
        """
        self.supabase = supabase_client
        self.stripe_secret_key = os.getenv("STRIPE_SECRET_KEY")
        self.stripe_price_id = os.getenv("STRIPE_PRICE_ID")
        self.stripe_webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
        self.app_url = os.getenv("APP_URL", "https://expertavatar.com")
        
        if self.stripe_secret_key:
            stripe.api_key = self.stripe_secret_key
        else:
            logger.warning("Stripe features disabled", reason="STRIPE_SECRET_KEY not configured")
    
    def get_monthly_usage(self, user_id: str) -> float:
        """
        Calculate total usage for the current month.
        
        Sums stt_audio_duration from chat_sessions where expert_user_id matches
        and created_at is in the current calendar month.
        
        Args:
            user_id: The expert's user ID
            
        Returns:
            Total usage in seconds
        """
        try:
            # Get the start of current month in UTC
            now = datetime.now(UTC)
            month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
            
            # Query chat_sessions for this expert in current month
            response = self.supabase.table("chat_sessions").select(
                "usage"
            ).eq(
                "expert_user_id", user_id
            ).gte(
                "created_at", month_start.isoformat()
            ).execute()
            
            if not response.data:
                return 0.0
            
            # Sum up stt_audio_duration from usage JSONB field
            total_seconds = 0.0
            for session in response.data:
                usage = session.get("usage") or {}
                stt_duration = usage.get("stt_audio_duration", 0)
                if stt_duration:
                    total_seconds += float(stt_duration)
            
            return total_seconds
            
        except Exception as e:
            logger.error(
                "Error calculating monthly usage",
                user_id=user_id,
                error=str(e)
            )
            return 0.0
    
    def get_user_billing_info(self, user_id: str) -> Dict[str, Any]:
        """
        Get billing information for a user from user_profiles.
        
        Args:
            user_id: The user's ID
            
        Returns:
            Dict with billing_status, is_admin, stripe_customer_id, stripe_subscription_id
        """
        try:
            response = self.supabase.table("user_profiles").select(
                "billing_status, is_admin, stripe_customer_id, stripe_subscription_id"
            ).eq("user_id", user_id).limit(1).execute()
            
            if response.data and len(response.data) > 0:
                data = response.data[0]
                return {
                    "billing_status": data.get("billing_status", "free"),
                    "is_admin": data.get("is_admin", False),
                    "stripe_customer_id": data.get("stripe_customer_id"),
                    "stripe_subscription_id": data.get("stripe_subscription_id"),
                }
            
            # Default for users without a profile
            return {
                "billing_status": "free",
                "is_admin": False,
                "stripe_customer_id": None,
                "stripe_subscription_id": None,
            }
            
        except Exception as e:
            logger.error(
                "Error fetching billing info",
                user_id=user_id,
                error=str(e)
            )
            return {
                "billing_status": "free",
                "is_admin": False,
                "stripe_customer_id": None,
                "stripe_subscription_id": None,
            }
    
    def get_usage_info(self, user_id: str) -> UsageInfo:
        """
        Get comprehensive usage information for a user.
        
        Args:
            user_id: The user's ID
            
        Returns:
            UsageInfo object with all usage and billing details
        """
        billing_info = self.get_user_billing_info(user_id)
        is_admin = billing_info.get("is_admin", False)
        billing_status = billing_info.get("billing_status", "free")
        is_paid = billing_status == "active"
        
        # Admins have unlimited usage
        if is_admin:
            return UsageInfo(
                used_seconds=0,
                free_tier_seconds=float('inf'),
                remaining_seconds=float('inf'),
                usage_percentage=0,
                is_exceeded=False,
                is_warning=False,
                billing_status=billing_status,
                is_admin=True,
                is_paid=is_paid,
            )
        
        used_seconds = self.get_monthly_usage(user_id)
        
        # Paid users have unlimited usage (they pay per minute)
        if is_paid:
            return UsageInfo(
                used_seconds=used_seconds,
                free_tier_seconds=FREE_TIER_SECONDS,
                remaining_seconds=float('inf'),
                usage_percentage=0,
                is_exceeded=False,
                is_warning=False,
                billing_status=billing_status,
                is_admin=False,
                is_paid=True,
            )
        
        # Free tier users
        remaining_seconds = max(0, FREE_TIER_SECONDS - used_seconds)
        usage_percentage = used_seconds / FREE_TIER_SECONDS if FREE_TIER_SECONDS > 0 else 0
        is_exceeded = used_seconds >= FREE_TIER_SECONDS
        is_warning = usage_percentage >= WARNING_THRESHOLD and not is_exceeded
        
        return UsageInfo(
            used_seconds=used_seconds,
            free_tier_seconds=FREE_TIER_SECONDS,
            remaining_seconds=remaining_seconds,
            usage_percentage=min(usage_percentage, 1.0),
            is_exceeded=is_exceeded,
            is_warning=is_warning,
            billing_status=billing_status,
            is_admin=False,
            is_paid=False,
        )
    
    def can_start_session(self, expert_user_id: str) -> tuple[bool, str]:
        """
        Check if a chat session can be started for an expert.
        
        Args:
            expert_user_id: The expert's user ID
            
        Returns:
            Tuple of (can_start, reason)
        """
        usage_info = self.get_usage_info(expert_user_id)
        
        if usage_info.is_admin:
            return True, "Admin user - unlimited access"
        
        if usage_info.is_paid:
            return True, "Paid subscription - unlimited access"
        
        if usage_info.is_exceeded:
            return False, "Free tier exhausted - upgrade required"
        
        return True, f"{round(usage_info.remaining_seconds / 60, 1)} minutes remaining"
    
    def create_checkout_session(self, user_id: str, user_email: str) -> Optional[str]:
        """
        Create a Stripe Checkout session for upgrading to paid.
        
        Args:
            user_id: The user's ID
            user_email: The user's email
            
        Returns:
            Stripe Checkout session URL, or None on error
        """
        if not self.stripe_secret_key:
            logger.error("Stripe checkout failed", reason="STRIPE_SECRET_KEY not configured")
            return None
        
        if not self.stripe_price_id:
            logger.error("Stripe checkout failed", reason="STRIPE_PRICE_ID not configured")
            return None
        
        try:
            # Get or create Stripe customer
            billing_info = self.get_user_billing_info(user_id)
            customer_id = billing_info.get("stripe_customer_id")
            
            if not customer_id:
                # Create new Stripe customer
                customer = stripe.Customer.create(
                    email=user_email,
                    metadata={"user_id": user_id}
                )
                customer_id = customer.id
                
                # Save customer ID to user_profiles
                self._update_billing_info(user_id, {"stripe_customer_id": customer_id})
            
            # Create checkout session with metered billing
            checkout_session = stripe.checkout.Session.create(
                customer=customer_id,
                payment_method_types=["card"],
                line_items=[{
                    "price": self.stripe_price_id,
                }],
                mode="subscription",
                success_url=f"{self.app_url}/settings?billing=success",
                cancel_url=f"{self.app_url}/settings?billing=cancelled",
                metadata={"user_id": user_id},
            )
            
            logger.info(
                "Stripe checkout session created",
                service="Stripe",
                operation="create_checkout_session",
                user_id=user_id
            )
            return checkout_session.url
            
        except stripe.error.StripeError as e:
            logger.error(
                "Stripe error creating checkout session",
                service="Stripe",
                operation="create_checkout_session",
                user_id=user_id,
                error=str(e)
            )
            return None
        except Exception as e:
            logger.error(
                "Error creating checkout session",
                user_id=user_id,
                error=str(e)
            )
            return None
    
    def create_customer_portal_session(self, user_id: str) -> Optional[str]:
        """
        Create a Stripe Customer Portal session for managing subscription.
        
        Allows users to view invoices, update payment methods, and cancel subscription.
        
        Args:
            user_id: The user's ID
            
        Returns:
            Stripe Customer Portal session URL, or None on error
        """
        if not self.stripe_secret_key:
            logger.error("Stripe portal failed", reason="STRIPE_SECRET_KEY not configured")
            return None
        
        billing_info = self.get_user_billing_info(user_id)
        customer_id = billing_info.get("stripe_customer_id")
        
        if not customer_id:
            logger.warning(
                "Cannot create portal session - no Stripe customer",
                user_id=user_id
            )
            return None
        
        try:
            session = stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=f"{self.app_url}/settings",
            )
            logger.info(
                "Customer portal session created",
                service="Stripe",
                operation="create_portal_session",
                user_id=user_id
            )
            return session.url
        except stripe.error.StripeError as e:
            logger.error(
                "Stripe error creating portal session",
                service="Stripe",
                operation="create_portal_session",
                user_id=user_id,
                error=str(e)
            )
            return None
        except Exception as e:
            logger.error(
                "Error creating portal session",
                user_id=user_id,
                error=str(e)
            )
            return None
    
    def handle_webhook(self, payload: bytes, sig_header: str) -> Dict[str, Any]:
        """
        Handle Stripe webhook events.
        
        Args:
            payload: Raw webhook payload
            sig_header: Stripe signature header
            
        Returns:
            Dict with status and message
        """
        if not self.stripe_webhook_secret:
            logger.error("Stripe webhook failed", reason="STRIPE_WEBHOOK_SECRET not configured")
            return {"status": "error", "message": "Webhook not configured"}
        
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, self.stripe_webhook_secret
            )
        except ValueError as e:
            logger.error(
                "Invalid Stripe webhook payload",
                service="Stripe",
                operation="webhook",
                error=str(e)
            )
            return {"status": "error", "message": "Invalid payload"}
        except stripe.error.SignatureVerificationError as e:
            logger.error(
                "Invalid Stripe webhook signature",
                service="Stripe",
                operation="webhook",
                error=str(e)
            )
            return {"status": "error", "message": "Invalid signature"}
        
        # Handle the event
        event_type = event["type"]
        data = event["data"]["object"]
        
        logger.info(
            "Processing Stripe webhook",
            service="Stripe",
            operation="webhook",
            event_type=event_type
        )
        
        if event_type == "checkout.session.completed":
            return self._handle_checkout_completed(data)
        elif event_type == "customer.subscription.updated":
            return self._handle_subscription_updated(data)
        elif event_type == "customer.subscription.deleted":
            return self._handle_subscription_deleted(data)
        elif event_type == "invoice.paid":
            return self._handle_invoice_paid(data)
        elif event_type == "invoice.payment_failed":
            return self._handle_payment_failed(data)
        
        return {"status": "ok", "message": f"Unhandled event type: {event_type}"}
    
    def _handle_checkout_completed(self, session: Dict) -> Dict[str, Any]:
        """Handle checkout.session.completed event."""
        user_id = session.get("metadata", {}).get("user_id")
        subscription_id = session.get("subscription")
        customer_id = session.get("customer")
        
        if not user_id:
            logger.warning(
                "Checkout completed without user_id in metadata",
                service="Stripe",
                event_type="checkout.session.completed",
                customer_id=customer_id
            )
            return {"status": "error", "message": "No user_id in metadata"}
        
        self._update_billing_info(user_id, {
            "stripe_customer_id": customer_id,
            "stripe_subscription_id": subscription_id,
            "billing_status": "active",
        })
        
        logger.info(
            "User upgraded to paid subscription",
            user_id=user_id,
            subscription_id=subscription_id,
            customer_id=customer_id
        )
        return {"status": "ok", "message": "Subscription activated"}
    
    def _handle_subscription_updated(self, subscription: Dict) -> Dict[str, Any]:
        """Handle customer.subscription.updated event."""
        customer_id = subscription.get("customer")
        status = subscription.get("status")
        
        user_id = self._get_user_by_customer_id(customer_id)
        if not user_id:
            return {"status": "error", "message": "User not found"}
        
        billing_status = self._map_stripe_status(status)
        self._update_billing_info(user_id, {"billing_status": billing_status})
        
        logger.info(
            "Subscription updated",
            user_id=user_id,
            billing_status=billing_status,
            stripe_status=status
        )
        return {"status": "ok", "message": "Subscription updated"}
    
    def _handle_subscription_deleted(self, subscription: Dict) -> Dict[str, Any]:
        """Handle customer.subscription.deleted event."""
        customer_id = subscription.get("customer")
        
        user_id = self._get_user_by_customer_id(customer_id)
        if not user_id:
            return {"status": "error", "message": "User not found"}
        
        self._update_billing_info(user_id, {
            "stripe_subscription_id": None,
            "billing_status": "cancelled",
        })
        
        logger.info(
            "Subscription cancelled",
            user_id=user_id,
            customer_id=customer_id
        )
        return {"status": "ok", "message": "Subscription cancelled"}
    
    def _handle_invoice_paid(self, invoice: Dict) -> Dict[str, Any]:
        """Handle invoice.paid event - report metered usage."""
        # For metered billing, we could report usage here
        # For now, just log it
        customer_id = invoice.get("customer")
        amount = invoice.get("amount_paid", 0) / 100  # Convert cents to dollars
        
        logger.info(
            "Invoice paid",
            service="Stripe",
            event_type="invoice.paid",
            customer_id=customer_id,
            amount_usd=amount
        )
        return {"status": "ok", "message": "Invoice paid"}
    
    def _handle_payment_failed(self, invoice: Dict) -> Dict[str, Any]:
        """Handle invoice.payment_failed event."""
        customer_id = invoice.get("customer")
        
        user_id = self._get_user_by_customer_id(customer_id)
        if not user_id:
            return {"status": "error", "message": "User not found"}
        
        self._update_billing_info(user_id, {"billing_status": "past_due"})
        
        logger.warning(
            "Payment failed",
            user_id=user_id,
            customer_id=customer_id
        )
        return {"status": "ok", "message": "Payment failure recorded"}
    
    def _map_stripe_status(self, stripe_status: str) -> str:
        """Map Stripe subscription status to our billing status."""
        status_map = {
            "active": "active",
            "past_due": "past_due",
            "canceled": "cancelled",
            "cancelled": "cancelled",
            "unpaid": "past_due",
            "incomplete": "free",
            "incomplete_expired": "free",
            "trialing": "active",
        }
        return status_map.get(stripe_status, "free")
    
    def _get_user_by_customer_id(self, customer_id: str) -> Optional[str]:
        """Look up user_id by Stripe customer ID."""
        try:
            response = self.supabase.table("user_profiles").select(
                "user_id"
            ).eq("stripe_customer_id", customer_id).limit(1).execute()
            
            if response.data and len(response.data) > 0:
                return response.data[0].get("user_id")
            return None
            
        except Exception as e:
            logger.error(
                "Error looking up user by customer ID",
                customer_id=customer_id,
                error=str(e)
            )
            return None
    
    def _update_billing_info(self, user_id: str, updates: Dict[str, Any]) -> bool:
        """
        Update billing information in user_profiles.
        Creates the profile if it doesn't exist.
        """
        try:
            # Check if profile exists
            existing = self.supabase.table("user_profiles").select(
                "id"
            ).eq("user_id", user_id).execute()
            
            if existing.data and len(existing.data) > 0:
                # Update existing profile
                self.supabase.table("user_profiles").update(
                    updates
                ).eq("user_id", user_id).execute()
            else:
                # Create new profile with billing info
                self.supabase.table("user_profiles").insert({
                    "user_id": user_id,
                    **updates
                }).execute()
            
            return True
            
        except Exception as e:
            logger.error(
                "Error updating billing info",
                user_id=user_id,
                error=str(e)
            )
            return False
    
    def report_usage_to_stripe(self, user_id: str, seconds: float) -> bool:
        """
        Report metered usage to Stripe using Billing Meters.
        
        Args:
            user_id: The user's ID
            seconds: Usage in seconds to report
            
        Returns:
            True if successful
        """
        if not self.stripe_secret_key:
            return False
        
        billing_info = self.get_user_billing_info(user_id)
        customer_id = billing_info.get("stripe_customer_id")
        
        if not customer_id:
            # Not a paid user, nothing to report
            return True
        
        try:
            # Report usage in minutes (rounded up)
            minutes = int((seconds + 59) / 60)  # Round up to nearest minute
            
            if minutes <= 0:
                return True
            
            # Use the Billing Meters API
            stripe.billing.MeterEvent.create(
                event_name="chat_usage_minutes", 
                payload={
                    "stripe_customer_id": customer_id,
                    "value": str(minutes),
                },
            )
            
            logger.info(
                "Usage reported to Stripe",
                service="Stripe",
                operation="report_usage",
                user_id=user_id,
                customer_id=customer_id,
                minutes=minutes
            )
            return True
            
        except stripe.error.StripeError as e:
            logger.error(
                "Stripe error reporting usage",
                service="Stripe",
                operation="report_usage",
                user_id=user_id,
                customer_id=customer_id,
                error=str(e)
            )
            return False
        except Exception as e:
            logger.error(
                "Error reporting usage",
                user_id=user_id,
                error=str(e)
            )
            return False
    
    def delete_user_account(self, user_id: str) -> Dict[str, Any]:
        """
        Delete a user account and all associated data.
        
        Steps:
        1. Cancel Stripe subscription if active
        2. Delete chat_sessions where expert_user_id = user_id
        3. Soft-delete avatar_share_tokens (set is_active = false)
        4. Delete interview_call_analysis
        5. Delete interview_calls
        6. Delete user_profiles
        7. Delete Supabase auth user
        
        Args:
            user_id: The user's ID
            
        Returns:
            Dict with status and message
        """
        logger.info("Starting account deletion", user_id=user_id)
        
        try:
            # 1. Cancel Stripe subscription if exists
            billing_info = self.get_user_billing_info(user_id)
            if billing_info.get("stripe_subscription_id"):
                self._cancel_subscription(billing_info["stripe_subscription_id"])
                logger.info("Cancelled Stripe subscription", user_id=user_id)
            
            # 2-6. Delete database records (cascade)
            self._delete_user_data(user_id)
            
            # 7. Delete auth user
            self.supabase.auth.admin.delete_user(user_id)
            
            logger.info("Account deletion completed", user_id=user_id)
            return {"status": "ok", "message": "Account deleted"}
            
        except Exception as e:
            logger.error("Account deletion failed", user_id=user_id, error=str(e))
            return {"status": "error", "message": str(e)}
    
    def _cancel_subscription(self, subscription_id: str) -> bool:
        """
        Cancel a Stripe subscription.
        
        Args:
            subscription_id: The Stripe subscription ID
            
        Returns:
            True if successful, False on error
        """
        if not self.stripe_secret_key:
            logger.warning(
                "Cannot cancel subscription - Stripe not configured",
                subscription_id=subscription_id
            )
            return False
        
        try:
            stripe.Subscription.cancel(subscription_id)
            logger.info(
                "Subscription cancelled",
                service="Stripe",
                operation="cancel_subscription",
                subscription_id=subscription_id
            )
            return True
        except stripe.error.StripeError as e:
            logger.error(
                "Failed to cancel subscription",
                service="Stripe",
                operation="cancel_subscription",
                subscription_id=subscription_id,
                error=str(e)
            )
            return False
    
    def _delete_user_data(self, user_id: str) -> None:
        """
        Delete all user data from the database.
        
        This performs a cascading delete in the correct order to respect
        potential foreign key constraints.
        
        Args:
            user_id: The user's ID
        """
        # 1. Get all session IDs for this user
        sessions_response = self.supabase.table("interview_calls").select(
            "session_id"
        ).eq("user_id", user_id).execute()
        
        session_ids = [s["session_id"] for s in (sessions_response.data or [])]
        
        # 2. Delete chat_sessions where expert_user_id = user_id
        self.supabase.table("chat_sessions").delete().eq(
            "expert_user_id", user_id
        ).execute()
        logger.info("Deleted chat_sessions", user_id=user_id)
        
        # 3. Soft-delete avatar_share_tokens for each session
        for session_id in session_ids:
            self.supabase.table("avatar_share_tokens").update({
                "is_active": False
            }).eq("session_id", session_id).execute()
        logger.info("Soft-deleted avatar_share_tokens", user_id=user_id, session_count=len(session_ids))
        
        # 4. Delete interview_call_analysis for each session
        for session_id in session_ids:
            self.supabase.table("interview_call_analysis").delete().eq(
                "session_id", session_id
            ).execute()
        logger.info("Deleted interview_call_analysis", user_id=user_id)
        
        # 5. Delete interview_calls
        self.supabase.table("interview_calls").delete().eq(
            "user_id", user_id
        ).execute()
        logger.info("Deleted interview_calls", user_id=user_id)
        
        # 6. Delete user_profiles
        self.supabase.table("user_profiles").delete().eq(
            "user_id", user_id
        ).execute()
        logger.info("Deleted user_profiles", user_id=user_id)