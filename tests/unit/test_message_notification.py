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
    """Test suite for process_mail_session."""
    
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
    
    def test_session_not_found_raises(self, orchestrator, mock_supabase):
        """Test that missing session raises error."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        
        with pytest.raises(ValueError) as exc_info:
            orchestrator.process_mail_session(
                "mail-session-123", 
                {"original_session_id": "source-456"}
            )
        
        assert "Mail session not found" in str(exc_info.value)
    
    def test_processes_answered_questions(self, orchestrator, mock_supabase):
        """Test processing answered questions from mail session."""
        # Mock session with answered question tool call
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{
            "conversation": {
                "items": [
                    {
                        "tool_calls": [
                            {
                                "name": "answer_question",
                                "arguments": {
                                    "message_id": "msg-123",
                                    "response": "Here is my answer..."
                                }
                            }
                        ]
                    }
                ]
            }
        }]
        
        result = orchestrator.process_mail_session(
            "mail-session-123",
            {"original_session_id": "source-456"}
        )
        
        assert result["status"] == "success"
        assert result["answered_questions"] == 1
    
    def test_processes_approved_introductions(self, orchestrator, mock_supabase, mock_email_service):
        """Test processing approved introductions from mail session."""
        # Mock session with approved intro tool call
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{
            "conversation": {
                "items": [
                    {
                        "tool_calls": [
                            {
                                "name": "approve_introduction",
                                "arguments": {
                                    "message_id": "msg-789"
                                }
                            }
                        ]
                    }
                ]
            }
        }]
        
        result = orchestrator.process_mail_session(
            "mail-session-123",
            {"original_session_id": "source-456"}
        )
        
        assert result["status"] == "success"
        assert result["approved_intros"] == 1


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
            student_name="Jane"
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
            student_name="Test"
        )


class TestNotifyStudentOfAnswer:
    """Test suite for _notify_student_of_answer."""
    
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
    
    def test_sends_notification_to_student(self, orchestrator, mock_supabase, mock_email_service):
        """Test that student notification is sent."""
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
            "student_user_id": "student-123"
        }
        
        orchestrator._notify_student_of_answer(message, "source-session-456")
        
        # Verify email was sent
        mock_email_service.send_student_chat_updated.assert_called_once()
    
    def test_handles_no_share_token(self, orchestrator, mock_supabase, mock_email_service):
        """Test handling when no share token exists."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        
        message = {
            "student_user_id": "student-123"
        }
        
        # Should not raise
        orchestrator._notify_student_of_answer(message, "source-session-456")
        
        # Email should not be sent
        mock_email_service.send_student_chat_updated.assert_not_called()
