"""
Unit tests for BillingService.

Tests cover:
- Usage calculation (get_monthly_usage)
- User billing info retrieval
- Usage info and limits (free tier, paid, admin)
- Session start permissions (can_start_session)
- Stripe checkout session creation
- Webhook handling (subscription lifecycle events)
- Usage reporting to Stripe
- Status mapping
- Error handling and edge cases
"""

import pytest
from datetime import datetime, UTC
from unittest.mock import MagicMock, Mock, patch
import stripe

from services.billing_service import (
    BillingService,
    UsageInfo,
    FREE_TIER_SECONDS,
    WARNING_THRESHOLD,
)


class TestUsageInfo:
    """Test UsageInfo dataclass."""
    
    def test_to_dict_basic(self):
        """Test UsageInfo.to_dict() conversion."""
        info = UsageInfo(
            used_seconds=1800,
            free_tier_seconds=3600,
            remaining_seconds=1800,
            usage_percentage=0.5,
            is_exceeded=False,
            is_warning=False,
            billing_status="free",
            is_admin=False,
            is_paid=False,
        )
        
        result = info.to_dict()
        
        assert result["used_seconds"] == 1800
        assert result["used_minutes"] == 30.0
        assert result["free_tier_seconds"] == 3600
        assert result["free_tier_minutes"] == 60.0
        assert result["remaining_seconds"] == 1800
        assert result["remaining_minutes"] == 30.0
        assert result["usage_percentage"] == 50.0
        assert result["is_exceeded"] is False
        assert result["is_warning"] is False
        assert result["billing_status"] == "free"
        assert result["is_admin"] is False
        assert result["is_paid"] is False
    
    def test_to_dict_exceeded(self):
        """Test UsageInfo.to_dict() when free tier is exceeded."""
        info = UsageInfo(
            used_seconds=4000,
            free_tier_seconds=3600,
            remaining_seconds=0,
            usage_percentage=1.0,
            is_exceeded=True,
            is_warning=False,
            billing_status="free",
            is_admin=False,
            is_paid=False,
        )
        
        result = info.to_dict()
        
        assert result["is_exceeded"] is True
        assert result["remaining_seconds"] == 0
        assert result["usage_percentage"] == 100.0


class TestBillingServiceInit:
    """Test BillingService initialization."""
    
    def test_init_with_stripe_key(self, mock_supabase):
        """Test initialization with Stripe API key."""
        with patch.dict('os.environ', {
            'STRIPE_SECRET_KEY': 'sk_test_123',
            'STRIPE_PRICE_ID': 'price_123',
            'STRIPE_WEBHOOK_SECRET': 'whsec_123',
        }):
            service = BillingService(mock_supabase)
            
            assert service.supabase == mock_supabase
            assert service.stripe_secret_key == 'sk_test_123'
            assert service.stripe_price_id == 'price_123'
            assert service.stripe_webhook_secret == 'whsec_123'
    
    def test_init_without_stripe_key(self, mock_supabase):
        """Test initialization without Stripe API key logs warning."""
        with patch.dict('os.environ', {}, clear=True):
            with patch('services.billing_service.logger') as mock_logger:
                service = BillingService(mock_supabase)
                
                mock_logger.warning.assert_called_once()
                assert 'STRIPE_SECRET_KEY not configured' in str(mock_logger.warning.call_args)
    
    def test_init_sets_stripe_api_key(self, mock_supabase):
        """Test that initialization sets stripe.api_key."""
        with patch.dict('os.environ', {'STRIPE_SECRET_KEY': 'sk_test_456'}):
            with patch('services.billing_service.stripe') as mock_stripe:
                service = BillingService(mock_supabase)
                
                assert mock_stripe.api_key == 'sk_test_456'


