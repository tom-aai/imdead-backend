"""
Pytest configuration and shared fixtures for all tests.
"""

import pytest
from unittest.mock import MagicMock, Mock
from PIL import Image
import io


@pytest.fixture
def mock_supabase():
    """
    Mock Supabase client with common methods.
    
    Usage:
        def test_something(mock_supabase):
            mock_supabase.table.return_value.select.return_value.execute.return_value.data = [...]
    """
    mock = MagicMock()
    
    # Setup common return value structures
    mock.table.return_value.select.return_value.execute.return_value.data = []
    mock.table.return_value.insert.return_value.execute.return_value.data = []
    mock.table.return_value.update.return_value.execute.return_value.data = []
    mock.storage.from_.return_value.download.return_value = b''
    
    return mock


@pytest.fixture
def sample_call_data():
    """Sample interview call data from database."""
    return {
        'session_id': 'test-session-123',
        'user_id': 'user-456',
        'user_firstname': 'John',
        'user_lastname': 'Doe',
        'picture_id': 'original-pic-123.jpg',
        'cartoon_id': None,
        'recording_id': 'recording-789',
        'call_type': 'onboarding',
        'created_at': '2024-01-15T10:30:00Z',
        'conversation': {
            'items': [
                {
                    'type': 'message',
                    'role': 'assistant',
                    'content': [{'text': 'Hello! How are you today?'}],
                    'interrupted': False
                },
                {
                    'type': 'message',
                    'role': 'user',
                    'content': [{'text': 'I am doing great, thanks!'}],
                    'interrupted': False,
                    'transcript_confidence': 0.92
                }
            ]
        }
    }


@pytest.fixture
def sample_analysis_data():
    """Sample interview call analysis data (from LLM, without conversation_narrative)."""
    return {
        'session_id': 'test-session-123',
        'user_id': 'user-456',
        'user_firstname': 'John',
        'user_lastname': 'Doe',
        'created_at': '2024-01-15T10:30:00Z',
        'main_topic': 'Artificial Intelligence',
        'myers_briggs_code': 'INTJ',
        'personality': 'Analytical and strategic thinker',
        'expertise_description': 'Expert in machine learning and AI systems',
        'hot_take_1': 'AI will revolutionize healthcare within 5 years'
    }


@pytest.fixture
def sample_user_avatar_image_record():
    """Sample user_avatar_images table record."""
    return {
        'id': 'record-123',
        'user_id': 'user-456',
        'picture_id': 'original-pic-123.jpg',
        'cartoon_id': None,
        'hedra_avatar_id': None,
        'picture_source': 'from_interview_session',
        'session_id': 'test-session-123',
        'created_at': '2024-01-15T10:30:00Z'
    }


@pytest.fixture
def create_test_image():
    """
    Factory fixture to create test images with specific properties.
    
    Usage:
        def test_something(create_test_image):
            image = create_test_image(mode='RGBA', size=(100, 100))
    """
    def _create_image(mode='RGB', size=(800, 600), color=(255, 0, 0)):
        """Create a test PIL Image."""
        return Image.new(mode, size, color)
    
    return _create_image


@pytest.fixture
def create_test_image_bytes():
    """
    Factory fixture to create image bytes for testing downloads.
    
    Usage:
        def test_something(create_test_image_bytes):
            image_bytes = create_test_image_bytes(format='PNG')
    """
    def _create_bytes(mode='RGB', size=(100, 100), format='PNG'):
        """Create test image bytes."""
        img = Image.new(mode, size, (255, 0, 0))
        buffer = io.BytesIO()
        img.save(buffer, format=format)
        return buffer.getvalue()
    
    return _create_bytes


@pytest.fixture
def mock_google_genai_client():
    """Mock Google GenAI client for cartoon generation."""
    mock = MagicMock()
    
    # Mock the response structure
    mock_response = MagicMock()
    mock_response.text = "Image generated successfully"
    
    # Mock the image part
    mock_part = MagicMock()
    mock_part.inline_data = True
    
    # Create a real test image for as_image()
    test_image = Image.new('RGB', (100, 100), (0, 255, 0))
    mock_part.as_image.return_value = test_image
    
    mock_response.parts = [mock_part]
    
    mock.models.generate_content.return_value = mock_response
    
    return mock


@pytest.fixture
def mock_anthropic_client():
    """Mock Anthropic client for transcript processing."""
    mock = MagicMock()
    
    # Mock message response
    mock_content = MagicMock()
    mock_content.text = '{"session_id": "test-123", "main_topic": "AI"}'
    
    mock_message = MagicMock()
    mock_message.content = [mock_content]
    
    mock.messages.create.return_value = mock_message
    
    return mock


@pytest.fixture
def mock_cartesia_client():
    """Mock Cartesia client for voice cloning."""
    mock = MagicMock()
    
    # Mock voice cloning response
    mock_voice = MagicMock()
    mock_voice.id = 'cloned-voice-id-123'
    mock_voice.name = 'user-456'
    
    mock.voices.clone.return_value = mock_voice
    
    return mock


@pytest.fixture
def mock_inworld_response():
    """Mock Inworld API response for voice cloning."""
    return {
        "voice": {
            "voiceId": "inworld-cloned-voice-id-456",
            "displayName": "user-456",
            "name": "workspaces/test-workspace/voices/inworld-cloned-voice-id-456"
        },
        "audioSamplesValidated": [
            {
                "transcription": "Test audio transcription",
                "warnings": [],
                "errors": []
            }
        ]
    }

