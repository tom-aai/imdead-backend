"""
Voice cloning service.
"""

import base64
import os
import logging
import tempfile
import time
import torch
import requests
from pydub import AudioSegment
import numpy as np
from cartesia import Cartesia

logger = logging.getLogger(__name__)


def _truncate_for_logging(text: str, max_length: int = 500) -> str:
    """Truncate text for logging to avoid swamping logs with large payloads."""
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}... [truncated, {len(text)} bytes total]"


class VoiceCloningService:
    """Service for processing voice recordings for cloning."""
    
    def __init__(
        self,
        supabase_client,
        voice_provider: str = "inworld",
        cartesia_api_key: str = None,
        inworld_api_key: str = None,
        inworld_workspace_id: str = None
    ):
        """
        Initialize the voice cloning service.
        
        Args:
            supabase_client: Initialized Supabase client
            voice_provider: Voice cloning provider to use ("inworld" or "cartesia", default: "inworld")
            cartesia_api_key: Cartesia API key (optional, will use env var if not provided)
            inworld_api_key: Inworld API key (optional, will use env var if not provided)
            inworld_workspace_id: Inworld workspace ID (optional, will use env var if not provided)
        """
        self.supabase = supabase_client
        self.voice_provider = voice_provider
        
        # Cartesia setup
        self.cartesia = Cartesia(api_key=cartesia_api_key or os.getenv("CARTESIA_API_KEY"))
        
        # Inworld setup
        self.inworld_api_key = inworld_api_key or os.getenv("INWORLD_API_KEY")
        self.inworld_workspace_id = inworld_workspace_id or os.getenv("INWORLD_WORKSPACE_ID")
    
    def process_voice_recording(self, session_id: str) -> dict:
        """
        Process voice recording for a given session.
        
        Process:
        1. Look up recording_id and user_id from interview_calls table
        2. Download recording from Supabase storage
        3. Process audio (silence removal, VAD)
        4. Clone voice with configured provider (Inworld or Cartesia)
        5. Update user_profiles with voice_id
        
        Args:
            session_id: The LiveKit session ID
            
        Returns:
            dict: Processing result with status and processed file path
            
        Raises:
            ValueError: If session_id is invalid or recording not found
            Exception: If processing fails
        """
        if not session_id:
            raise ValueError("session_id is required")
        
        if not self.supabase:
            raise Exception("Supabase client not initialized")
        
        logger.info(f"Processing voice recording for session {session_id}")
        
        # Step 1: Get recording info from interview_calls table
        recording_id, call_type, user_id = self._get_recording_info(session_id)
        
        if not recording_id:
            raise ValueError(f"No recording_id found for session {session_id}")
        
        if not user_id:
            raise ValueError(f"No user_id found for session {session_id}")
        
        
        # Check if user already has a voice clone - only clone for first-time users
        
        existing_voice_id = self._get_existing_voice_id(user_id)
        if existing_voice_id:
            logger.info(f"Skipping voice cloning - user {user_id} already has a voice clone: {existing_voice_id}")
            return {
                "status": "skipped",
                "message": "Voice cloning skipped - user already has a voice clone",
                "session_id": session_id,
                "recording_id": recording_id,
                "user_id": user_id,
                "existing_voice_id": existing_voice_id
            }
        
        logger.info(f"Processing voice cloning for session {session_id}")
        
        # Step 2: Download recording from Supabase storage
        local_recording_path = self._download_recording(recording_id, user_id)
        
        processed_path = None
        try:
            # Step 3: Process audio (silence removal, VAD)
            processed_path = self._process_audio(local_recording_path, recording_id)
            
            logger.info(f"Voice recording processing completed for session {session_id}")
                        
            # Step 4: Clone voice and update profile
            voice_id = self._clone_voice_and_update_profile(processed_path, user_id)
            
            return {
                "status": "success",
                "message": "Voice recording processed and cloned successfully",
                "session_id": session_id,
                "recording_id": recording_id,
                "user_id": user_id,
                "voice_id": voice_id
            }
            
        finally:
            # Clean up temporary files
            for path in [local_recording_path, processed_path]:
                if path and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except:
                        pass
    
    def clone_voice_from_audio_data(self, user_id: str, audio_data: bytes) -> dict:
        """
        Clone voice from raw audio data (e.g., from a re-clone recording).
        
        This method is used for the re-clone voice feature where users record
        a dedicated voice sample. Unlike process_voice_recording(),
        this method:
        - Accepts raw audio bytes directly (no session/recording lookup)
        - Auto-detects and converts WebM to WAV if needed
        - Skips VAD processing (the sample is already clean)
        - Always overwrites the existing voice_id
        
        Args:
            user_id: The user's ID
            audio_data: Raw audio bytes (WAV or WebM format)
            
        Returns:
            dict: Result with status and voice_id
            
        Raises:
            ValueError: If user_id is missing or audio_data is empty
            Exception: If cloning fails
        """
        if not user_id:
            raise ValueError("user_id is required")
        
        if not audio_data or len(audio_data) == 0:
            raise ValueError("audio_data is required and cannot be empty")
        
        if not self.supabase:
            raise Exception("Supabase client not initialized")
        
        logger.info(f"Cloning voice for user {user_id} ({len(audio_data):,} bytes)")
        
        temp_audio_path = None
        converted_path = None
        
        try:
            # Detect audio format by magic bytes
            is_webm = audio_data[:4] == b'\x1a\x45\xdf\xa3'  # WebM/Matroska magic bytes
            is_wav = audio_data[:4] == b'RIFF' and audio_data[8:12] == b'WAVE'
            
            if is_webm:
                # Save WebM to temp file and convert to WAV
                temp_webm = tempfile.NamedTemporaryFile(delete=False, suffix='.webm')
                temp_webm.write(audio_data)
                temp_webm.close()
                
                try:
                    audio = AudioSegment.from_file(temp_webm.name, format='webm')
                    
                    # Check if audio is silent
                    if audio.dBFS < -50 or audio.dBFS == float('-inf'):
                        raise ValueError("Recording appears to be silent. Please check your microphone.")
                    
                    logger.info(f"Audio: {len(audio)/1000:.1f}s, {audio.dBFS:.1f} dBFS")
                    
                    # Export as WAV (mono, 16-bit, 48kHz)
                    audio = audio.set_channels(1).set_frame_rate(48000).set_sample_width(2)
                    converted_path = tempfile.NamedTemporaryFile(delete=False, suffix='.wav').name
                    audio.export(converted_path, format='wav')
                    temp_audio_path = converted_path
                finally:
                    try:
                        os.unlink(temp_webm.name)
                    except:
                        pass
                        
            elif is_wav:
                temp_audio_file = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
                temp_audio_file.write(audio_data)
                temp_audio_file.close()
                temp_audio_path = temp_audio_file.name
                
                # Verify the audio has content
                try:
                    audio = AudioSegment.from_file(temp_audio_path, format='wav')
                    if audio.dBFS < -50:
                        logger.warning(f"Audio appears quiet: {audio.dBFS:.1f} dBFS")
                    else:
                        logger.info(f"Audio: {len(audio)/1000:.1f}s, {audio.dBFS:.1f} dBFS")
                except Exception as e:
                    logger.warning(f"Could not analyze WAV: {e}")
            else:
                # Unknown format, try to let pydub figure it out
                logger.warning(f"Unknown audio format, attempting conversion...")
                temp_input = tempfile.NamedTemporaryFile(delete=False, suffix='.bin')
                temp_input.write(audio_data)
                temp_input.close()
                
                try:
                    audio = AudioSegment.from_file(temp_input.name)
                    audio = audio.set_channels(1).set_frame_rate(48000).set_sample_width(2)
                    converted_path = tempfile.NamedTemporaryFile(delete=False, suffix='.wav').name
                    audio.export(converted_path, format='wav')
                    temp_audio_path = converted_path
                    logger.info(f"Audio: {len(audio)/1000:.1f}s, {audio.dBFS:.1f} dBFS")
                finally:
                    try:
                        os.unlink(temp_input.name)
                    except:
                        pass
            
            # Clone voice and update profile
            voice_id = self._clone_voice_and_update_profile(temp_audio_path, user_id)
            
            return {
                "status": "success",
                "message": "Voice re-cloned successfully",
                "user_id": user_id,
                "voice_id": voice_id
            }
            
        except Exception as e:
            logger.error(f"Voice cloning failed for user {user_id}: {e}")
            raise
            
        finally:
            # Clean up temp files
            if temp_audio_path and os.path.exists(temp_audio_path):
                try:
                    os.unlink(temp_audio_path)
                except:
                    pass
    
    def _get_recording_info(self, session_id: str) -> tuple[str, str, str]:
        """
        Get recording info from interview_calls table.
        
        Args:
            session_id: The LiveKit session ID
            
        Returns:
            Tuple of (recording_id, call_type, user_id)
            
        Raises:
            ValueError: If session not found
        """
        try:
            response = self.supabase.table("interview_calls")\
                .select("recording_id, call_type, user_id")\
                .eq("session_id", session_id)\
                .limit(1)\
                .execute()
            
            if not response.data or len(response.data) == 0:
                raise ValueError(f"No call found for session_id: {session_id}")
            
            call_data = response.data[0]
            recording_id = call_data.get('recording_id')
            call_type = call_data.get('call_type', 'interview')  # Default to 'interview' for backward compatibility
            user_id = call_data.get('user_id')
            
            return recording_id, call_type, user_id
            
        except Exception as e:
            logger.error(f"Failed to fetch recording info for session {session_id}: {e}")
            raise
    
    def _get_existing_voice_id(self, user_id: str) -> str | None:
        """
        Check if user already has a voice_id in user_profiles table.
        
        This returns None in three cases, all of which allow voice cloning to proceed:
        1. User has no record in user_profiles (new user)
        2. User has a record but voice_id is null/empty
        3. Database query fails (we err on the side of cloning)
        
        Args:
            user_id: The user's ID
            
        Returns:
            str: The existing voice_id if found, None otherwise
        """
        try:
            response = self.supabase.table("user_profiles")\
                .select("voice_id")\
                .eq("user_id", user_id)\
                .limit(1)\
                .execute()
            
            # Handle case where user has no profile record yet (new user)
            # response.data will be an empty list [], which is falsy
            if response.data and len(response.data) > 0:
                voice_id = response.data[0].get('voice_id')
                if voice_id:
                    logger.debug(f"Found existing voice_id for user {user_id}: {voice_id}")
                    return voice_id
            
            logger.debug(f"No existing voice_id found for user {user_id}")
            return None
            
        except Exception as e:
            logger.warning(f"Failed to check existing voice_id for user {user_id}: {e}")
            # Return None to allow voice cloning to proceed if check fails
            return None
    
    def _download_recording(self, recording_id: str, user_id: str) -> str:
        """
        Download recording from Supabase storage bucket.
        
        Args:
            recording_id: The recording UUID
            user_id: The user's ID
            
        Returns:
            str: Path to downloaded temporary file
            
        Raises:
            Exception: If download fails
        """
        try:
            storage_path = f"{user_id}/{recording_id}.mp4"
            
            logger.debug(f"Downloading recording from interview-recordings bucket: {storage_path}")
            
            # Download from Supabase storage
            response = self.supabase.storage.from_("interview-recordings").download(storage_path)
            
            if not response:
                raise Exception(f"Empty response downloading recording {storage_path}")
            
            # Save to temporary file
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
            temp_file.write(response)
            temp_file.close()
            
            logger.debug(f"Recording downloaded successfully ({len(response)} bytes)")
            return temp_file.name
            
        except Exception as e:
            logger.error(f"Failed to download recording '{storage_path}': {e}")
            raise Exception(f"Recording download failed for '{storage_path}': {e}")
    
    def _process_audio(
        self,
        input_path: str,
        recording_id: str,
        buffer_ms: int = 250,
        sample_rate: int = 16000,
        min_silence_length_ms: int = 500
    ) -> str:
        """
        Process audio to remove silence using Silero VAD.
        
        Args:
            input_path: Path to input audio file
            recording_id: The recording UUID (for naming output)
            buffer_ms: Buffer duration between speech segments (milliseconds)
            sample_rate: Sample rate for VAD processing (16000 Hz recommended)
            min_silence_length_ms: Minimum silence length to be considered a gap
            
        Returns:
            str: Path to processed audio file
            
        Raises:
            Exception: If processing fails
        """
        try:
            logger.debug(f"Processing audio: {input_path}")
            
            # Step 1: Load audio
            audio = self._load_audio(input_path)
            
            # Step 2: Detect speech segments using Silero VAD
            speech_segments = self._detect_speech_segments(
                audio,
                sample_rate,
                min_silence_length_ms
            )
            
            # Step 3: Stitch segments together
            processed_audio = self._stitch_audio_segments(audio, speech_segments, buffer_ms)
            
            # Step 4: Export to temporary file
            output_path = os.path.join(
                tempfile.gettempdir(),
                f"processed_{recording_id}.wav"
            )
            
            logger.debug(f"Exporting processed audio to: {output_path}")
            processed_audio.export(
                output_path,
                format="wav"
            )
            
            logger.debug(f"Audio processing complete: {len(processed_audio)}ms processed")
            return output_path
            
        except Exception as e:
            logger.error(f"Audio processing failed: {e}")
            raise Exception(f"Audio processing failed: {e}")
    
    def _load_audio(self, input_path: str) -> AudioSegment:
        """
        Load audio file and convert to AudioSegment.
        
        Args:
            input_path: Path to input audio file
            
        Returns:
            AudioSegment object
        """
        logger.debug(f"Loading audio from: {input_path}")
        audio = AudioSegment.from_file(input_path)
        logger.debug(f"Audio loaded: {len(audio)}ms, {audio.frame_rate}Hz, {audio.channels} channel(s)")
        return audio
    
    def _detect_speech_segments(
        self,
        audio: AudioSegment,
        sample_rate: int,
        min_silence_length_ms: int
    ) -> list[tuple[int, int]]:
        """
        Use Silero VAD to detect speech segments in the audio.
        
        Args:
            audio: AudioSegment object
            sample_rate: Sample rate for VAD processing
            min_silence_length_ms: Minimum silence length between speech
            
        Returns:
            List of (start_ms, end_ms) tuples indicating speech segments
        """
        logger.debug("Detecting speech segments using Silero VAD...")
        
        # Load Silero VAD model
        model, utils = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False,
            onnx=False
        )
        
        (get_speech_timestamps, _, read_audio, *_) = utils
        
        # Convert audio to the right format for Silero
        # Silero expects 16kHz mono audio
        audio_16k = audio.set_frame_rate(sample_rate).set_channels(1)
        
        # Convert to numpy array
        samples = np.array(audio_16k.get_array_of_samples()).astype(np.float32)
        
        # Normalize to [-1, 1]
        if audio_16k.sample_width == 2:  # 16-bit audio
            samples = samples / 32768.0
        elif audio_16k.sample_width == 4:  # 32-bit audio
            samples = samples / 2147483648.0
        
        # Convert to torch tensor
        audio_tensor = torch.from_numpy(samples)
        
        # Get speech timestamps
        speech_timestamps = get_speech_timestamps(
            audio_tensor,
            model,
            sampling_rate=sample_rate,
            threshold=0.5,  # Speech probability threshold
            min_speech_duration_ms=100,  # Minimum speech duration
            min_silence_duration_ms=min_silence_length_ms,
            window_size_samples=512,
        )
        
        # Convert sample indices to milliseconds
        segments = []
        for timestamp in speech_timestamps:
            start_sample = timestamp['start']
            end_sample = timestamp['end']
            
            # Convert from 16kHz samples to milliseconds
            start_ms = int((start_sample / sample_rate) * 1000)
            end_ms = int((end_sample / sample_rate) * 1000)
            
            segments.append((start_ms, end_ms))
        
        logger.debug(f"Found {len(segments)} speech segments")
        return segments
    
    def _stitch_audio_segments(
        self,
        audio: AudioSegment,
        segments: list[tuple[int, int]],
        buffer_ms: int,
        target_duration_ms: int = 10000
    ) -> AudioSegment:
        """
        Extract speech segments and stitch them together with buffers.
        Stops adding segments once the target duration is reached.
        
        Args:
            audio: Original AudioSegment
            segments: List of (start_ms, end_ms) tuples
            buffer_ms: Buffer duration to add between segments
            target_duration_ms: Target duration in milliseconds (default: 10 seconds)
            
        Returns:
            Stitched AudioSegment
        """
        logger.debug(f"Stitching segments with {buffer_ms}ms buffers (target: {target_duration_ms}ms)...")
        
        if not segments:
            logger.warning("No speech segments found, returning original audio")
            return audio
        
        # Create silence buffer
        silence_buffer = AudioSegment.silent(duration=buffer_ms, frame_rate=audio.frame_rate)
        
        # Extract and concatenate segments until target duration is reached
        result = None
        total_duration_ms = 0
        segments_used = 0
        
        for i, (start_ms, end_ms) in enumerate(segments):
            segment = audio[start_ms:end_ms]
            segment_duration = end_ms - start_ms
            
            if result is None:
                result = segment
                total_duration_ms = segment_duration
                segments_used = 1
                logger.debug(f"  Segment {i+1}: {start_ms}ms - {end_ms}ms ({segment_duration}ms) | Total: {total_duration_ms}ms")
                
                # If first segment is already >= target duration, stop here
                if total_duration_ms >= target_duration_ms:
                    logger.debug(f"First segment meets target duration ({total_duration_ms}ms)")
                    break
            else:
                # Check if adding this segment (plus buffer) would be useful
                potential_total = total_duration_ms + buffer_ms + segment_duration
                
                # Add buffer between segments
                result = result + silence_buffer + segment
                total_duration_ms = len(result)  # Actual duration of stitched audio
                segments_used += 1
                
                logger.debug(f"  Segment {i+1}: {start_ms}ms - {end_ms}ms ({segment_duration}ms) | Total: {total_duration_ms}ms")
                
                # Stop if we've reached or exceeded the target duration
                if total_duration_ms >= target_duration_ms:
                    logger.debug(f"Reached target duration: {total_duration_ms}ms, using {segments_used} segments")
                    break
        
        logger.debug(f"Stitched {segments_used} of {len(segments)} segments, final duration: {total_duration_ms}ms")
        return result
    
    def _clone_voice_with_cartesia(self, audio_file_path: str, user_id: str) -> str:
        """
        Clone voice using Cartesia API.
        
        Args:
            audio_file_path: Path to the processed audio file (WAV)
            user_id: The user's ID (used for naming the voice)
            
        Returns:
            str: The voice_id from Cartesia
            
        Raises:
            Exception: If cloning fails
        """
        try:
            if not os.path.exists(audio_file_path):
                raise FileNotFoundError(f"Audio file not found at: {audio_file_path}")
            
            voice_name = user_id
            
            
            # Clone the voice using Cartesia SDK
            with open(audio_file_path, "rb") as file_handle:
                response = self.cartesia.voices.clone(
                    clip=file_handle,
                    name=voice_name,
                    description=f"Cloned voice for user {user_id}",
                    language="en",
                    mode="similarity",
                    enhance=True
                )
            
            # Extract voice ID from response (VoiceMetadata object)
            voice_id = response.id
            
            return voice_id
            
        except Exception as e:
            raise Exception(f"Cartesia voice cloning failed: {e}")
    
    def _clone_voice_with_inworld(
        self,
        audio_file_path: str,
        user_id: str,
        max_retries: int = 3,
        base_delay: float = 60.0
    ) -> str:
        """
        Clone voice using Inworld API with retry logic for rate limiting.
        
        Args:
            audio_file_path: Path to the processed audio file (WAV)
            user_id: The user's ID (used for naming the voice)
            max_retries: Maximum number of retries for rate limit errors (default: 3)
            base_delay: Base delay in seconds for exponential backoff (default: 60s)
            
        Returns:
            str: The voiceId from Inworld
            
        Raises:
            Exception: If cloning fails after all retries
        """
        if not os.path.exists(audio_file_path):
            raise FileNotFoundError(f"Audio file not found at: {audio_file_path}")
        
        if not self.inworld_api_key:
            raise ValueError("Inworld API key not configured")
        
        if not self.inworld_workspace_id:
            raise ValueError("Inworld workspace ID not configured")
        
        voice_name = user_id
        
        # Read audio file and encode as base64
        with open(audio_file_path, 'rb') as f:
            audio_data = f.read()
        audio_base64 = base64.b64encode(audio_data).decode('utf-8')
        
        # Format workspace path
        workspace_id = self.inworld_workspace_id
        if not workspace_id.startswith("workspaces/"):
            workspace_path = f"workspaces/{workspace_id}"
        else:
            workspace_path = workspace_id
        
        url = f"https://api.inworld.ai/voices/v1/{workspace_path}/voices:clone"
        
        headers = {
            "Authorization": f"Basic {self.inworld_api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "displayName": voice_name,
            "langCode": "EN_US",
            "voiceSamples": [
                {
                    "audioData": audio_base64
                }
            ]
        }
        
        last_error = None
        
        for attempt in range(max_retries + 1):
            try:
                response = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=300  # Voice cloning can take a while
                )
                
                if response.status_code == 200:
                    result = response.json()
                    
                    # Extract voice ID from response
                    voice = result.get("voice") or {}
                    voice_id = voice.get("voiceId") if isinstance(voice, dict) else None
                    
                    if not voice_id:
                        truncated_result = _truncate_for_logging(str(result))
                        raise Exception(f"No voiceId in Inworld response: {truncated_result}")
                    
                    return voice_id
                
                # Handle rate limiting with exponential backoff
                if response.status_code == 429:
                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(f"Rate limited, retrying in {delay:.0f}s (attempt {attempt + 1}/{max_retries + 1})")
                        time.sleep(delay)
                        continue
                    else:
                        raise Exception(f"Inworld API rate limited after {max_retries + 1} attempts")
                
                # For other errors, don't retry
                truncated_response = _truncate_for_logging(response.text)
                raise Exception(f"Inworld API error {response.status_code}: {truncated_response}")
                
            except requests.exceptions.RequestException as e:
                # Network/timeout errors - could retry but let's fail fast for now
                logger.error(f"Inworld API request failed: {e}")
                last_error = e
                raise Exception(f"Voice cloning request failed: {e}")
        
        # Should not reach here, but just in case
        raise Exception(f"Voice cloning failed after {max_retries + 1} attempts: {last_error}")
    
    def _clone_voice_and_update_profile(self, audio_path: str, user_id: str) -> str:
        """
        Clone voice using the configured provider and update user profile.
        
        This helper method consolidates the cloning + profile update logic
        used by both process_voice_recording() and clone_voice_from_audio_data().
        
        Args:
            audio_path: Path to the audio file (WAV format)
            user_id: The user's ID
            
        Returns:
            str: The voice_id from the cloning provider
            
        Raises:
            Exception: If cloning or profile update fails
        """
        # Clone voice with configured provider
        if self.voice_provider == "cartesia":
            voice_id = self._clone_voice_with_cartesia(audio_path, user_id)
        else:
            voice_id = self._clone_voice_with_inworld(audio_path, user_id)
        
        # Update user_profiles table with voice_id
        self._update_user_profile_voice_id(user_id, voice_id)
        
        logger.info(f"Voice cloned: {voice_id}")
        
        return voice_id
    
    def _update_user_profile_voice_id(self, user_id: str, voice_id: str) -> None:
        """
        Update the user_profiles table with the voice_id.
        
        Args:
            user_id: The user's ID
            voice_id: The voice_id from the voice cloning provider (Inworld or Cartesia)
            
        Raises:
            Exception: If update fails
        """
        try:
            existing = self.supabase.table("user_profiles")\
                .select("id")\
                .eq("user_id", user_id)\
                .execute()
            
            if existing.data and len(existing.data) > 0:
                self.supabase.table("user_profiles")\
                    .update({"voice_id": voice_id})\
                    .eq("user_id", user_id)\
                    .execute()
            else:
                self.supabase.table("user_profiles")\
                    .insert({"user_id": user_id, "voice_id": voice_id})\
                    .execute()
            
        except Exception as e:
            raise Exception(f"Database update failed: {e}")
