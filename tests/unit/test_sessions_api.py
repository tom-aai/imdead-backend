"""
Unit tests for Sessions API endpoint (PATCH /sessions/<session_id>).

Tests cover:
- Authentication via Supabase JWT token
- Authorization (session ownership verification)
- Input validation (main_topic field)
- Successful session updates
- Error handling and edge cases
"""

import pytest
from unittest.mock import MagicMock, patch
import json


class TestUpdateSessionEndpoint:
    """Test the PATCH /sessions/<session_id> endpoint."""
    
    @pytest.fixture
    def app(self):
        """Create and configure a Flask test app."""
        # Import app here to ensure fresh state for each test
        from app import app
        app.config['TESTING'] = True
        return app
    
    @pytest.fixture
    def client(self, app):
        """Create a test client."""
        return app.test_client()
    
    @pytest.fixture
    def mock_verify_user(self):
        """Mock the _verify_supabase_user function."""
        with patch('app._verify_supabase_user') as mock:
            mock.return_value = {"id": "user-123", "email": "test@example.com"}
            yield mock
    
    @pytest.fixture
    def mock_supabase_client(self):
        """Mock the supabase global client."""
        with patch('app.supabase') as mock:
            yield mock


class TestAuthentication(TestUpdateSessionEndpoint):
    """Test authentication scenarios."""
    
    def test_unauthorized_no_auth_header(self, client):
        """Test that request without Authorization header returns 401."""
        response = client.patch(
            '/sessions/session-123',
            data=json.dumps({'main_topic': 'New Title'}),
            content_type='application/json'
        )
        
        assert response.status_code == 401
        data = json.loads(response.data)
        assert data['error'] == 'Unauthorized'
    
    def test_unauthorized_invalid_token(self, client):
        """Test that request with invalid token returns 401."""
        with patch('app._verify_supabase_user', return_value=None):
            response = client.patch(
                '/sessions/session-123',
                data=json.dumps({'main_topic': 'New Title'}),
                content_type='application/json',
                headers={'Authorization': 'Bearer invalid-token'}
            )
            
            assert response.status_code == 401
            data = json.loads(response.data)
            assert data['error'] == 'Unauthorized'
    
    def test_authorized_valid_token(self, client, mock_verify_user, mock_supabase_client):
        """Test that request with valid token proceeds to authorization check."""
        # Mock session lookup to return session owned by different user
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {'user_id': 'user-123'}
        ]
        # Mock successful update
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {'session_id': 'session-123', 'main_topic': 'New Title'}
        ]
        
        response = client.patch(
            '/sessions/session-123',
            data=json.dumps({'main_topic': 'New Title'}),
            content_type='application/json',
            headers={'Authorization': 'Bearer valid-token'}
        )
        
        # Should succeed with 200
        assert response.status_code == 200


class TestAuthorization(TestUpdateSessionEndpoint):
    """Test authorization scenarios."""
    
    def test_forbidden_different_user_session(self, client, mock_verify_user, mock_supabase_client):
        """Test that user cannot update another user's session."""
        # User is user-123, but session belongs to user-456
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {'user_id': 'user-456'}
        ]
        
        response = client.patch(
            '/sessions/session-123',
            data=json.dumps({'main_topic': 'New Title'}),
            content_type='application/json',
            headers={'Authorization': 'Bearer valid-token'}
        )
        
        assert response.status_code == 403
        data = json.loads(response.data)
        assert 'Forbidden' in data['error']
    
    def test_session_not_found(self, client, mock_verify_user, mock_supabase_client):
        """Test that updating non-existent session returns 404."""
        # Session not found
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        
        response = client.patch(
            '/sessions/nonexistent-session',
            data=json.dumps({'main_topic': 'New Title'}),
            content_type='application/json',
            headers={'Authorization': 'Bearer valid-token'}
        )
        
        assert response.status_code == 404
        data = json.loads(response.data)
        assert data['error'] == 'Session not found'


