"""
Voice cloning service.
"""

import base64
import os
import logging
import tempfile
import torch
import requests
from pydub import AudioSegment
import numpy as np
from cartesia import Cartesia

logger = logging.getLogger(__name__)


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
        1. Look up recording_id from interview_calls table
        2. Download recording from Supabase storage
        3. Process audio (silence removal, VAD)
        4. Get user_id from interview_calls table
        5. Clone voice with configured provider (Inworld or Cartesia)
        6. Update user_profiles with voice_id
        
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
        
        logger.debug(f"Found recording_id: {recording_id}, call_type: {call_type}, user_id: {user_id}")
        
        # Only clone voices for onboarding calls
        if call_type != "onboarding":
            logger.info(f"Skipping voice cloning for '{call_type}' call - only onboarding calls require voice cloning")
            return {
                "status": "skipped",
                "message": f"Voice cloning skipped for {call_type} call",
                "session_id": session_id,
                "recording_id": recording_id,
                "call_type": call_type
            }
        
        logger.info(f"Processing voice cloning for onboarding call {session_id}")
        
        # Step 2: Download recording from Supabase storage
        local_recording_path = self._download_recording(recording_id, user_id)
        
        processed_path = None
        try:
            # Step 3: Process audio (silence removal, VAD)
            processed_path = self._process_audio(local_recording_path, recording_id)
            
            logger.info(f"Voice recording processing completed for session {session_id}")
                        
            # Step 4: Get user_id for voice cloning and profile update
            user_id = self._get_user_id_from_session(session_id)
            
            if not user_id:
                raise ValueError(f"No user_id found for session {session_id}")
            
            # Step 5: Clone voice with configured provider
            if self.voice_provider == "cartesia":
                voice_id = self._clone_voice_with_cartesia(processed_path, user_id)
                logger.info(f"Voice cloned successfully with Cartesia. Voice ID: {voice_id}")
            else:
                # Default to Inworld
                voice_id = self._clone_voice_with_inworld(processed_path, user_id)
                logger.info(f"Voice cloned successfully with Inworld. Voice ID: {voice_id}")
            
            # Step 6: Update user_profiles table with voice_id
            self._update_user_profile_voice_id(user_id, voice_id)
            
            logger.info(f"Updated user_profiles with voice_id for user {user_id}")
            
            return {
                "status": "success",
                "message": "Voice recording processed and cloned successfully",
                "session_id": session_id,
                "recording_id": recording_id,
                "user_id": user_id,
                "voice_id": voice_id
            }
            
        finally:
            # Clean up temporary input file
            if local_recording_path and os.path.exists(local_recording_path):
                try:
                    os.unlink(local_recording_path)
                    logger.debug(f"Cleaned up temporary input file: {local_recording_path}")
                except Exception as e:
                    logger.warning(f"Failed to clean up temporary file {local_recording_path}: {e}")
            
            # Clean up processed file
            if processed_path and os.path.exists(processed_path):
                try:
                    os.unlink(processed_path)
                    logger.debug(f"Cleaned up processed file: {processed_path}")
                except Exception as e:
                    logger.warning(f"Failed to clean up processed file {processed_path}: {e}")
    
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
            
            logger.info(f"Cloning voice with Cartesia: '{voice_name}'")
            
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
            
            logger.info(f"Voice cloned successfully. Voice ID: {voice_id}")
            return voice_id
            
        except Exception as e:
            logger.error(f"Failed to clone voice with Cartesia: {e}")
            raise Exception(f"Voice cloning failed: {e}")
    
    def _clone_voice_with_inworld(self, audio_file_path: str, user_id: str) -> str:
        """
        Clone voice using Inworld API.
        
        Args:
            audio_file_path: Path to the processed audio file (WAV)
            user_id: The user's ID (used for naming the voice)
            
        Returns:
            str: The voiceId from Inworld
            
        Raises:
            Exception: If cloning fails
        """
        try:
            if not os.path.exists(audio_file_path):
                raise FileNotFoundError(f"Audio file not found at: {audio_file_path}")
            
            if not self.inworld_api_key:
                raise ValueError("Inworld API key not configured")
            
            if not self.inworld_workspace_id:
                raise ValueError("Inworld workspace ID not configured")
            
            voice_name = user_id
            
            logger.info(f"Cloning voice with Inworld: '{voice_name}'")
            
            # Read audio file and encode as base64
            with open(audio_file_path, 'rb') as f:
                audio_data = f.read()
            audio_base64 = base64.b64encode(audio_data).decode('utf-8')
            
            logger.debug(f"Audio file size: {len(audio_data):,} bytes")
            
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
            
            logger.debug(f"Calling Inworld API: {url}")
            
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=300  # Voice cloning can take a while
            )
            
            if response.status_code != 200:
                logger.error(f"Inworld API error: {response.status_code} - {response.text}")
                raise Exception(f"Inworld API returned status {response.status_code}: {response.text}")
            
            result = response.json()
            
            # Extract voice ID from response
            voice = result.get("voice", {})
            voice_id = voice.get("voiceId")
            
            if not voice_id:
                raise Exception(f"No voiceId in Inworld response: {result}")
            
            logger.info(f"Voice cloned successfully with Inworld. Voice ID: {voice_id}")
            return voice_id
            
        except Exception as e:
            logger.error(f"Failed to clone voice with Inworld: {e}")
            raise Exception(f"Voice cloning failed: {e}")
    
    def _get_user_id_from_session(self, session_id: str) -> str:
        """
        Get user_id from interview_calls table.
        
        Args:
            session_id: The LiveKit session ID
            
        Returns:
            str: user_id
            
        Raises:
            ValueError: If session not found
        """
        try:
            response = self.supabase.table("interview_calls")\
                .select("user_id")\
                .eq("session_id", session_id)\
                .limit(1)\
                .execute()
            
            if not response.data or len(response.data) == 0:
                raise ValueError(f"No call found for session_id: {session_id}")
            
            call_data = response.data[0]
            user_id = call_data.get('user_id')
            
            return user_id
            
        except Exception as e:
            logger.error(f"Failed to fetch user_id for session {session_id}: {e}")
            raise
    
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
            # Check if profile exists
            existing = self.supabase.table("user_profiles")\
                .select("id")\
                .eq("user_id", user_id)\
                .execute()
            
            if existing.data and len(existing.data) > 0:
                # Update existing profile
                logger.debug(f"Updating voice_id for user {user_id}")
                self.supabase.table("user_profiles")\
                    .update({"voice_id": voice_id})\
                    .eq("user_id", user_id)\
                    .execute()
            else:
                # Create new profile with just the voice_id
                logger.debug(f"Creating new profile with voice_id for user {user_id}")
                self.supabase.table("user_profiles")\
                    .insert({"user_id": user_id, "voice_id": voice_id})\
                    .execute()
            
            logger.debug(f"Successfully updated voice_id in user_profiles for user {user_id}")
            
        except Exception as e:
            logger.error(f"Failed to update user_profiles with voice_id: {e}")
            raise Exception(f"Database update failed: {e}")
