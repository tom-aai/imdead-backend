"""
Unit tests for the resume session workflow.

Tests cover:
- TranscriptProcessor append_to_transcript
- End-to-end resume workflow with conversation merging
"""

import pytest
import json
from unittest.mock import MagicMock, patch


class TestResumeSessionFlow:
    """Integration-style tests for the complete resume session flow."""
    
    def test_complete_resume_flow(self, mock_supabase):
        """Test the complete flow from original session to merged conversation."""
        # 1. Simulate the original session data
        original_narrative = {
            "items": [
                {"role": "assistant", "content": ["Hello!"]},
                {"role": "user", "content": ["Hi there!"]}
            ]
        }
        
        # 2. Simulate the new session conversation
        new_conversation = {
            "items": [
                {"type": "message", "role": "assistant", "content": ["Welcome back!"]},
                {"type": "message", "role": "user", "content": ["Thanks!"]}
            ]
        }
        
        # Mock Supabase responses
        mock_original_response = MagicMock()
        mock_original_response.data = [{
            "session_id": "original-session-123",
            "transcript": json.dumps(original_narrative),
            "user_id": "user-456"
        }]
        
        mock_new_response = MagicMock()
        mock_new_response.data = [{
            "session_id": "new-session-789",
            "user_id": "user-456",
            "conversation": new_conversation
        }]
        
        mock_update_response = MagicMock()
        mock_update_response.data = [{"session_id": "original-session-123"}]
        
        # Configure mock chain
        def select_side_effect(columns):
            mock_select = MagicMock()
            mock_eq = MagicMock()
            if "transcript" in columns:
                mock_eq.execute.return_value = mock_original_response
            else:
                mock_eq.execute.return_value = mock_new_response
            mock_select.eq.return_value = mock_eq
            return mock_select
        
        mock_table = MagicMock()
        mock_table.select.side_effect = select_side_effect
        mock_table.update.return_value.eq.return_value.execute.return_value = mock_update_response
        mock_supabase.table.return_value = mock_table
        
        # 3. Process through transcript processor
        from services.transcript_processor import TranscriptProcessor
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        
        result = processor.append_to_transcript(
            original_session_id="original-session-123",
            new_session_id="new-session-789"
        )
        
        # 4. Verify the result
        assert result["status"] == "success"
        assert result["items_appended"] == 2
        assert result["total_items"] == 4
        
        # 5. Verify the update was called with merged content
        update_calls = mock_table.update.call_args_list
        narrative_update = update_calls[0][0][0]
        merged_narrative = json.loads(narrative_update["transcript"])
        
        assert len(merged_narrative["items"]) == 4
        assert merged_narrative["items"][0]["content"] == ["Hello!"]  # Original first
        assert merged_narrative["items"][2]["content"] == ["Welcome back!"]  # New third
    
    def test_resume_preserves_order(self, mock_supabase):
        """Test that original items come before new items."""
        original_narrative = {
            "items": [
                {"role": "assistant", "content": ["Original 1"]},
                {"role": "user", "content": ["Original 2"]}
            ]
        }
        
        new_conversation = {
            "items": [
                {"type": "message", "role": "assistant", "content": ["New 1"]},
                {"type": "message", "role": "user", "content": ["New 2"]}
            ]
        }
        
        mock_original_response = MagicMock()
        mock_original_response.data = [{
            "session_id": "original-123",
            "transcript": json.dumps(original_narrative),
            "user_id": "user-1"
        }]
        
        mock_new_response = MagicMock()
        mock_new_response.data = [{
            "session_id": "new-789",
            "user_id": "user-1",
            "conversation": new_conversation
        }]
        
        mock_update_response = MagicMock()
        mock_update_response.data = [{"session_id": "original-123"}]
        
        def select_side_effect(columns):
            mock_select = MagicMock()
            mock_eq = MagicMock()
            if "transcript" in columns:
                mock_eq.execute.return_value = mock_original_response
            else:
                mock_eq.execute.return_value = mock_new_response
            mock_select.eq.return_value = mock_eq
            return mock_select
        
        mock_table = MagicMock()
        mock_table.select.side_effect = select_side_effect
        mock_table.update.return_value.eq.return_value.execute.return_value = mock_update_response
        mock_supabase.table.return_value = mock_table
        
        from services.transcript_processor import TranscriptProcessor
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        
        result = processor.append_to_transcript(
            original_session_id="original-123",
            new_session_id="new-789"
        )
        
        # Verify order in merged narrative
        update_calls = mock_table.update.call_args_list
        narrative_update = update_calls[0][0][0]
        merged_narrative = json.loads(narrative_update["transcript"])
        
        assert merged_narrative["items"][0]["content"] == ["Original 1"]
        assert merged_narrative["items"][1]["content"] == ["Original 2"]
        assert merged_narrative["items"][2]["content"] == ["New 1"]
        assert merged_narrative["items"][3]["content"] == ["New 2"]
    
    def test_resume_filters_new_items(self, mock_supabase):
        """Test that new items are filtered to essential fields only."""
        original_narrative = {"items": []}
        
        # New conversation with extra fields that should be filtered
        new_conversation = {
            "items": [
                {
                    "id": "msg-1",
                    "type": "message",
                    "role": "assistant",
                    "content": ["Hello!"],
                    "metrics": {"latency": 0.5},
                    "extra": {"data": "value"}
                }
            ]
        }
        
        mock_original_response = MagicMock()
        mock_original_response.data = [{
            "session_id": "original-123",
            "transcript": json.dumps(original_narrative),
            "user_id": "user-1"
        }]
        
        mock_new_response = MagicMock()
        mock_new_response.data = [{
            "session_id": "new-789",
            "user_id": "user-1",
            "conversation": new_conversation
        }]
        
        mock_update_response = MagicMock()
        mock_update_response.data = [{"session_id": "original-123"}]
        
        def select_side_effect(columns):
            mock_select = MagicMock()
            mock_eq = MagicMock()
            if "transcript" in columns:
                mock_eq.execute.return_value = mock_original_response
            else:
                mock_eq.execute.return_value = mock_new_response
            mock_select.eq.return_value = mock_eq
            return mock_select
        
        mock_table = MagicMock()
        mock_table.select.side_effect = select_side_effect
        mock_table.update.return_value.eq.return_value.execute.return_value = mock_update_response
        mock_supabase.table.return_value = mock_table
        
        from services.transcript_processor import TranscriptProcessor
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        
        processor.append_to_transcript(
            original_session_id="original-123",
            new_session_id="new-789"
        )
        
        # Verify filtered fields
        update_calls = mock_table.update.call_args_list
        narrative_update = update_calls[0][0][0]
        merged_narrative = json.loads(narrative_update["transcript"])
        
        item = merged_narrative["items"][0]
        assert "id" not in item
        assert "type" not in item
        assert "metrics" not in item
        assert "extra" not in item
        assert item["role"] == "assistant"
        assert item["content"] == ["Hello!"]
    
    def test_resume_skips_non_message_items(self, mock_supabase):
        """Test that non-message items are not included in merged narrative."""
        original_narrative = {"items": []}
        
        new_conversation = {
            "items": [
                {"type": "agent_handoff", "new_agent": "interview"},
                {"type": "message", "role": "assistant", "content": ["Hello!"]},
                {"type": "function_call", "function": "end_call"}
            ]
        }
        
        mock_original_response = MagicMock()
        mock_original_response.data = [{
            "session_id": "original-123",
            "transcript": json.dumps(original_narrative),
            "user_id": "user-1"
        }]
        
        mock_new_response = MagicMock()
        mock_new_response.data = [{
            "session_id": "new-789",
            "user_id": "user-1",
            "conversation": new_conversation
        }]
        
        mock_update_response = MagicMock()
        mock_update_response.data = [{"session_id": "original-123"}]
        
        def select_side_effect(columns):
            mock_select = MagicMock()
            mock_eq = MagicMock()
            if "transcript" in columns:
                mock_eq.execute.return_value = mock_original_response
            else:
                mock_eq.execute.return_value = mock_new_response
            mock_select.eq.return_value = mock_eq
            return mock_select
        
        mock_table = MagicMock()
        mock_table.select.side_effect = select_side_effect
        mock_table.update.return_value.eq.return_value.execute.return_value = mock_update_response
        mock_supabase.table.return_value = mock_table
        
        from services.transcript_processor import TranscriptProcessor
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        
        result = processor.append_to_transcript(
            original_session_id="original-123",
            new_session_id="new-789"
        )
        
        # Only 1 message item should be appended (not agent_handoff or function_call)
        assert result["items_appended"] == 1
        assert result["total_items"] == 1
    
    def test_resume_updates_updated_at_timestamp(self, mock_supabase):
        """Test that the updated_at field is set on the merged record."""
        original_narrative = {"items": []}
        new_conversation = {
            "items": [{"type": "message", "role": "user", "content": ["Test"]}]
        }
        
        mock_original_response = MagicMock()
        mock_original_response.data = [{
            "session_id": "original-123",
            "transcript": json.dumps(original_narrative),
            "user_id": "user-1"
        }]
        
        mock_new_response = MagicMock()
        mock_new_response.data = [{
            "session_id": "new-789",
            "user_id": "user-1",
            "conversation": new_conversation
        }]
        
        mock_update_response = MagicMock()
        mock_update_response.data = [{"session_id": "original-123"}]
        
        def select_side_effect(columns):
            mock_select = MagicMock()
            mock_eq = MagicMock()
            if "transcript" in columns:
                mock_eq.execute.return_value = mock_original_response
            else:
                mock_eq.execute.return_value = mock_new_response
            mock_select.eq.return_value = mock_eq
            return mock_select
        
        mock_table = MagicMock()
        mock_table.select.side_effect = select_side_effect
        mock_table.update.return_value.eq.return_value.execute.return_value = mock_update_response
        mock_supabase.table.return_value = mock_table
        
        from services.transcript_processor import TranscriptProcessor
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        
        processor.append_to_transcript(
            original_session_id="original-123",
            new_session_id="new-789"
        )
        
        # Verify updated_at is set in the update call
        update_calls = mock_table.update.call_args_list
        narrative_update = update_calls[0][0][0]
        assert "updated_at" in narrative_update
        assert narrative_update["updated_at"] == "now()"
