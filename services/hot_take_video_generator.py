"""
Hot take video generation service.

This service generates videos from hot takes:
1. Generates audio from hot_take_x text using TTS (Inworld or Cartesia)
2. Animates the user's avatar image (cartoon or photorealistic) with that audio
   using WaveSpeed's InfiniteTalk model to produce a talking-head video
3. Optionally adds captions using the CaptionGenerator service

The avatar image already exists in Supabase (`avatar-images-bucket`) as either
the cartoonized image (`cartoon_id`) or the original photo (`picture_id`), chosen
by the `use_hedra_photo_avatar` flag. InfiniteTalk takes an image URL + an audio
URL, so we upload both to WaveSpeed's media store, run the model, and download
the resulting video. This replaces the previous Hedra avatar pipeline.
"""

import os
import mimetypes
import tempfile
import time
import json
import wave
import base64
from pathlib import Path
from typing import List, Dict, Tuple
import requests

# Import CaptionGenerator for adding captions
from .caption_generator import CaptionGenerator
from .logging_config import get_logger

logger = get_logger(__name__)

# --- WaveSpeed InfiniteTalk configuration ---------------------------------
# Unified REST API base. Submit a job to /{model}, then poll the result URL.
WAVESPEED_API_BASE = "https://api.wavespeed.ai/api/v3"
# Media upload endpoint: POST a binary file, get back a public download_url.
WAVESPEED_UPLOAD_URL = f"{WAVESPEED_API_BASE}/media/upload/binary"
# InfiniteTalk talking-head lip-sync model slug (env-overridable).
WAVESPEED_VIDEO_MODEL = os.getenv("WAVESPEED_VIDEO_MODEL", "wavespeed-ai/infinitetalk")

# InfiniteTalk can take minutes (a few seconds of clip -> tens of seconds of wall
# time), so the overall poll budget is generous even though each request is quick.
WAVESPEED_POLL_INTERVAL_S = 5
WAVESPEED_TIMEOUT_S = 600

# Bucket holding the avatar images (cartoon + original photo) fed to InfiniteTalk.
AVATAR_IMAGES_BUCKET = "avatar-images-bucket"


