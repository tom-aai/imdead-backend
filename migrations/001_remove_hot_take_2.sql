-- Migration: Remove hot_take_2 columns from interview_call_analysis
-- Description: This migration removes the second hot take video feature, consolidating to a single hot take.
-- Date: 2024-12-30
-- 
-- IMPORTANT: Run this migration in the Supabase SQL Editor after deploying the code changes.
-- The code changes should be deployed first since the backend and frontend no longer reference these columns.

-- Remove hot_take_2 column (the text content for the second hot take)
ALTER TABLE interview_call_analysis DROP COLUMN IF EXISTS hot_take_2;

-- Remove hot_take_video_2 column (the storage path for the second hot take video)
ALTER TABLE interview_call_analysis DROP COLUMN IF EXISTS hot_take_video_2;

-- Note: Existing data in hot_take_2 and hot_take_video_2 will be permanently deleted.
-- If you need to preserve this data, create a backup before running this migration:
--
-- CREATE TABLE interview_call_analysis_backup AS SELECT * FROM interview_call_analysis;

