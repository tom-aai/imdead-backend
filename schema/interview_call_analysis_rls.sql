-- Enable Row Level Security on interview_call_analysis table
ALTER TABLE interview_call_analysis ENABLE ROW LEVEL SECURITY;

-- Policy 1: Allow service role full access (for backend operations)
-- This allows your backend (using SUPABASE_SERVICE_KEY) to do everything
CREATE POLICY "Service role has full access"
  ON interview_call_analysis
  FOR ALL
  TO service_role
  USING (true)
  WITH CHECK (true);

-- Policy 2: Allow authenticated users to read their own analysis
-- This allows users to view their own interview analysis from the frontend
CREATE POLICY "Users can view their own analysis"
  ON interview_call_analysis
  FOR SELECT
  TO authenticated
  USING (auth.uid()::text = user_id);

-- Policy 3: Prevent all public access
-- No unauthenticated users can access this table
-- (This is implicit when RLS is enabled and no policy grants access to anon role)

-- Optional Policy 4: Allow users to update nullable fields on their own records
-- Uncomment if you want users to be able to update their own records
-- CREATE POLICY "Users can update their own nullable fields"
--   ON interview_call_analysis
--   FOR UPDATE
--   TO authenticated
--   USING (auth.uid()::text = user_id)
--   WITH CHECK (auth.uid()::text = user_id);

