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
        
        # Mock the LLM processing
        with patch.object(processor, '_process_with_llm') as mock_llm:
            mock_llm.return_value = {
                'session_id': sample_call_data['session_id'],
                'user_id': sample_call_data['user_id'],
                'user_firstname': sample_call_data['user_firstname'],
                'user_lastname': sample_call_data['user_lastname'],
                'created_at': sample_call_data['created_at'],
                'main_topic': 'AI',
                'conversation_narrative': 'Discussion about AI...',
                'myers_briggs_code': 'INTJ',
                'personality': 'Analytical',
                'expertise_description': 'AI Expert',
                'hot_take_1': 'AI will change everything',
                'social_post_draft': 'Great AI discussion...'
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
                assert call_kwargs['model'] == 'claude-3-5-haiku-20241022'
                assert call_kwargs['max_tokens'] == 4096


class TestUploadToSupabase:
    """Test _upload_to_supabase method."""
    
    def test_successful_upload(self, mock_supabase, sample_analysis_data):
        """Test successful upload to interview_call_analysis table."""
        expected_record = sample_analysis_data.copy()
        expected_record['id'] = 'analysis-123'
        expected_record['hedra_avatar_id'] = None
        expected_record['hot_take_video_1'] = None
        
        mock_supabase.table.return_value.upsert.return_value.execute.return_value.data = [expected_record]
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        result = processor._upload_to_supabase(sample_analysis_data)
        
        assert result['id'] == 'analysis-123'
        assert result['session_id'] == sample_analysis_data['session_id']
        
        # Verify correct table and upsert was called
        mock_supabase.table.assert_called_with("interview_call_analysis")
        mock_supabase.table.return_value.upsert.assert_called_once()
        
        # Verify nullable fields are included
        upload_data = mock_supabase.table.return_value.upsert.call_args.args[0]
        assert upload_data['hedra_avatar_id'] is None
        assert upload_data['hot_take_video_1'] is None
    
    def test_upload_failure(self, mock_supabase, sample_analysis_data):
        """Test error handling when upload fails."""
        mock_supabase.table.return_value.upsert.return_value.execute.return_value.data = None
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        
        with pytest.raises(Exception, match="Failed to upload analysis"):
            processor._upload_to_supabase(sample_analysis_data)
    
    def test_upload_includes_all_fields(self, mock_supabase, sample_analysis_data):
        """Test that all expected fields are included in upload."""
        mock_supabase.table.return_value.upsert.return_value.execute.return_value.data = [{'id': '123'}]
        
        processor = TranscriptProcessor(mock_supabase, anthropic_api_key="test-key")
        processor._upload_to_supabase(sample_analysis_data)
        
        upload_data = mock_supabase.table.return_value.upsert.call_args.args[0]
        
        # Verify all expected fields
        expected_fields = [
            'session_id', 'user_id', 'user_firstname', 'user_lastname', 'created_at',
            'main_topic', 'conversation_narrative', 'myers_briggs_code', 'personality',
            'expertise_description', 'hot_take_1', 'social_post_draft',
            'hedra_avatar_id', 'hot_take_video_1'
        ]
        
        for field in expected_fields:
            assert field in upload_data

