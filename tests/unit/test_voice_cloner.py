"""
Unit tests for VoiceCloningService.

Tests cover:
- Voice recording processing and VAD
- Audio download from Supabase storage
- Silero VAD for speech detection
- Audio segment stitching
- Cartesia voice cloning integration
- Inworld voice cloning integration
- Provider selection (Cartesia vs Inworld)
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
import requests

from services.voice_cloner import VoiceCloningService


class TestVoiceCloningServiceInit:
    """Test VoiceCloningService initialization."""
    
    def test_init_with_cartesia_api_key(self, mock_supabase):
        """Test initialization with Cartesia API key provided."""
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-api-key")
        
        assert service.supabase == mock_supabase
        assert service.cartesia is not None
    
    def test_init_without_cartesia_api_key(self, mock_supabase):
        """Test initialization uses env var when no Cartesia key provided."""
        with patch.dict('os.environ', {'CARTESIA_API_KEY': 'env-api-key'}):
            service = VoiceCloningService(mock_supabase, cartesia_api_key=None)
            assert service.cartesia is not None
    
    def test_init_with_inworld_credentials(self, mock_supabase):
        """Test initialization with Inworld credentials."""
        service = VoiceCloningService(
            mock_supabase,
            inworld_api_key="test-inworld-key",
            inworld_workspace_id="test-workspace"
        )
        
        assert service.inworld_api_key == "test-inworld-key"
        assert service.inworld_workspace_id == "test-workspace"
    
    def test_init_inworld_from_env_vars(self, mock_supabase):
        """Test initialization uses env vars for Inworld when not provided."""
        with patch.dict('os.environ', {
            'INWORLD_API_KEY': 'env-inworld-key',
            'INWORLD_WORKSPACE_ID': 'env-workspace'
        }):
            service = VoiceCloningService(mock_supabase)
            assert service.inworld_api_key == 'env-inworld-key'
            assert service.inworld_workspace_id == 'env-workspace'
    
    def test_default_provider_is_inworld(self, mock_supabase):
        """Test that default voice provider is Inworld."""
        service = VoiceCloningService(mock_supabase)
        assert service.voice_provider == "inworld"
    
    def test_explicit_provider_setting(self, mock_supabase):
        """Test explicit provider setting."""
        service = VoiceCloningService(mock_supabase, voice_provider="cartesia")
        assert service.voice_provider == "cartesia"


class TestProcessVoiceRecording:
    """Test the main process_voice_recording method."""
    
    def test_successful_processing_onboarding_call_with_inworld(self, mock_supabase):
        """Test successful voice cloning for onboarding call using Inworld (default)."""
        service = VoiceCloningService(
            mock_supabase,
            inworld_api_key="test-inworld-key",
            inworld_workspace_id="test-workspace"
        )
        
        # Mock the internal methods to avoid complex mock chaining
        with patch.object(service, '_get_recording_info', return_value=('rec-123', 'onboarding', 'user-456')):
            with patch.object(service, '_download_recording', return_value='/tmp/recording.mp3'):
                with patch.object(service, '_process_audio', return_value='/tmp/processed.wav'):
                    with patch.object(service, '_get_user_id_from_session', return_value='user-456'):
                        with patch.object(service, '_clone_voice_with_inworld', return_value='inworld-voice-id-123'):
                            with patch.object(service, '_update_user_profile_voice_id'):
                                result = service.process_voice_recording('session-123')
                                
                                assert result['status'] == 'success'
                                assert result['session_id'] == 'session-123'
                                assert result['recording_id'] == 'rec-123'
                                assert result['user_id'] == 'user-456'
                                assert result['voice_id'] == 'inworld-voice-id-123'
    
    def test_successful_processing_onboarding_call_with_cartesia(self, mock_supabase, mock_cartesia_client):
        """Test successful voice cloning for onboarding call using Cartesia."""
        service = VoiceCloningService(
            mock_supabase,
            voice_provider="cartesia",
            cartesia_api_key="test-key"
        )
        service.cartesia = mock_cartesia_client
        
        # Mock the internal methods to avoid complex mock chaining
        with patch.object(service, '_get_recording_info', return_value=('rec-123', 'onboarding', 'user-456')):
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
        service = VoiceCloningService(mock_supabase)
        
        # Mock _get_recording_info to return interview call
        with patch.object(service, '_get_recording_info', return_value=('rec-123', 'interview', 'user-456')):
            result = service.process_voice_recording('session-123')
            
            assert result['status'] == 'skipped'
            assert 'Voice cloning skipped' in result['message']
            assert result['call_type'] == 'interview'
    
    def test_missing_session_id(self, mock_supabase):
        """Test error when session_id is missing."""
        service = VoiceCloningService(mock_supabase)
        
        with pytest.raises(ValueError, match="session_id is required"):
            service.process_voice_recording(None)
    
    def test_no_recording_found(self, mock_supabase):
        """Test error when no recording_id found."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {'recording_id': None, 'call_type': 'onboarding', 'user_id': 'user-456'}
        ]
        
        service = VoiceCloningService(mock_supabase)
        
        with pytest.raises(ValueError, match="No recording_id found"):
            service.process_voice_recording('session-123')