class TestGetMonthlyUsage:
    """Test get_monthly_usage method."""
    
    def test_no_sessions(self, mock_supabase):
        """Test usage calculation with no sessions."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value.data = []
        
        service = BillingService(mock_supabase)
        result = service.get_monthly_usage('user-123')
        
        assert result == 0.0
    
    def test_single_session(self, mock_supabase):
        """Test usage calculation with one session."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value.data = [
            {'usage': {'stt_audio_duration': 120.5}}
        ]
        
        service = BillingService(mock_supabase)
        result = service.get_monthly_usage('user-123')
        
        assert result == 120.5
    
    def test_multiple_sessions(self, mock_supabase):
        """Test usage calculation with multiple sessions."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value.data = [
            {'usage': {'stt_audio_duration': 100}},
            {'usage': {'stt_audio_duration': 200}},
            {'usage': {'stt_audio_duration': 300}},
        ]
        
        service = BillingService(mock_supabase)
        result = service.get_monthly_usage('user-123')
        
        assert result == 600.0
    
    def test_sessions_with_null_usage(self, mock_supabase):
        """Test usage calculation with null usage fields."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value.data = [
            {'usage': None},
            {'usage': {'stt_audio_duration': 100}},
            {'usage': {}},
        ]
        
        service = BillingService(mock_supabase)
        result = service.get_monthly_usage('user-123')
        
        assert result == 100.0
    
    def test_database_error_returns_zero(self, mock_supabase):
        """Test that database errors return 0."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.side_effect = Exception("DB Error")
        
        service = BillingService(mock_supabase)
        result = service.get_monthly_usage('user-123')
        
        assert result == 0.0


class TestGetUserBillingInfo:
    """Test get_user_billing_info method."""
    
    def test_existing_user_with_billing_info(self, mock_supabase):
        """Test getting billing info for existing user."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{
            'billing_status': 'active',
            'is_admin': False,
            'stripe_customer_id': 'cus_123',
            'stripe_subscription_id': 'sub_456',
        }]
        
        service = BillingService(mock_supabase)
        result = service.get_user_billing_info('user-123')
        
        assert result['billing_status'] == 'active'
        assert result['is_admin'] is False
        assert result['stripe_customer_id'] == 'cus_123'
        assert result['stripe_subscription_id'] == 'sub_456'
    
    def test_existing_admin_user(self, mock_supabase):
        """Test getting billing info for admin user."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{
            'billing_status': 'free',
            'is_admin': True,
            'stripe_customer_id': None,
            'stripe_subscription_id': None,
        }]
        
        service = BillingService(mock_supabase)
        result = service.get_user_billing_info('user-123')
        
        assert result['is_admin'] is True
    
    def test_user_without_profile(self, mock_supabase):
        """Test default values for user without profile."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        
        service = BillingService(mock_supabase)
        result = service.get_user_billing_info('user-123')
        
        assert result['billing_status'] == 'free'
        assert result['is_admin'] is False
        assert result['stripe_customer_id'] is None
        assert result['stripe_subscription_id'] is None
    
    def test_database_error_returns_defaults(self, mock_supabase):
        """Test that database errors return defaults."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.side_effect = Exception("DB Error")
        
        service = BillingService(mock_supabase)
        result = service.get_user_billing_info('user-123')
        
        assert result['billing_status'] == 'free'
        assert result['is_admin'] is False


class TestGetUsageInfo:
    """Test get_usage_info method."""
    
    def test_admin_user_unlimited(self, mock_supabase):
        """Test that admin users have unlimited access."""
        service = BillingService(mock_supabase)
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'is_admin': True,
            'billing_status': 'free',
        }):
            result = service.get_usage_info('user-123')
            
            assert result.is_admin is True
            assert result.is_exceeded is False
            assert result.remaining_seconds == float('inf')
    
    def test_paid_user_unlimited(self, mock_supabase):
        """Test that paid users have unlimited access."""
        service = BillingService(mock_supabase)
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'is_admin': False,
            'billing_status': 'active',
        }):
            with patch.object(service, 'get_monthly_usage', return_value=5000.0):
                result = service.get_usage_info('user-123')
                
                assert result.is_paid is True
                assert result.is_exceeded is False
                assert result.remaining_seconds == float('inf')
                assert result.used_seconds == 5000.0
    
    def test_free_user_under_limit(self, mock_supabase):
        """Test free user under the limit."""
        service = BillingService(mock_supabase)
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'is_admin': False,
            'billing_status': 'free',
        }):
            with patch.object(service, 'get_monthly_usage', return_value=1800.0):  # 30 minutes
                result = service.get_usage_info('user-123')
                
                assert result.is_paid is False
                assert result.is_exceeded is False
                assert result.is_warning is False
                assert result.used_seconds == 1800.0
                assert result.remaining_seconds == FREE_TIER_SECONDS - 1800.0
                assert result.usage_percentage == 0.5
    
    def test_free_user_warning_threshold(self, mock_supabase):
        """Test free user at warning threshold (80%)."""
        service = BillingService(mock_supabase)
        warning_usage = FREE_TIER_SECONDS * WARNING_THRESHOLD  # 2880 seconds = 48 minutes
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'is_admin': False,
            'billing_status': 'free',
        }):
            with patch.object(service, 'get_monthly_usage', return_value=warning_usage):
                result = service.get_usage_info('user-123')
                
                assert result.is_warning is True
                assert result.is_exceeded is False
    
    def test_free_user_exceeded(self, mock_supabase):
        """Test free user who exceeded the limit."""
        service = BillingService(mock_supabase)
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'is_admin': False,
            'billing_status': 'free',
        }):
            with patch.object(service, 'get_monthly_usage', return_value=4000.0):  # Over 3600
                result = service.get_usage_info('user-123')
                
                assert result.is_exceeded is True
                assert result.is_warning is False  # Not warning when exceeded
                assert result.remaining_seconds == 0
                assert result.usage_percentage == 1.0  # Capped at 100%


class TestCanStartSession:
    """Test can_start_session method."""
    
    def test_admin_can_start(self, mock_supabase):
        """Test that admin users can always start sessions."""
        service = BillingService(mock_supabase)
        
        with patch.object(service, 'get_usage_info', return_value=UsageInfo(
            used_seconds=0,
            free_tier_seconds=float('inf'),
            remaining_seconds=float('inf'),
            usage_percentage=0,
            is_exceeded=False,
            is_warning=False,
            billing_status='free',
            is_admin=True,
            is_paid=False,
        )):
            can_start, reason = service.can_start_session('user-123')
            
            assert can_start is True
            assert 'Admin user' in reason
    
    def test_paid_user_can_start(self, mock_supabase):
        """Test that paid users can always start sessions."""
        service = BillingService(mock_supabase)
        
        with patch.object(service, 'get_usage_info', return_value=UsageInfo(
            used_seconds=10000,
            free_tier_seconds=FREE_TIER_SECONDS,
            remaining_seconds=float('inf'),
            usage_percentage=0,
            is_exceeded=False,
            is_warning=False,
            billing_status='active',
            is_admin=False,
            is_paid=True,
        )):
            can_start, reason = service.can_start_session('user-123')
            
            assert can_start is True
            assert 'Paid subscription' in reason
    
    def test_free_user_can_start(self, mock_supabase):
        """Test that free user with remaining time can start."""
        service = BillingService(mock_supabase)
        
        with patch.object(service, 'get_usage_info', return_value=UsageInfo(
            used_seconds=1800,
            free_tier_seconds=FREE_TIER_SECONDS,
            remaining_seconds=1800,
            usage_percentage=0.5,
            is_exceeded=False,
            is_warning=False,
            billing_status='free',
            is_admin=False,
            is_paid=False,
        )):
            can_start, reason = service.can_start_session('user-123')
            
            assert can_start is True
            assert '30.0 minutes remaining' in reason
    
    def test_exceeded_user_cannot_start(self, mock_supabase):
        """Test that exceeded free user cannot start."""
        service = BillingService(mock_supabase)
        
        with patch.object(service, 'get_usage_info', return_value=UsageInfo(
            used_seconds=4000,
            free_tier_seconds=FREE_TIER_SECONDS,
            remaining_seconds=0,
            usage_percentage=1.0,
            is_exceeded=True,
            is_warning=False,
            billing_status='free',
            is_admin=False,
            is_paid=False,
        )):
            can_start, reason = service.can_start_session('user-123')
            
            assert can_start is False
            assert 'upgrade required' in reason.lower()


class TestCreateCheckoutSession:
    """Test create_checkout_session method."""
    
    def test_stripe_not_configured(self, mock_supabase):
        """Test error when Stripe is not configured."""
        with patch.dict('os.environ', {}, clear=True):
            service = BillingService(mock_supabase)
            service.stripe_secret_key = None
            
            result = service.create_checkout_session('user-123', 'user@example.com')
            
            assert result is None
    
    def test_price_id_not_configured(self, mock_supabase):
        """Test error when price ID is not configured."""
        with patch.dict('os.environ', {'STRIPE_SECRET_KEY': 'sk_test_123'}):
            service = BillingService(mock_supabase)
            service.stripe_price_id = None
            
            result = service.create_checkout_session('user-123', 'user@example.com')
            
            assert result is None
    
    def test_creates_new_customer(self, mock_supabase):
        """Test creating checkout session for new customer."""
        service = BillingService(mock_supabase)
        service.stripe_secret_key = 'sk_test_123'
        service.stripe_price_id = 'price_123'
        service.app_url = 'https://example.com'
        
        mock_customer = MagicMock()
        mock_customer.id = 'cus_new_123'
        
        mock_checkout_session = MagicMock()
        mock_checkout_session.url = 'https://checkout.stripe.com/session_123'
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'stripe_customer_id': None,
        }):
            with patch.object(service, '_update_billing_info') as mock_update:
                with patch('services.billing_service.stripe.Customer.create', return_value=mock_customer):
                    with patch('services.billing_service.stripe.checkout.Session.create', return_value=mock_checkout_session):
                        result = service.create_checkout_session('user-123', 'user@example.com')
                        
                        assert result == 'https://checkout.stripe.com/session_123'
                        mock_update.assert_called_once_with('user-123', {'stripe_customer_id': 'cus_new_123'})
    
    def test_uses_existing_customer(self, mock_supabase):
        """Test creating checkout session for existing customer."""
        service = BillingService(mock_supabase)
        service.stripe_secret_key = 'sk_test_123'
        service.stripe_price_id = 'price_123'
        service.app_url = 'https://example.com'
        
        mock_checkout_session = MagicMock()
        mock_checkout_session.url = 'https://checkout.stripe.com/session_123'
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'stripe_customer_id': 'cus_existing_123',
        }):
            with patch('services.billing_service.stripe.checkout.Session.create', return_value=mock_checkout_session) as mock_create:
                result = service.create_checkout_session('user-123', 'user@example.com')
                
                assert result == 'https://checkout.stripe.com/session_123'
                # Verify existing customer was used
                call_kwargs = mock_create.call_args.kwargs
                assert call_kwargs['customer'] == 'cus_existing_123'
    
    def test_stripe_error_returns_none(self, mock_supabase):
        """Test that Stripe errors return None."""
        service = BillingService(mock_supabase)
        service.stripe_secret_key = 'sk_test_123'
        service.stripe_price_id = 'price_123'
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'stripe_customer_id': 'cus_123',
        }):
            with patch('services.billing_service.stripe.checkout.Session.create', side_effect=stripe.error.StripeError("API Error")):
                result = service.create_checkout_session('user-123', 'user@example.com')
                
                assert result is None


class TestHandleWebhook:
    """Test handle_webhook method."""
    
    def test_webhook_secret_not_configured(self, mock_supabase):
        """Test error when webhook secret is not configured."""
        service = BillingService(mock_supabase)
        service.stripe_webhook_secret = None
        
        result = service.handle_webhook(b'payload', 'sig_123')
        
        assert result['status'] == 'error'
        assert 'not configured' in result['message']
    
    def test_invalid_payload(self, mock_supabase):
        """Test error for invalid payload."""
        service = BillingService(mock_supabase)
        service.stripe_webhook_secret = 'whsec_123'
        
        with patch('services.billing_service.stripe.Webhook.construct_event', side_effect=ValueError("Invalid")):
            result = service.handle_webhook(b'payload', 'sig_123')
            
            assert result['status'] == 'error'
            assert 'Invalid payload' in result['message']
    
    def test_invalid_signature(self, mock_supabase):
        """Test error for invalid signature."""
        service = BillingService(mock_supabase)
        service.stripe_webhook_secret = 'whsec_123'
        
        with patch('services.billing_service.stripe.Webhook.construct_event', side_effect=stripe.error.SignatureVerificationError("Bad sig", "sig")):
            result = service.handle_webhook(b'payload', 'sig_123')
            
            assert result['status'] == 'error'
            assert 'Invalid signature' in result['message']
    
    def test_checkout_completed(self, mock_supabase):
        """Test handling checkout.session.completed event."""
        service = BillingService(mock_supabase)
        service.stripe_webhook_secret = 'whsec_123'
        
        event = {
            'type': 'checkout.session.completed',
            'data': {
                'object': {
                    'metadata': {'user_id': 'user-123'},
                    'subscription': 'sub_456',
                    'customer': 'cus_789',
                }
            }
        }
        
        with patch('services.billing_service.stripe.Webhook.construct_event', return_value=event):
            with patch.object(service, '_update_billing_info') as mock_update:
                result = service.handle_webhook(b'payload', 'sig_123')
                
                assert result['status'] == 'ok'
                mock_update.assert_called_once_with('user-123', {
                    'stripe_customer_id': 'cus_789',
                    'stripe_subscription_id': 'sub_456',
                    'billing_status': 'active',
                })
    
    def test_subscription_deleted(self, mock_supabase):
        """Test handling customer.subscription.deleted event."""
        service = BillingService(mock_supabase)
        service.stripe_webhook_secret = 'whsec_123'
        
        event = {
            'type': 'customer.subscription.deleted',
            'data': {
                'object': {
                    'customer': 'cus_789',
                }
            }
        }
        
        with patch('services.billing_service.stripe.Webhook.construct_event', return_value=event):
            with patch.object(service, '_get_user_by_customer_id', return_value='user-123'):
                with patch.object(service, '_update_billing_info') as mock_update:
                    result = service.handle_webhook(b'payload', 'sig_123')
                    
                    assert result['status'] == 'ok'
                    mock_update.assert_called_once_with('user-123', {
                        'stripe_subscription_id': None,
                        'billing_status': 'cancelled',
                    })
    
    def test_payment_failed(self, mock_supabase):
        """Test handling invoice.payment_failed event."""
        service = BillingService(mock_supabase)
        service.stripe_webhook_secret = 'whsec_123'
        
        event = {
            'type': 'invoice.payment_failed',
            'data': {
                'object': {
                    'customer': 'cus_789',
                }
            }
        }
        
        with patch('services.billing_service.stripe.Webhook.construct_event', return_value=event):
            with patch.object(service, '_get_user_by_customer_id', return_value='user-123'):
                with patch.object(service, '_update_billing_info') as mock_update:
                    result = service.handle_webhook(b'payload', 'sig_123')
                    
                    assert result['status'] == 'ok'
                    mock_update.assert_called_once_with('user-123', {'billing_status': 'past_due'})
    
    def test_unhandled_event_type(self, mock_supabase):
        """Test handling unrecognized event type."""
        service = BillingService(mock_supabase)
        service.stripe_webhook_secret = 'whsec_123'
        
        event = {
            'type': 'some.other.event',
            'data': {
                'object': {}
            }
        }
        
        with patch('services.billing_service.stripe.Webhook.construct_event', return_value=event):
            result = service.handle_webhook(b'payload', 'sig_123')
            
            assert result['status'] == 'ok'
            assert 'Unhandled event type' in result['message']


class TestMapStripeStatus:
    """Test _map_stripe_status method."""
    
    def test_status_mapping(self, mock_supabase):
        """Test Stripe status to billing status mapping."""
        service = BillingService(mock_supabase)
        
        assert service._map_stripe_status('active') == 'active'
        assert service._map_stripe_status('past_due') == 'past_due'
        assert service._map_stripe_status('canceled') == 'cancelled'
        assert service._map_stripe_status('cancelled') == 'cancelled'
        assert service._map_stripe_status('unpaid') == 'past_due'
        assert service._map_stripe_status('incomplete') == 'free'
        assert service._map_stripe_status('incomplete_expired') == 'free'
        assert service._map_stripe_status('trialing') == 'active'
        assert service._map_stripe_status('unknown_status') == 'free'  # Default


class TestGetUserByCustomerId:
    """Test _get_user_by_customer_id method."""
    
    def test_user_found(self, mock_supabase):
        """Test finding user by customer ID."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {'user_id': 'user-123'}
        ]
        
        service = BillingService(mock_supabase)
        result = service._get_user_by_customer_id('cus_123')
        
        assert result == 'user-123'
    
    def test_user_not_found(self, mock_supabase):
        """Test when user is not found."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        
        service = BillingService(mock_supabase)
        result = service._get_user_by_customer_id('cus_123')
        
        assert result is None
    
    def test_database_error(self, mock_supabase):
        """Test handling database error."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.side_effect = Exception("DB Error")
        
        service = BillingService(mock_supabase)
        result = service._get_user_by_customer_id('cus_123')
        
        assert result is None


