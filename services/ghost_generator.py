"""
Ghost Generator service.

Transforms a guest's portrait into the ghost of their older, dead self using
Google Gemini. The proven Gemini call from the old CartoonGenerator is kept
intact (google-genai, gemini-2.5-flash-image, image-in/image-out, PIL normalize,
forward-facing requirement); only the prompt changed — from an editorial
illustration to a ghost — and the transport is local files instead of Supabase.
"""

import os
import time
from PIL import Image
from google import genai
from google.genai import types

from services.logging_config import get_logger

logger = get_logger(__name__)


class GhostGenerator:
    """Transform a guest portrait into a ghost of themselves as they are now."""

    # One move: make them a translucent Hollywood ghost — same person, same age,
    # just a spirit. Forward-facing is non-negotiable — the image drives a
    # talking LemonSlice avatar.
    GHOST_PROMPT = """Transform the uploaded portrait into the ghost of this exact person — the same person as they appear now, turned into a classic Hollywood movie ghost.

Keep it recognizably the same person: the same age, the same face, the same bone structure and features. Do NOT age them or make them look older or more tired — do not add wrinkles, grey hair, or age spots. They are a ghost, not an old person. Just turn them, exactly as they are, into a spirit.

Make them a ghost: semi-transparent and ethereal, with a soft pale luminous glow, slightly desaturated toward cool silvery or bluish tones, edges fading gently into a dark, empty background. You can faintly see through them, like an apparition. Keep it tasteful and cinematic, not gory or zombie-like: a spirit, not a corpse.

Framing requirements (critical — this image drives a talking avatar):
- The person must face forward, looking straight at the camera, head and shoulders centered.
- Show them from the chest/shoulders up; the shoulders should reach the bottom edge so they are not floating in the middle of the frame.
- A calm, neutral expression with the mouth closed.
- Keep the face clearly visible, well-lit enough to read every feature, and undistorted, even while translucent and glowing.

Background: a plain, dark, softly vignetted void that the ghostly form fades into. No text, no props, no extra figures."""

    def __init__(self, google_api_key: str | None = None):
        self.google_api_key = google_api_key or os.getenv("GOOGLE_API_KEY")
        if not self.google_api_key:
            raise Exception(
                "GOOGLE_API_KEY not configured. Please set GOOGLE_API_KEY in environment variables."
            )
        self.client = genai.Client(api_key=self.google_api_key)

    def ghost_image(self, input_path: str, output_path: str) -> str:
        """
        Read a portrait from input_path, ghost it, and write it to output_path.

        Returns output_path. Raises on failure.
        """
        self._transform_with_gemini(input_path, output_path)
        logger.info(f"Ghosted image: {input_path} -> {output_path}")
        return output_path

    def _transform_with_gemini(self, image_path: str, output_path: str) -> str:
        """Transform the image with Gemini and save the result to output_path."""
        transform_start = time.time()

        try:
            logger.info(
                "Google Gemini ghost transformation started",
                service="Google",
                operation="ghost_image",
                model="gemini-2.5-flash-image",
            )

            # Load and preprocess the image
            image = Image.open(image_path)

            # Convert to RGB if necessary (remove alpha channel)
            if image.mode in ("RGBA", "LA", "P"):
                background = Image.new("RGB", image.size, (255, 255, 255))
                if image.mode == "P":
                    image = image.convert("RGBA")
                background.paste(
                    image,
                    mask=image.split()[-1] if image.mode in ("RGBA", "LA") else None,
                )
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")

            # Resize if image is too large (max 4096 on longest side)
            max_size = 4096
            if max(image.size) > max_size:
                ratio = max_size / max(image.size)
                new_size = tuple(int(dim * ratio) for dim in image.size)
                image = image.resize(new_size, Image.Resampling.LANCZOS)
                logger.debug(f"Resized image to {new_size}")

            logger.debug(f"Image prepared: mode={image.mode}, size={image.size}")

            # Transform image using Gemini API
            response = self.client.models.generate_content(
                model="gemini-2.5-flash-image",
                contents=[self.GHOST_PROMPT, image],
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"]
                ),
            )

            # Extract the generated image from response
            generated_image = None
            for part in response.parts:
                if part.inline_data:
                    generated_image = part.as_image()
                    break

            if not generated_image:
                logger.error(
                    f"No image found in response. Response text: "
                    f"{response.text if hasattr(response, 'text') else 'N/A'}"
                )
                raise Exception("No image returned from Gemini API")

            generated_image.save(output_path)

            duration_ms = int((time.time() - transform_start) * 1000)
            logger.info(
                "Google Gemini ghost transformation completed",
                service="Google",
                operation="ghost_image",
                duration_ms=duration_ms,
                status="success",
            )
            return output_path

        except Exception as e:
            duration_ms = int((time.time() - transform_start) * 1000)
            logger.error(
                f"Google Gemini ghost transformation failed: {e}",
                service="Google",
                operation="ghost_image",
                duration_ms=duration_ms,
                error=str(e),
            )
            raise Exception(f"Gemini transformation failed: {e}")