class TestInputValidation(TestUpdateSessionEndpoint):
    """Test input validation scenarios."""
    
    def test_missing_request_body(self, client, mock_verify_user):
        """Test that missing request body returns 400."""
        response = client.patch(
            '/sessions/session-123',
            content_type='application/json',
            headers={'Authorization': 'Bearer valid-token'}
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'required' in data['error'].lower()
    
    def test_missing_main_topic_field(self, client, mock_verify_user):
        """Test that missing main_topic field returns 400."""
        response = client.patch(
            '/sessions/session-123',
            data=json.dumps({'other_field': 'value'}),
            content_type='application/json',
            headers={'Authorization': 'Bearer valid-token'}
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'main_topic is required' in data['error']
    
    def test_empty_main_topic(self, client, mock_verify_user):
        """Test that empty main_topic returns 400."""
        response = client.patch(
            '/sessions/session-123',
            data=json.dumps({'main_topic': ''}),
            content_type='application/json',
            headers={'Authorization': 'Bearer valid-token'}
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'non-empty' in data['error']
    
    def test_whitespace_only_main_topic(self, client, mock_verify_user):
        """Test that whitespace-only main_topic returns 400."""
        response = client.patch(
            '/sessions/session-123',
            data=json.dumps({'main_topic': '   '}),
            content_type='application/json',
            headers={'Authorization': 'Bearer valid-token'}
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'non-empty' in data['error']
    
    def test_null_main_topic(self, client, mock_verify_user):
        """Test that null main_topic returns 400."""
        response = client.patch(
            '/sessions/session-123',
            data=json.dumps({'main_topic': None}),
            content_type='application/json',
            headers={'Authorization': 'Bearer valid-token'}
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'main_topic is required' in data['error']
    
    def test_non_string_main_topic(self, client, mock_verify_user):
        """Test that non-string main_topic returns 400."""
        response = client.patch(
            '/sessions/session-123',
            data=json.dumps({'main_topic': 12345}),
            content_type='application/json',
            headers={'Authorization': 'Bearer valid-token'}
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'non-empty string' in data['error']


class TestSuccessfulUpdate(TestUpdateSessionEndpoint):
    """Test successful update scenarios."""
    
    def test_successful_title_update(self, client, mock_verify_user, mock_supabase_client):
        """Test successful session title update."""
        # Mock session ownership check
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {'user_id': 'user-123'}
        ]
        # Mock successful update
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {'session_id': 'session-123', 'main_topic': 'Updated Title'}
        ]
        
        response = client.patch(
            '/sessions/session-123',
            data=json.dumps({'main_topic': 'Updated Title'}),
            content_type='application/json',
            headers={'Authorization': 'Bearer valid-token'}
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is True
        assert data['main_topic'] == 'Updated Title'
    
    def test_title_is_trimmed(self, client, mock_verify_user, mock_supabase_client):
        """Test that title whitespace is trimmed."""
        # Mock session ownership check
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {'user_id': 'user-123'}
        ]
        # Mock successful update
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {'session_id': 'session-123', 'main_topic': 'Trimmed Title'}
        ]
        
        response = client.patch(
            '/sessions/session-123',
            data=json.dumps({'main_topic': '  Trimmed Title  '}),
            content_type='application/json',
            headers={'Authorization': 'Bearer valid-token'}
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['main_topic'] == 'Trimmed Title'
    
    def test_long_title(self, client, mock_verify_user, mock_supabase_client):
        """Test that long titles are accepted."""
        long_title = 'A' * 500  # 500 character title
        
        # Mock session ownership check
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {'user_id': 'user-123'}
        ]
        # Mock successful update
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {'session_id': 'session-123', 'main_topic': long_title}
        ]
        
        response = client.patch(
            '/sessions/session-123',
            data=json.dumps({'main_topic': long_title}),
            content_type='application/json',
            headers={'Authorization': 'Bearer valid-token'}
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['main_topic'] == long_title
    
    def test_unicode_title(self, client, mock_verify_user, mock_supabase_client):
        """Test that unicode titles are accepted."""
        unicode_title = 'Machine Learning & AI 🤖 日本語'
        
        # Mock session ownership check
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {'user_id': 'user-123'}
        ]
        # Mock successful update
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {'session_id': 'session-123', 'main_topic': unicode_title}
        ]
        
        response = client.patch(
            '/sessions/session-123',
            data=json.dumps({'main_topic': unicode_title}),
            content_type='application/json',
            headers={'Authorization': 'Bearer valid-token'}
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['main_topic'] == unicode_title


class TestErrorHandling(TestUpdateSessionEndpoint):
    """Test error handling scenarios."""
    
    def test_database_update_failure(self, client, mock_verify_user, mock_supabase_client):
        """Test handling of database update failure."""
        # Mock session ownership check
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {'user_id': 'user-123'}
        ]
        # Mock failed update (no data returned)
        mock_supabase_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = []
        
        response = client.patch(
            '/sessions/session-123',
            data=json.dumps({'main_topic': 'New Title'}),
            content_type='application/json',
            headers={'Authorization': 'Bearer valid-token'}
        )
        
        assert response.status_code == 500
        data = json.loads(response.data)
        assert 'Failed to update session' in data['error']
    
    def test_database_exception(self, client, mock_verify_user, mock_supabase_client):
        """Test handling of database exceptions."""
        # Mock session ownership check to raise exception
        mock_supabase_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.side_effect = Exception("Database connection failed")
        
        response = client.patch(
            '/sessions/session-123',
            data=json.dumps({'main_topic': 'New Title'}),
            content_type='application/json',
            headers={'Authorization': 'Bearer valid-token'}
        )
        
        assert response.status_code == 500
        data = json.loads(response.data)
        assert 'Database connection failed' in data['error']
    
    def test_invalid_json_body(self, client, mock_verify_user):
        """Test handling of invalid JSON in request body."""
        response = client.patch(
            '/sessions/session-123',
            data='not valid json',
            content_type='application/json',
            headers={'Authorization': 'Bearer valid-token'}
        )
        
        assert response.status_code == 400 or response.status_code == 500