class TestUpdateBillingInfo:
    """Test _update_billing_info method."""
    
    def test_update_existing_profile(self, mock_supabase):
        """Test updating existing user profile."""
        # Mock existing profile check
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {'id': 'profile-123'}
        ]
        
        service = BillingService(mock_supabase)
        result = service._update_billing_info('user-123', {'billing_status': 'active'})
        
        assert result is True
        mock_supabase.table.return_value.update.assert_called_with({'billing_status': 'active'})
    
    def test_create_new_profile(self, mock_supabase):
        """Test creating new profile when none exists."""
        # Mock no existing profile
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        
        service = BillingService(mock_supabase)
        result = service._update_billing_info('user-123', {'billing_status': 'active'})
        
        assert result is True
        mock_supabase.table.return_value.insert.assert_called_with({
            'user_id': 'user-123',
            'billing_status': 'active',
        })
    
    def test_database_error(self, mock_supabase):
        """Test handling database error."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.side_effect = Exception("DB Error")
        
        service = BillingService(mock_supabase)
        result = service._update_billing_info('user-123', {'billing_status': 'active'})
        
        assert result is False


class TestReportUsageToStripe:
    """Test report_usage_to_stripe method."""
    
    def test_stripe_not_configured(self, mock_supabase):
        """Test returns False when Stripe not configured."""
        service = BillingService(mock_supabase)
        service.stripe_secret_key = None
        
        result = service.report_usage_to_stripe('user-123', 120.0)
        
        assert result is False
    
    def test_no_customer_id(self, mock_supabase):
        """Test returns True (no-op) when user has no customer ID."""
        service = BillingService(mock_supabase)
        service.stripe_secret_key = 'sk_test_123'
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'stripe_customer_id': None,
        }):
            result = service.report_usage_to_stripe('user-123', 120.0)
            
            assert result is True  # Successful no-op
    
    def test_zero_usage(self, mock_supabase):
        """Test returns True for zero usage."""
        service = BillingService(mock_supabase)
        service.stripe_secret_key = 'sk_test_123'
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'stripe_customer_id': 'cus_123',
        }):
            result = service.report_usage_to_stripe('user-123', 0.0)
            
            assert result is True
    
    def test_successful_usage_report(self, mock_supabase):
        """Test successful usage reporting."""
        service = BillingService(mock_supabase)
        service.stripe_secret_key = 'sk_test_123'
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'stripe_customer_id': 'cus_123',
        }):
            with patch('services.billing_service.stripe.billing.MeterEvent.create') as mock_meter:
                result = service.report_usage_to_stripe('user-123', 120.0)
                
                assert result is True
                mock_meter.assert_called_once()
                call_kwargs = mock_meter.call_args.kwargs
                assert call_kwargs['event_name'] == 'chat_usage_minutes'
                assert call_kwargs['payload']['stripe_customer_id'] == 'cus_123'
                assert call_kwargs['payload']['value'] == '2'  # 120 seconds = 2 minutes
    
    def test_rounds_up_to_nearest_minute(self, mock_supabase):
        """Test that usage is rounded up to nearest minute."""
        service = BillingService(mock_supabase)
        service.stripe_secret_key = 'sk_test_123'
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'stripe_customer_id': 'cus_123',
        }):
            with patch('services.billing_service.stripe.billing.MeterEvent.create') as mock_meter:
                # 61 seconds should round up to 2 minutes
                result = service.report_usage_to_stripe('user-123', 61.0)
                
                assert result is True
                call_kwargs = mock_meter.call_args.kwargs
                assert call_kwargs['payload']['value'] == '2'
    
    def test_stripe_error_returns_false(self, mock_supabase):
        """Test that Stripe errors return False."""
        service = BillingService(mock_supabase)
        service.stripe_secret_key = 'sk_test_123'
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'stripe_customer_id': 'cus_123',
        }):
            with patch('services.billing_service.stripe.billing.MeterEvent.create', side_effect=stripe.error.StripeError("API Error")):
                result = service.report_usage_to_stripe('user-123', 120.0)
                
                assert result is False


class TestCheckoutCompletedWithoutUserId:
    """Test edge cases for checkout completed webhook."""
    
    def test_checkout_without_user_id(self, mock_supabase):
        """Test checkout completed without user_id in metadata."""
        service = BillingService(mock_supabase)
        
        result = service._handle_checkout_completed({
            'metadata': {},
            'subscription': 'sub_123',
            'customer': 'cus_123',
        })
        
        assert result['status'] == 'error'
        assert 'No user_id' in result['message']


class TestSubscriptionUpdated:
    """Test subscription update handling."""
    
    def test_subscription_updated_success(self, mock_supabase):
        """Test successful subscription update."""
        service = BillingService(mock_supabase)
        
        with patch.object(service, '_get_user_by_customer_id', return_value='user-123'):
            with patch.object(service, '_update_billing_info') as mock_update:
                result = service._handle_subscription_updated({
                    'customer': 'cus_123',
                    'status': 'active',
                })
                
                assert result['status'] == 'ok'
                mock_update.assert_called_once_with('user-123', {'billing_status': 'active'})
    
    def test_subscription_updated_user_not_found(self, mock_supabase):
        """Test subscription update when user not found."""
        service = BillingService(mock_supabase)
        
        with patch.object(service, '_get_user_by_customer_id', return_value=None):
            result = service._handle_subscription_updated({
                'customer': 'cus_123',
                'status': 'active',
            })
            
            assert result['status'] == 'error'
            assert 'User not found' in result['message']


class TestInvoicePaid:
    """Test invoice paid handling."""
    
    def test_invoice_paid_logging(self, mock_supabase):
        """Test invoice paid event is logged."""
        service = BillingService(mock_supabase)
        
        result = service._handle_invoice_paid({
            'customer': 'cus_123',
            'amount_paid': 1000,  # $10.00
        })
        
        assert result['status'] == 'ok'
        assert 'Invoice paid' in result['message']


class TestCreateCustomerPortalSession:
    """Test create_customer_portal_session method."""
    
    def test_stripe_not_configured(self, mock_supabase):
        """Test returns None when Stripe not configured."""
        service = BillingService(mock_supabase)
        service.stripe_secret_key = None
        
        result = service.create_customer_portal_session('user-123')
        
        assert result is None
    
    def test_no_customer_id(self, mock_supabase):
        """Test returns None when user has no Stripe customer."""
        service = BillingService(mock_supabase)
        service.stripe_secret_key = 'sk_test_123'
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'stripe_customer_id': None,
        }):
            result = service.create_customer_portal_session('user-123')
            
            assert result is None
    
    def test_successful_portal_session(self, mock_supabase):
        """Test successful portal session creation."""
        service = BillingService(mock_supabase)
        service.stripe_secret_key = 'sk_test_123'
        service.app_url = 'https://example.com'
        
        mock_session = MagicMock()
        mock_session.url = 'https://billing.stripe.com/session_123'
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'stripe_customer_id': 'cus_123',
        }):
            with patch('services.billing_service.stripe.billing_portal.Session.create', 
                       return_value=mock_session):
                result = service.create_customer_portal_session('user-123')
                
                assert result == 'https://billing.stripe.com/session_123'
    
    def test_stripe_error_returns_none(self, mock_supabase):
        """Test Stripe errors return None."""
        service = BillingService(mock_supabase)
        service.stripe_secret_key = 'sk_test_123'
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'stripe_customer_id': 'cus_123',
        }):
            with patch('services.billing_service.stripe.billing_portal.Session.create',
                       side_effect=stripe.error.StripeError("API Error")):
                result = service.create_customer_portal_session('user-123')
                
                assert result is None
    
    def test_portal_session_with_return_url(self, mock_supabase):
        """Test that portal session is created with correct return URL."""
        service = BillingService(mock_supabase)
        service.stripe_secret_key = 'sk_test_123'
        service.app_url = 'https://expertavatar.com'
        
        mock_session = MagicMock()
        mock_session.url = 'https://billing.stripe.com/session_123'
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'stripe_customer_id': 'cus_123',
        }):
            with patch('services.billing_service.stripe.billing_portal.Session.create', 
                       return_value=mock_session) as mock_create:
                service.create_customer_portal_session('user-123')
                
                call_kwargs = mock_create.call_args.kwargs
                assert call_kwargs['customer'] == 'cus_123'
                assert call_kwargs['return_url'] == 'https://expertavatar.com/settings'


class TestCancelSubscription:
    """Test _cancel_subscription method."""
    
    def test_stripe_not_configured(self, mock_supabase):
        """Test returns False when Stripe not configured."""
        service = BillingService(mock_supabase)
        service.stripe_secret_key = None
        
        result = service._cancel_subscription('sub_123')
        
        assert result is False
    
    def test_successful_cancellation(self, mock_supabase):
        """Test successful subscription cancellation."""
        service = BillingService(mock_supabase)
        service.stripe_secret_key = 'sk_test_123'
        
        with patch('services.billing_service.stripe.Subscription.cancel'):
            result = service._cancel_subscription('sub_123')
            
            assert result is True
    
    def test_stripe_error_returns_false(self, mock_supabase):
        """Test Stripe error during cancellation returns False."""
        service = BillingService(mock_supabase)
        service.stripe_secret_key = 'sk_test_123'
        
        with patch('services.billing_service.stripe.Subscription.cancel',
                   side_effect=stripe.error.StripeError("Error")):
            result = service._cancel_subscription('sub_123')
            
            assert result is False


class TestDeleteUserAccount:
    """Test delete_user_account method."""
    
    def test_deletes_free_user(self, mock_supabase):
        """Test deleting user without subscription."""
        service = BillingService(mock_supabase)
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'stripe_subscription_id': None,
        }):
            with patch.object(service, '_delete_user_data'):
                result = service.delete_user_account('user-123')
                
                assert result['status'] == 'ok'
    
    def test_cancels_subscription_before_delete(self, mock_supabase):
        """Test subscription is cancelled before deletion."""
        service = BillingService(mock_supabase)
        service.stripe_secret_key = 'sk_test_123'
        
        with patch.object(service, 'get_user_billing_info', return_value={
            'stripe_subscription_id': 'sub_123',
        }):
            with patch.object(service, '_cancel_subscription') as mock_cancel:
                with patch.object(service, '_delete_user_data'):
                    service.delete_user_account('user-123')
                    
                    mock_cancel.assert_called_once_with('sub_123')
    
    def test_calls_delete_user_data(self, mock_supabase):
        """Test that _delete_user_data is called."""
        service = BillingService(mock_supabase)
        
        with patch.object(service, 'get_user_billing_info', return_value={}):
            with patch.object(service, '_delete_user_data') as mock_delete:
                service.delete_user_account('user-123')
                
                mock_delete.assert_called_once_with('user-123')
    
    def test_deletes_supabase_auth_user(self, mock_supabase):
        """Test that Supabase auth user is deleted."""
        service = BillingService(mock_supabase)
        
        with patch.object(service, 'get_user_billing_info', return_value={}):
            with patch.object(service, '_delete_user_data'):
                service.delete_user_account('user-123')
                
                mock_supabase.auth.admin.delete_user.assert_called_once_with('user-123')
    
    def test_handles_deletion_error(self, mock_supabase):
        """Test error handling during deletion."""
        service = BillingService(mock_supabase)
        
        with patch.object(service, 'get_user_billing_info', return_value={}):
            with patch.object(service, '_delete_user_data', 
                             side_effect=Exception("DB Error")):
                result = service.delete_user_account('user-123')
                
                assert result['status'] == 'error'
                assert 'DB Error' in result['message']


class TestDeleteUserData:
    """Test _delete_user_data method."""
    
    def test_deletes_chat_sessions(self, mock_supabase):
        """Test that chat_sessions are deleted."""
        # Setup mock for session lookup
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        
        service = BillingService(mock_supabase)
        service._delete_user_data('user-123')
        
        # Verify delete was called on chat_sessions
        mock_supabase.table.assert_any_call("chat_sessions")
    
    def test_soft_deletes_avatar_share_tokens(self, mock_supabase):
        """Test that avatar_share_tokens are soft-deleted."""
        # Setup mock for session lookup
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {'session_id': 'session-1'},
            {'session_id': 'session-2'},
        ]
        
        service = BillingService(mock_supabase)
        service._delete_user_data('user-123')
        
        # Verify update was called on avatar_share_tokens
        mock_supabase.table.assert_any_call("avatar_share_tokens")
    
    def test_deletes_interview_call_analysis(self, mock_supabase):
        """Test that interview_call_analysis records are deleted."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {'session_id': 'session-1'},
        ]
        
        service = BillingService(mock_supabase)
        service._delete_user_data('user-123')
        
        mock_supabase.table.assert_any_call("interview_call_analysis")
    
    def test_deletes_interview_calls(self, mock_supabase):
        """Test that interview_calls are deleted."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        
        service = BillingService(mock_supabase)
        service._delete_user_data('user-123')
        
        mock_supabase.table.assert_any_call("interview_calls")
    
    def test_deletes_user_profiles(self, mock_supabase):
        """Test that user_profiles are deleted."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        
        service = BillingService(mock_supabase)
        service._delete_user_data('user-123')
        
        mock_supabase.table.assert_any_call("user_profiles")
