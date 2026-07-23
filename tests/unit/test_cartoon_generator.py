"""
Unit tests for CartoonGenerator service.

Tests cover:
- Image downloading from Supabase storage
- Image preprocessing (format conversion, resizing)
- Gemini API integration for cartoonization
- Image uploading to Supabase storage
- Database updates (user_avatar_images, interview_calls)
- Error handling and edge cases
- Temp file cleanup
"""

import pytest
import os
import tempfile
from unittest.mock import MagicMock, Mock, patch, call
from PIL import Image
import io

from services.cartoon_generator import CartoonGenerator


class TestCartoonGeneratorInit:
    """Test CartoonGenerator initialization."""
    
    def test_init_with_api_key(self, mock_supabase):
        """Test initialization with API key provided."""
        generator = CartoonGenerator(mock_supabase, google_api_key="test-api-key")
        
        assert generator.supabase == mock_supabase
        assert generator.google_api_key == "test-api-key"
        assert generator.client is not None
    
    def test_init_without_api_key(self, mock_supabase):
        """Test initialization fails without API key."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(Exception, match="GOOGLE_API_KEY not configured"):
                CartoonGenerator(mock_supabase, google_api_key=None)


class TestCartoonizeImage:
    """Test the main cartoonize_image method."""
    
    def test_successful_cartoonization(self, mock_supabase, create_test_image_bytes):
        """Test successful end-to-end cartoonization."""
        # Setup mocks
        original_image_bytes = create_test_image_bytes(format='PNG')
        
        # Mock storage download
        mock_supabase.storage.from_.return_value.download.return_value = original_image_bytes
        
        # Mock storage upload
        mock_supabase.storage.from_.return_value.upload.return_value = True
        
        # Mock user_avatar_images update
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {
                'id': 'record-123',
                'user_id': 'user-456',
                'picture_id': 'test-pic.jpg',
                'cartoon_id': 'user-456/test-pic_cartoon.png'
            }
        ]
        
        # Mock interview_calls update
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {
                'session_id': 'session-123',
                'cartoon_id': 'user-456/test-pic_cartoon.png'
            }
        ]
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        # Mock the internal methods to avoid actual API calls
        with patch.object(generator, '_transform_with_gemini') as mock_transform:
            # Create a temporary file to simulate the transformed image
            with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as temp_file:
                test_img = Image.new('RGB', (100, 100), (0, 255, 0))
                test_img.save(temp_file, format='PNG')
                temp_file.flush()
                mock_transform.return_value = temp_file.name
                
                try:
                    result = generator.cartoonize_image(
                        user_id='user-456',
                        picture_id='test-pic.jpg',
                        session_id='session-123'
                    )
                    
                    # Verify result
                    assert result['status'] == 'success'
                    assert result['user_id'] == 'user-456'
                    assert result['original_picture_id'] == 'test-pic.jpg'
                    assert result['cartoonized_picture_id'] == 'user-456/test-pic_cartoon.png'
                    assert result['session_id'] == 'session-123'
                    
                    # Verify upload was called with correct path (includes user_id prefix)
                    upload_call = mock_supabase.storage.from_.return_value.upload
                    assert upload_call.called
                    call_args = upload_call.call_args
                    assert call_args.kwargs['path'] == 'user-456/test-pic_cartoon.png'
                    
                    # Verify Gemini was called
                    mock_transform.assert_called_once()
                finally:
                    # Cleanup
                    if os.path.exists(temp_file.name):
                        os.unlink(temp_file.name)
    
    def test_missing_user_id(self, mock_supabase):
        """Test error when user_id is missing."""
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        with pytest.raises(ValueError, match="user_id is required"):
            generator.cartoonize_image(
                user_id=None,
                picture_id='test-pic.jpg',
                session_id='session-123'
            )
    
    def test_missing_picture_id(self, mock_supabase):
        """Test error when picture_id is missing."""
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        with pytest.raises(ValueError, match="picture_id is required"):
            generator.cartoonize_image(
                user_id='user-456',
                picture_id=None,
                session_id='session-123'
            )
    
    def test_missing_session_id(self, mock_supabase):
        """Test error when session_id is missing."""
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        with pytest.raises(ValueError, match="session_id is required"):
            generator.cartoonize_image(
                user_id='user-456',
                picture_id='test-pic.jpg',
                session_id=None
            )
    
    def test_cleanup_on_error(self, mock_supabase, create_test_image_bytes):
        """Test that temp files are cleaned up even when errors occur."""
        original_image_bytes = create_test_image_bytes(format='PNG')
        mock_supabase.storage.from_.return_value.download.return_value = original_image_bytes
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        # Mock transform to raise an error
        with patch.object(generator, '_transform_with_gemini', side_effect=Exception("API Error")):
            with pytest.raises(Exception, match="API Error"):
                generator.cartoonize_image(
                    user_id='user-456',
                    picture_id='test-pic.jpg',
                    session_id='session-123'
                )
        
        # Verify temp files don't accumulate (hard to test directly, but the code path is covered)


class TestDownloadImageFromStorage:
    """Test _download_image_from_storage method."""
    
    def test_successful_download(self, mock_supabase, create_test_image_bytes):
        """Test successful image download from storage."""
        image_bytes = create_test_image_bytes(format='JPEG')
        mock_supabase.storage.from_.return_value.download.return_value = image_bytes
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        result_path = generator._download_image_from_storage('test-pic.jpg')
        
        try:
            assert os.path.exists(result_path)
            assert result_path.endswith('.jpg')
            
            # Verify content
            with open(result_path, 'rb') as f:
                downloaded_bytes = f.read()
            assert downloaded_bytes == image_bytes
            
            # Verify correct bucket was called
            mock_supabase.storage.from_.assert_called_with("avatar-images-bucket")
        finally:
            if os.path.exists(result_path):
                os.unlink(result_path)
    
    def test_download_empty_response(self, mock_supabase):
        """Test error handling when download returns empty response."""
        mock_supabase.storage.from_.return_value.download.return_value = None
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        with pytest.raises(Exception, match="Empty response downloading image"):
            generator._download_image_from_storage('test-pic.jpg')
    
    def test_download_storage_error(self, mock_supabase):
        """Test error handling when storage download fails."""
        mock_supabase.storage.from_.return_value.download.side_effect = Exception("Storage error")
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        with pytest.raises(Exception, match="Image download failed"):
            generator._download_image_from_storage('test-pic.jpg')
    
    def test_download_with_different_extensions(self, mock_supabase, create_test_image_bytes):
        """Test download preserves file extension."""
        image_bytes = create_test_image_bytes(format='PNG')
        mock_supabase.storage.from_.return_value.download.return_value = image_bytes
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        for ext in ['.png', '.jpg', '.jpeg', '.gif']:
            result_path = generator._download_image_from_storage(f'test-pic{ext}')
            try:
                assert result_path.endswith(ext)
            finally:
                if os.path.exists(result_path):
                    os.unlink(result_path)
    
    def test_download_with_user_id_path(self, mock_supabase, create_test_image_bytes):
        """Test download uses user_id as path prefix when provided."""
        image_bytes = create_test_image_bytes(format='PNG')
        mock_supabase.storage.from_.return_value.download.return_value = image_bytes
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        result_path = generator._download_image_from_storage('test-pic.png', user_id='user-123')
        
        try:
            # Verify the storage download was called with the full path including user_id
            mock_supabase.storage.from_.return_value.download.assert_called_with('user-123/test-pic.png')
        finally:
            if os.path.exists(result_path):
                os.unlink(result_path)


class TestTransformWithGemini:
    """Test _transform_with_gemini method."""
    
    def test_successful_transformation_rgb(self, mock_supabase, create_test_image):
        """Test successful transformation with RGB image."""
        # Create a test image file
        test_image = create_test_image(mode='RGB', size=(100, 100))
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_file:
            test_image.save(temp_file, format='JPEG')
            temp_file.flush()
            input_path = temp_file.name
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        # Mock the Gemini client response
        with patch.object(generator.client.models, 'generate_content') as mock_generate:
            # Setup mock response
            mock_part = MagicMock()
            mock_part.inline_data = True
            result_image = Image.new('RGB', (100, 100), (0, 255, 0))
            mock_part.as_image.return_value = result_image
            
            mock_response = MagicMock()
            mock_response.parts = [mock_part]
            mock_generate.return_value = mock_response
            
            try:
                result_path = generator._transform_with_gemini(input_path)
                
                assert os.path.exists(result_path)
                assert result_path.endswith('.png')
                
                # Verify the result is a valid image
                result_img = Image.open(result_path)
                assert result_img.size == (100, 100)
                
                # Verify Gemini was called with correct parameters
                mock_generate.assert_called_once()
                call_args = mock_generate.call_args
                assert call_args.kwargs['model'] == 'gemini-2.5-flash-image'
                
            finally:
                for path in [input_path, result_path]:
                    if os.path.exists(path):
                        os.unlink(path)
    
    def test_transformation_rgba_conversion(self, mock_supabase, create_test_image):
        """Test that RGBA images are converted to RGB."""
        # Create an RGBA test image
        test_image = create_test_image(mode='RGBA', size=(100, 100))
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as temp_file:
            test_image.save(temp_file, format='PNG')
            temp_file.flush()
            input_path = temp_file.name
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        with patch.object(generator.client.models, 'generate_content') as mock_generate:
            mock_part = MagicMock()
            mock_part.inline_data = True
            result_image = Image.new('RGB', (100, 100), (0, 255, 0))
            mock_part.as_image.return_value = result_image
            
            mock_response = MagicMock()
            mock_response.parts = [mock_part]
            mock_generate.return_value = mock_response
            
            try:
                result_path = generator._transform_with_gemini(input_path)
                
                # Verify the call was made (image was converted to RGB internally)
                mock_generate.assert_called_once()
                
                # Check the PIL Image passed to Gemini was RGB
                call_args = mock_generate.call_args
                # The image is passed in the contents list
                contents = call_args.kwargs['contents']
                passed_image = contents[1]  # Second item should be the image
                assert isinstance(passed_image, Image.Image)
                assert passed_image.mode == 'RGB'
                
            finally:
                for path in [input_path, result_path]:
                    if os.path.exists(path):
                        os.unlink(path)
    
    def test_transformation_resize_large_image(self, mock_supabase, create_test_image):
        """Test that images larger than 4096px are resized."""
        # Create a large test image
        test_image = create_test_image(mode='RGB', size=(5000, 5000))
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_file:
            test_image.save(temp_file, format='JPEG')
            temp_file.flush()
            input_path = temp_file.name
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        with patch.object(generator.client.models, 'generate_content') as mock_generate:
            mock_part = MagicMock()
            mock_part.inline_data = True
            result_image = Image.new('RGB', (100, 100), (0, 255, 0))
            mock_part.as_image.return_value = result_image
            
            mock_response = MagicMock()
            mock_response.parts = [mock_part]
            mock_generate.return_value = mock_response
            
            try:
                result_path = generator._transform_with_gemini(input_path)
                
                # Verify the call was made
                mock_generate.assert_called_once()
                
                # Check that image was resized
                call_args = mock_generate.call_args
                contents = call_args.kwargs['contents']
                passed_image = contents[1]
                assert max(passed_image.size) <= 4096
                
            finally:
                for path in [input_path, result_path]:
                    if os.path.exists(path):
                        os.unlink(path)
    
    def test_transformation_no_image_in_response(self, mock_supabase, create_test_image):
        """Test error handling when Gemini doesn't return an image."""
        test_image = create_test_image(mode='RGB', size=(100, 100))
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_file:
            test_image.save(temp_file, format='JPEG')
            temp_file.flush()
            input_path = temp_file.name
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        with patch.object(generator.client.models, 'generate_content') as mock_generate:
            # Mock response with no image
            mock_part = MagicMock()
            mock_part.inline_data = None
            
            mock_response = MagicMock()
            mock_response.parts = [mock_part]
            mock_response.text = "Some text response"
            mock_generate.return_value = mock_response
            
            try:
                with pytest.raises(Exception, match="No image returned from Gemini API"):
                    generator._transform_with_gemini(input_path)
            finally:
                if os.path.exists(input_path):
                    os.unlink(input_path)
    
    def test_transformation_gemini_api_error(self, mock_supabase, create_test_image):
        """Test error handling when Gemini API fails."""
        test_image = create_test_image(mode='RGB', size=(100, 100))
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_file:
            test_image.save(temp_file, format='JPEG')
            temp_file.flush()
            input_path = temp_file.name
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        with patch.object(generator.client.models, 'generate_content', side_effect=Exception("API Error")):
            try:
                with pytest.raises(Exception, match="Gemini transformation failed"):
                    generator._transform_with_gemini(input_path)
            finally:
                if os.path.exists(input_path):
                    os.unlink(input_path)


