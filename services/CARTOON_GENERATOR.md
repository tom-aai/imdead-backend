# Cartoon Generator Service

## Overview

The Cartoon Generator service transforms user portrait photos into editorial illustration style images using Google's Gemini AI. This creates a professional, modern look that's ideal for generating talking avatars with Hedra.

## Workflow Integration

The cartoonization step has been integrated into the post-call orchestration workflow:

1. **Transcript Processing** - Analyze interview conversation
2. **Profile Aggregation** - Aggregate user profile data
3. **Image Cartoonization** ← CARTOON GENERATOR
4. **Hedra Avatar Generation** - Create talking avatar (uses cartoonized image)
5. **Voice Cloning** - Process voice recording
6. **Hot Take Videos** - Generate video content

## Service: `CartoonGenerator`

Located at: `services/cartoon_generator.py`

### Key Features

- Downloads user's original image from Supabase storage
- Transforms image using Google Gemini's image generation model
- Applies editorial illustration style with configurable prompt
- Uploads cartoonized image back to Supabase storage
- Updates `interview_calls.cartoon_id` with cartoonized picture_id
- Returns new picture_id for downstream Hedra processing

### Configuration

**Environment Variable Required:**
```bash
GOOGLE_API_KEY=your_google_api_key_here
```

**Dependencies:**
- `google-genai` - Google Generative AI SDK
- `Pillow` - Python Imaging Library

### Editorial Style Prompt

The service uses a carefully crafted prompt to ensure the generated images are:
- Professional editorial illustration style
- Forward-facing (required for talking avatars)
- Calm, thoughtful expression
- Simplified features while preserving identity
- Minimal, uncluttered background
- Muted color palette
- Even, soft lighting

### API Usage

```python
from services.cartoon_generator import CartoonGenerator

# Initialize service
cartoon_gen = CartoonGenerator(supabase_client, google_api_key)

# Transform image
result = cartoon_gen.cartoonize_image(
    user_id="user_123",
    picture_id="original_image.jpg",
    session_id="session_456"
)

# Result contains:
# {
#     "status": "success",
#     "cartoonized_picture_id": "original_image_cartoon.png",
#     "original_picture_id": "original_image.jpg",
#     "user_id": "user_123",
#     "session_id": "session_456"
# }
```

## Database Schema

The service updates `interview_calls` table with:
- `cartoon_id`: The cartoonized picture_id (e.g., `user_123_cartoon.png`)
- `picture_id`: Remains unchanged (original image)

## Testing

Test the cartoon generator independently:

```bash
# Test only image cartoonization
python services/sandbox_orchestrator.py \
  --session-id YOUR_SESSION_ID \
  --task image_cartoonization

# Test full workflow (includes cartoonization)
python services/sandbox_orchestrator.py \
  --session-id YOUR_SESSION_ID
```

## Error Handling

The service includes comprehensive error handling:
- Missing API key raises exception immediately
- Image download failures
- Gemini API errors
- Storage upload failures
- Database operation errors
- Automatic cleanup of temporary files

**Important:** If cartoonization fails, the entire workflow will fail. The cartoonized image is required for Hedra avatar generation.

## Retry Logic

The post-call orchestrator runs the cartoonization task with:
- **Max retries**: 3
- **Backoff strategy**: Exponential (2^attempt seconds)

## Storage Paths

- **Original images**: `avatar-images-bucket/{original_filename}`
- **Cartoonized images**: `avatar-images-bucket/{original_filename}_cartoon.png`

## Model Information

- **Model**: `gemini-2.0-flash-exp`
- **Type**: Image generation with vision capabilities
- **Input**: Prompt + original portrait image
- **Output**: Generated editorial illustration image

## Future Enhancements

Potential improvements:
- Multiple style options (beyond editorial illustration)
- Batch processing for multiple images
- Quality/resolution settings
- Style preview before final generation
- Caching to avoid re-processing same images
