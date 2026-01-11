"""
Tests for ChatMessageExtractor service.
"""

import pytest
from unittest.mock import MagicMock, patch
from services.chat_message_extractor import ChatMessageExtractor


@pytest.fixture
def mock_anthropic_for_extraction():
    """Mock Anthropic client for message extraction."""
    mock = MagicMock()
    
    mock_content = MagicMock()
    mock_content.text = '[]'
    
    mock_message = MagicMock()
    mock_message.content = [mock_content]
    
    mock.messages.create.return_value = mock_message
    
    return mock


@pytest.fixture
def sample_chat_session():
    """Sample chat session data."""
    return {
        "session_id": "chat-session-123",
        "source_session_id": "source-session-456",
        "expert_user_id": "expert-user-id",
        "student_user_id": "student-user-id",
        "conversation": {
            "items": [
                {
                    "role": "assistant",
                    "content": [{"text": "Hello! I'm the avatar for Expert Name."}]
                },
                {
                    "role": "user",
                    "content": [{"text": "Hi, I have a question about machine learning."}]
                },
                {
                    "role": "assistant",
                    "content": [{"text": "I can help with that! What would you like to know?"}]
                }
            ]
        }
    }


@pytest.fixture
def sample_chat_session_with_message_request():
    """Sample chat session where student asked to pass a message."""
    return {
        "session_id": "chat-session-123",
        "source_session_id": "source-session-456",
        "expert_user_id": "expert-user-id",
        "student_user_id": "student-user-id",
        "conversation": {
            "items": [
                {
                    "role": "assistant",
                    "content": [{"text": "Hello! I'm the avatar for Expert Name."}]
                },
                {
                    "role": "user",
                    "content": [{"text": "Can you ask the real expert about quantum computing?"}]
                },
                {
                    "role": "assistant",
                    "content": [{"text": "I don't know about that. Would you like me to pass that question to the real expert?"}]
                },
                {
                    "role": "user",
                    "content": [{"text": "Yes please. I'm Jane from TechCorp, I'm an ML Engineer."}]
                },
                {
                    "role": "assistant",
                    "content": [{"text": "I've noted that for Expert Name. They'll receive your message."}]
                }
            ]
        }
    }