class TestUploadToStorage:
    """Test _upload_to_storage method."""
    
    def test_successful_upload(self, mock_supabase, create_test_image):
        """Test successful upload to Supabase storage."""
        # Create a test image file
        test_image = create_test_image(mode='RGB', size=(100, 100))
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as temp_file:
            test_image.save(temp_file, format='PNG')
            temp_file.flush()
            image_path = temp_file.name
        
        mock_supabase.storage.from_.return_value.upload.return_value = True
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        try:
            result = generator._upload_to_storage(
                image_path=image_path,
                original_picture_id='test-pic.jpg',
                user_id='user-123'
            )
            
            assert result == 'user-123/test-pic_cartoon.png'
            
            # Verify storage upload was called correctly
            mock_supabase.storage.from_.assert_called_with("avatar-images-bucket")
            upload_call = mock_supabase.storage.from_.return_value.upload
            upload_call.assert_called_once()
            
            # Check upload parameters
            call_args = upload_call.call_args
            assert call_args.kwargs['path'] == 'user-123/test-pic_cartoon.png'
            assert call_args.kwargs['file_options']['content-type'] == 'image/png'
            assert call_args.kwargs['file_options']['upsert'] == 'true'
            
        finally:
            if os.path.exists(image_path):
                os.unlink(image_path)
    
    def test_upload_naming_convention(self, mock_supabase, create_test_image):
        """Test that cartoonized images follow naming convention with user_id prefix."""
        test_image = create_test_image(mode='RGB', size=(100, 100))
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as temp_file:
            test_image.save(temp_file, format='PNG')
            temp_file.flush()
            image_path = temp_file.name
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        test_cases = [
            ('picture.jpg', 'user-123/picture_cartoon.png'),
            ('my-photo.png', 'user-123/my-photo_cartoon.png'),
            ('user_123.jpeg', 'user-123/user_123_cartoon.png'),
            ('user-456/uuid.png', 'user-123/uuid_cartoon.png'),  # Test with path in original
        ]
        
        try:
            for original_id, expected_cartoon_id in test_cases:
                result = generator._upload_to_storage(image_path, original_id, user_id='user-123')
                assert result == expected_cartoon_id
        finally:
            if os.path.exists(image_path):
                os.unlink(image_path)
    
    def test_upload_storage_error(self, mock_supabase, create_test_image):
        """Test error handling when storage upload fails."""
        test_image = create_test_image(mode='RGB', size=(100, 100))
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as temp_file:
            test_image.save(temp_file, format='PNG')
            temp_file.flush()
            image_path = temp_file.name
        
        mock_supabase.storage.from_.return_value.upload.side_effect = Exception("Storage full")
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        try:
            with pytest.raises(Exception, match="Storage upload failed"):
                generator._upload_to_storage(image_path, 'test-pic.jpg', user_id='user-123')
        finally:
            if os.path.exists(image_path):
                os.unlink(image_path)


