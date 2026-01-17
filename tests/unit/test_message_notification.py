"""
Tests for message notification and mail session processing in MessageOrchestrator.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, UTC
from services.message_orchestrator import MessageOrchestrator


class TestMessageNotificationProcessing:
    """Test suite for process_message_notifications."""
    
    @pytest.fixture
    def mock_email_service(self):
        """Create a mock email service."""
        return MagicMock()
    
    @pytest.fixture
    def orchestrator(self, mock_supabase, mock_email_service):
        """Create a MessageOrchestrator with mocked dependencies."""
        return MessageOrchestrator(
            supabase_client=mock_supabase,
            email_service=mock_email_service
        )
    
    def test_no_pending_messages(self, orchestrator, mock_supabase):
        """Test when there are no pending messages to notify."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.is_.return_value.execute.return_value.data = []
        
        result = orchestrator.process_message_notifications()
        
        assert result["status"] == "success"
        assert result["notifications_sent"] == 0
        assert "No pending notifications" in result["message"]
    
    def test_sends_notifications_for_pending_messages(self, orchestrator, mock_supabase, mock_email_service):
        """Test that notifications are sent for pending messages."""
        # Mock pending messages
        mock_supabase.table.return_value.select.return_value.eq.return_value.is_.return_value.execute.return_value.data = [
            {"expert_user_id": "expert-1", "source_session_id": "session-1"},
            {"expert_user_id": "expert-1", "source_session_id": "session-1"},
            {"expert_user_id": "expert-2", "source_session_id": "session-2"},
        ]
        
        # Mock successful email sends
        mock_email_service.send_expert_notification.return_value = {"status": "sent"}
        
        result = orchestrator.process_message_notifications()
        
        assert result["status"] == "success"
        assert result["notifications_sent"] == 2  # Two unique expert/session combinations
        
        # Verify email service was called for each unique combination
        assert mock_email_service.send_expert_notification.call_count == 2
    
    def test_handles_email_send_failure(self, orchestrator, mock_supabase, mock_email_service):
        """Test that email failures don't crash the process."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.is_.return_value.execute.return_value.data = [
            {"expert_user_id": "expert-1", "source_session_id": "session-1"},
        ]
        
        # Mock email failure
        mock_email_service.send_expert_notification.return_value = {"status": "error", "error": "API Error"}
        
        result = orchestrator.process_message_notifications()
        
        # Should still succeed overall, just with 0 sent
        assert result["status"] == "success"
        assert result["notifications_sent"] == 0


class TestMailSessionProcessing:
    """Test suite for process_mail_session - status-driven approach."""
    
    @pytest.fixture
    def mock_email_service(self):
        """Create a mock email service."""
        return MagicMock()
    
    @pytest.fixture
    def orchestrator(self, mock_supabase, mock_email_service):
        """Create a MessageOrchestrator with mocked dependencies."""
        return MessageOrchestrator(
            supabase_client=mock_supabase,
            email_service=mock_email_service
        )
    
    def test_requires_original_session_id(self, orchestrator):
        """Test that original_session_id is required."""
        with pytest.raises(ValueError) as exc_info:
            orchestrator.process_mail_session("mail-session-123", {})
        
        assert "original_session_id" in str(exc_info.value)
    
    def test_processes_answered_messages_by_status(self, orchestrator, mock_supabase):
        """Test processing answered messages from seeker_messages table."""
        mock_supabase.table.return_value.select.return_value\
            .eq.return_value.in_.return_value\
            .execute.return_value.data = [{
                "id": "msg-123",
                "message_content": "What is ML?",
                "expert_response": "ML is...",
                "seeker_name": "Jane",
                "seeker_user_id": "seeker-456",
                "status": "answered"
            }]
        
        result = orchestrator.process_mail_session(
            "mail-session-123",
            {"original_session_id": "source-456"}
        )
        
        assert result["status"] == "success"
        assert result["answered_processed"] == 1
        assert result["intros_processed"] == 0
    
    def test_processes_intro_approved_messages(self, orchestrator, mock_supabase, mock_email_service):
        """Test processing intro_approved messages."""
        mock_supabase.table.return_value.select.return_value\
            .eq.return_value.in_.return_value\
            .execute.return_value.data = [{
                "id": "msg-789",
                "seeker_name": "Alex",
                "seeker_user_id": "seeker-789",
                "seeker_company": "TechCorp",
                "seeker_role": "Engineer",
                "expert_user_id": "expert-001",
                "message_content": "I'd love to connect",
                "status": "intro_approved"
            }]
        
        result = orchestrator.process_mail_session(
            "mail-session-123",
            {"original_session_id": "source-456"}
        )
        
        assert result["status"] == "success"
        assert result["answered_processed"] == 0
        assert result["intros_processed"] == 1
        mock_email_service.send_introduction_email.assert_called_once()
    
    def test_updates_answered_to_answer_delivered(self, orchestrator, mock_supabase):
        """Test that answered messages get status updated to answer_delivered."""
        mock_supabase.table.return_value.select.return_value\
            .eq.return_value.in_.return_value\
            .execute.return_value.data = [{
                "id": "msg-123",
                "status": "answered",
                "message_content": "Question",
                "expert_response": "Answer",
                "seeker_name": "Test",
                "seeker_user_id": "seeker-123"
            }]
        
        orchestrator.process_mail_session(
            "mail-session-123",
            {"original_session_id": "source-456"}
        )
        
        # Verify status update to answer_delivered was called
        update_calls = [call for call in mock_supabase.table.return_value.update.call_args_list]
        assert any(
            call[0][0].get("status") == "answer_delivered" 
            for call in update_calls
        )
    
    def test_updates_intro_approved_to_intro_sent(
        self, orchestrator, mock_supabase, mock_email_service
    ):
        """Test that intro_approved messages get status updated to intro_sent."""
        mock_supabase.table.return_value.select.return_value\
            .eq.return_value.in_.return_value\
            .execute.return_value.data = [{
                "id": "msg-789",
                "seeker_name": "Alex",
                "seeker_user_id": "seeker-789",
                "expert_user_id": "expert-001",
                "status": "intro_approved"
            }]
        
        orchestrator.process_mail_session(
            "mail-session-123",
            {"original_session_id": "source-456"}
        )
        
        mock_email_service.send_introduction_email.assert_called_once()
        # Verify status update to intro_sent was called
        update_calls = [call for call in mock_supabase.table.return_value.update.call_args_list]
        assert any(
            call[0][0].get("status") == "intro_sent" 
            for call in update_calls
        )
    
    def test_ignores_already_processed_messages(self, orchestrator, mock_supabase):
        """Test that answer_delivered and intro_sent are not reprocessed."""
        # Query only returns answered/intro_approved, so already-processed messages
        # won't be in the result set
        mock_supabase.table.return_value.select.return_value\
            .eq.return_value.in_.return_value\
            .execute.return_value.data = []
        
        result = orchestrator.process_mail_session(
            "mail-session-123",
            {"original_session_id": "source-456"}
        )
        
        assert result["answered_processed"] == 0
        assert result["intros_processed"] == 0
    
    def test_processes_mixed_message_types(self, orchestrator, mock_supabase, mock_email_service):
        """Test processing a mix of answered and intro_approved messages."""
        mock_supabase.table.return_value.select.return_value\
            .eq.return_value.in_.return_value\
            .execute.return_value.data = [
                {
                    "id": "msg-1",
                    "status": "answered",
                    "message_content": "Question 1",
                    "expert_response": "Answer 1",
                    "seeker_name": "Jane",
                    "seeker_user_id": "seeker-1"
                },
                {
                    "id": "msg-2",
                    "status": "intro_approved",
                    "seeker_name": "Alex",
                    "seeker_user_id": "seeker-2",
                    "expert_user_id": "expert-1"
                },
                {
                    "id": "msg-3",
                    "status": "answered",
                    "message_content": "Question 2",
                    "expert_response": "Answer 2",
                    "seeker_name": "Bob",
                    "seeker_user_id": "seeker-3"
                }
            ]
        
        result = orchestrator.process_mail_session(
            "mail-session-123",
            {"original_session_id": "source-456"}
        )
        
        assert result["status"] == "success"
        assert result["answered_processed"] == 2
        assert result["intros_processed"] == 1


class TestAppendQAToTranscript:
    """Test suite for _append_qa_to_transcript."""
    
    @pytest.fixture
    def mock_email_service(self):
        """Create a mock email service."""
        return MagicMock()
    
    @pytest.fixture
    def orchestrator(self, mock_supabase, mock_email_service):
        """Create a MessageOrchestrator with mocked dependencies."""
        return MessageOrchestrator(
            supabase_client=mock_supabase,
            email_service=mock_email_service
        )
    
    def test_appends_qa_to_existing_transcript(self, orchestrator, mock_supabase):
        """Test appending Q&A to existing transcript."""
        import json
        
        existing_transcript = {
            "items": [
                {"role": "user", "content": [{"text": "Previous content"}]}
            ]
        }
        
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{
            "transcript": json.dumps(existing_transcript)
        }]
        
        orchestrator._append_qa_to_transcript(
            source_session_id="session-123",
            question="What is machine learning?",
            answer="ML is a subset of AI...",
            seeker_name="Jane"
        )
        
        # Verify update was called
        mock_supabase.table.return_value.update.assert_called()
    
    def test_handles_missing_transcript(self, orchestrator, mock_supabase):
        """Test handling when no transcript exists."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        
        # Should not raise
        orchestrator._append_qa_to_transcript(
            source_session_id="session-123",
            question="What is AI?",
            answer="AI is...",
            seeker_name="Test"
        )


