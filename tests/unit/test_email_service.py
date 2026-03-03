"""
Tests for EmailService using Resend.
"""

import pytest
from unittest.mock import MagicMock, patch
from services.email_service import EmailService


@pytest.fixture
def mock_user_info():
    """Mock user info from Supabase auth."""
    return MagicMock(
        user=MagicMock(
            email="expert@example.com",
            user_metadata={"given_name": "John", "name": "John Doe"}
        )
    )


@pytest.fixture
def mock_seeker_info():
    """Mock seeker user info."""
    return MagicMock(
        user=MagicMock(
            email="seeker@example.com",
            user_metadata={"given_name": "Jane", "name": "Jane Smith"}
        )
    )


class TestEmailServiceInit:
    """Test EmailService initialization."""
    
    def test_init_with_api_key(self, mock_supabase):
        """Test initialization with explicit API key."""
        service = EmailService(
            supabase_client=mock_supabase,
            resend_api_key="test-api-key",
            from_email="test@example.com"
        )
        
        assert service.resend_api_key == "test-api-key"
        assert service.from_email == "test@example.com"
    
    def test_init_without_api_key_warns(self, mock_supabase):
        """Test that missing API key logs a warning."""
        with patch.dict('os.environ', {}, clear=True):
            with patch('services.email_service.os.getenv', return_value=None):
                service = EmailService(
                    supabase_client=mock_supabase,
                    resend_api_key=None
                )
                
                assert service.resend_api_key is None


class TestExpertNotification:
    """Test expert notification emails."""
    
    def test_send_expert_notification_no_api_key(self, mock_supabase):
        """Test that no email is sent when API key is missing."""
        service = EmailService(
            supabase_client=mock_supabase,
            resend_api_key=None
        )
        
        result = service.send_expert_notification(
            expert_user_id="expert-123",
            message_count=5,
            session_id="session-456"
        )
        
        assert result["status"] == "skipped"
        assert result["reason"] == "No API key"
    
    def test_send_expert_notification_opted_out(self, mock_supabase):
        """Test that no email is sent when expert has opted out."""
        # Mock profile with notifications disabled
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"email_notifications_enabled": False}
        ]
        
        service = EmailService(
            supabase_client=mock_supabase,
            resend_api_key="test-key"
        )
        
        result = service.send_expert_notification(
            expert_user_id="expert-123",
            message_count=5,
            session_id="session-456"
        )
        
        assert result["status"] == "skipped"
        assert result["reason"] == "User opted out"
    
    def test_send_expert_notification_no_email(self, mock_supabase):
        """Test handling when expert email is not found."""
        # Mock notifications enabled
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"email_notifications_enabled": True}
        ]
        
        # Mock no user found
        mock_supabase.auth.admin.get_user_by_id.return_value = None
        
        service = EmailService(
            supabase_client=mock_supabase,
            resend_api_key="test-key"
        )
        
        result = service.send_expert_notification(
            expert_user_id="expert-123",
            message_count=5,
            session_id="session-456"
        )
        
        assert result["status"] == "error"
        assert "Expert email not found" in result["error"]
    
    @patch('services.email_service.resend.Emails.send')
    def test_send_expert_notification_success(
        self, 
        mock_resend_send, 
        mock_supabase, 
        mock_user_info
    ):
        """Test successful expert notification."""
        # Mock notifications enabled
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"email_notifications_enabled": True}
        ]
        
        # Mock user info
        mock_supabase.auth.admin.get_user_by_id.return_value = mock_user_info
        
        # Mock session info
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"main_topic": "Machine Learning"}
        ]
        
        # Mock Resend response
        mock_resend_send.return_value = {"id": "email-123"}
        
        service = EmailService(
            supabase_client=mock_supabase,
            resend_api_key="test-key",
            from_email="noreply@test.com"
        )
        
        result = service.send_expert_notification(
            expert_user_id="expert-123",
            message_count=3,
            session_id="session-456"
        )
        
        assert result["status"] == "sent"
        assert result["email_id"] == "email-123"
        
        # Verify email was sent with correct parameters
        mock_resend_send.assert_called_once()
        call_args = mock_resend_send.call_args[0][0]
        assert call_args["to"] == ["expert@example.com"]
        assert "3 new messages" in call_args["subject"]