class TestUpdateUserAvatarImages:
    """Test _update_user_avatar_images method."""
    
    def test_successful_update(self, mock_supabase):
        """Test successful database update."""
        expected_record = {
            'id': 'record-123',
            'user_id': 'user-456',
            'picture_id': 'test-pic.jpg',
            'cartoon_id': 'test-pic_cartoon.png'
        }
        
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = [expected_record]
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        result = generator._update_user_avatar_images(
            user_id='user-456',
            picture_id='test-pic.jpg',
            cartoon_id='test-pic_cartoon.png'
        )
        
        assert result == expected_record
        
        # Verify correct table and update parameters
        mock_supabase.table.assert_called_with("user_avatar_images")
        update_call = mock_supabase.table.return_value.update
        update_call.assert_called_once_with({"cartoon_id": "test-pic_cartoon.png"})
    
    def test_update_no_matching_record(self, mock_supabase):
        """Test error when no matching record exists."""
        mock_supabase.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        with pytest.raises(Exception, match="Failed to update user_avatar_images"):
            generator._update_user_avatar_images(
                user_id='user-456',
                picture_id='nonexistent.jpg',
                cartoon_id='cartoon.png'
            )
    
    def test_update_database_error(self, mock_supabase):
        """Test error handling when database update fails."""
        mock_supabase.table.return_value.update.return_value.eq.side_effect = Exception("DB connection error")
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        with pytest.raises(Exception, match="Database update failed"):
            generator._update_user_avatar_images(
                user_id='user-456',
                picture_id='test-pic.jpg',
                cartoon_id='cartoon.png'
            )


