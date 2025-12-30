"""
Hot take video generation service.

This service generates videos from hot takes:
1. Generates audio from hot_take_x text using Cartesia
2. Uses the Hedra avatar to create a video with the audio
3. Optionally adds captions using the CaptionGenerator service
"""

import os
import logging
import tempfile
import time
import json
import wave
from pathlib import Path
from typing import List, Dict, Tuple
import requests
from cartesia import Cartesia

# Import CaptionGenerator for adding captions
from .caption_generator import CaptionGenerator

logger = logging.getLogger(__name__)

# Hedra Character-3 model ID
CHARACTER_3_MODEL_ID = "d1dd37a3-e39a-4854-a298-6510289f9cf2"


class HedraSession(requests.Session):
    """Custom session class that prepends the base URL to all requests"""
    
    def __init__(self, api_key: str):
        super().__init__()
        self.base_url = "https://api.hedra.com/web-app/public"
        self.headers["x-api-key"] = api_key

    def request(self, method, url, *args, **kwargs):
        full_url = f"{self.base_url}{url}"
        return super().request(method, full_url, *args, **kwargs)


class HotTakeVideoGenerationService:
    """Service for generating videos from hot takes."""
    
    def __init__(
        self,
        supabase_client,
        cartesia_api_key: str = None,
        hedra_api_key: str = None,
        output_dir: str = None
    ):
        """
        Initialize the hot take video generation service.
        
        Args:
            supabase_client: Initialized Supabase client
            cartesia_api_key: Cartesia API key (optional, will use env var if not provided)
            hedra_api_key: Hedra API key (optional, will use env var if not provided)
            output_dir: Directory to save generated videos (optional, defaults to temp dir)
        """
        self.supabase = supabase_client
        self.cartesia_api_key = cartesia_api_key or os.getenv("CARTESIA_API_KEY")
        self.cartesia = Cartesia(api_key=self.cartesia_api_key)
        self.hedra_api_key = hedra_api_key or os.getenv("HEDRA_API_KEY")
        self.output_dir = output_dir or tempfile.gettempdir()
        
        if not self.hedra_api_key:
            raise ValueError("HEDRA_API_KEY is required")
        
        # Initialize CaptionGenerator for adding captions
        self.caption_generator = CaptionGenerator()
    
    def generate_hot_take_video(
        self,
        session_id: str,
        text_prompt: str = "professional interview conversation",
        aspect_ratio: str = "9:16",
        resolution: str = "720p",
        hot_take_number: int = 1,
        output_dir: str = None,
        add_captions: bool = True
    ) -> dict:
        """
        Generate a video from a hot take and upload to Supabase storage.
        
        Process:
        1. Get hot_take text and hedra_avatar_id from interview_call_analysis
        2. Get user_id from interview_calls
        3. Get voice_id from user_profiles
        4. Generate audio from hot_take text using Cartesia (with word timestamps)
        5. Generate video using Hedra avatar and audio
        6. Optionally add captions with word-by-word highlighting
        7. Upload video to Supabase storage and update database
        
        Args:
            session_id: The LiveKit session ID
            text_prompt: Description for the video generation
            aspect_ratio: Video aspect ratio ("16:9", "9:16", "1:1")
            resolution: Video resolution ("540p", "720p")
            hot_take_number: Which hot take to use (1 or 2)
            output_dir: Directory to save outputs (defaults to current working directory)
            add_captions: Whether to add captions to the video (default: True)
            
        Returns:
            dict: Generation result with paths to video, audio, and timestamps files
            
        Raises:
            ValueError: If session_id is invalid or data not found
            Exception: If generation fails
        """
        if not session_id:
            raise ValueError("session_id is required")
        
        if not self.supabase:
            raise Exception("Supabase client not initialized")
        
        if hot_take_number not in [1, 2]:
            raise ValueError("hot_take_number must be 1 or 2")
        
        logger.info(f"Starting hot take #{hot_take_number} video generation for session {session_id}")
        
        # Step 1: Get hot_take and hedra_avatar_id from interview_call_analysis
        hot_take_text, hedra_avatar_id = self._get_hot_take_data(session_id, hot_take_number)
        
        if not hot_take_text:
            raise ValueError(f"No hot_take_{hot_take_number} found for session {session_id}")
        
        if not hedra_avatar_id:
            raise ValueError(f"No hedra_avatar_id found for session {session_id}")
        
        logger.debug(f"Hot take #{hot_take_number} text preview: {hot_take_text[:100]}...")
        logger.debug(f"Using Hedra avatar ID: {hedra_avatar_id}")
        
        # Step 2: Get user_id from interview_calls
        user_id = self._get_user_id_from_session(session_id)
        
        if not user_id:
            raise ValueError(f"No user_id found for session {session_id}")
        
        # Step 3: Get voice_id from user_profiles
        voice_id = self._get_voice_id(user_id)
        
        if not voice_id:
            raise ValueError(f"No voice_id found for user {user_id}")
        
        logger.debug(f"Using voice ID: {voice_id}")
        
        # Determine output directory
        if output_dir is None:
            output_dir = os.getcwd()
        
        os.makedirs(output_dir, exist_ok=True)
        
        # Step 4: Generate audio from hot_take text using Cartesia (with word timestamps)
        temp_audio_path, word_timestamps = self._generate_audio_with_cartesia(
            hot_take_text,
            voice_id,
            session_id,
            hot_take_number
        )
        
        logger.info(f"Audio generated for hot take #{hot_take_number}")
        
        # Step 5: Generate video using Hedra
        temp_video_path = self._generate_video_with_hedra(
            hedra_avatar_id=hedra_avatar_id,
            audio_path=temp_audio_path,
            session_id=session_id,
            hot_take_number=hot_take_number,
            text_prompt=text_prompt,
            aspect_ratio=aspect_ratio,
            resolution=resolution
        )
        
        logger.info(f"Video generated successfully for hot take #{hot_take_number}")
        
        # Step 6: Save all outputs to the specified directory
        base_filename = f"hot_take_{hot_take_number}_{session_id}"
        
        # Save audio (move from current directory to output_dir if needed)
        audio_filename = f"{base_filename}_audio.wav"
        audio_path = os.path.join(output_dir, audio_filename)
        if temp_audio_path != audio_path:
            import shutil
            shutil.copy2(temp_audio_path, audio_path)
            # Clean up temp audio
            try:
                os.unlink(temp_audio_path)
            except Exception as e:
                logger.warning(f"Failed to clean up temp audio: {e}")
        
        # Save timestamps as JSON
        timestamps_path = os.path.join(output_dir, f"{base_filename}_timestamps.json")
        with open(timestamps_path, 'w') as f:
            json.dump(word_timestamps, f, indent=2)
        
        # Step 7: Optionally add captions using CaptionGenerator
        if add_captions:
            logger.info(f"Adding captions to hot take #{hot_take_number} video")
            
            try:
                # Use CaptionGenerator to add captions with word-by-word highlighting
                captioned_video_path = self.caption_generator.add_captions_to_video(
                    video_path=temp_video_path,
                    audio_path=audio_path,
                    timestamps_path=timestamps_path,
                    output_path=None  # Will auto-generate output path
                )
                
                # Move the captioned video to the final output location
                video_path = os.path.join(output_dir, f"{base_filename}.mp4")
                if captioned_video_path != video_path:
                    import shutil
                    shutil.copy2(captioned_video_path, video_path)
                    # Clean up temp captioned video
                    try:
                        os.unlink(captioned_video_path)
                    except Exception as e:
                        logger.warning(f"Failed to clean up temp captioned video: {e}")
                
                # Clean up original temp video (no longer needed)
                try:
                    os.unlink(temp_video_path)
                except Exception as e:
                    logger.warning(f"Failed to clean up temp video: {e}")
                
                logger.info(f"Captions added successfully to hot take #{hot_take_number}")
                message = f"Hot take #{hot_take_number} video generated with captions"
                
            except Exception as e:
                logger.error(f"Failed to add captions: {e}")
                logger.warning(f"Falling back to video without captions")
                
                # Save video without captions
                video_path = os.path.join(output_dir, f"{base_filename}.mp4")
                if temp_video_path != video_path:
                    import shutil
                    shutil.copy2(temp_video_path, video_path)
                    try:
                        os.unlink(temp_video_path)
                    except Exception as e:
                        logger.warning(f"Failed to clean up temp video: {e}")
                
                message = f"Hot take #{hot_take_number} video generated (captions failed, video without captions)"
        else:
            # Save video without captions
            video_path = os.path.join(output_dir, f"{base_filename}.mp4")
            if temp_video_path != video_path:
                import shutil
                shutil.copy2(temp_video_path, video_path)
                # Clean up temp video
                try:
                    os.unlink(temp_video_path)
                except Exception as e:
                    logger.warning(f"Failed to clean up temp video: {e}")
            
            message = f"Hot take #{hot_take_number} video generated (without captions)"
        
        logger.debug(f"Saved video to {video_path}")
        
        # Step 8: Upload video to Supabase storage and update database
        storage_path = None
        try:
            logger.info(f"Uploading hot take #{hot_take_number} video to Supabase")
            
            # Upload the final video to Supabase storage
            storage_path = self._upload_video_to_storage(
                video_path=video_path,
                user_id=user_id,
                session_id=session_id,
                hot_take_number=hot_take_number
            )
            
            # Update the database with the storage path
            self._update_video_filename_in_db(
                session_id=session_id,
                storage_path=storage_path,
                hot_take_number=hot_take_number
            )
            
            logger.info(f"Video uploaded to storage and database updated: {storage_path}")
            
            # Step 9: Clean up local files after successful upload
            logger.debug(f"Cleaning up local files for hot take #{hot_take_number}")
            files_to_cleanup = [video_path, audio_path, timestamps_path]
            for file_path_to_delete in files_to_cleanup:
                try:
                    if os.path.exists(file_path_to_delete):
                        os.unlink(file_path_to_delete)
                        logger.debug(f"Deleted: {os.path.basename(file_path_to_delete)}")
                except Exception as cleanup_error:
                    logger.warning(f"Failed to delete {os.path.basename(file_path_to_delete)}: {cleanup_error}")
            
        except Exception as e:
            logger.error(f"Failed to upload video to Supabase: {e}")
            logger.warning(f"Video saved locally at {video_path} but not uploaded to cloud storage")
        
        return {
            "status": "success",
            "message": message,
            "session_id": session_id,
            "user_id": user_id,
            "video_path": video_path,
            "audio_path": audio_path,
            "timestamps_path": timestamps_path,
            "hot_take_text": hot_take_text,
            "hot_take_number": hot_take_number,
            "captions_added": add_captions,
            "storage_path": storage_path
        }
    
    def _get_hot_take_data(self, session_id: str, hot_take_number: int = 1) -> tuple[str, str]:
        """
        Get hot_take and hedra_avatar_id from interview_call_analysis table.
        
        Args:
            session_id: The LiveKit session ID
            hot_take_number: Which hot take to retrieve (1 or 2)
            
        Returns:
            tuple[str, str]: (hot_take_text, hedra_avatar_id)
            
        Raises:
            ValueError: If session not found
        """
        try:
            response = self.supabase.table("interview_call_analysis")\
                .select("hot_take_1, hot_take_2, hedra_avatar_id")\
                .eq("session_id", session_id)\
                .limit(1)\
                .execute()
            
            if not response.data or len(response.data) == 0:
                raise ValueError(f"No call analysis found for session_id: {session_id}")
            
            analysis_data = response.data[0]
            hot_take_text = analysis_data.get(f'hot_take_{hot_take_number}')
            hedra_avatar_id = analysis_data.get('hedra_avatar_id')
            
            return hot_take_text, hedra_avatar_id
            
        except Exception as e:
            logger.error(f"Failed to fetch hot take data for session {session_id}: {e}")
            raise
    
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
    
    def _get_voice_id(self, user_id: str) -> str:
        """
        Get voice_id from user_profiles table.
        
        Args:
            user_id: The user's ID
            
        Returns:
            str: voice_id
            
        Raises:
            ValueError: If user profile not found
        """
        try:
            response = self.supabase.table("user_profiles")\
                .select("voice_id")\
                .eq("user_id", user_id)\
                .limit(1)\
                .execute()
            
            if not response.data or len(response.data) == 0:
                raise ValueError(f"No profile found for user_id: {user_id}")
            
            profile_data = response.data[0]
            voice_id = profile_data.get('voice_id')
            
            return voice_id
            
        except Exception as e:
            logger.error(f"Failed to fetch voice_id for user {user_id}: {e}")
            raise
    
    def _generate_audio_with_cartesia(
        self,
        text: str,
        voice_id: str,
        session_id: str,
        hot_take_number: int = 1
    ) -> Tuple[str, List[Dict]]:
        """
        Generate audio from text using Cartesia with word timestamps.
        
        Args:
            text: The text to convert to speech
            voice_id: The Cartesia voice ID to use
            session_id: Session ID for naming the output file
            hot_take_number: Which hot take this is for
            
        Returns:
            Tuple[str, List[Dict]]: (audio_file_path, word_timestamps)
            
        Raises:
            Exception: If audio generation fails
        """
        try:
            logger.info(f"Generating audio with Cartesia SSE (voice: {voice_id})")
            
            # Prepare SSE request
            url = "https://api.cartesia.ai/tts/sse"
            headers = {
                "X-API-Key": self.cartesia_api_key,
                "Cartesia-Version": "2024-06-10",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model_id": "sonic-english",
                "transcript": text,
                "voice": {
                    "mode": "id",
                    "id": voice_id
                },
                "output_format": {
                    "container": "raw",
                    "encoding": "pcm_s16le",
                    "sample_rate": 44100
                },
                "language": "en",
                "add_timestamps": True,
                "generation_config": {
                    "speed": 0.85  # Slower speech (85% of normal speed)
                }
            }
            
            # Make SSE request
            response = requests.post(url, headers=headers, json=payload, stream=True)
            
            if not response.ok:
                raise Exception(f"Cartesia API error: {response.status_code} {response.text}")
            
            # Process SSE stream
            audio_chunks = []
            word_timestamps = []
            event_count = 0
            
            for line in response.iter_lines():
                if not line:
                    continue
                    
                line = line.decode('utf-8')
                
                # SSE format: "event: <event_type>" and "data: <json_data>"
                if line.startswith('event:'):
                    continue
                    
                if line.startswith('data:'):
                    data_str = line[5:].strip()
                    event_count += 1
                    
                    try:
                        data = json.loads(data_str)
                        
                        # Handle audio chunk - data is in 'data' field when type is 'chunk'
                        if data.get('type') == 'chunk' and 'data' in data:
                            # Audio comes as base64 encoded in the 'data' field
                            import base64
                            audio_bytes = base64.b64decode(data['data'])
                            audio_chunks.append(audio_bytes)
                        
                        # Handle word timestamps - look for timestamp events
                        if 'word_timestamps' in data:
                            timestamps_data = data['word_timestamps']
                            
                            # Cartesia format: {'words': ['word1', 'word2'], 'start': [0.1, 0.5], 'end': [0.3, 0.7]}
                            # Convert to: [{'word': 'word1', 'start': 0.1, 'end': 0.3}, ...]
                            if 'words' in timestamps_data and 'start' in timestamps_data and 'end' in timestamps_data:
                                words = timestamps_data['words']
                                starts = timestamps_data['start']
                                ends = timestamps_data['end']
                                
                                for i in range(len(words)):
                                    word_timestamps.append({
                                        'word': words[i],
                                        'start': starts[i],
                                        'end': ends[i]
                                    })
                        
                        # Also check if timestamps are in the main data object
                        if data.get('type') == 'timestamps':
                            if 'words' in data and 'start' in data and 'end' in data:
                                words = data['words']
                                starts = data['start']
                                ends = data['end']
                                
                                for i in range(len(words)):
                                    word_timestamps.append({
                                        'word': words[i],
                                        'start': starts[i],
                                        'end': ends[i]
                                    })
                            
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse SSE data: {e}")
                        continue
            
            # Combine all audio chunks
            raw_audio = b''.join(audio_chunks)
            
            logger.debug(f"Processed {event_count} SSE events, received {len(audio_chunks)} audio chunks ({len(raw_audio)} bytes) and {len(word_timestamps)} word timestamps")
            
            # Convert raw PCM to WAV file
            output_filename = f"hot_take_{hot_take_number}_audio_{session_id}.wav"
            output_path = os.path.join(os.getcwd(), output_filename)
            
            # Write as WAV file with proper headers
            with wave.open(output_path, 'wb') as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(2)  # 16-bit (2 bytes)
                wav_file.setframerate(44100)  # Sample rate
                wav_file.writeframes(raw_audio)
            
            logger.debug(f"Audio saved to {output_path}")
            
            return output_path, word_timestamps
            
        except Exception as e:
            logger.error(f"Failed to generate audio with Cartesia: {e}")
            raise Exception(f"Audio generation failed: {e}")
    
    def _generate_video_with_hedra(
        self,
        hedra_avatar_id: str,
        audio_path: str,
        session_id: str,
        hot_take_number: int,
        text_prompt: str,
        aspect_ratio: str,
        resolution: str
    ) -> str:
        """
        Generate video using Hedra avatar and audio.
        
        Args:
            hedra_avatar_id: The Hedra avatar asset ID
            audio_path: Path to the audio file
            session_id: Session ID for naming the output file
            hot_take_number: Which hot take this is for
            text_prompt: Description for the video generation
            aspect_ratio: Video aspect ratio
            resolution: Video resolution
            
        Returns:
            str: Path to the generated video file
            
        Raises:
            Exception: If video generation fails
        """
        try:
            logger.info(f"Generating video with Hedra for hot take #{hot_take_number}")
            
            # Initialize Hedra session
            session = HedraSession(api_key=self.hedra_api_key)
            
            # Step 1: Upload audio file
            audio_id = self._upload_audio_file(session, audio_path)
            
            # Step 2: Create video generation
            generation_id = self._create_video_generation(
                session=session,
                image_asset_id=hedra_avatar_id,
                audio_asset_id=audio_id,
                text_prompt=text_prompt,
                aspect_ratio=aspect_ratio,
                resolution=resolution
            )
            
            # Step 3: Poll for completion
            status_data = self._poll_generation_status(session, generation_id)
            
            # Step 4: Download the video
            if not status_data.get("download_url"):
                raise Exception("No download URL found in the response")
            
            # Download to a temporary file first
            temp_download_path = os.path.join(
                tempfile.gettempdir(),
                f"hot_take_{hot_take_number}_video_{session_id}_raw.mp4"
            )
            
            self._download_video(status_data["download_url"], temp_download_path)
            
            # Step 5: Crop video to 9:16 aspect ratio for social media
            output_filename = os.path.join(
                tempfile.gettempdir(),
                f"hot_take_{hot_take_number}_video_{session_id}.mp4"
            )
            
            self._crop_video_to_9_16(temp_download_path, output_filename)
            
            # Clean up the raw downloaded video
            try:
                os.unlink(temp_download_path)
            except Exception as e:
                logger.warning(f"Failed to clean up raw video: {e}")
            
            logger.debug(f"Video cropped and saved to {output_filename}")
            return output_filename
            
        except Exception as e:
            logger.error(f"Failed to generate video with Hedra: {e}")
            raise Exception(f"Video generation failed: {e}")
    
    def _upload_audio_file(self, session: HedraSession, audio_path: str) -> str:
        """Upload an audio file to Hedra and return the asset ID"""
        logger.debug(f"Uploading audio file to Hedra: {audio_path}")
        
        # Create the audio asset
        create_response = session.post(
            "/assets",
            json={"name": os.path.basename(audio_path), "type": "audio"}
        )
        
        if not create_response.ok:
            logger.error(
                f"Error creating audio asset: {create_response.status_code} {create_response.text}"
            )
            raise Exception(f"Failed to create audio asset: {create_response.text}")
        
        audio_id = create_response.json()["id"]
        logger.debug(f"Created audio asset with ID: {audio_id}")
        
        # Upload the audio file
        with open(audio_path, "rb") as f:
            upload_response = session.post(
                f"/assets/{audio_id}/upload",
                files={"file": f}
            )
        
        if not upload_response.ok:
            logger.error(
                f"Error uploading audio file: {upload_response.status_code} {upload_response.text}"
            )
            raise Exception(f"Failed to upload audio file: {upload_response.text}")
        
        logger.debug(f"Audio uploaded with ID: {audio_id}")
        return audio_id
    
    def _create_video_generation(
        self,
        session: HedraSession,
        image_asset_id: str,
        audio_asset_id: str,
        text_prompt: str,
        aspect_ratio: str,
        resolution: str
    ) -> str:
        """Create a video generation request and return the generation ID"""
        logger.debug(f"Creating video generation request (resolution: {resolution}, aspect_ratio: {aspect_ratio})")
        
        generation_data = {
            "type": "video",
            "ai_model_id": CHARACTER_3_MODEL_ID,
            "start_keyframe_id": image_asset_id,
            "audio_id": audio_asset_id,
            "generated_video_inputs": {
                "text_prompt": text_prompt,
                "resolution": resolution,
                "aspect_ratio": aspect_ratio,
            },
        }
        
        response = session.post("/generations", json=generation_data)
        
        if not response.ok:
            logger.error(
                f"Error creating generation: {response.status_code} {response.text}"
            )
            raise Exception(f"Failed to create generation: {response.text}")
        
        result = response.json()
        generation_id = result["id"]
        logger.debug(f"Created generation with ID: {generation_id}")
        
        return generation_id
    
    def _poll_generation_status(self, session: HedraSession, generation_id: str) -> dict:
        """Poll the generation status until it completes or errors"""
        logger.debug(f"Polling generation status: {generation_id}")
        
        poll_count = 0
        while True:
            response = session.get(f"/generations/{generation_id}/status")
            
            if not response.ok:
                logger.error(f"Error getting generation status: {response.status_code} {response.text}")
                raise Exception(f"Failed to get generation status: {response.text}")
            
            status_data = response.json()
            status = status_data["status"]
            
            poll_count += 1
            if poll_count % 6 == 0:  # Log every 30 seconds (6 * 5s)
                logger.debug(f"Generation status: {status}")
            
            if status == "complete":
                logger.info("Video generation complete")
                return status_data
            elif status == "error":
                error_message = status_data.get("error_message", "Unknown error")
                logger.error(f"Hedra generation error: {error_message}")
                raise Exception(f"Video generation failed: {error_message}")
            
            # Still processing, wait and check again
            time.sleep(5)
    
    def _upload_video_to_storage(
        self,
        video_path: str,
        user_id: str,
        session_id: str,
        hot_take_number: int
    ) -> str:
        """
        Upload video to Supabase storage.
        
        Args:
            video_path: Local path to the video file
            user_id: The user's ID
            session_id: The session ID
            hot_take_number: Which hot take this is (1 or 2)
            
        Returns:
            str: The storage path of the uploaded video
            
        Raises:
            Exception: If upload fails
        """
        try:
            storage_path = f"{user_id}/hot_take_{hot_take_number}_{session_id}.mp4"
            
            logger.debug(f"Uploading video to Supabase storage: {storage_path}")
            
            # Read the video file
            with open(video_path, "rb") as f:
                video_data = f.read()
            
            # Upload to Supabase storage
            # Using the storage API to upload to the interview-hot-take-videos bucket
            response = self.supabase.storage.from_("interview-hot-take-videos").upload(
                path=storage_path,
                file=video_data,
                file_options={
                    "content-type": "video/mp4",
                    "upsert": "true"  # Overwrite if exists
                }
            )
            
            logger.debug(f"Video uploaded to storage: {storage_path}")
            return storage_path
            
        except Exception as e:
            logger.error(f"Failed to upload video to storage: {e}")
            raise Exception(f"Storage upload failed: {e}")
    
    def _update_video_filename_in_db(
        self,
        session_id: str,
        storage_path: str,
        hot_take_number: int
    ) -> None:
        """
        Update interview_call_analysis table with the video filename.
        
        Args:
            session_id: The LiveKit session ID
            storage_path: The storage path of the video
            hot_take_number: Which hot take this is (1 or 2)
            
        Raises:
            Exception: If database update fails
        """
        try:
            column_name = f"hot_take_video_{hot_take_number}"
            
            logger.debug(f"Updating database column {column_name} with: {storage_path}")
            
            # Update the specific column in interview_call_analysis
            response = self.supabase.table("interview_call_analysis")\
                .update({column_name: storage_path})\
                .eq("session_id", session_id)\
                .execute()
            
            if not response.data or len(response.data) == 0:
                raise Exception(f"No rows updated for session_id: {session_id}")
            
        except Exception as e:
            logger.error(f"Failed to update database: {e}")
            raise Exception(f"Database update failed: {e}")
    
    def _download_video(self, download_url: str, output_path: str) -> None:
        """Download the generated video from the provided URL"""
        logger.debug(f"Downloading video to {output_path}")
        
        try:
            with requests.get(download_url, stream=True) as r:
                r.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            
            logger.debug(f"Video downloaded successfully")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to download video: {e}")
            raise
        except IOError as e:
            logger.error(f"Failed to save video file: {e}")
            raise
    
    def _crop_video_to_9_16(self, input_path: str, output_path: str = None) -> str:
        """
        Crop video to 9:16 aspect ratio (vertical format for social media).
        
        Uses center-crop to extract the 9:16 portion from the video.
        If the video is already 9:16 or narrower, it will be padded with black bars.
        
        Args:
            input_path: Path to the input video file
            output_path: Path for the output video (optional, auto-generated if not provided)
            
        Returns:
            str: Path to the cropped video file
            
        Raises:
            Exception: If cropping fails
        """
        import subprocess
        import json as json_module
        
        try:
            logger.info(f"Cropping video to 9:16 aspect ratio: {input_path}")
            
            # Generate output path if not provided
            if output_path is None:
                base_name = os.path.splitext(input_path)[0]
                output_path = f"{base_name}_9x16.mp4"
            
            # Get video dimensions using ffprobe
            probe_cmd = [
                'ffprobe',
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height',
                '-of', 'json',
                input_path
            ]
            
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)
            
            if probe_result.returncode != 0:
                logger.error(f"ffprobe failed: {probe_result.stderr}")
                raise Exception(f"Failed to probe video: {probe_result.stderr}")
            
            probe_data = json_module.loads(probe_result.stdout)
            input_width = probe_data['streams'][0]['width']
            input_height = probe_data['streams'][0]['height']
            
            logger.debug(f"Input video dimensions: {input_width}x{input_height}")
            
            # Calculate target 9:16 dimensions
            # Target aspect ratio: 9/16 = 0.5625
            target_aspect = 9 / 16
            input_aspect = input_width / input_height
            
            if abs(input_aspect - target_aspect) < 0.01:
                # Already 9:16, just copy
                logger.debug("Video is already 9:16, copying without modification")
                import shutil
                shutil.copy2(input_path, output_path)
                return output_path
            
            if input_aspect > target_aspect:
                # Video is wider than 9:16, crop width (center crop)
                # Keep full height, calculate new width
                new_width = int(input_height * target_aspect)
                new_width = new_width - (new_width % 2)  # Ensure even number
                new_height = input_height
                x_offset = (input_width - new_width) // 2
                y_offset = 0
                crop_filter = f"crop={new_width}:{new_height}:{x_offset}:{y_offset}"
                logger.debug(f"Cropping width: {input_width} -> {new_width} (center crop)")
            else:
                # Video is taller than 9:16, crop height (center crop)
                # Keep full width, calculate new height
                new_width = input_width
                new_height = int(input_width / target_aspect)
                new_height = new_height - (new_height % 2)  # Ensure even number
                x_offset = 0
                y_offset = (input_height - new_height) // 2
                crop_filter = f"crop={new_width}:{new_height}:{x_offset}:{y_offset}"
                logger.debug(f"Cropping height: {input_height} -> {new_height} (center crop)")
            
            # Build ffmpeg command
            cmd = [
                'ffmpeg',
                '-i', input_path,
                '-vf', crop_filter,
                '-codec:a', 'copy',  # Copy audio stream as-is
                '-codec:v', 'libx264',
                '-crf', '18',  # High quality
                '-preset', 'medium',
                '-y',  # Overwrite output file
                output_path
            ]
            
            logger.debug(f"Running ffmpeg with crop filter: {crop_filter}")
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"ffmpeg crop failed: {result.stderr}")
                raise Exception(f"ffmpeg crop failed: {result.stderr}")
            
            # Verify output
            if not os.path.exists(output_path):
                raise Exception("Cropped output file was not created")
            
            file_size = os.path.getsize(output_path)
            logger.info(f"Video cropped to 9:16: {output_path} ({file_size / 1024 / 1024:.2f} MB)")
            
            return output_path
            
        except Exception as e:
            logger.error(f"Failed to crop video to 9:16: {e}")
            raise Exception(f"Video cropping failed: {e}")

