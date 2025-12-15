-- Enable Row Level Security on post_call_jobs table
-- This prevents unauthorized access to job data

-- Enable RLS
ALTER TABLE post_call_jobs ENABLE ROW LEVEL SECURITY;

-- Policy 1: Allow service role to do everything
-- This is for your backend API and worker (they use SUPABASE_SERVICE_KEY)
CREATE POLICY "Service role has full access"
ON post_call_jobs
FOR ALL
TO service_role
USING (true)
WITH CHECK (true);

-- Policy 2: Allow authenticated users to read only their own jobs
-- This is if you want to allow frontend to check job status directly
-- (requires joining with interview_calls to match user_id)
CREATE POLICY "Users can read their own jobs"
ON post_call_jobs
FOR SELECT
TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM interview_calls
        WHERE interview_calls.session_id = post_call_jobs.session_id
        AND interview_calls.user_id = auth.uid()
    )
);

-- Policy 3: Deny all access to anonymous users
-- No unauthenticated access to job data
CREATE POLICY "No anonymous access"
ON post_call_jobs
FOR ALL
TO anon
USING (false)
WITH CHECK (false);

-- Verify RLS is enabled
SELECT 
    schemaname, 
    tablename, 
    rowsecurity 
FROM pg_tables 
WHERE tablename = 'post_call_jobs';

-- Verify policies are created
SELECT 
    schemaname,
    tablename,
    policyname,
    permissive,
    roles,
    cmd
FROM pg_policies
WHERE tablename = 'post_call_jobs';