class TestNotifySeekerOfAnswer:
    """Test suite for _notify_seeker_of_answer."""
    
    @pytest.fixture
    def mock_email_service(self):
        """Create a mock email service."""
        return MagicMock()
    
    @pytest.fixture
    def orchestrator(self, mock_supabase, mock_email_service):
        """Create a MessageOrchestrator with mocked dependencies."""
        return MessageOrchestrator(
            supabase_client=mock_supabase,
            email_service=mock_email_service
        )
    
    def test_sends_notification_to_seeker(self, orchestrator, mock_supabase, mock_email_service):
        """Test that seeker notification is sent."""
        # Mock share token lookup
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{
            "share_token": "abc123"
        }]
        
        # Mock expert info lookup
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{
            "user_firstname": "John",
            "user_lastname": "Doe"
        }]
        
        message = {
            "seeker_user_id": "seeker-123"
        }
        
        orchestrator._notify_seeker_of_answer(message, "source-session-456")
        
        # Verify email was sent
        mock_email_service.send_seeker_chat_updated.assert_called_once()
    
    def test_handles_no_share_token(self, orchestrator, mock_supabase, mock_email_service):
        """Test handling when no share token exists."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        
        message = {
            "seeker_user_id": "seeker-123"
        }
        
        # Should not raise
        orchestrator._notify_seeker_of_answer(message, "source-session-456")
        
        # Email should not be sent
        mock_email_service.send_seeker_chat_updated.assert_not_called()
