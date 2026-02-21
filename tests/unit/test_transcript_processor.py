"""
Unit tests for TranscriptProcessor service.

Tests cover:
- Transcript fetching and processing
- Metadata and transcript formatting
- Claude API integration for analysis
- JSON parsing (including markdown code blocks)
- Database uploads to interview_call_analysis table
- Error handling and edge cases
"""

import pytest
import json
from unittest.mock import MagicMock, Mock, patch
from anthropic import Anthropic

from services.transcript_processor import TranscriptProcessor


class TestTranscriptProcessorInit:
    """Test TranscriptProcessor initialization."""
    
    def test_init_with_api_key(self, mock_supabase):
        """Test initialization with API key provided."""
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-api-key")
        
        assert processor.supabase == mock_supabase
        assert processor.anthropic is not None
    
    def test_init_without_api_key(self, mock_supabase):
        """Test initialization uses env var when no key provided."""
        with patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'env-api-key'}):
            processor = TranscriptProcessor(mock_supabase, anthropic_api_key=None)
            assert processor.anthropic is not None


class TestProcessTranscript:
    """Test the main process_transcript method."""
    
    def test_successful_processing(self, mock_supabase, sample_call_data):
        """Test successful end-to-end transcript processing."""
        # Mock Supabase fetch
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [sample_call_data]
        
        # Mock Supabase upload
        uploaded_record = {
            'id': 'analysis-123',
            'session_id': sample_call_data['session_id'],
            'main_topic': 'AI'
        }
        mock_supabase.table.return_value.upsert.return_value.execute.return_value.data = [uploaded_record]
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        
        # Mock the LLM processing (includes conversation_summary for the summary)
        with patch.object(processor, '_process_with_llm') as mock_llm:
            mock_llm.return_value = {
                'session_id': sample_call_data['session_id'],
                'user_id': sample_call_data['user_id'],
                'user_firstname': sample_call_data['user_firstname'],
                'user_lastname': sample_call_data['user_lastname'],
                'created_at': sample_call_data['created_at'],
                'main_topic': 'AI',
                'myers_briggs_code': 'INTJ',
                'personality': 'Analytical',
                'expertise_description': 'AI Expert',
                'conversation_summary': 'The user discussed AI topics in depth.',
                'hot_take_1': 'AI will change everything'
            }
            
            result = processor.process_transcript(sample_call_data['session_id'])
            
            assert result['status'] == 'success'
            assert result['session_id'] == sample_call_data['session_id']
            assert result['analysis_id'] == 'analysis-123'
            assert 'analysis' in result
    
    def test_missing_session_id(self, mock_supabase):
        """Test error when session_id is missing."""
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        
        with pytest.raises(ValueError, match="session_id is required"):
            processor.process_transcript(None)
    
    def test_call_not_found(self, mock_supabase):
        """Test error when call not found in database."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        
        with pytest.raises(ValueError, match="Call not found"):
            processor.process_transcript('nonexistent-session')
    
    def test_empty_conversation(self, mock_supabase, sample_call_data):
        """Test handling of empty conversation content."""
        call_data_no_conversation = sample_call_data.copy()
        call_data_no_conversation['conversation'] = {'items': []}
        
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [call_data_no_conversation]
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        result = processor.process_transcript('test-session')
        
        assert result['status'] == 'error'
        assert 'No conversation content' in result['message']
    
    def test_supabase_not_initialized(self, sample_call_data):
        """Test error when Supabase client not initialized."""
        processor = TranscriptProcessor(None, anthropic_api_key="test-key")
        
        with pytest.raises(Exception, match="Supabase client not initialized"):
            processor.process_transcript('test-session')


class TestFormatMetadata:
    """Test _format_metadata method."""
    
    def test_format_metadata(self, mock_supabase, sample_call_data):
        """Test metadata formatting."""
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        result = processor._format_metadata(sample_call_data)
        
        assert 'Session ID: test-session-123' in result
        assert 'User ID: user-456' in result
        assert 'User First Name: John' in result
        assert 'User Last Name: Doe' in result
        assert '2024-01-15' in result
    
    def test_format_metadata_missing_fields(self, mock_supabase):
        """Test metadata formatting with missing fields."""
        call_data = {'session_id': 'test-123'}
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        result = processor._format_metadata(call_data)
        
        assert 'Session ID: test-123' in result
        assert 'N/A' in result  # Missing fields show as N/A


class TestFormatTranscript:
    """Test _format_transcript method."""
    
    def test_format_simple_conversation(self, mock_supabase):
        """Test formatting of simple conversation."""
        conversation = {
            'items': [
                {'role': 'assistant', 'content': 'Hello!'},
                {'role': 'user', 'content': 'Hi there!'}
            ]
        }
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        result = processor._format_transcript(conversation)
        
        assert '[ASSISTANT]: Hello!' in result
        assert '[USER]: Hi there!' in result
    
    def test_format_conversation_with_list_content(self, mock_supabase):
        """Test formatting when content is a list of dicts."""
        conversation = {
            'items': [
                {
                    'role': 'assistant',
                    'content': [
                        {'text': 'First message'},
                        {'text': 'Second message'}
                    ]
                },
                {
                    'role': 'user',
                    'content': [
                        {'transcript': 'User response'}
                    ]
                }
            ]
        }
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        result = processor._format_transcript(conversation)
        
        assert '[ASSISTANT]: First message' in result
        assert '[ASSISTANT]: Second message' in result
        assert '[USER]: User response' in result
    
    def test_format_empty_conversation(self, mock_supabase):
        """Test formatting of empty conversation."""
        conversation = {'items': []}
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        result = processor._format_transcript(conversation)
        
        assert result == ''
    
    def test_format_conversation_mixed_content_types(self, mock_supabase):
        """Test formatting with mixed content types."""
        conversation = {
            'items': [
                {'role': 'assistant', 'content': 'String content'},
                {'role': 'user', 'content': [{'text': 'Dict content'}]},
                {'role': 'assistant', 'content': ['List', 'of', 'strings']}
            ]
        }
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        result = processor._format_transcript(conversation)
        
        assert '[ASSISTANT]: String content' in result
        assert '[USER]: Dict content' in result
        assert '[ASSISTANT]: List' in result


class TestFilterConversationItems:
    """Test _filter_conversation_items method."""
    
    def test_filters_to_essential_fields(self, mock_supabase):
        """Test that only essential fields are kept."""
        conversation = {
            'items': [
                {
                    'id': 'item-123',
                    'type': 'message',
                    'role': 'assistant',
                    'content': ['Hello!'],
                    'extra': {},
                    'metrics': {'llm_node_ttft': 0.5},
                    'interrupted': False
                },
                {
                    'id': 'item-456',
                    'type': 'message',
                    'role': 'user',
                    'content': ['Hi there!'],
                    'extra': {},
                    'metrics': {'end_of_turn_delay': 0.6},
                    'interrupted': False,
                    'transcript_confidence': 0.95
                }
            ]
        }
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        result = processor._filter_conversation_items(conversation)
        
        assert len(result['items']) == 2
        
        # Check first item - should not have id, type, extra, metrics
        assert 'id' not in result['items'][0]
        assert 'type' not in result['items'][0]
        assert 'extra' not in result['items'][0]
        assert 'metrics' not in result['items'][0]
        assert result['items'][0]['role'] == 'assistant'
        assert result['items'][0]['content'] == ['Hello!']
        assert result['items'][0]['interrupted'] == False
        
        # Check second item - should have transcript_confidence
        assert result['items'][1]['role'] == 'user'
        assert result['items'][1]['content'] == ['Hi there!']
        assert result['items'][1]['transcript_confidence'] == 0.95
    
    def test_skips_non_message_items(self, mock_supabase):
        """Test that non-message items like agent_handoff are skipped."""
        conversation = {
            'items': [
                {
                    'id': 'item-001',
                    'type': 'agent_handoff',
                    'new_agent_id': 'interview_agent'
                },
                {
                    'id': 'item-002',
                    'type': 'message',
                    'role': 'assistant',
                    'content': ['Hello!']
                },
                {
                    'id': 'item-003',
                    'type': 'function_call',
                    'function': 'some_function'
                }
            ]
        }
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        result = processor._filter_conversation_items(conversation)
        
        # Only the message item should be included
        assert len(result['items']) == 1
        assert result['items'][0]['role'] == 'assistant'
        assert result['items'][0]['content'] == ['Hello!']
    
    def test_handles_empty_conversation(self, mock_supabase):
        """Test handling of empty conversation."""
        conversation = {'items': []}
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        result = processor._filter_conversation_items(conversation)
        
        assert result == {'items': []}
    
    def test_handles_missing_optional_fields(self, mock_supabase):
        """Test that optional fields are only included when present."""
        conversation = {
            'items': [
                {
                    'type': 'message',
                    'role': 'assistant',
                    'content': ['Hello!']
                    # No interrupted or transcript_confidence
                }
            ]
        }
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        result = processor._filter_conversation_items(conversation)
        
        assert len(result['items']) == 1
        assert 'interrupted' not in result['items'][0]
        assert 'transcript_confidence' not in result['items'][0]
        assert result['items'][0]['role'] == 'assistant'
        assert result['items'][0]['content'] == ['Hello!']
    
    def test_preserves_interrupted_flag(self, mock_supabase):
        """Test that interrupted flag is preserved when present."""
        conversation = {
            'items': [
                {
                    'type': 'message',
                    'role': 'assistant',
                    'content': ['I was saying...'],
                    'interrupted': True
                }
            ]
        }
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        result = processor._filter_conversation_items(conversation)
        
        assert result['items'][0]['interrupted'] == True


class TestProcessWithLLM:
    """Test _process_with_llm method."""
    
    def test_successful_llm_processing(self, mock_supabase):
        """Test successful Claude API call and JSON parsing."""
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        
        # Mock Claude response
        mock_content = MagicMock()
        mock_content.text = json.dumps({
            'session_id': 'test-123',
            'main_topic': 'AI',
            'hot_take_1': 'AI is transformative'
        })
        
        mock_message = MagicMock()
        mock_message.content = [mock_content]
        
        with patch.object(processor.anthropic.messages, 'create', return_value=mock_message):
            result = processor._process_with_llm('metadata', 'transcript')
            
            assert result['session_id'] == 'test-123'
            assert result['main_topic'] == 'AI'
            assert result['hot_take_1'] == 'AI is transformative'
    
    def test_llm_processing_with_markdown_json_block(self, mock_supabase):
        """Test parsing JSON from markdown code block."""
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        
        # Mock Claude response with markdown
        mock_content = MagicMock()
        mock_content.text = '''Here's the analysis:
```json
{
    "session_id": "test-123",
    "main_topic": "AI"
}
```
'''
        
        mock_message = MagicMock()
        mock_message.content = [mock_content]
        
        with patch.object(processor.anthropic.messages, 'create', return_value=mock_message):
            result = processor._process_with_llm('metadata', 'transcript')
            
            assert result['session_id'] == 'test-123'
            assert result['main_topic'] == 'AI'
    
    def test_llm_processing_with_generic_code_block(self, mock_supabase):
        """Test parsing JSON from generic code block."""
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        
        mock_content = MagicMock()
        mock_content.text = '''```
{"session_id": "test-123", "main_topic": "AI"}
```'''
        
        mock_message = MagicMock()
        mock_message.content = [mock_content]
        
        with patch.object(processor.anthropic.messages, 'create', return_value=mock_message):
            result = processor._process_with_llm('metadata', 'transcript')
            
            assert result['session_id'] == 'test-123'
    
    def test_llm_processing_uses_correct_model(self, mock_supabase):
        """Test that correct Claude model is used."""
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        
        mock_content = MagicMock()
        mock_content.text = '{"session_id": "test-123"}'
        mock_message = MagicMock()
        mock_message.content = [mock_content]
        
        with patch.object(processor.anthropic.messages, 'create', return_value=mock_message) as mock_create:
            with patch('services.transcript_processor.load_prompt', return_value='Test prompt {metadata} {transcript}'):
                processor._process_with_llm('metadata', 'transcript')
                
                # Verify correct model and parameters
                mock_create.assert_called_once()
                call_kwargs = mock_create.call_args.kwargs
                assert call_kwargs['model'] == 'claude-haiku-4-5'
                assert call_kwargs['max_tokens'] == 4096
    
    def test_llm_processing_returns_conversation_summary(self, mock_supabase):
        """Test that LLM output includes conversation_summary field."""
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        
        # Mock Claude response with conversation_summary
        mock_content = MagicMock()
        mock_content.text = json.dumps({
            'session_id': 'test-123',
            'main_topic': 'AI',
            'conversation_summary': 'The user discussed their expertise in artificial intelligence. They explained their background in machine learning and their current focus on healthcare AI applications.',
            'hot_take_1': 'AI is transformative'
        })
        
        mock_message = MagicMock()
        mock_message.content = [mock_content]
        
        with patch.object(processor.anthropic.messages, 'create', return_value=mock_message):
            result = processor._process_with_llm('metadata', 'transcript')
            
            assert 'conversation_summary' in result
            assert 'The user discussed' in result['conversation_summary']
            assert result['session_id'] == 'test-123'


class TestUploadToSupabase:
    """Test _upload_to_supabase method."""
    
    def test_successful_upload(self, mock_supabase, sample_analysis_data, sample_call_data):
        """Test successful upload to interview_call_analysis table."""
        expected_record = sample_analysis_data.copy()
        expected_record['id'] = 'analysis-123'
        expected_record['hedra_avatar_id'] = None
        expected_record['hot_take_video_1'] = None
        
        mock_supabase.table.return_value.upsert.return_value.execute.return_value.data = [expected_record]
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        result = processor._upload_to_supabase(sample_analysis_data, sample_call_data)
        
        assert result['id'] == 'analysis-123'
        assert result['session_id'] == sample_analysis_data['session_id']
        
        # Verify correct table and upsert was called
        mock_supabase.table.assert_called_with("interview_call_analysis")
        mock_supabase.table.return_value.upsert.assert_called_once()
        
        # Verify nullable fields are included
        upload_data = mock_supabase.table.return_value.upsert.call_args.args[0]
        assert upload_data['hedra_avatar_id'] is None
        assert upload_data['hot_take_video_1'] is None
    
    def test_upload_failure(self, mock_supabase, sample_analysis_data, sample_call_data):
        """Test error handling when upload fails."""
        mock_supabase.table.return_value.upsert.return_value.execute.return_value.data = None
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        
        with pytest.raises(Exception, match="Failed to upload analysis"):
            processor._upload_to_supabase(sample_analysis_data, sample_call_data)
    
    def test_upload_includes_all_fields(self, mock_supabase, sample_analysis_data, sample_call_data):
        """Test that all expected fields are included in upload."""
        mock_supabase.table.return_value.upsert.return_value.execute.return_value.data = [{'id': '123'}]
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        processor._upload_to_supabase(sample_analysis_data, sample_call_data)
        
        upload_data = mock_supabase.table.return_value.upsert.call_args.args[0]
        
        # Verify all expected fields
        expected_fields = [
            'session_id', 'user_id', 'user_firstname', 'user_lastname', 'created_at',
            'main_topic', 'transcript', 'conversation_summary', 'myers_briggs_code', 'personality',
            'expertise_description', 'hot_take_1',
            'hedra_avatar_id', 'hot_take_video_1'
        ]
        
        for field in expected_fields:
            assert field in upload_data
    
    def test_upload_stores_filtered_conversation_in_transcript(self, mock_supabase, sample_analysis_data, sample_call_data):
        """Test that transcript contains filtered conversation JSON."""
        mock_supabase.table.return_value.upsert.return_value.execute.return_value.data = [{'id': '123'}]
        
        # Add message type to sample_call_data items for filtering
        call_data_with_types = sample_call_data.copy()
        call_data_with_types['conversation'] = {
            'items': [
                {
                    'id': 'item-1',
                    'type': 'message',
                    'role': 'assistant',
                    'content': [{'text': 'Hello!'}],
                    'metrics': {'llm_node_ttft': 0.5}
                },
                {
                    'id': 'item-2',
                    'type': 'message',
                    'role': 'user',
                    'content': [{'text': 'Hi!'}],
                    'transcript_confidence': 0.9
                }
            ]
        }
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        processor._upload_to_supabase(sample_analysis_data, call_data_with_types)
        
        upload_data = mock_supabase.table.return_value.upsert.call_args.args[0]
        
        # transcript should be a JSON string containing filtered conversation
        import json
        conversation = json.loads(upload_data['transcript'])
        
        # Should have filtered items
        assert 'items' in conversation
        assert len(conversation['items']) == 2
        
        # Should not contain metrics, id, type
        assert 'metrics' not in conversation['items'][0]
        assert 'id' not in conversation['items'][0]
        assert 'type' not in conversation['items'][0]
        
        # Should contain role and content
        assert conversation['items'][0]['role'] == 'assistant'
        assert conversation['items'][1]['role'] == 'user'
        assert conversation['items'][1]['transcript_confidence'] == 0.9
    
    def test_upload_stores_summary_in_conversation_summary(self, mock_supabase, sample_analysis_data, sample_call_data):
        """Test that conversation_summary contains the LLM-generated summary."""
        mock_supabase.table.return_value.upsert.return_value.execute.return_value.data = [{'id': '123'}]
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        processor._upload_to_supabase(sample_analysis_data, sample_call_data)
        
        upload_data = mock_supabase.table.return_value.upsert.call_args.args[0]
        
        # conversation_summary should contain the summary from analysis_data
        assert upload_data['conversation_summary'] == sample_analysis_data['conversation_summary']
        # It should be a plain string, not JSON
        assert 'The user discussed' in upload_data['conversation_summary']


class TestAppendToTranscript:
    """Test append_to_transcript method for resume sessions."""
    
    def test_successful_append(self, mock_supabase):
        """Test successfully appending new conversation to existing session."""
        # Mock original session with existing transcript
        original_transcript = {
            "items": [
                {"role": "assistant", "content": ["Hello!"], "interrupted": False},
                {"role": "user", "content": ["Hi there!"], "transcript_confidence": 0.95}
            ]
        }
        mock_original_response = MagicMock()
        mock_original_response.data = [{
            "session_id": "original-session-123",
            "transcript": json.dumps(original_transcript),
            "user_id": "user-456"
        }]
        
        # Mock new session with new conversation
        new_conversation = {
            "items": [
                {
                    "id": "item-new-1",
                    "type": "message",
                    "role": "assistant",
                    "content": ["Welcome back!"],
                    "metrics": {"llm_node_ttft": 0.3}
                },
                {
                    "id": "item-new-2",
                    "type": "message",
                    "role": "user",
                    "content": ["Thanks!"],
                    "transcript_confidence": 0.92
                }
            ]
        }
        mock_new_response = MagicMock()
        mock_new_response.data = [{
            "session_id": "new-session-789",
            "user_id": "user-456",
            "conversation": new_conversation
        }]
        
        # Mock update response
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
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        result = processor.append_to_transcript(
            original_session_id="original-session-123",
            new_session_id="new-session-789"
        )
        
        assert result["status"] == "success"
        assert result["original_session_id"] == "original-session-123"
        assert result["new_session_id"] == "new-session-789"
        assert result["items_appended"] == 2  # Two message items from new session
        assert result["total_items"] == 4  # 2 original + 2 new
    
    def test_original_session_not_found(self, mock_supabase):
        """Test error when original session is not found."""
        mock_response = MagicMock()
        mock_response.data = []
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_response
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        
        with pytest.raises(ValueError, match="Original session not found"):
            processor.append_to_transcript(
                original_session_id="nonexistent-session",
                new_session_id="new-session-789"
            )
    
    def test_new_session_not_found(self, mock_supabase):
        """Test error when new session is not found."""
        # Mock original session exists
        mock_original_response = MagicMock()
        mock_original_response.data = [{
            "session_id": "original-session-123",
            "transcript": json.dumps({"items": []}),
            "user_id": "user-456"
        }]
        
        # Mock new session doesn't exist
        mock_new_response = MagicMock()
        mock_new_response.data = []
        
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
        
        mock_supabase.table.return_value.select.side_effect = select_side_effect
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        
        with pytest.raises(ValueError, match="New session not found"):
            processor.append_to_transcript(
                original_session_id="original-session-123",
                new_session_id="nonexistent-session"
            )
    
    def test_user_mismatch_raises_error(self, mock_supabase):
        """Test error when sessions belong to different users."""
        # Mock original session with user-1
        mock_original_response = MagicMock()
        mock_original_response.data = [{
            "session_id": "original-session-123",
            "transcript": json.dumps({"items": []}),
            "user_id": "user-1"
        }]
        
        # Mock new session with different user-2
        mock_new_response = MagicMock()
        mock_new_response.data = [{
            "session_id": "new-session-789",
            "user_id": "user-2",  # Different user!
            "conversation": {"items": []}
        }]
        
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
        
        mock_supabase.table.return_value.select.side_effect = select_side_effect
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        
        with pytest.raises(ValueError, match="Session ownership mismatch"):
            processor.append_to_transcript(
                original_session_id="original-session-123",
                new_session_id="new-session-789"
            )
    
    def test_filters_new_conversation_items(self, mock_supabase):
        """Test that new conversation items are filtered before appending."""
        # Mock original session
        mock_original_response = MagicMock()
        mock_original_response.data = [{
            "session_id": "original-session-123",
            "transcript": json.dumps({"items": []}),
            "user_id": "user-456"
        }]
        
        # Mock new session with items that should be filtered
        new_conversation = {
            "items": [
                {
                    "id": "item-1",  # Should be removed
                    "type": "message",
                    "role": "assistant",
                    "content": ["Hello again!"],
                    "metrics": {"llm_node_ttft": 0.3},  # Should be removed
                    "extra": {},  # Should be removed
                    "interrupted": True
                },
                {
                    "id": "item-2",  # Should be removed
                    "type": "agent_handoff",  # Non-message type - should be skipped entirely
                    "new_agent_id": "interview_agent"
                }
            ]
        }
        mock_new_response = MagicMock()
        mock_new_response.data = [{
            "session_id": "new-session-789",
            "user_id": "user-456",
            "conversation": new_conversation
        }]
        
        # Mock update to capture the merged transcript
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
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        result = processor.append_to_transcript(
            original_session_id="original-session-123",
            new_session_id="new-session-789"
        )
        
        # Only 1 item should be appended (the message, not the agent_handoff)
        assert result["items_appended"] == 1
        
        # Verify update was called with filtered content
        # Note: There are two update calls - one for transcript, one for resumed_from
        # Get the first one which contains the transcript update
        update_calls = mock_table.update.call_args_list
        transcript_update_call = update_calls[0]  # First update is the transcript
        update_args = transcript_update_call[0][0]  # First positional arg
        updated_transcript = json.loads(update_args["transcript"])
        
        # Should only have 1 item (the message, not agent_handoff)
        assert len(updated_transcript["items"]) == 1
        
        # Filtered item should not have id, metrics, extra
        filtered_item = updated_transcript["items"][0]
        assert "id" not in filtered_item
        assert "metrics" not in filtered_item
        assert "extra" not in filtered_item
        assert "type" not in filtered_item
        
        # Should have essential fields
        assert filtered_item["role"] == "assistant"
        assert filtered_item["content"] == ["Hello again!"]
        assert filtered_item["interrupted"] == True
    
    def test_handles_empty_original_transcript(self, mock_supabase):
        """Test appending when original transcript is empty or missing."""
        # Mock original session with empty/null transcript
        mock_original_response = MagicMock()
        mock_original_response.data = [{
            "session_id": "original-session-123",
            "transcript": None,  # Empty transcript
            "user_id": "user-456"
        }]
        
        # Mock new session
        new_conversation = {
            "items": [
                {"type": "message", "role": "user", "content": ["First message!"]}
            ]
        }
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
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        result = processor.append_to_transcript(
            original_session_id="original-session-123",
            new_session_id="new-session-789"
        )
        
        # Should work even with empty original
        assert result["status"] == "success"
        assert result["items_appended"] == 1
        assert result["total_items"] == 1
    

