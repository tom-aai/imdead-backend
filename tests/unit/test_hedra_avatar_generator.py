"""
Unit tests for HedraAvatarGenerator service.

Tests cover:
- Avatar generation from cartoonized images
- Hedra's two-step API process (create asset, upload image)
- Cartoon ID lookup from user_avatar_images
- Image downloading from Supabase storage
- Database updates (user_avatar_images, interview_call_analysis)
- Picture source detection (interview vs user upload)
- Error handling and edge cases
"""

import pytest
import os
import tempfile
import requests
from unittest.mock import MagicMock, Mock, patch, call
from PIL import Image

from services.hedra_avatar_id_generator import HedraAvatarGenerator


class TestHedraAvatarGeneratorInit:
    """Test HedraAvatarGenerator initialization."""
    
    def test_init_with_api_key(self, mock_supabase):
        """Test initialization with API key provided."""
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key="test-api-key")
        
        assert generator.supabase == mock_supabase
        assert generator.hedra_api_key == "test-api-key"
    
    def test_init_without_api_key(self, mock_supabase):
        """Test initialization logs warning without API key."""
        with patch.dict(os.environ, {}, clear=True):
            generator = HedraAvatarGenerator(mock_supabase, hedra_api_key=None)
            assert generator.hedra_api_key is None


class TestGenerateAvatar:
    """Test the main generate_avatar method."""
    
    def test_successful_avatar_generation(self, mock_supabase, create_test_image_bytes):
        """Test successful end-to-end avatar generation."""
        image_bytes = create_test_image_bytes(format='PNG')
        
        # Mock cartoon_id lookup
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {'cartoon_id': 'cartoon-pic.png'}
        ]
        
        # Mock image download
        mock_supabase.storage.from_.return_value.download.return_value = image_bytes
        
        # Mock user_avatar_images update - need to return picture_source first
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {'picture_source': 'from_interview_session'}
        ]
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {'id': 'record-123', 'hedra_avatar_id': 'hedra-123'}
        ]
        
        # Mock interview_call_analysis update
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {'session_id': 'session-123', 'hedra_avatar_id': 'hedra-123'}
        ]
        
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key="test-key")
        
        # Mock Hedra upload
        with patch.object(generator, '_upload_to_hedra', return_value='hedra-avatar-123'):
            result = generator.generate_avatar(
                user_id='user-456',
                picture_id='original-pic.jpg',
                session_id='session-123'
            )
            
            assert result['status'] == 'success'
            assert result['user_id'] == 'user-456'
            assert result['picture_id'] == 'original-pic.jpg'
            assert result['cartoon_id'] == 'cartoon-pic.png'
            assert result['hedra_avatar_id'] == 'hedra-avatar-123'
    
    def test_missing_user_id(self, mock_supabase):
        """Test error when user_id is missing."""
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key="test-key")
        
        with pytest.raises(ValueError, match="user_id is required"):
            generator.generate_avatar(
                user_id=None,
                picture_id='pic.jpg',
                session_id='session-123'
            )
    
    def test_missing_picture_id(self, mock_supabase):
        """Test error when picture_id is missing."""
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key="test-key")
        
        with pytest.raises(ValueError, match="picture_id is required"):
            generator.generate_avatar(
                user_id='user-456',
                picture_id=None,
                session_id='session-123'
            )
    
    def test_missing_session_id(self, mock_supabase):
        """Test error when session_id is missing."""
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key="test-key")
        
        with pytest.raises(ValueError, match="session_id is required"):
            generator.generate_avatar(
                user_id='user-456',
                picture_id='pic.jpg',
                session_id=None
            )
    
    def test_missing_api_key(self, mock_supabase):
        """Test error when HEDRA_API_KEY not configured."""
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key=None)
        
        with pytest.raises(Exception, match="HEDRA_API_KEY not configured"):
            generator.generate_avatar(
                user_id='user-456',
                picture_id='pic.jpg',
                session_id='session-123'
            )
    
    def test_no_cartoon_id_found(self, mock_supabase):
        """Test error when cartoon_id not found."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {'cartoon_id': None}
        ]
        
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key="test-key")
        
        with pytest.raises(ValueError, match="No cartoon_id found"):
            generator.generate_avatar(
                user_id='user-456',
                picture_id='pic.jpg',
                session_id='session-123'
            )


class TestGetCartoonId:
    """Test _get_cartoon_id method."""
    
    def test_successful_lookup(self, mock_supabase):
        """Test successful cartoon_id lookup."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {'cartoon_id': 'test-cartoon.png'}
        ]
        
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key="test-key")
        result = generator._get_cartoon_id('user-456', 'original-pic.jpg')
        
        assert result == 'test-cartoon.png'
        
        # Verify correct table and query
        mock_supabase.table.assert_called_with("user_avatar_images")
    
    def test_no_record_found(self, mock_supabase):
        """Test error when no record found."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key="test-key")
        
        with pytest.raises(Exception, match="No record found"):
            generator._get_cartoon_id('user-456', 'nonexistent.jpg')
    
    def test_null_cartoon_id(self, mock_supabase):
        """Test error when cartoon_id is null."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {'cartoon_id': None}
        ]
        
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key="test-key")
        
        with pytest.raises(Exception, match="No cartoon_id found"):
            generator._get_cartoon_id('user-456', 'pic.jpg')


