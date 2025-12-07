"""
Hot take video generation service.

This service generates videos from hot takes:
1. Generates audio from hot_take_x text using Cartesia
2. Uses the Hedra avatar to create a video with the audio
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
from moviepy import VideoFileClip, TextClip, CompositeVideoClip

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
    
    def _get_font_path(self) -> str:
        """
        Get the path to a suitable font for captions.
        
        On macOS, tries to find Arial Bold or falls back to other available fonts.
        
        Returns:
            str: Path to a font file that can be used with TextClip
        """
        # List of font paths to try (in order of preference)
        font_paths = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Supplemental/Helvetica.ttc",
            "/System/Library/Fonts/SFNSDisplay.ttf",
            "/System/Library/Fonts/SFNS.ttf",
        ]
        
        for font_path in font_paths:
            if os.path.exists(font_path):
                logger.info(f"Using font: {font_path}")
                return font_path
        
        # If no fonts found, raise an error
        raise FileNotFoundError(
            "Could not find a suitable font file. Please ensure Arial Bold or Helvetica is installed."
        )
    
    def generate_hot_take_video(
        self,
        session_id: str,
        text_prompt: str = "professional interview conversation",
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
        hot_take_number: int = 1,
        output_dir: str = None
    ) -> dict:
        """
        Generate a video from a hot take and save video, audio, and timestamps locally.
        
        Process:
        1. Get hot_take text and hedra_avatar_id from interview_call_analysis table
        2. Get user_id from interview_calls table
        3. Get voice_id from user_profiles table
        4. Generate audio from hot_take text using Cartesia (with word timestamps)
        5. Generate video using Hedra avatar and audio
        6. Save video, audio, and timestamps locally for caption generation
        
        Args:
            session_id: The LiveKit session ID
            text_prompt: Description for the video generation
            aspect_ratio: Video aspect ratio ("16:9", "9:16", "1:1")
            resolution: Video resolution ("540p", "720p")
            hot_take_number: Which hot take to use (1 or 2)
            output_dir: Directory to save outputs (defaults to current working directory)
            
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
        
        logger.info(f"🎬 Starting hot take #{hot_take_number} video generation for session {session_id}")
        
        # Step 1: Get hot_take and hedra_avatar_id from interview_call_analysis
        hot_take_text, hedra_avatar_id = self._get_hot_take_data(session_id, hot_take_number)
        
        if not hot_take_text:
            raise ValueError(f"No hot_take_{hot_take_number} found for session {session_id}")
        
        if not hedra_avatar_id:
            raise ValueError(f"No hedra_avatar_id found for session {session_id}")
        
        logger.info(f"📝 Hot take #{hot_take_number} text: {hot_take_text[:100]}...")
        logger.info(f"🎭 Hedra avatar ID: {hedra_avatar_id}")
        
        # Step 2: Get user_id from interview_calls
        user_id = self._get_user_id_from_session(session_id)
        
        if not user_id:
            raise ValueError(f"No user_id found for session {session_id}")
        
        # Step 3: Get voice_id from user_profiles
        voice_id = self._get_voice_id(user_id)
        
        if not voice_id:
            raise ValueError(f"No voice_id found for user {user_id}")
        
        logger.info(f"🎤 Using voice ID: {voice_id}")
        
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
        
        logger.info(f"✅ Audio generated: {temp_audio_path}")
        
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
        
        logger.info(f"🎉 Video generated successfully: {temp_video_path}")
        
        # Step 6: Save all outputs to the specified directory
        base_filename = f"hot_take_{hot_take_number}_{session_id}"
        
        # Save video
        video_path = os.path.join(output_dir, f"{base_filename}.mp4")
        if temp_video_path != video_path:
            import shutil
            shutil.copy2(temp_video_path, video_path)
            # Clean up temp video
            try:
                os.unlink(temp_video_path)
            except Exception as e:
                logger.warning(f"Failed to clean up temp video: {e}")
        
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
        
        logger.info(f"💾 Saved outputs:")
        logger.info(f"  - Video: {video_path}")
        logger.info(f"  - Audio: {audio_path}")
        logger.info(f"  - Timestamps: {timestamps_path}")
        
        return {
            "status": "success",
            "message": f"Hot take #{hot_take_number} video generated (without captions)",
            "session_id": session_id,
            "user_id": user_id,
            "video_path": video_path,
            "audio_path": audio_path,
            "timestamps_path": timestamps_path,
            "hot_take_text": hot_take_text,
            "hot_take_number": hot_take_number
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
        Generate audio from text using Cartesia SSE endpoint with word timestamps.
        
        Args:
            text: The text to convert to speech
            voice_id: The Cartesia voice ID to use
            session_id: Session ID for naming the output file
            hot_take_number: Which hot take this is for
            
        Returns:
            Tuple[str, List[Dict]]: (audio_file_path, word_timestamps)
                word_timestamps format: [{"word": "hello", "start": 0.0, "end": 0.5}, ...]
            
        Raises:
            Exception: If audio generation fails
        """
        try:
            logger.info(f"🎵 Generating audio with Cartesia SSE (voice: {voice_id})")
            
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
                        
                        # Log first few events to see structure
                        if event_count <= 3:
                            logger.info(f"SSE event #{event_count} type: {data.get('type')}")
                        
                        # Handle audio chunk - data is in 'data' field when type is 'chunk'
                        if data.get('type') == 'chunk' and 'data' in data:
                            # Audio comes as base64 encoded in the 'data' field
                            import base64
                            audio_bytes = base64.b64decode(data['data'])
                            audio_chunks.append(audio_bytes)
                            if event_count <= 3:
                                logger.info(f"Found audio chunk of {len(audio_bytes)} bytes")
                        
                        # Handle word timestamps - look for timestamp events
                        if 'word_timestamps' in data:
                            timestamps_data = data['word_timestamps']
                            if event_count <= 10:
                                logger.info(f"Timestamp event structure: {timestamps_data}")
                            
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
                                
                                if event_count <= 10:
                                    logger.info(f"Added {len(words)} words from timestamp event")
                        
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
                                
                                logger.info(f"Added {len(words)} words from timestamp event (type=timestamps)")
                            
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse SSE data on event {event_count}: {e}")
                        logger.warning(f"Data string: {data_str[:200]}")
                        continue
            
            logger.info(f"📊 Processed {event_count} SSE events")
            
            # Combine all audio chunks
            raw_audio = b''.join(audio_chunks)
            
            logger.info(f"📊 Received {len(audio_chunks)} audio chunks totaling {len(raw_audio)} bytes")
            logger.info(f"📊 Received {len(word_timestamps)} word timestamps")
            
            # Convert raw PCM to WAV file - save to current directory for debugging
            output_filename = f"hot_take_{hot_take_number}_audio_{session_id}.wav"
            output_path = os.path.join(os.getcwd(), output_filename)
            
            # Write as WAV file with proper headers
            with wave.open(output_path, 'wb') as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(2)  # 16-bit (2 bytes)
                wav_file.setframerate(44100)  # Sample rate
                wav_file.writeframes(raw_audio)
            
            # Get file size for logging
            file_size = os.path.getsize(output_path)
            logger.info(f"✅ Audio generated: {output_path}")
            logger.info(f"📁 Audio file size: {file_size:,} bytes ({file_size / 1024:.2f} KB)")
            
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
            logger.info(f"🎬 Generating video with Hedra")
            
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
            
            output_filename = os.path.join(
                tempfile.gettempdir(),
                f"hot_take_{hot_take_number}_video_{session_id}.mp4"
            )
            
            self._download_video(status_data["download_url"], output_filename)
            
            logger.info(f"✅ Video saved to: {output_filename}")
            return output_filename
            
        except Exception as e:
            logger.error(f"Failed to generate video with Hedra: {e}")
            raise Exception(f"Video generation failed: {e}")
    
    def _upload_audio_file(self, session: HedraSession, audio_path: str) -> str:
        """Upload an audio file to Hedra and return the asset ID"""
        logger.info(f"📤 Uploading audio file to Hedra: {audio_path}")
        
        # Log file info
        file_size = os.path.getsize(audio_path)
        logger.info(f"📁 File size: {file_size:,} bytes ({file_size / 1024:.2f} KB)")
        
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
        logger.info(f"Created audio asset with ID: {audio_id}")
        
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
        
        upload_result = upload_response.json()
        logger.info(f"✅ Audio uploaded with ID: {audio_id}")
        logger.debug(f"Upload response: {json.dumps(upload_result, indent=2)}")
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
        logger.info("🎬 Creating video generation request")
        
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
        
        logger.info(f"Generation request data: {json.dumps(generation_data, indent=2)}")
        
        response = session.post("/generations", json=generation_data)
        
        if not response.ok:
            logger.error(
                f"Error creating generation: {response.status_code} {response.text}"
            )
            raise Exception(f"Failed to create generation: {response.text}")
        
        result = response.json()
        generation_id = result["id"]
        logger.info(f"✅ Created generation with ID: {generation_id}")
        logger.debug(f"Generation response: {json.dumps(result, indent=2)}")
        
        return generation_id
    
    def _poll_generation_status(self, session: HedraSession, generation_id: str) -> dict:
        """Poll the generation status until it completes or errors"""
        logger.info(f"⏳ Polling status for generation: {generation_id}")
        
        while True:
            response = session.get(f"/generations/{generation_id}/status")
            
            if not response.ok:
                logger.error(
                    f"Error getting status: {response.status_code} {response.text}"
                )
                raise Exception(f"Failed to get generation status: {response.text}")
            
            status_data = response.json()
            status = status_data["status"]
            logger.info(f"Generation status: {status}")
            
            if status == "complete":
                logger.info("✅ Generation complete!")
                return status_data
            elif status == "error":
                error_message = status_data.get("error_message", "Unknown error")
                # Log the full response to see what Hedra is returning
                logger.error(f"❌ Hedra generation error!")
                logger.error(f"Full error response: {json.dumps(status_data, indent=2)}")
                logger.error(f"Error message: {error_message}")
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
        
        Videos are stored in the interview-hot-take-videos bucket with a flat structure:
        hot_take_{hot_take_number}_{session_id}.mp4
        
        Args:
            video_path: Local path to the video file
            user_id: The user's ID (not used for storage path, kept for compatibility)
            session_id: The session ID
            hot_take_number: Which hot take this is (1 or 2)
            
        Returns:
            str: The storage path of the uploaded video
            
        Raises:
            Exception: If upload fails
        """
        try:
            # Construct the storage path (flat structure)
            storage_path = f"hot_take_{hot_take_number}_{session_id}.mp4"
            
            logger.info(f"☁️ Uploading video to Supabase storage: {storage_path}")
            
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
            
            logger.info(f"✅ Video uploaded successfully to storage: {storage_path}")
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
            
            logger.info(f"💾 Updating database column {column_name} with: {storage_path}")
            
            # Update the specific column in interview_call_analysis
            response = self.supabase.table("interview_call_analysis")\
                .update({column_name: storage_path})\
                .eq("session_id", session_id)\
                .execute()
            
            if not response.data or len(response.data) == 0:
                raise Exception(f"No rows updated for session_id: {session_id}")
            
            logger.info(f"✅ Database updated successfully")
            
        except Exception as e:
            logger.error(f"Failed to update database: {e}")
            raise Exception(f"Database update failed: {e}")
    
    def _group_words_into_phrases(
        self,
        word_timestamps: List[Dict],
        words_per_phrase: int = 3
    ) -> List[List[Dict]]:
        """
        Group words into phrases of 2-3 words for caption display.
        
        Args:
            word_timestamps: List of word timestamp dicts
            words_per_phrase: Number of words per phrase (default 3)
            
        Returns:
            List of phrases, where each phrase is a list of word dicts
        """
        phrases = []
        for i in range(0, len(word_timestamps), words_per_phrase):
            phrase = word_timestamps[i:i + words_per_phrase]
            phrases.append(phrase)
        
        return phrases
    
    def _create_highlighted_caption_clip(
        self,
        phrase_words: List[Dict],
        current_word_index: int,
        video_width: int,
        video_height: int,
        start_time: float,
        duration: float
    ) -> TextClip:
        """
        Create a caption clip with word highlighting.
        
        Args:
            phrase_words: List of words in the current phrase
            current_word_index: Index of the word to highlight
            video_width: Width of the video
            video_height: Height of the video
            start_time: When this caption should appear
            duration: How long this caption should last
            
        Returns:
            TextClip with highlighted word
        """
        # Build the caption text with color markup
        caption_parts = []
        for i, word_data in enumerate(phrase_words):
            word = word_data['word']
            if i == current_word_index:
                # Current word - highlighted in yellow
                caption_parts.append(word)
            else:
                # Other words - white
                caption_parts.append(word)
        
        caption_text = ' '.join(caption_parts)
        
        # Create text clip with styling
        # Note: MoviePy doesn't support inline color changes easily,
        # so we'll create overlapping clips for the highlight effect
        txt_clip = TextClip(
            caption_text,
            fontsize=70,
            color='white',
            font=self._get_font_path(),
            stroke_color='black',
            stroke_width=3,
            method='caption',
            size=(video_width * 0.9, None)
        )
        
        # Position at bottom of video (10% from bottom)
        txt_clip = txt_clip.with_position(('center', video_height * 0.85))
        txt_clip = txt_clip.with_start(start_time)
        txt_clip = txt_clip.with_duration(duration)
        
        return txt_clip
    
    def _create_word_highlight_clips(
        self,
        word_timestamps: List[Dict],
        video_width: int,
        video_height: int
    ) -> List[TextClip]:
        """
        Create caption clips with word-by-word highlighting effect.
        
        Args:
            word_timestamps: List of word timestamp dicts
            video_width: Width of the video
            video_height: Height of the video
            
        Returns:
            List of TextClip objects for compositing
        """
        caption_clips = []
        
        # Group words into phrases
        phrases = self._group_words_into_phrases(word_timestamps)
        
        logger.info(f"🎬 Creating {len(phrases)} caption phrases from {len(word_timestamps)} words")
        logger.info(f"📐 Video dimensions: {video_width}x{video_height}")
        
        for phrase_idx, phrase_words in enumerate(phrases):
            # Determine the time span for this phrase
            phrase_start = phrase_words[0]['start']
            phrase_end = phrase_words[-1]['end']
            phrase_duration = phrase_end - phrase_start
            
            # Create base text (all words in white) - ONE clip for the entire phrase
            phrase_text = ' '.join([w['word'] for w in phrase_words])
            
            logger.info(f"\n📝 Phrase {phrase_idx + 1}/{len(phrases)}: '{phrase_text}'")
            logger.info(f"   ⏱️  Phrase timing: start={phrase_start:.3f}s, end={phrase_end:.3f}s, duration={phrase_duration:.3f}s")
            logger.info(f"   📊 Words in phrase: {len(phrase_words)}")
            
            # MoviePy 2.x uses text parameter instead of positional
            base_clip = TextClip(
                text=phrase_text,
                font_size=70,
                color='white',
                font=self._get_font_path(),
                stroke_color='black',
                stroke_width=3,
                size=(int(video_width * 0.9), None)
            )
            base_clip = base_clip.with_position(('center', int(video_height * 0.85)))
            base_clip = base_clip.with_start(phrase_start)
            base_clip = base_clip.with_duration(phrase_duration)
            
            logger.info(f"   ✅ Created base clip (WHITE) - start={phrase_start:.3f}s, duration={phrase_duration:.3f}s")
            caption_clips.append(base_clip)
            
            # Create a highlight clip for each word in the phrase
            for i, word_data in enumerate(phrase_words):
                word_start = word_data['start']
                word_end = word_data['end']
                word_duration = word_end - word_start
                
                # Create highlight overlay for current word
                highlighted_word = word_data['word']
                
                logger.info(f"      🟡 Word {i + 1}/{len(phrase_words)}: '{highlighted_word}'")
                logger.info(f"         ⏱️  Timing: start={word_start:.3f}s, end={word_end:.3f}s, duration={word_duration:.3f}s")
                
                # Calculate position offset for the highlighted word
                words_before = ' '.join([w['word'] for w in phrase_words[:i]])
                
                highlight_clip = TextClip(
                    text=highlighted_word,
                    font_size=70,
                    color='yellow',
                    font=self._get_font_path(),
                    stroke_color='black',
                    stroke_width=3
                )
                
                # Position the highlight over the correct word
                # This is approximate - calculating exact x position is complex
                base_x_offset = len(words_before) * 35 if words_before else 0
                x_pos = (video_width // 2) - (len(phrase_text) * 20) + base_x_offset
                y_pos = int(video_height * 0.85)
                
                logger.info(f"         📍 Position: x={x_pos}, y={y_pos} (offset={base_x_offset}, words_before='{words_before}')")
                
                highlight_clip = highlight_clip.with_position((x_pos, y_pos))
                highlight_clip = highlight_clip.with_start(word_start)
                highlight_clip = highlight_clip.with_duration(word_duration)
                
                logger.info(f"         ✅ Created highlight clip (YELLOW)")
                caption_clips.append(highlight_clip)
        
        logger.info(f"\n🎉 Total caption clips created: {len(caption_clips)}")
        logger.info(f"   📊 Expected: {len(phrases)} base clips + {len(word_timestamps)} highlight clips = {len(phrases) + len(word_timestamps)}")
        
        return caption_clips
    
    def _add_captions_to_video(
        self,
        video_path: str,
        word_timestamps: List[Dict],
        session_id: str,
        hot_take_number: int
    ) -> str:
        """
        Add word-highlighted captions to video using MoviePy.
        
        Args:
            video_path: Path to the video file
            word_timestamps: List of word timestamp dicts from Cartesia
            session_id: Session ID for naming the output file
            hot_take_number: Which hot take this is for
            
        Returns:
            str: Path to the captioned video file
            
        Raises:
            Exception: If caption generation fails
        """
        try:
            logger.info(f"🎬 Adding captions to video: {video_path}")
            logger.info(f"📊 Total word timestamps received: {len(word_timestamps)}")
            
            # Log first few word timestamps for debugging
            logger.info(f"🔍 First 5 word timestamps:")
            for i, wt in enumerate(word_timestamps[:5]):
                logger.info(f"   {i + 1}. '{wt.get('word')}' - start={wt.get('start'):.3f}s, end={wt.get('end'):.3f}s")
            
            # Load the video
            video = VideoFileClip(video_path)
            video_width, video_height = video.size
            video_duration = video.duration
            
            logger.info(f"📹 Video info:")
            logger.info(f"   - Dimensions: {video_width}x{video_height}")
            logger.info(f"   - Duration: {video_duration:.3f}s")
            logger.info(f"   - FPS: {video.fps}")
            
            # Create caption clips
            caption_clips = self._create_word_highlight_clips(
                word_timestamps,
                video_width,
                video_height
            )
            
            logger.info(f"\n✅ Created {len(caption_clips)} total caption clips")
            
            # Composite captions onto video
            logger.info(f"🎞️  Compositing captions onto video...")
            final_video = CompositeVideoClip([video] + caption_clips)
            
            # Generate output path
            output_path = os.path.join(
                tempfile.gettempdir(),
                f"hot_take_{hot_take_number}_captioned_{session_id}.mp4"
            )
            
            logger.info(f"📝 Rendering captioned video to: {output_path}")
            logger.info(f"⏳ This may take a few minutes...")
            
            # Write the video file
            final_video.write_videofile(
                output_path,
                codec='libx264',
                audio_codec='aac',
                temp_audiofile=f'{output_path}.temp-audio.m4a',
                remove_temp=True,
                fps=video.fps
            )
            
            # Clean up
            video.close()
            final_video.close()
            
            logger.info(f"✅ Captioned video created: {output_path}")
            
            return output_path
            
        except Exception as e:
            logger.error(f"Failed to add captions to video: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise Exception(f"Caption generation failed: {e}")
    
    def _download_video(self, download_url: str, output_path: str) -> None:
        """Download the generated video from the provided URL"""
        logger.info(f"📥 Downloading video from {download_url}")
        logger.info(f"💾 Saving to: {output_path}")
        
        try:
            with requests.get(download_url, stream=True) as r:
                r.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            
            logger.info(f"✅ Successfully downloaded video to {output_path}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to download video: {e}")
            raise
        except IOError as e:
            logger.error(f"Failed to save video file: {e}")
            raise

