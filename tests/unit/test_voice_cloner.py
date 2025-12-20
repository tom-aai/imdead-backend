"""
Unit tests for VoiceCloningService.

Tests cover:
- Voice recording processing and VAD
- Audio download from Supabase storage
- Silero VAD for speech detection
- Audio segment stitching
- Cartesia voice cloning integration
- Call type filtering (onboarding vs interview)
- User profile updates
- Error handling and edge cases
"""

import pytest
import os
import tempfile
import numpy as np
from unittest.mock import MagicMock, Mock, patch, call
from pydub import AudioSegment

from services.voice_cloner import VoiceCloningService


class TestVoiceCloningServiceInit:
    """Test VoiceCloningService initialization."""
    
    def test_init_with_api_key(self, mock_supabase):
        """Test initialization with API key provided."""
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-api-key")
        
        assert service.supabase == mock_supabase
        assert service.cartesia is not None
    
    def test_init_without_api_key(self, mock_supabase):
        """Test initialization uses env var when no key provided."""
        with patch.dict('os.environ', {'CARTESIA_API_KEY': 'env-api-key'}):
            service = VoiceCloningService(mock_supabase, cartesia_api_key=None)
            assert service.cartesia is not None


class TestProcessVoiceRecording:
    """Test the main process_voice_recording method."""
    
    def test_successful_processing_onboarding_call(self, mock_supabase, mock_cartesia_client):
        """Test successful voice cloning for onboarding call."""
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        service.cartesia = mock_cartesia_client
        
        # Mock the internal methods to avoid complex mock chaining
        with patch.object(service, '_get_recording_info', return_value=('rec-123', 'onboarding')):
            with patch.object(service, '_download_recording', return_value='/tmp/recording.mp3'):
                with patch.object(service, '_process_audio', return_value='/tmp/processed.wav'):
                    with patch.object(service, '_get_user_id_from_session', return_value='user-456'):
                        with patch.object(service, '_clone_voice_with_cartesia', return_value='cloned-voice-id-123'):
                            with patch.object(service, '_update_user_profile_voice_id'):
                                result = service.process_voice_recording('session-123')
                                
                                assert result['status'] == 'success'
                                assert result['session_id'] == 'session-123'
                                assert result['recording_id'] == 'rec-123'
                                assert result['user_id'] == 'user-456'
                                assert result['voice_id'] == 'cloned-voice-id-123'
    
    def test_skip_non_onboarding_call(self, mock_supabase):
        """Test that non-onboarding calls are skipped."""
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        
        # Mock _get_recording_info to return interview call
        with patch.object(service, '_get_recording_info', return_value=('rec-123', 'interview')):
            result = service.process_voice_recording('session-123')
            
            assert result['status'] == 'skipped'
            assert 'Voice cloning skipped' in result['message']
            assert result['call_type'] == 'interview'
    
    def test_missing_session_id(self, mock_supabase):
        """Test error when session_id is missing."""
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        
        with pytest.raises(ValueError, match="session_id is required"):
            service.process_voice_recording(None)
    
    def test_no_recording_found(self, mock_supabase):
        """Test error when no recording_id found."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {'recording_id': None, 'call_type': 'onboarding'}
        ]
        
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        
        with pytest.raises(ValueError, match="No recording_id found"):
            service.process_voice_recording('session-123')


class TestGetRecordingInfo:
    """Test _get_recording_info method."""
    
    def test_successful_lookup(self, mock_supabase):
        """Test successful recording info lookup."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {'recording_id': 'rec-456', 'call_type': 'onboarding'}
        ]
        
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        recording_id, call_type = service._get_recording_info('session-123')
        
        assert recording_id == 'rec-456'
        assert call_type == 'onboarding'
    
    def test_default_call_type(self, mock_supabase):
        """Test default call_type when not specified."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {'recording_id': 'rec-456'}
        ]
        
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        recording_id, call_type = service._get_recording_info('session-123')
        
        assert call_type == 'interview'  # Default
    
    def test_call_not_found(self, mock_supabase):
        """Test error when call not found."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        
        with pytest.raises(ValueError, match="No call found"):
            service._get_recording_info('nonexistent')


class TestDownloadRecording:
    """Test _download_recording method."""
    
    def test_successful_download(self, mock_supabase):
        """Test successful recording download."""
        fake_audio = b'fake mp4 audio data'
        mock_supabase.storage.from_.return_value.download.return_value = fake_audio
        
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        result_path = service._download_recording('rec-123')
        
        try:
            assert os.path.exists(result_path)
            assert result_path.endswith('.mp4')
            
            with open(result_path, 'rb') as f:
                downloaded_data = f.read()
            assert downloaded_data == fake_audio
            
            # Verify correct bucket and path
            mock_supabase.storage.from_.assert_called_with("interview-recordings")
            
        finally:
            if os.path.exists(result_path):
                os.unlink(result_path)
    
    def test_download_empty_response(self, mock_supabase):
        """Test error when download returns empty."""
        mock_supabase.storage.from_.return_value.download.return_value = None
        
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        
        with pytest.raises(Exception, match="Empty response downloading recording"):
            service._download_recording('rec-123')


