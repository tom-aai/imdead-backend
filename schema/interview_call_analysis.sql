-- Create interview_call_analysis table
-- This table stores the processed transcript analysis from Claude
-- Excludes: myers_briggs_code, personality, expertise_description (not needed for this workflow)

CREATE TABLE IF NOT EXISTS interview_call_analysis (
  -- Primary key
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  
  -- Core fields from transcript analysis
  session_id TEXT NOT NULL UNIQUE,
  user_id TEXT NOT NULL,
  user_firstname TEXT NOT NULL,
  user_lastname TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  
  -- Analysis content
  main_topic TEXT NOT NULL,
  conversation_narrative TEXT NOT NULL,
  hot_take_1 TEXT NOT NULL,
  hot_take_2 TEXT NOT NULL,
  social_post_draft TEXT NOT NULL,
  
  -- Nullable fields (filled by later workflow steps)
  avatar_chat_prompt TEXT,
  hot_take_video_1 TEXT,
  hot_take_video_2 TEXT,
  
  -- Timestamps
  analyzed_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_interview_call_analysis_session_id ON interview_call_analysis(session_id);
CREATE INDEX IF NOT EXISTS idx_interview_call_analysis_user_id ON interview_call_analysis(user_id);

-- Optional: Add foreign key constraint to interview_calls table
-- Uncomment if you want to enforce referential integrity
-- ALTER TABLE interview_call_analysis 
-- ADD CONSTRAINT fk_interview_call 
-- FOREIGN KEY (session_id) 
-- REFERENCES interview_calls(session_id) 
-- ON DELETE CASCADE;

-- Create updated_at trigger
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER IF NOT EXISTS update_interview_call_analysis_updated_at 
  BEFORE UPDATE ON interview_call_analysis 
  FOR EACH ROW 
  EXECUTE FUNCTION update_updated_at_column();

