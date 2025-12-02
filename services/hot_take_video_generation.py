"""
Hot take video generation service.

This service generates videos from hot takes:
1. Generates audio from hot_take_1 text using Cartesia
2. Uses the Hedra avatar to create a video with the audio
"""

import os
import logging
import tempfile
import time
from pathlib import Path
import requests
from cartesia import Cartesia

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
        self.cartesia = Cartesia(api_key=cartesia_api_key or os.getenv("CARTESIA_API_KEY"))
        self.hedra_api_key = hedra_api_key or os.getenv("HEDRA_API_KEY")
        self.output_dir = output_dir or tempfile.gettempdir()
        
        if not self.hedra_api_key:
            raise ValueError("HEDRA_API_KEY is required")
    
    def generate_hot_take_video(
        self,
        session_id: str,
        text_prompt: str = "professional interview conversation",
        aspect_ratio: str = "16:9",
        resolution: str = "720p"
    ) -> dict:
        """
        Generate a video from a hot take.
        
        Process:
        1. Get hot_take_1 and hedra_avatar_id from interview_call_analysis table
        2. Get user_id from interview_calls table
        3. Get voice_id from user_profiles table
        4. Generate audio from hot_take_1 text using Cartesia
        5. Generate video using Hedra avatar and audio
        6. Save video locally
        
        Args:
            session_id: The LiveKit session ID
            text_prompt: Description for the video generation
            aspect_ratio: Video aspect ratio ("16:9", "9:16", "1:1")
            resolution: Video resolution ("540p", "720p")
            
        Returns:
            dict: Generation result with status and video path
            
        Raises:
            ValueError: If session_id is invalid or data not found
            Exception: If generation fails
        """
        if not session_id:
            raise ValueError("session_id is required")
        
        if not self.supabase:
            raise Exception("Supabase client not initialized")
        
        logger.info(f"🎬 Starting hot take video generation for session {session_id}")
        
        # Step 1: Get hot_take_1 and hedra_avatar_id from interview_call_analysis
        hot_take_text, hedra_avatar_id = self._get_hot_take_data(session_id)
        
        if not hot_take_text:
            raise ValueError(f"No hot_take_1 found for session {session_id}")
        
        if not hedra_avatar_id:
            raise ValueError(f"No hedra_avatar_id found for session {session_id}")
        
        logger.info(f"📝 Hot take text: {hot_take_text[:100]}...")
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
        
        audio_path = None
        try:
            # Step 4: Generate audio from hot_take_1 text using Cartesia
            audio_path = self._generate_audio_with_cartesia(
                hot_take_text,
                voice_id,
                session_id
            )
            
            logger.info(f"✅ Audio generated: {audio_path}")
            
            # Step 5: Generate video using Hedra
            video_path = self._generate_video_with_hedra(
                hedra_avatar_id=hedra_avatar_id,
                audio_path=audio_path,
                session_id=session_id,
                text_prompt=text_prompt,
                aspect_ratio=aspect_ratio,
                resolution=resolution
            )
            
            logger.info(f"🎉 Video generated successfully: {video_path}")
            
            return {
                "status": "success",
                "message": "Hot take video generated successfully",
                "session_id": session_id,
                "user_id": user_id,
                "video_path": video_path,
                "hot_take_text": hot_take_text
            }
            
        finally:
            # Clean up temporary audio file
            if audio_path and os.path.exists(audio_path):
                try:
                    os.unlink(audio_path)
                    logger.debug(f"Cleaned up temporary audio file: {audio_path}")
                except Exception as e:
                    logger.warning(f"Failed to clean up audio file {audio_path}: {e}")
    
    def _get_hot_take_data(self, session_id: str) -> tuple[str, str]:
        """
        Get hot_take_1 and hedra_avatar_id from interview_call_analysis table.
        
        Args:
            session_id: The LiveKit session ID
            
        Returns:
            tuple[str, str]: (hot_take_1, hedra_avatar_id)
            
        Raises:
            ValueError: If session not found
        """
        try:
            response = self.supabase.table("interview_call_analysis")\
                .select("hot_take_1, hedra_avatar_id")\
                .eq("session_id", session_id)\
                .limit(1)\
                .execute()
            
            if not response.data or len(response.data) == 0:
                raise ValueError(f"No call analysis found for session_id: {session_id}")
            
            analysis_data = response.data[0]
            hot_take_1 = analysis_data.get('hot_take_1')
            hedra_avatar_id = analysis_data.get('hedra_avatar_id')
            
            return hot_take_1, hedra_avatar_id
            
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
        session_id: str
    ) -> str:
        """
        Generate audio from text using Cartesia.
        
        Args:
            text: The text to convert to speech
            voice_id: The Cartesia voice ID to use
            session_id: Session ID for naming the output file
            
        Returns:
            str: Path to the generated audio file
            
        Raises:
            Exception: If audio generation fails
        """
        try:
            logger.info(f"🎵 Generating audio with Cartesia (voice: {voice_id})")
            
            # Generate audio using Cartesia REST API
            # WAV format provides better quality for video generation
            url = "https://api.cartesia.ai/tts/bytes"
            
            payload = {
                "model_id": "sonic-english",
                "transcript": text,
                "voice": {
                    "mode": "id",
                    "id": voice_id
                },
                "language": "en",
                "output_format": {
                    "container": "wav",
                    "encoding": "pcm_s16le",
                    "sample_rate": 44100
                }
            }
            
            headers = {
                "Cartesia-Version": "2024-06-10",
                "X-API-Key": os.getenv("CARTESIA_API_KEY"),
                "Content-Type": "application/json"
            }
            
            response = requests.post(url, json=payload, headers=headers)
            
            if not response.ok:
                logger.error(f"Cartesia API error: {response.status_code} {response.text}")
                raise Exception(f"Cartesia API returned {response.status_code}: {response.text}")
            
            # Save to temporary file
            output_path = os.path.join(
                tempfile.gettempdir(),
                f"hot_take_audio_{session_id}.wav"
            )
            
            # Write the audio bytes directly to file
            with open(output_path, "wb") as f:
                f.write(response.content)
            
            logger.info(f"✅ Audio generated and saved to: {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"Failed to generate audio with Cartesia: {e}")
            raise Exception(f"Audio generation failed: {e}")
    
    def _generate_video_with_hedra(
        self,
        hedra_avatar_id: str,
        audio_path: str,
        session_id: str,
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
                self.output_dir,
                f"hot_take_video_{session_id}.mp4"
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
        
        logger.info(f"✅ Audio uploaded with ID: {audio_id}")
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
        
        response = session.post("/generations", json=generation_data)
        
        if not response.ok:
            logger.error(
                f"Error creating generation: {response.status_code} {response.text}"
            )
            raise Exception(f"Failed to create generation: {response.text}")
        
        result = response.json()
        generation_id = result["id"]
        logger.info(f"✅ Created generation with ID: {generation_id}")
        logger.debug(f"Generation response: {result}")
        
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
                logger.error(f"Generation failed: {error_message}")
                raise Exception(f"Video generation failed: {error_message}")
            
            # Still processing, wait and check again
            time.sleep(5)
    
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

