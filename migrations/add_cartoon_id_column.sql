-- Add cartoon_id column to interview_calls table
-- This stores the cartoonized version of the picture_id

ALTER TABLE interview_calls
ADD COLUMN IF NOT EXISTS cartoon_id TEXT NULL;

COMMENT ON COLUMN interview_calls.cartoon_id IS 'The cartoonized picture_id (e.g., user_123_cartoon.png). Null if no cartoonization has been applied yet or for legacy records. When present, Hedra should use this instead of picture_id.';