class TestSeekerNotification:
    """Test seeker notification emails."""
    
    def test_send_seeker_notification_no_api_key(self, mock_supabase):
        """Test that no email is sent when API key is missing."""
        service = EmailService(
            supabase_client=mock_supabase,
            resend_api_key=None
        )
        
        result = service.send_seeker_chat_updated(
            seeker_user_id="seeker-123",
            expert_name="John Doe",
            share_token="abc123"
        )
        
        assert result["status"] == "skipped"
    
    @patch('services.email_service.resend.Emails.send')
    def test_send_seeker_notification_success(
        self, 
        mock_resend_send, 
        mock_supabase, 
        mock_seeker_info
    ):
        """Test successful seeker notification."""
        mock_supabase.auth.admin.get_user_by_id.return_value = mock_seeker_info
        mock_resend_send.return_value = {"id": "email-456"}
        
        service = EmailService(
            supabase_client=mock_supabase,
            resend_api_key="test-key",
            from_email="noreply@test.com"
        )
        
        result = service.send_seeker_chat_updated(
            seeker_user_id="seeker-123",
            expert_name="Dr. Smith",
            share_token="share-token-abc"
        )
        
        assert result["status"] == "sent"
        assert result["email_id"] == "email-456"
        
        call_args = mock_resend_send.call_args[0][0]
        assert call_args["to"] == ["seeker@example.com"]
        assert "Dr. Smith" in call_args["subject"]


class TestIntroductionEmail:
    """Test introduction emails."""
    
    def test_send_introduction_no_api_key(self, mock_supabase):
        """Test that no email is sent when API key is missing."""
        service = EmailService(
            supabase_client=mock_supabase,
            resend_api_key=None
        )
        
        result = service.send_introduction_email(
            expert_user_id="expert-123",
            seeker_user_id="seeker-456",
            seeker_name="Jane Doe",
            seeker_company="TechCorp",
            seeker_role="Engineer",
            seeker_message="I'd like to connect"
        )
        
        assert result["status"] == "skipped"
    
    @patch('services.email_service.resend.Emails.send')
    def test_send_introduction_success(
        self, 
        mock_resend_send, 
        mock_supabase,
        mock_user_info,
        mock_seeker_info
    ):
        """Test successful introduction email."""
        # Mock both users
        def get_user_by_id(user_id):
            if "expert" in user_id:
                return mock_user_info
            return mock_seeker_info
        
        mock_supabase.auth.admin.get_user_by_id.side_effect = get_user_by_id
        mock_resend_send.return_value = {"id": "intro-email-789"}
        
        service = EmailService(
            supabase_client=mock_supabase,
            resend_api_key="test-key",
            from_email="noreply@test.com"
        )
        
        result = service.send_introduction_email(
            expert_user_id="expert-123",
            seeker_user_id="seeker-456",
            seeker_name="Jane Doe",
            seeker_company="TechCorp",
            seeker_role="ML Engineer",
            seeker_message="I'd love to discuss AI applications"
        )
        
        assert result["status"] == "sent"
        assert result["email_id"] == "intro-email-789"
        
        call_args = mock_resend_send.call_args[0][0]
        # Expert is the primary recipient, seeker is CC'd
        assert "expert@example.com" in call_args["to"]
        assert "seeker@example.com" in call_args["cc"]
        # Both emails should be in reply-to
        assert len(call_args["reply_to"]) == 2


class TestEmailPreferenceCheck:
    """Test email preference checking."""
    
    def test_is_email_enabled_default_true(self, mock_supabase):
        """Test that default is True when no profile exists."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        
        service = EmailService(
            supabase_client=mock_supabase,
            resend_api_key="test-key"
        )
        
        assert service._is_email_enabled("user-123") is True
    
    def test_is_email_enabled_from_profile(self, mock_supabase):
        """Test reading preference from profile."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"email_notifications_enabled": False}
        ]
        
        service = EmailService(
            supabase_client=mock_supabase,
            resend_api_key="test-key"
        )
        
        assert service._is_email_enabled("user-123") is False
    
    def test_is_email_enabled_error_defaults_true(self, mock_supabase):
        """Test that errors default to True."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.side_effect = Exception("DB Error")
        
        service = EmailService(
            supabase_client=mock_supabase,
            resend_api_key="test-key"
        )
        
        assert service._is_email_enabled("user-123") is True