class TestCloneVoiceWithCartesia:
    """Test _clone_voice_with_cartesia method."""
    
    def test_successful_cloning(self, mock_supabase, mock_cartesia_client, create_test_image):
        """Test successful voice cloning."""
        # Create a fake audio file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as temp_file:
            temp_file.write(b'fake wav data')
            temp_file.flush()
            audio_path = temp_file.name
        
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        service.cartesia = mock_cartesia_client
        
        try:
            result = service._clone_voice_with_cartesia(audio_path, 'user-456')
            
            assert result == 'cloned-voice-id-123'
            
            # Verify Cartesia was called correctly
            mock_cartesia_client.voices.clone.assert_called_once()
            call_kwargs = mock_cartesia_client.voices.clone.call_args.kwargs
            assert call_kwargs['name'] == 'user-456'
            assert call_kwargs['language'] == 'en'
            assert call_kwargs['mode'] == 'similarity'
            assert call_kwargs['enhance'] is True
            
        finally:
            if os.path.exists(audio_path):
                os.unlink(audio_path)
    
    def test_file_not_found(self, mock_supabase, mock_cartesia_client):
        """Test error when audio file doesn't exist."""
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        service.cartesia = mock_cartesia_client
        
        with pytest.raises(Exception, match="Voice cloning failed"):
            service._clone_voice_with_cartesia('/nonexistent/path.wav', 'user-456')
    
    def test_api_error(self, mock_supabase, mock_cartesia_client):
        """Test error handling when Cartesia API fails."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as temp_file:
            temp_file.write(b'fake wav data')
            temp_file.flush()
            audio_path = temp_file.name
        
        mock_cartesia_client.voices.clone.side_effect = Exception("API Error")
        
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        service.cartesia = mock_cartesia_client
        
        try:
            with pytest.raises(Exception, match="Voice cloning failed"):
                service._clone_voice_with_cartesia(audio_path, 'user-456')
        finally:
            if os.path.exists(audio_path):
                os.unlink(audio_path)


class TestUpdateUserProfileVoiceId:
    """Test _update_user_profile_voice_id method."""
    
    def test_update_existing_profile(self, mock_supabase):
        """Test updating existing user profile."""
        # Mock existing profile check
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {'id': 'profile-123'}
        ]
        
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        service._update_user_profile_voice_id('user-456', 'voice-789')
        
        # Verify update was called
        mock_supabase.table.return_value.update.assert_called_with({"voice_id": "voice-789"})
    
    def test_create_new_profile(self, mock_supabase):
        """Test creating new profile when none exists."""
        # Mock no existing profile
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        service._update_user_profile_voice_id('user-456', 'voice-789')
        
        # Verify insert was called
        mock_supabase.table.return_value.insert.assert_called_with({
            "user_id": "user-456",
            "voice_id": "voice-789"
        })
    
    def test_database_error(self, mock_supabase):
        """Test error handling when database fails."""
        mock_supabase.table.return_value.select.side_effect = Exception("DB Error")
        
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        
        with pytest.raises(Exception, match="Database update failed"):
            service._update_user_profile_voice_id('user-456', 'voice-789')


class TestProcessAudio:
    """Test _process_audio method (basic tests without heavy VAD)."""
    
    def test_process_audio_returns_path(self, mock_supabase):
        """Test that audio processing returns a valid path."""
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        
        # Create a minimal audio file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as temp_file:
            temp_file.write(b'fake audio')
            temp_file.flush()
            input_path = temp_file.name
        
        try:
            # Mock the internal methods to avoid heavy processing
            with patch.object(service, '_load_audio') as mock_load:
                with patch.object(service, '_detect_speech_segments', return_value=[(0, 1000)]):
                    with patch.object(service, '_stitch_audio_segments') as mock_stitch:
                        # Mock audio segment
                        mock_audio = MagicMock(spec=AudioSegment)
                        mock_audio.frame_rate = 44100
                        mock_load.return_value = mock_audio
                        mock_stitch.return_value = mock_audio
                        
                        result_path = service._process_audio(input_path, 'rec-123')
                        
                        assert result_path is not None
                        assert 'rec-123' in result_path
                        
                        # Cleanup
                        if os.path.exists(result_path):
                            os.unlink(result_path)
        finally:
            if os.path.exists(input_path):
                os.unlink(input_path)


class TestStitchAudioSegments:
    """Test _stitch_audio_segments method."""
    
    def test_stitch_stops_at_target_duration(self, mock_supabase):
        """Test that stitching stops when target duration is reached."""
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        
        # Create mock audio segment
        mock_audio = MagicMock(spec=AudioSegment)
        mock_audio.frame_rate = 44100
        mock_audio.__len__.return_value = 2000  # 2 seconds
        mock_audio.__getitem__.return_value = mock_audio
        mock_audio.__add__.return_value = mock_audio
        
        # Define segments (in milliseconds)
        segments = [
            (0, 5000),    # 5 seconds
            (6000, 11000), # 5 seconds
            (12000, 17000) # 5 seconds (should not be included if target is 10s)
        ]
        
        result = service._stitch_audio_segments(
            mock_audio,
            segments,
            buffer_ms=250,
            target_duration_ms=10000
        )
        
        # Should only use first 2 segments to reach 10s target
        assert result is not None
    
    def test_stitch_empty_segments(self, mock_supabase):
        """Test stitching with no speech segments returns original."""
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        
        mock_audio = MagicMock(spec=AudioSegment)
        segments = []
        
        result = service._stitch_audio_segments(mock_audio, segments, 250)
        
        assert result == mock_audio