class TestUploadToHedra:
    """Test _upload_to_hedra method."""
    
    @patch('services.hedra_avatar_id_generator.requests.post')
    def test_successful_upload(self, mock_post, mock_supabase, create_test_image):
        """Test successful two-step Hedra upload."""
        # Create a test image file
        test_image = create_test_image(mode='RGB', size=(100, 100))
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_file:
            test_image.save(temp_file, format='JPEG')
            temp_file.flush()
            image_path = temp_file.name
        
        # Mock the two API calls
        mock_post.side_effect = [
            # Step 1: Create asset
            MagicMock(status_code=200, json=lambda: {'id': 'hedra-asset-123'}),
            # Step 2: Upload image
            MagicMock(status_code=200)
        ]
        
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key="test-key")
        
        try:
            result = generator._upload_to_hedra(image_path)
            
            assert result == 'hedra-asset-123'
            
            # Verify two API calls were made
            assert mock_post.call_count == 2
            
            # Verify first call (create asset)
            first_call = mock_post.call_args_list[0]
            assert 'assets' in first_call.args[0]
            assert first_call.kwargs['headers']['X-API-Key'] == 'test-key'
            assert first_call.kwargs['json']['type'] == 'image'
            
            # Verify second call (upload image)
            second_call = mock_post.call_args_list[1]
            assert 'hedra-asset-123/upload' in second_call.args[0]
            
        finally:
            if os.path.exists(image_path):
                os.unlink(image_path)
    
    @patch('services.hedra_avatar_id_generator.requests.post')
    def test_create_asset_failure(self, mock_post, mock_supabase, create_test_image):
        """Test error handling when asset creation fails."""
        test_image = create_test_image(mode='RGB', size=(100, 100))
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_file:
            test_image.save(temp_file, format='JPEG')
            temp_file.flush()
            image_path = temp_file.name
        
        # Mock failed asset creation
        mock_post.return_value = MagicMock(
            status_code=400,
            text='Bad request'
        )
        
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key="test-key")
        
        try:
            with pytest.raises(Exception, match="Failed to create Hedra asset"):
                generator._upload_to_hedra(image_path)
        finally:
            if os.path.exists(image_path):
                os.unlink(image_path)
    
    @patch('services.hedra_avatar_id_generator.requests.post')
    def test_upload_image_failure(self, mock_post, mock_supabase, create_test_image):
        """Test error handling when image upload fails."""
        test_image = create_test_image(mode='RGB', size=(100, 100))
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_file:
            test_image.save(temp_file, format='JPEG')
            temp_file.flush()
            image_path = temp_file.name
        
        # Mock successful create, failed upload
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'id': 'asset-123'}),
            MagicMock(status_code=500, text='Server error')
        ]
        
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key="test-key")
        
        try:
            with pytest.raises(Exception, match="Failed to upload image to Hedra"):
                generator._upload_to_hedra(image_path)
        finally:
            if os.path.exists(image_path):
                os.unlink(image_path)