class HotTakeVideoGenerationService:
    """Service for generating videos from hot takes."""

    def __init__(
        self,
        supabase_client,
        tts_provider: str = "inworld",
        cartesia_api_key: str = None,
        inworld_api_key: str = None,
        wavespeed_api_key: str = None,
        output_dir: str = None,
    ):
        """
        Initialize the hot take video generation service.

        Args:
            supabase_client: Initialized Supabase client
            tts_provider: TTS provider to use ("inworld" or "cartesia", default: "inworld")
            cartesia_api_key: Cartesia API key (optional, will use env var if not provided)
            inworld_api_key: Inworld API key (optional, will use env var if not provided)
            wavespeed_api_key: WaveSpeed API key (optional, will use env var if not provided)
            output_dir: Directory to save generated videos (optional, defaults to temp dir)
        """
        self.supabase = supabase_client
        self.tts_provider = tts_provider

        # Cartesia setup (deferred import to reduce baseline memory)
        self.cartesia_api_key = cartesia_api_key or os.getenv("CARTESIA_API_KEY")
        self.cartesia = None
        if self.cartesia_api_key and self.tts_provider == "cartesia":
            from cartesia import Cartesia

            self.cartesia = Cartesia(api_key=self.cartesia_api_key)

        # Inworld setup
        self.inworld_api_key = inworld_api_key or os.getenv("INWORLD_API_KEY")

        self.wavespeed_api_key = wavespeed_api_key or os.getenv("WAVESPEED_API_KEY")
        self.output_dir = output_dir or tempfile.gettempdir()

        if not self.wavespeed_api_key:
            raise ValueError("WAVESPEED_API_KEY is required")

        # Initialize CaptionGenerator for adding captions
        self.caption_generator = CaptionGenerator()

    def generate_hot_take_video(
        self,
        session_id: str,
        text_prompt: str = "professional conversation",
        aspect_ratio: str = "16:9",
        resolution: str = "480p",
        output_dir: str = None,
        add_captions: bool = True,
    ) -> dict:
        """
        Generate a video from a hot take and upload to Supabase storage.

        Process:
        1. Get hot_take text and avatar image path from interview_call_analysis
        2. Get user_id from interview_calls
        3. Get voice_id from user_profiles
        4. Generate audio from hot_take text using Cartesia (with word timestamps)
        5. Generate video using WaveSpeed InfiniteTalk (avatar image + audio)
        6. Optionally add captions with word-by-word highlighting
        7. Upload video to Supabase storage and update database

        Args:
            session_id: The LiveKit session ID
            text_prompt: Expression/pose guidance passed to InfiniteTalk
            aspect_ratio: Retained for API compatibility; InfiniteTalk inherits the
                aspect of the input avatar image, so this is not sent to the model
            resolution: Video resolution ("480p" or "720p")
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

        logger.info(f"Starting hot take video generation for session {session_id}")

        # Step 1: Get hot_take text and the avatar image path from interview_call_analysis
        hot_take_text, image_storage_path = self._get_hot_take_data(session_id)

        if not hot_take_text:
            raise ValueError(f"No hot_take_1 found for session {session_id}")

        if not image_storage_path:
            raise ValueError(f"No avatar image found for session {session_id}")

        logger.debug(f"Hot take text preview: {hot_take_text[:100]}...")
        logger.debug(f"Using avatar image: {image_storage_path}")

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

        # Step 4: Generate audio from hot_take text using configured TTS provider (with word timestamps)
        if self.tts_provider == "cartesia":
            temp_audio_path, word_timestamps = self._generate_audio_with_cartesia(
                hot_take_text, voice_id, session_id
            )
        else:
            temp_audio_path, word_timestamps = self._generate_audio_with_inworld(
                hot_take_text, voice_id, session_id
            )

        logger.info(f"Audio generated for hot take using {self.tts_provider}")

        # Step 5: Generate video using WaveSpeed InfiniteTalk
        temp_video_path = self._generate_video_with_infinitetalk(
            image_storage_path=image_storage_path,
            audio_path=temp_audio_path,
            session_id=session_id,
            text_prompt=text_prompt,
            resolution=resolution,
        )

        logger.info(f"Video generated successfully for hot take")

        # Step 6: Save all outputs to the specified directory
        base_filename = f"hot_take_{session_id}"

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
        with open(timestamps_path, "w") as f:
            json.dump(word_timestamps, f, indent=2)

        # Step 7: Optionally add captions using CaptionGenerator
        if add_captions:
            logger.info("Adding captions to hot take video")

            try:
                captioned_video_path = self.caption_generator.add_captions_to_video(
                    video_path=temp_video_path,
                    audio_path=audio_path,
                    timestamps_path=timestamps_path,
                    output_path=None,  # Will auto-generate output path
                    crop_to_aspect_ratio=None,
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

                logger.info(f"Captions added successfully to hot take video")
                message = f"Hot take video generated with captions"

            except Exception as e:
                logger.error(f"Failed to add captions: {e}")
                logger.warning(f"Falling back to video without captions (crop only)")

                video_path = os.path.join(output_dir, f"{base_filename}.mp4")
                import shutil

                shutil.copy2(temp_video_path, video_path)
                try:
                    os.unlink(temp_video_path)
                except Exception as e2:
                    logger.warning(f"Failed to clean up temp video: {e2}")

                message = f"Hot take video generated (captions failed, video without captions)"
        else:
            video_path = os.path.join(output_dir, f"{base_filename}.mp4")
            import shutil

            shutil.copy2(temp_video_path, video_path)
            try:
                os.unlink(temp_video_path)
            except Exception as e:
                logger.warning(f"Failed to clean up temp video: {e}")

            message = f"Hot take video generated (without captions)"

        logger.debug(f"Saved video to {video_path}")

        # Step 8: Upload video to Supabase storage and update database
        storage_path = None
        try:
            logger.info(f"Uploading hot take video to Supabase")

            # Upload the final video to Supabase storage
            storage_path = self._upload_video_to_storage(
                video_path=video_path, user_id=user_id, session_id=session_id
            )

            # Update the database with the storage path
            self._update_video_filename_in_db(
                session_id=session_id, storage_path=storage_path
            )

            logger.info(
                f"Video uploaded to storage and database updated: {storage_path}"
            )

            # Step 9: Clean up local files after successful upload
            logger.debug(f"Cleaning up local files for hot take")
            files_to_cleanup = [video_path, audio_path, timestamps_path]
            for file_path_to_delete in files_to_cleanup:
                try:
                    if os.path.exists(file_path_to_delete):
                        os.unlink(file_path_to_delete)
                        logger.debug(
                            f"Deleted: {os.path.basename(file_path_to_delete)}"
                        )
                except Exception as cleanup_error:
                    logger.warning(
                        f"Failed to delete {os.path.basename(file_path_to_delete)}: {cleanup_error}"
                    )

        except Exception as e:
            logger.error(f"Failed to upload video to Supabase: {e}")
            logger.warning(
                f"Video saved locally at {video_path} but not uploaded to cloud storage"
            )

        return {
            "status": "success",
            "message": message,
            "session_id": session_id,
            "user_id": user_id,
            "video_path": video_path,
            "audio_path": audio_path,
            "timestamps_path": timestamps_path,
            "hot_take_text": hot_take_text,
            "captions_added": add_captions,
            "storage_path": storage_path,
        }

    def _get_hot_take_data(self, session_id: str) -> tuple[str, str]:
        """
        Get the hot_take text and the avatar image path (in avatar-images-bucket)
        to feed InfiniteTalk. Selects the photorealistic photo or the cartoon
        based on the use_hedra_photo_avatar flag.

        The cartoon image lives at `cartoon_id` (interview_call_analysis); the
        original photo lives at `picture_id` (interview_calls) under `{user_id}/`.

        Args:
            session_id: The LiveKit session ID

        Returns:
            tuple[str, str]: (hot_take_text, image_storage_path)

        Raises:
            ValueError: If session not found
        """
        try:
            response = (
                self.supabase.table("interview_call_analysis")
                .select("hot_take_1, cartoon_id, use_hedra_photo_avatar")
                .eq("session_id", session_id)
                .limit(1)
                .execute()
            )

            if not response.data or len(response.data) == 0:
                raise ValueError(f"No call analysis found for session_id: {session_id}")

            analysis_data = response.data[0]
            hot_take_text = analysis_data.get("hot_take_1")
            cartoon_id = analysis_data.get("cartoon_id")
            use_photo = analysis_data.get("use_hedra_photo_avatar", False)

            # picture_id / user_id (original photo) live on interview_calls.
            picture_id, user_id = self._get_picture_and_user(session_id)

            if use_photo and picture_id:
                image_storage_path = self._resolve_image_path(picture_id, user_id)
            elif cartoon_id:
                image_storage_path = self._resolve_image_path(cartoon_id, user_id)
            elif picture_id:
                # Fallback: no cartoon available, use the original photo.
                image_storage_path = self._resolve_image_path(picture_id, user_id)
            else:
                image_storage_path = None

            return hot_take_text, image_storage_path

        except Exception as e:
            logger.error(f"Failed to fetch hot take data for session {session_id}: {e}")
            raise

    def _get_picture_and_user(self, session_id: str) -> tuple[str, str]:
        """
        Look up (picture_id, user_id) from interview_calls for a session.

        Returns (None, None) if not found so the caller can fall back to the
        cartoon image.
        """
        response = (
            self.supabase.table("interview_calls")
            .select("picture_id, user_id")
            .eq("session_id", session_id)
            .limit(1)
            .execute()
        )
        if not response.data or len(response.data) == 0:
            return None, None
        row = response.data[0]
        return row.get("picture_id"), row.get("user_id")

    @staticmethod
    def _resolve_image_path(image_id: str, user_id: str = None) -> str:
        """
        Resolve a storage path in avatar-images-bucket. Bare filenames are
        prefixed with `{user_id}/`; values that already contain a path are used
        as-is (mirrors HedraAvatarGenerator._download_image_from_storage).
        """
        if "/" in image_id:
            return image_id
        return f"{user_id}/{image_id}" if user_id else image_id

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
            response = (
                self.supabase.table("interview_calls")
                .select("user_id")
                .eq("session_id", session_id)
                .limit(1)
                .execute()
            )

            if not response.data or len(response.data) == 0:
                raise ValueError(f"No call found for session_id: {session_id}")

            call_data = response.data[0]
            user_id = call_data.get("user_id")

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
            response = (
                self.supabase.table("user_profiles")
                .select("voice_id")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )

            if not response.data or len(response.data) == 0:
                raise ValueError(f"No profile found for user_id: {user_id}")

            profile_data = response.data[0]
            voice_id = profile_data.get("voice_id")

            return voice_id

        except Exception as e:
            logger.error(f"Failed to fetch voice_id for user {user_id}: {e}")
            raise

    def _generate_audio_with_cartesia(
        self, text: str, voice_id: str, session_id: str
    ) -> Tuple[str, List[Dict]]:
        """
        Generate audio from text using Cartesia with word timestamps.

        Args:
            text: The text to convert to speech
            voice_id: The Cartesia voice ID to use
            session_id: Session ID for naming the output file

        Returns:
            Tuple[str, List[Dict]]: (audio_file_path, word_timestamps)

        Raises:
            Exception: If audio generation fails
        """
        tts_start = time.time()

        try:
            logger.info(
                "Cartesia TTS generation started",
                service="Cartesia",
                operation="tts_generate",
                text_length=len(text),
            )

            # Prepare SSE request
            url = "https://api.cartesia.ai/tts/sse"
            headers = {
                "X-API-Key": self.cartesia_api_key,
                "Cartesia-Version": "2024-06-10",
                "Content-Type": "application/json",
            }

            payload = {
                "model_id": "sonic-english",
                "transcript": text,
                "voice": {"mode": "id", "id": voice_id},
                "output_format": {
                    "container": "raw",
                    "encoding": "pcm_s16le",
                    "sample_rate": 44100,
                },
                "language": "en",
                "add_timestamps": True,
                "generation_config": {
                    "speed": 0.85  # Slower speech (85% of normal speed)
                },
            }

            # Make SSE request
            response = requests.post(url, headers=headers, json=payload, stream=True)

            if not response.ok:
                raise Exception(
                    f"Cartesia API error: {response.status_code} {response.text}"
                )

            # Process SSE stream
            audio_chunks = []
            word_timestamps = []
            event_count = 0

            for line in response.iter_lines():
                if not line:
                    continue

                line = line.decode("utf-8")

                # SSE format: "event: <event_type>" and "data: <json_data>"
                if line.startswith("event:"):
                    continue

                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    event_count += 1

                    try:
                        data = json.loads(data_str)

                        # Handle audio chunk - data is in 'data' field when type is 'chunk'
                        if data.get("type") == "chunk" and "data" in data:
                            # Audio comes as base64 encoded in the 'data' field
                            import base64

                            audio_bytes = base64.b64decode(data["data"])
                            audio_chunks.append(audio_bytes)

                        # Handle word timestamps - look for timestamp events
                        if "word_timestamps" in data:
                            timestamps_data = data["word_timestamps"]

                            # Cartesia format: {'words': ['word1', 'word2'], 'start': [0.1, 0.5], 'end': [0.3, 0.7]}
                            # Convert to: [{'word': 'word1', 'start': 0.1, 'end': 0.3}, ...]
                            if (
                                "words" in timestamps_data
                                and "start" in timestamps_data
                                and "end" in timestamps_data
                            ):
                                words = timestamps_data["words"]
                                starts = timestamps_data["start"]
                                ends = timestamps_data["end"]

                                for i in range(len(words)):
                                    word_timestamps.append(
                                        {
                                            "word": words[i],
                                            "start": starts[i],
                                            "end": ends[i],
                                        }
                                    )

                        # Also check if timestamps are in the main data object
                        if data.get("type") == "timestamps":
                            if "words" in data and "start" in data and "end" in data:
                                words = data["words"]
                                starts = data["start"]
                                ends = data["end"]

                                for i in range(len(words)):
                                    word_timestamps.append(
                                        {
                                            "word": words[i],
                                            "start": starts[i],
                                            "end": ends[i],
                                        }
                                    )

                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse SSE data: {e}")
                        continue

            # Combine all audio chunks
            raw_audio = b"".join(audio_chunks)

            logger.debug(
                f"Processed {event_count} SSE events, received {len(audio_chunks)} audio chunks ({len(raw_audio)} bytes) and {len(word_timestamps)} word timestamps"
            )

            # Convert raw PCM to WAV file
            output_filename = f"hot_take_audio_{session_id}.wav"
            output_path = os.path.join(os.getcwd(), output_filename)

            # Write as WAV file with proper headers
            with wave.open(output_path, "wb") as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(2)  # 16-bit (2 bytes)
                wav_file.setframerate(44100)  # Sample rate
                wav_file.writeframes(raw_audio)

            duration_ms = int((time.time() - tts_start) * 1000)
            logger.info(
                "Cartesia TTS generation completed",
                service="Cartesia",
                operation="tts_generate",
                duration_ms=duration_ms,
                audio_bytes=len(raw_audio),
                word_count=len(word_timestamps),
                status="success",
            )

            return output_path, word_timestamps

        except Exception as e:
            duration_ms = int((time.time() - tts_start) * 1000)
            logger.error(
                f"Cartesia TTS generation failed: {e}",
                service="Cartesia",
                operation="tts_generate",
                duration_ms=duration_ms,
                error=str(e),
            )
            raise Exception(f"Audio generation failed: {e}")

    def _generate_audio_with_inworld(
        self, text: str, voice_id: str, session_id: str
    ) -> Tuple[str, List[Dict]]:
        """
        Generate audio from text using Inworld TTS with word timestamps.

        Args:
            text: The text to convert to speech
            voice_id: The Inworld voice ID to use (cloned voice ID)
            session_id: Session ID for naming the output file

        Returns:
            Tuple[str, List[Dict]]: (audio_file_path, word_timestamps)

        Raises:
            Exception: If audio generation fails
        """
        tts_start = time.time()

        try:
            logger.info(
                "Inworld TTS generation started",
                service="Inworld",
                operation="tts_generate",
                text_length=len(text),
            )

            if not self.inworld_api_key:
                raise ValueError("Inworld API key not configured")

            # Prepare request to Inworld sync TTS endpoint
            url = "https://api.inworld.ai/tts/v1/voice"
            headers = {
                "Authorization": f"Basic {self.inworld_api_key}",
                "Content-Type": "application/json",
            }

            payload = {
                "text": text,
                "voiceId": voice_id,
                "modelId": "inworld-tts-1-max",
                "audioConfig": {
                    "audioEncoding": "LINEAR16",
                    "sampleRateHertz": 44100,
                    "speakingRate": 0.85,  # Slower speech (85% of normal speed)
                },
                "temperature": 1.1,
                "timestampType": "WORD",
            }

            # Make sync request
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=300,  # TTS can take a while for long text
            )

            if not response.ok:
                raise Exception(
                    f"Inworld API error: {response.status_code} {response.text}"
                )

            result = response.json()

            # Extract audio content (base64 encoded)
            audio_content_b64 = result.get("audioContent")
            if not audio_content_b64:
                raise Exception("No audio content in Inworld response")

            # Decode audio
            audio_bytes = base64.b64decode(audio_content_b64)

            # Extract word timestamps
            word_timestamps = []
            timestamp_info = result.get("timestampInfo", {})
            word_alignment = timestamp_info.get("wordAlignment", {})

            if word_alignment:
                words = word_alignment.get("words", [])
                starts = word_alignment.get("wordStartTimeSeconds", [])
                ends = word_alignment.get("wordEndTimeSeconds", [])

                for i in range(len(words)):
                    word_timestamps.append(
                        {
                            "word": words[i],
                            "start": starts[i] if i < len(starts) else 0,
                            "end": ends[i] if i < len(ends) else 0,
                        }
                    )

            logger.debug(
                f"Received {len(audio_bytes)} bytes of audio and {len(word_timestamps)} word timestamps"
            )

            # Save audio to file
            # Inworld returns LINEAR16 with WAV header, so we can save directly
            output_filename = f"hot_take_audio_{session_id}.wav"
            output_path = os.path.join(os.getcwd(), output_filename)

            with open(output_path, "wb") as f:
                f.write(audio_bytes)

            duration_ms = int((time.time() - tts_start) * 1000)
            logger.info(
                "Inworld TTS generation completed",
                service="Inworld",
                operation="tts_generate",
                duration_ms=duration_ms,
                audio_bytes=len(audio_bytes),
                word_count=len(word_timestamps),
                status="success",
            )

            return output_path, word_timestamps

        except Exception as e:
            duration_ms = int((time.time() - tts_start) * 1000)
            logger.error(
                f"Inworld TTS generation failed: {e}",
                service="Inworld",
                operation="tts_generate",
                duration_ms=duration_ms,
                error=str(e),
            )
            raise Exception(f"Audio generation failed: {e}")

    def _generate_video_with_infinitetalk(
        self,
        image_storage_path: str,
        audio_path: str,
        session_id: str,
        text_prompt: str,
        resolution: str,
    ) -> str:
        """
        Generate a talking-head video using WaveSpeed's InfiniteTalk model.

        Downloads the avatar image from avatar-images-bucket, uploads the image
        and the generated audio to WaveSpeed's media store, runs InfiniteTalk,
        and downloads the resulting video.

        Args:
            image_storage_path: Path of the avatar image in avatar-images-bucket
            audio_path: Path to the local audio file
            session_id: Session ID for naming the output file
            text_prompt: Expression/pose guidance (optional, sent as `prompt`)
            resolution: Video resolution ("480p" or "720p")

        Returns:
            str: Path to the downloaded video file

        Raises:
            Exception: If video generation fails
        """
        video_start = time.time()

        try:
            logger.info(
                "InfiniteTalk video generation started",
                service="WaveSpeed",
                operation="generate_video",
                image_path=image_storage_path,
                resolution=resolution,
            )

            # Step 1: Download the avatar image from Supabase storage.
            image_bytes = self.supabase.storage.from_(AVATAR_IMAGES_BUCKET).download(
                image_storage_path
            )
            if not image_bytes:
                raise Exception(
                    f"Empty avatar image downloaded from {image_storage_path}"
                )

            # Step 2: Upload image + audio to WaveSpeed to get public URLs.
            image_name = os.path.basename(image_storage_path) or "avatar.jpg"
            image_mime = mimetypes.guess_type(image_name)[0] or "image/jpeg"
            image_url = self._upload_to_wavespeed(image_bytes, image_name, image_mime)

            with open(audio_path, "rb") as f:
                audio_bytes = f.read()
            audio_name = os.path.basename(audio_path)
            audio_url = self._upload_to_wavespeed(audio_bytes, audio_name, "audio/wav")

            # Step 3: Submit the InfiniteTalk job and poll for the result.
            request_id, result_url = self._submit_infinitetalk(
                image_url=image_url,
                audio_url=audio_url,
                text_prompt=text_prompt,
                resolution=resolution,
            )
            video_url = self._poll_wavespeed(request_id, result_url)

            # Step 4: Download the raw video (cropping is deferred to the caption /
            # post-processing step so crop+caption happen in a single ffmpeg pass).
            output_path = os.path.join(
                tempfile.gettempdir(), f"hot_take_video_{session_id}_raw.mp4"
            )
            self._download_video(video_url, output_path)

            duration_ms = int((time.time() - video_start) * 1000)
            logger.info(
                "InfiniteTalk video generation completed",
                service="WaveSpeed",
                operation="generate_video",
                duration_ms=duration_ms,
                status="success",
            )
            return output_path

        except Exception as e:
            duration_ms = int((time.time() - video_start) * 1000)
            logger.error(
                f"InfiniteTalk video generation failed: {e}",
                service="WaveSpeed",
                operation="generate_video",
                duration_ms=duration_ms,
                error=str(e),
            )
            raise Exception(f"Video generation failed: {e}")

    def _wavespeed_headers(self) -> dict:
        """Authorization header shared by all WaveSpeed requests."""
        return {"Authorization": f"Bearer {self.wavespeed_api_key}"}

    def _upload_to_wavespeed(
        self, data: bytes, filename: str, content_type: str
    ) -> str:
        """
        Upload a binary file to WaveSpeed's media store and return its public URL.

        Args:
            data: File bytes
            filename: Name to attach to the multipart upload
            content_type: MIME type of the file

        Returns:
            str: The public download_url of the uploaded file

        Raises:
            Exception: If the upload fails
        """
        logger.debug(f"Uploading {filename} to WaveSpeed ({len(data)} bytes)")

        response = requests.post(
            WAVESPEED_UPLOAD_URL,
            headers=self._wavespeed_headers(),  # no Content-Type for multipart
            files={"file": (filename, data, content_type)},
            timeout=120,
        )

        if not response.ok:
            raise Exception(
                f"WaveSpeed upload failed: {response.status_code} {response.text}"
            )

        download_url = ((response.json() or {}).get("data") or {}).get("download_url")
        if not download_url:
            raise Exception(f"No download_url in WaveSpeed upload response: {response.text}")

        logger.debug(f"Uploaded {filename} -> {download_url}")
        return download_url

    def _submit_infinitetalk(
        self,
        image_url: str,
        audio_url: str,
        text_prompt: str,
        resolution: str,
    ) -> tuple[str, str]:
        """
        Submit an InfiniteTalk job and return (request_id, result_url).

        Aspect ratio is inherited from the input image, so it is not sent. The
        prompt is optional and omitted when empty.

        Raises:
            Exception: If submission fails
        """
        submit_url = f"{WAVESPEED_API_BASE}/{WAVESPEED_VIDEO_MODEL}"

        payload = {
            "image": image_url,
            "audio": audio_url,
            "resolution": resolution,
            "seed": -1,  # random
        }
        if text_prompt:
            payload["prompt"] = text_prompt

        logger.debug(
            f"Submitting InfiniteTalk job (model: {WAVESPEED_VIDEO_MODEL}, resolution: {resolution})"
        )

        response = requests.post(
            submit_url,
            headers={**self._wavespeed_headers(), "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )

        if not response.ok:
            raise Exception(
                f"Failed to submit InfiniteTalk job: {response.status_code} {response.text}"
            )

        data = (response.json() or {}).get("data") or {}
        request_id = data.get("id")
        if not request_id:
            raise Exception(f"No request id in InfiniteTalk submit response: {response.text}")

        result_url = (data.get("urls") or {}).get("get")
        logger.debug(f"InfiniteTalk job submitted with id: {request_id}")
        return request_id, result_url

    def _poll_wavespeed(self, request_id: str, result_url: str = None) -> str:
        """
        Poll a WaveSpeed prediction until it terminates and return the output URL.

        Args:
            request_id: The prediction/task id
            result_url: Optional result URL from the submit response; falls back to
                the conventional predictions endpoint if not provided

        Returns:
            str: URL of the generated video (first output)

        Raises:
            Exception: On a failed/cancelled/timeout status, or if the poll budget
                is exceeded
        """
        result_url = result_url or f"{WAVESPEED_API_BASE}/predictions/{request_id}/result"
        logger.debug(f"Polling WaveSpeed prediction: {request_id}")

        deadline = time.time() + WAVESPEED_TIMEOUT_S
        poll_count = 0
        while True:
            response = requests.get(result_url, headers=self._wavespeed_headers(), timeout=30)

            if not response.ok:
                raise Exception(
                    f"Failed to get WaveSpeed result: {response.status_code} {response.text}"
                )

            data = (response.json() or {}).get("data") or {}
            status = data.get("status")

            poll_count += 1
            if poll_count % 6 == 0:  # Log every ~30 seconds (6 * 5s)
                logger.debug(f"InfiniteTalk status: {status}")

            if status == "completed":
                outputs = data.get("outputs") or []
                if not outputs:
                    raise Exception(f"No outputs in completed InfiniteTalk result: {data!r}")
                logger.info("InfiniteTalk generation complete")
                return outputs[0]
            elif status in ("failed", "cancelled", "timeout"):
                error_message = data.get("error") or status
                raise Exception(f"InfiniteTalk generation {status}: {error_message}")

            if time.time() >= deadline:
                raise Exception(
                    f"InfiniteTalk generation timed out after {WAVESPEED_TIMEOUT_S}s (last status: {status})"
                )

            # Still created/processing, wait and check again.
            time.sleep(WAVESPEED_POLL_INTERVAL_S)

    def _upload_video_to_storage(
        self, video_path: str, user_id: str, session_id: str
    ) -> str:
        """
        Upload video to Supabase storage.

        Args:
            video_path: Local path to the video file
            user_id: The user's ID
            session_id: The session ID

        Returns:
            str: The storage path of the uploaded video

        Raises:
            Exception: If upload fails
        """
        try:
            storage_path = f"{user_id}/hot_take_{session_id}.mp4"

            file_size_mb = os.path.getsize(video_path) / 1024 / 1024
            logger.debug(
                f"Uploading video to Supabase storage: {storage_path} ({file_size_mb:.1f} MB)"
            )

            # Upload using file handle to avoid loading entire video into memory
            with open(video_path, "rb") as f:
                response = self.supabase.storage.from_(
                    "interview-hot-take-videos"
                ).upload(
                    path=storage_path,
                    file=f,
                    file_options={
                        "content-type": "video/mp4",
                        "upsert": "true",  # Overwrite if exists
                    },
                )

            logger.debug(f"Video uploaded to storage: {storage_path}")
            return storage_path

        except Exception as e:
            logger.error(f"Failed to upload video to storage: {e}")
            raise Exception(f"Storage upload failed: {e}")

    def _update_video_filename_in_db(self, session_id: str, storage_path: str) -> None:
        """
        Update interview_call_analysis table with the video filename.

        Args:
            session_id: The LiveKit session ID
            storage_path: The storage path of the video

        Raises:
            Exception: If database update fails
        """
        try:
            logger.debug(
                f"Updating database column hot_take_video_1 with: {storage_path}"
            )

            # Update the hot_take_video_1 column in interview_call_analysis
            response = (
                self.supabase.table("interview_call_analysis")
                .update({"hot_take_video_1": storage_path})
                .eq("session_id", session_id)
                .execute()
            )

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
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                input_path,
            ]

            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True)

            if probe_result.returncode != 0:
                logger.error(f"ffprobe failed: {probe_result.stderr}")
                raise Exception(f"Failed to probe video: {probe_result.stderr}")

            probe_data = json_module.loads(probe_result.stdout)
            input_width = probe_data["streams"][0]["width"]
            input_height = probe_data["streams"][0]["height"]

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
                logger.debug(
                    f"Cropping width: {input_width} -> {new_width} (center crop)"
                )
            else:
                # Video is taller than 9:16, crop height (center crop)
                # Keep full width, calculate new height
                new_width = input_width
                new_height = int(input_width / target_aspect)
                new_height = new_height - (new_height % 2)  # Ensure even number
                x_offset = 0
                y_offset = (input_height - new_height) // 2
                crop_filter = f"crop={new_width}:{new_height}:{x_offset}:{y_offset}"
                logger.debug(
                    f"Cropping height: {input_height} -> {new_height} (center crop)"
                )

            # Build ffmpeg command
            cmd = [
                "ffmpeg",
                "-i",
                input_path,
                "-vf",
                crop_filter,
                "-codec:a",
                "copy",  # Copy audio stream as-is
                "-codec:v",
                "libx264",
                "-crf",
                "23",  # Good quality, lower memory than CRF 18
                "-preset",
                "fast",  # Lower memory usage than 'medium'
                "-y",  # Overwrite output file
                output_path,
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
            logger.info(
                f"Video cropped to 9:16: {output_path} ({file_size / 1024 / 1024:.2f} MB)"
            )

            return output_path

        except Exception as e:
            logger.error(f"Failed to crop video to 9:16: {e}")
            raise Exception(f"Video cropping failed: {e}")