class TestChatMessageExtractor:
    """Test suite for ChatMessageExtractor."""
    
    def test_init_with_api_key(self, mock_supabase):
        """Test initialization with explicit API key."""
        with patch('services.chat_message_extractor.Anthropic') as mock_anthropic_class:
            extractor = ChatMessageExtractor(
                supabase_client=mock_supabase,
                anthropic_api_key="test-api-key"
            )
            
            mock_anthropic_class.assert_called_once_with(api_key="test-api-key")
            assert extractor.supabase == mock_supabase
    
    def test_process_chat_session_no_session_id(self, mock_supabase):
        """Test that empty session_id raises error."""
        with patch('services.chat_message_extractor.Anthropic'):
            extractor = ChatMessageExtractor(
                supabase_client=mock_supabase,
                anthropic_api_key="test-key"
            )
            
            with pytest.raises(ValueError) as exc_info:
                extractor.process_chat_session("")
            
            assert "chat_session_id is required" in str(exc_info.value)
    
    def test_process_chat_session_not_found(self, mock_supabase):
        """Test that missing chat session raises error."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        
        with patch('services.chat_message_extractor.Anthropic'):
            extractor = ChatMessageExtractor(
                supabase_client=mock_supabase,
                anthropic_api_key="test-key"
            )
            
            with pytest.raises(ValueError) as exc_info:
                extractor.process_chat_session("nonexistent-session")
            
            assert "Chat session not found" in str(exc_info.value)
    
    def test_process_chat_session_no_messages(self, mock_supabase, sample_chat_session):
        """Test processing a session with no messages to extract."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            sample_chat_session
        ]
        
        with patch('services.chat_message_extractor.Anthropic') as mock_anthropic_class:
            mock_anthropic = MagicMock()
            mock_anthropic_class.return_value = mock_anthropic
            
            # Mock LLM extraction returning empty array
            mock_content = MagicMock()
            mock_content.text = '[]'
            mock_message = MagicMock()
            mock_message.content = [mock_content]
            mock_anthropic.messages.create.return_value = mock_message
            
            extractor = ChatMessageExtractor(
                supabase_client=mock_supabase,
                anthropic_api_key="test-key"
            )
            
            result = extractor.process_chat_session("chat-session-123")
            
            assert result["status"] == "success"
            assert result["messages_extracted"] == 0
            assert result["messages_inserted"] == 0
    
    def test_process_chat_session_with_messages(
        self, 
        mock_supabase, 
        sample_chat_session_with_message_request
    ):
        """Test processing a session where LLM extracts messages."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            sample_chat_session_with_message_request
        ]
        
        with patch('services.chat_message_extractor.Anthropic') as mock_anthropic_class:
            mock_anthropic = MagicMock()
            mock_anthropic_class.return_value = mock_anthropic
            
            # Mock LLM extraction returning a message
            mock_content = MagicMock()
            mock_content.text = '''[{
                "message_content": "Can you ask the real expert about quantum computing?",
                "message_type": "question",
                "student_name": "Jane",
                "student_company": "TechCorp",
                "student_role": "ML Engineer"
            }]'''
            mock_message = MagicMock()
            mock_message.content = [mock_content]
            mock_anthropic.messages.create.return_value = mock_message
            
            extractor = ChatMessageExtractor(
                supabase_client=mock_supabase,
                anthropic_api_key="test-key"
            )
            
            result = extractor.process_chat_session("chat-session-123")
            
            assert result["status"] == "success"
            assert result["messages_extracted"] == 1


class TestLLMExtraction:
    """Test suite for LLM-based message extraction."""
    
    def test_extract_messages_empty_conversation(self, mock_supabase):
        """Test extraction from empty conversation."""
        with patch('services.chat_message_extractor.Anthropic'):
            extractor = ChatMessageExtractor(
                supabase_client=mock_supabase,
                anthropic_api_key="test-key"
            )
            
            result = extractor._extract_messages_with_llm({})
            
            assert result == []
    
    def test_extract_messages_no_items(self, mock_supabase):
        """Test extraction from conversation with no items."""
        with patch('services.chat_message_extractor.Anthropic'):
            extractor = ChatMessageExtractor(
                supabase_client=mock_supabase,
                anthropic_api_key="test-key"
            )
            
            result = extractor._extract_messages_with_llm({"items": []})
            
            assert result == []
    
    def test_extract_messages_llm_error_returns_empty(self, mock_supabase):
        """Test that LLM errors return empty list instead of crashing."""
        with patch('services.chat_message_extractor.Anthropic') as mock_anthropic_class:
            mock_anthropic = MagicMock()
            mock_anthropic_class.return_value = mock_anthropic
            mock_anthropic.messages.create.side_effect = Exception("API Error")
            
            extractor = ChatMessageExtractor(
                supabase_client=mock_supabase,
                anthropic_api_key="test-key"
            )
            
            conversation = {
                "items": [
                    {"role": "user", "content": [{"text": "Hello"}]}
                ]
            }
            
            result = extractor._extract_messages_with_llm(conversation)
            
            assert result == []
    
    def test_extract_messages_parses_json_correctly(self, mock_supabase):
        """Test that JSON responses are parsed correctly."""
        with patch('services.chat_message_extractor.Anthropic') as mock_anthropic_class:
            mock_anthropic = MagicMock()
            mock_anthropic_class.return_value = mock_anthropic
            
            # Mock LLM response with JSON wrapped in markdown
            mock_content = MagicMock()
            mock_content.text = '''```json
[{
    "message_content": "What about AI?",
    "message_type": "question",
    "student_name": "Test User"
}]
```'''
            mock_message = MagicMock()
            mock_message.content = [mock_content]
            mock_anthropic.messages.create.return_value = mock_message
            
            extractor = ChatMessageExtractor(
                supabase_client=mock_supabase,
                anthropic_api_key="test-key"
            )
            
            conversation = {
                "items": [
                    {"role": "user", "content": [{"text": "What about AI?"}]}
                ]
            }
            
            result = extractor._extract_messages_with_llm(conversation)
            
            assert len(result) == 1
            assert result[0]["message_content"] == "What about AI?"
            assert result[0]["message_type"] == "question"


class TestMessageInsertion:
    """Test suite for message insertion."""
    
    def test_insert_messages_success(self, mock_supabase):
        """Test that messages are inserted correctly."""
        with patch('services.chat_message_extractor.Anthropic'):
            extractor = ChatMessageExtractor(
                supabase_client=mock_supabase,
                anthropic_api_key="test-key"
            )
            
            messages = [
                {
                    "message_content": "Question 1",
                    "message_type": "question",
                    "student_name": "Jane",
                    "student_company": "TechCorp",
                    "student_role": "Engineer"
                }
            ]
            
            count = extractor._insert_messages(
                messages=messages,
                source_session_id="source-123",
                chat_session_id="chat-456",
                expert_user_id="expert-789",
                student_user_id="student-012"
            )
            
            assert count == 1
            mock_supabase.table.assert_called_with("student_messages")