class TestGetRecordingInfo:
    """Test _get_recording_info method."""
    
    def test_successful_lookup(self, mock_supabase):
        """Test successful recording info lookup."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {'recording_id': 'rec-456', 'call_type': 'onboarding', 'user_id': 'user-789'}
        ]
        
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        recording_id, call_type, user_id = service._get_recording_info('session-123')
        
        assert recording_id == 'rec-456'
        assert call_type == 'onboarding'
        assert user_id == 'user-789'
    
    def test_default_call_type(self, mock_supabase):
        """Test default call_type when not specified."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {'recording_id': 'rec-456', 'user_id': 'user-789'}
        ]
        
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        recording_id, call_type, user_id = service._get_recording_info('session-123')
        
        assert call_type == 'interview'  # Default
        assert user_id == 'user-789'
    
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
        result_path = service._download_recording('rec-123', 'user-456')
        
        try:
            assert os.path.exists(result_path)
            assert result_path.endswith('.mp4')
            
            with open(result_path, 'rb') as f:
                downloaded_data = f.read()
            assert downloaded_data == fake_audio
            
            # Verify correct bucket and path
            mock_supabase.storage.from_.assert_called_with("interview-recordings")
            mock_supabase.storage.from_.return_value.download.assert_called_with("user-456/rec-123.mp4")
            
        finally:
            if os.path.exists(result_path):
                os.unlink(result_path)
    
    def test_download_empty_response(self, mock_supabase):
        """Test error when download returns empty."""
        mock_supabase.storage.from_.return_value.download.return_value = None
        
        service = VoiceCloningService(mock_supabase, cartesia_api_key="test-key")
        
        with pytest.raises(Exception, match="Empty response downloading recording"):
            service._download_recording('rec-123', 'user-456')


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


class TestCloneVoiceWithInworld:
    """Test _clone_voice_with_inworld method."""
    
    def test_successful_cloning(self, mock_supabase, mock_inworld_response):
        """Test successful voice cloning with Inworld."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as temp_file:
            temp_file.write(b'fake wav data')
            temp_file.flush()
            audio_path = temp_file.name
        
        service = VoiceCloningService(
            mock_supabase,
            voice_provider="inworld",
            inworld_api_key="test-inworld-key",
            inworld_workspace_id="test-workspace"
        )
        
        try:
            with patch('services.voice_cloner.requests.post') as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = mock_inworld_response
                mock_post.return_value = mock_response
                
                result = service._clone_voice_with_inworld(audio_path, 'user-456')
                
                assert result == 'inworld-cloned-voice-id-456'
                
                # Verify Inworld API was called correctly
                mock_post.assert_called_once()
                call_args = mock_post.call_args
                assert 'api.inworld.ai' in call_args[0][0]
                assert 'voices:clone' in call_args[0][0]
                assert call_args[1]['headers']['Authorization'] == 'Basic test-inworld-key'
                
                # Verify payload
                payload = call_args[1]['json']
                assert payload['displayName'] == 'user-456'
                assert payload['langCode'] == 'EN_US'
                assert 'voiceSamples' in payload
                assert len(payload['voiceSamples']) == 1
        finally:
            if os.path.exists(audio_path):
                os.unlink(audio_path)
    
    def test_file_not_found(self, mock_supabase):
        """Test error when audio file doesn't exist."""
        service = VoiceCloningService(
            mock_supabase,
            voice_provider="inworld",
            inworld_api_key="test-inworld-key",
            inworld_workspace_id="test-workspace"
        )
        
        with pytest.raises(Exception, match="Voice cloning failed"):
            service._clone_voice_with_inworld('/nonexistent/path.wav', 'user-456')
    
    def test_missing_api_key(self, mock_supabase):
        """Test error when Inworld API key is not configured."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as temp_file:
            temp_file.write(b'fake wav data')
            temp_file.flush()
            audio_path = temp_file.name
        
        service = VoiceCloningService(
            mock_supabase,
            voice_provider="inworld",
            inworld_api_key=None,
            inworld_workspace_id="test-workspace"
        )
        # Clear env var if set
        service.inworld_api_key = None
        
        try:
            with pytest.raises(Exception, match="Voice cloning failed"):
                service._clone_voice_with_inworld(audio_path, 'user-456')
        finally:
            if os.path.exists(audio_path):
                os.unlink(audio_path)
    
    def test_missing_workspace_id(self, mock_supabase):
        """Test error when Inworld workspace ID is not configured."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as temp_file:
            temp_file.write(b'fake wav data')
            temp_file.flush()
            audio_path = temp_file.name
        
        service = VoiceCloningService(
            mock_supabase,
            voice_provider="inworld",
            inworld_api_key="test-inworld-key",
            inworld_workspace_id=None
        )
        # Clear env var if set
        service.inworld_workspace_id = None
        
        try:
            with pytest.raises(Exception, match="Voice cloning failed"):
                service._clone_voice_with_inworld(audio_path, 'user-456')
        finally:
            if os.path.exists(audio_path):
                os.unlink(audio_path)
    
    def test_api_error(self, mock_supabase):
        """Test error handling when Inworld API fails."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as temp_file:
            temp_file.write(b'fake wav data')
            temp_file.flush()
            audio_path = temp_file.name
        
        service = VoiceCloningService(
            mock_supabase,
            voice_provider="inworld",
            inworld_api_key="test-inworld-key",
            inworld_workspace_id="test-workspace"
        )
        
        try:
            with patch('services.voice_cloner.requests.post') as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 500
                mock_response.text = "Internal Server Error"
                mock_post.return_value = mock_response
                
                with pytest.raises(Exception, match="Voice cloning failed"):
                    service._clone_voice_with_inworld(audio_path, 'user-456')
        finally:
            if os.path.exists(audio_path):
                os.unlink(audio_path)
    
    def test_workspace_id_prefix_handling(self, mock_supabase, mock_inworld_response):
        """Test that workspace ID with 'workspaces/' prefix is handled correctly."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as temp_file:
            temp_file.write(b'fake wav data')
            temp_file.flush()
            audio_path = temp_file.name
        
        # Test with already-prefixed workspace ID
        service = VoiceCloningService(
            mock_supabase,
            voice_provider="inworld",
            inworld_api_key="test-inworld-key",
            inworld_workspace_id="workspaces/test-workspace"
        )
        
        try:
            with patch('services.voice_cloner.requests.post') as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.json.return_value = mock_inworld_response
                mock_post.return_value = mock_response
                
                result = service._clone_voice_with_inworld(audio_path, 'user-456')
                
                # Verify URL doesn't double the prefix
                call_args = mock_post.call_args
                url = call_args[0][0]
                assert 'workspaces/workspaces/' not in url
                assert 'workspaces/test-workspace' in url
        finally:
            if os.path.exists(audio_path):
                os.unlink(audio_path)