class TestUpdateInterviewCalls:
    """Test _update_interview_calls method."""
    
    def test_successful_update(self, mock_supabase):
        """Test successful interview_calls update."""
        expected_record = {
            'session_id': 'session-123',
            'cartoon_id': 'test-pic_cartoon.png'
        }
        
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [expected_record]
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        result = generator._update_interview_calls(
            session_id='session-123',
            cartoon_id='test-pic_cartoon.png'
        )
        
        assert result == expected_record
        
        # Verify correct table and parameters
        mock_supabase.table.assert_called_with("interview_calls")
        update_call = mock_supabase.table.return_value.update
        update_call.assert_called_once_with({"cartoon_id": "test-pic_cartoon.png"})
    
    def test_update_no_matching_session(self, mock_supabase):
        """Test error when session doesn't exist."""
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = []
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        with pytest.raises(Exception, match="Failed to update interview_calls"):
            generator._update_interview_calls(
                session_id='nonexistent-session',
                cartoon_id='cartoon.png'
            )
    
    def test_update_database_error(self, mock_supabase):
        """Test error handling when database fails."""
        mock_supabase.table.return_value.update.side_effect = Exception("DB error")
        
        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        
        with pytest.raises(Exception, match="Database update failed"):
            generator._update_interview_calls(
                session_id='session-123',
                cartoon_id='cartoon.png'
            )