class TestUpdateUserAvatarImages:
    """Test _update_user_avatar_images method."""
    
    def test_update_from_interview_session(self, mock_supabase):
        """Test update for image from interview session (includes session_id)."""
        # Mock existing record check
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {'picture_source': 'from_interview_session'}
        ]
        
        # Mock update
        expected_record = {
            'id': 'record-123',
            'hedra_avatar_id': 'hedra-123',
            'session_id': 'session-123'
        }
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = [expected_record]
        
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key="test-key")
        result = generator._update_user_avatar_images(
            user_id='user-456',
            session_id='session-123',
            picture_id='pic.jpg',
            hedra_avatar_id='hedra-123'
        )
        
        assert result['id'] == 'record-123'
        
        # Verify update included session_id
        update_call = mock_supabase.table.return_value.update
        update_data = update_call.call_args.args[0]
        assert update_data['hedra_avatar_id'] == 'hedra-123'
        assert update_data['session_id'] == 'session-123'
    
    def test_update_from_user_upload(self, mock_supabase):
        """Test update for user-uploaded image (no session_id)."""
        # Mock existing record check
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {'picture_source': 'user_upload'}
        ]
        
        # Mock update
        expected_record = {'id': 'record-123', 'hedra_avatar_id': 'hedra-123'}
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = [expected_record]
        
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key="test-key")
        result = generator._update_user_avatar_images(
            user_id='user-456',
            session_id='session-123',
            picture_id='pic.jpg',
            hedra_avatar_id='hedra-123'
        )
        
        # Verify update did NOT include session_id
        update_call = mock_supabase.table.return_value.update
        update_data = update_call.call_args.args[0]
        assert update_data['hedra_avatar_id'] == 'hedra-123'
        assert 'session_id' not in update_data
    
    def test_update_no_existing_record(self, mock_supabase):
        """Test error when no existing record found."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key="test-key")
        
        with pytest.raises(Exception, match="No existing record found"):
            generator._update_user_avatar_images(
                user_id='user-456',
                session_id='session-123',
                picture_id='nonexistent.jpg',
                hedra_avatar_id='hedra-123'
            )
    
    def test_update_failure(self, mock_supabase):
        """Test error when update fails."""
        # Mock existing record check
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {'picture_source': 'from_interview_session'}
        ]
        
        # Mock failed update
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key="test-key")
        
        with pytest.raises(Exception, match="Failed to update user_avatar_images"):
            generator._update_user_avatar_images(
                user_id='user-456',
                session_id='session-123',
                picture_id='pic.jpg',
                hedra_avatar_id='hedra-123'
            )


class TestUpdateInterviewAnalysis:
    """Test _update_interview_analysis method."""
    
    def test_successful_update(self, mock_supabase):
        """Test successful interview_call_analysis update."""
        expected_record = {
            'session_id': 'session-123',
            'hedra_avatar_id': 'hedra-123'
        }
        
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [expected_record]
        
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key="test-key")
        result = generator._update_interview_analysis(
            session_id='session-123',
            hedra_avatar_id='hedra-123'
        )
        
        assert result['session_id'] == 'session-123'
        
        # Verify correct table and update
        mock_supabase.table.assert_called_with("interview_call_analysis")
        update_call = mock_supabase.table.return_value.update
        update_call.assert_called_with({"hedra_avatar_id": "hedra-123"})
    
    def test_update_no_matching_session(self, mock_supabase):
        """Test error when session doesn't exist."""
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = []
        
        generator = HedraAvatarGenerator(mock_supabase, hedra_api_key="test-key")
        
        with pytest.raises(Exception, match="Failed to update interview_call_analysis"):
            generator._update_interview_analysis(
                session_id='nonexistent',
                hedra_avatar_id='hedra-123'
            )