class TestProviderSelection:
    """Test voice provider selection."""
    
    def test_default_provider_is_inworld(self, mock_supabase):
        """Test that default provider is Inworld."""
        service = VoiceCloningService(mock_supabase)
        assert service.voice_provider == "inworld"
    
    def test_explicit_inworld_provider(self, mock_supabase):
        """Test explicitly setting Inworld provider."""
        service = VoiceCloningService(mock_supabase, voice_provider="inworld")
        assert service.voice_provider == "inworld"
    
    def test_cartesia_provider(self, mock_supabase):
        """Test setting Cartesia provider."""
        service = VoiceCloningService(mock_supabase, voice_provider="cartesia")
        assert service.voice_provider == "cartesia"
    
    def test_process_uses_inworld_by_default(self, mock_supabase, mock_inworld_response):
        """Test that process_voice_recording uses Inworld by default."""
        service = VoiceCloningService(
            mock_supabase,
            inworld_api_key="test-key",
            inworld_workspace_id="test-workspace"
        )
        
        with patch.object(service, '_get_recording_info', return_value=('rec-123', 'onboarding', 'user-456')):
            with patch.object(service, '_download_recording', return_value='/tmp/recording.mp3'):
                with patch.object(service, '_process_audio', return_value='/tmp/processed.wav'):
                    with patch.object(service, '_get_user_id_from_session', return_value='user-456'):
                        with patch.object(service, '_clone_voice_with_inworld', return_value='inworld-voice-id') as mock_inworld:
                            with patch.object(service, '_clone_voice_with_cartesia') as mock_cartesia:
                                with patch.object(service, '_update_user_profile_voice_id'):
                                    result = service.process_voice_recording('session-123')
                                    
                                    # Verify Inworld was called, not Cartesia
                                    mock_inworld.assert_called_once()
                                    mock_cartesia.assert_not_called()
                                    assert result['voice_id'] == 'inworld-voice-id'
    
    def test_process_uses_cartesia_when_specified(self, mock_supabase, mock_cartesia_client):
        """Test that process_voice_recording uses Cartesia when specified."""
        service = VoiceCloningService(
            mock_supabase,
            voice_provider="cartesia",
            cartesia_api_key="test-key"
        )
        service.cartesia = mock_cartesia_client
        
        with patch.object(service, '_get_recording_info', return_value=('rec-123', 'onboarding', 'user-456')):
            with patch.object(service, '_download_recording', return_value='/tmp/recording.mp3'):
                with patch.object(service, '_process_audio', return_value='/tmp/processed.wav'):
                    with patch.object(service, '_get_user_id_from_session', return_value='user-456'):
                        with patch.object(service, '_clone_voice_with_inworld') as mock_inworld:
                            with patch.object(service, '_clone_voice_with_cartesia', return_value='cartesia-voice-id') as mock_cartesia:
                                with patch.object(service, '_update_user_profile_voice_id'):
                                    result = service.process_voice_recording('session-123')
                                    
                                    # Verify Cartesia was called, not Inworld
                                    mock_cartesia.assert_called_once()
                                    mock_inworld.assert_not_called()
                                    assert result['voice_id'] == 'cartesia-voice-id'


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