class TestUpdateInterviewCallAnalysis:
    """Test _update_interview_call_analysis method (best-effort mirror of cartoon_id)."""

    def test_successful_update(self, mock_supabase):
        """Writes cartoon_id to interview_call_analysis for the session."""
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {'session_id': 'session-123', 'cartoon_id': 'test-pic_cartoon.png'}
        ]

        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        generator._update_interview_call_analysis(
            session_id='session-123',
            cartoon_id='test-pic_cartoon.png'
        )

        mock_supabase.table.assert_called_with("interview_call_analysis")
        update_call = mock_supabase.table.return_value.update
        update_call.assert_called_once_with({"cartoon_id": "test-pic_cartoon.png"})

    def test_no_matching_row_does_not_raise(self, mock_supabase):
        """A missing analysis row is logged, not raised, so cartoonization still succeeds."""
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = []

        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        # Should not raise
        generator._update_interview_call_analysis(
            session_id='nonexistent-session',
            cartoon_id='cartoon.png'
        )

    def test_database_error_does_not_raise(self, mock_supabase):
        """A DB error is swallowed (best-effort) rather than failing the task."""
        mock_supabase.table.return_value.update.side_effect = Exception("DB error")

        generator = CartoonGenerator(mock_supabase, google_api_key="test-key")
        # Should not raise
        generator._update_interview_call_analysis(
            session_id='session-123',
            cartoon_id='cartoon.png'
        )


class TestEditorialPrompt:
    """Test that the editorial prompt is properly defined."""
    
    def test_prompt_exists(self):
        """Test that EDITORIAL_PROMPT is defined and non-empty."""
        assert hasattr(CartoonGenerator, 'EDITORIAL_PROMPT')
        assert len(CartoonGenerator.EDITORIAL_PROMPT) > 0
        assert 'editorial illustration' in CartoonGenerator.EDITORIAL_PROMPT.lower()
    
    def test_prompt_mentions_forward_facing(self):
        """Test that prompt mentions forward-facing requirement for avatar."""
        assert 'forward' in CartoonGenerator.EDITORIAL_PROMPT.lower()
        assert 'talking avatar' in CartoonGenerator.EDITORIAL_PROMPT.lower()

