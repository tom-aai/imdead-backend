# Transcript Analysis Workflow

This document describes the transcript processing and analysis workflow for the Expert Avatar Interview system.

## Overview

After a voice interview call completes, the transcript is automatically processed by Claude to extract structured insights that will be used to:
1. Create an avatar agent that emulates the interviewee
2. Generate promotional content (hot takes, social posts)

## Database Schema

### Table: `interview_call_analysis`

The processed analysis is stored in the `interview_call_analysis` table with the following fields:

#### Fields from Transcript Processing
- `session_id` (TEXT, UNIQUE) - Links to the interview call
- `user_id` (TEXT) - User identifier
- `user_firstname` (TEXT) - User's first name
- `user_lastname` (TEXT) - User's last name
- `created_at` (TIMESTAMPTZ) - Call timestamp
- `main_topic` (TEXT) - Primary topic discussed (5 words or less)
- `conversation_narrative` (TEXT) - Comprehensive summary for avatar agent creation
- `hot_take_1` (TEXT) - First compelling statement from the user
- `hot_take_2` (TEXT) - Second compelling statement from the user
- `social_post_draft` (TEXT) - Social media post copy (thesis + CTA)

#### Nullable Fields (Filled by Later Workflow)
- `avatar_chat_prompt` (TEXT, nullable) - Generated prompt for avatar agent
- `hot_take_video_1` (TEXT, nullable) - URL/reference to first video
- `hot_take_video_2` (TEXT, nullable) - URL/reference to second video

#### Metadata
- `id` (UUID) - Primary key
- `analyzed_at` (TIMESTAMPTZ) - When analysis was created
- `updated_at` (TIMESTAMPTZ) - When record was last updated

### Excluded Fields

The following fields from Claude's analysis are NOT stored in the database:
- `myers_briggs_code` - MBTI personality type (not needed for this workflow)
- `personality` - Detailed personality description (not needed for this workflow)
- `expertise_description` - Expertise summary (not needed for this workflow)

## Setup

### 1. Create the Database Table

Run the SQL schema in Supabase:

```bash
psql <your-database-url> < schema/interview_call_analysis.sql
```

Or manually execute the SQL in the Supabase SQL Editor.

### 2. Environment Variables

Ensure these environment variables are set in `.env.local`:

```env
SUPABASE_URL=your_supabase_url
SUPABASE_SERVICE_KEY=your_service_key
ANTHROPIC_API_KEY=your_anthropic_api_key
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

## Usage

### Production API

The Flask backend exposes a `/process-transcript` endpoint:

```bash
curl -X POST http://localhost:5001/process-transcript \
  -H "Content-Type: application/json" \
  -d '{"session_id": "your_session_id"}'
```

Response:
```json
{
  "status": "success",
  "message": "Transcript processed and uploaded successfully",
  "session_id": "your_session_id",
  "analysis_id": "uuid"
}
```

### Sandbox Testing

For development and prompt iteration, use the sandbox script:

```bash
cd expert-avatar-backend/services
python sandbox_transcript_processing.py
```

Edit the `SESSION_ID` constant in the script to test different calls.

## Workflow Steps

1. **Call Completes**: User finishes voice interview
2. **Transcript Saved**: Agent saves conversation to `interview_calls` table
3. **Analysis Triggered**: Backend receives POST to `/process-transcript`
4. **Claude Processing**: Transcript is analyzed using the prompt in `prompts/transcript_analysis_prompt.txt`
5. **Data Upload**: Structured analysis is saved to `interview_call_analysis` table
6. **Later Steps** (separate workflow):
   - Generate avatar chat prompt → update `avatar_chat_prompt`
   - Create video clips → update `hot_take_video_1`, `hot_take_video_2`

## Files

- `services/transcript_processing.py` - Main production service
- `services/sandbox_transcript_processing.py` - Development/testing script
- `prompts/transcript_analysis_prompt.txt` - Claude analysis prompt
- `schema/interview_call_analysis.sql` - Database schema
- `app.py` - Flask API with `/process-transcript` endpoint

## Testing

To test the transcript processing:

1. Complete a test call to generate transcript data
2. Get the `session_id` from the `interview_calls` table
3. Run the sandbox script:
   ```python
   SESSION_ID = "your_session_id"  # Update this in sandbox_transcript_processing.py
   python services/sandbox_transcript_processing.py
   ```
4. Verify the output and check the database for the new record

## Error Handling

The system handles several error cases:
- Missing session_id → 400 Bad Request
- Session not found → 404 Not Found
- No conversation data → Error response
- Claude API errors → 500 Internal Server Error
- Database errors → 500 Internal Server Error

## Next Steps

After transcript analysis completes:
1. Generate the avatar agent prompt using the `conversation_narrative`
2. Create short video clips from the `hot_take_1` and `hot_take_2` content
3. Update the `interview_call_analysis` record with the new data:
   ```python
   supabase.table("interview_call_analysis").update({
       "avatar_chat_prompt": "...",
       "hot_take_video_1": "...",
       "hot_take_video_2": "..."
   }).eq("session_id", session_id).execute()
   ```

