-- Create post_call_jobs table for background job processing
-- This table stores queued jobs for post-call orchestration tasks

CREATE TABLE IF NOT EXISTS post_call_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    attempts INT DEFAULT 0,
    max_attempts INT DEFAULT 3,
    error TEXT,
    result JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

-- Index for efficiently finding pending jobs
CREATE INDEX idx_pending_jobs ON post_call_jobs(created_at) 
WHERE status = 'pending';

-- Index for status queries
CREATE INDEX idx_jobs_status ON post_call_jobs(status, created_at);

-- Index for session_id lookups
CREATE INDEX idx_jobs_session ON post_call_jobs(session_id);

-- Function to claim a pending job (prevents multiple workers from picking same job)
-- This uses advisory locks to ensure only one worker can claim a job at a time
CREATE OR REPLACE FUNCTION claim_pending_job()
RETURNS SETOF post_call_jobs
LANGUAGE plpgsql
AS $$
DECLARE
    job_record post_call_jobs;
BEGIN
    -- Find the oldest pending job and lock it
    SELECT * INTO job_record
    FROM post_call_jobs
    WHERE status = 'pending'
    ORDER BY created_at ASC
    LIMIT 1
    FOR UPDATE SKIP LOCKED;
    
    -- If we found a job, mark it as processing
    IF FOUND THEN
        UPDATE post_call_jobs
        SET status = 'processing',
            started_at = NOW()
        WHERE id = job_record.id;
        
        RETURN NEXT job_record;
    END IF;
    
    RETURN;
END;
$$;

-- Optional: Function to reset stuck jobs (jobs that have been processing for too long)
CREATE OR REPLACE FUNCTION reset_stuck_jobs(timeout_minutes INT DEFAULT 30)
RETURNS INT
LANGUAGE plpgsql
AS $$
DECLARE
    reset_count INT;
BEGIN
    WITH reset_jobs AS (
        UPDATE post_call_jobs
        SET status = 'pending',
            started_at = NULL
        WHERE status = 'processing'
        AND started_at < NOW() - (timeout_minutes || ' minutes')::INTERVAL
        RETURNING id
    )
    SELECT COUNT(*) INTO reset_count FROM reset_jobs;
    
    RETURN reset_count;
END;
$$;

-- Optional: Function to clean up old completed jobs
CREATE OR REPLACE FUNCTION cleanup_old_jobs(days_to_keep INT DEFAULT 7)
RETURNS INT
LANGUAGE plpgsql
AS $$
DECLARE
    deleted_count INT;
BEGIN
    WITH deleted_jobs AS (
        DELETE FROM post_call_jobs
        WHERE status IN ('completed', 'failed')
        AND completed_at < NOW() - (days_to_keep || ' days')::INTERVAL
        RETURNING id
    )
    SELECT COUNT(*) INTO deleted_count FROM deleted_jobs;
    
    RETURN deleted_count;
END;
$$;

COMMENT ON TABLE post_call_jobs IS 'Queue for background post-call processing tasks';
COMMENT ON COLUMN post_call_jobs.status IS 'Job status: pending, processing, completed, or failed';
COMMENT ON COLUMN post_call_jobs.attempts IS 'Number of processing attempts (for retry logic)';
COMMENT ON FUNCTION claim_pending_job() IS 'Atomically claims the next pending job for processing';
COMMENT ON FUNCTION reset_stuck_jobs(INT) IS 'Resets jobs stuck in processing state for longer than timeout';
COMMENT ON FUNCTION cleanup_old_jobs(INT) IS 'Deletes completed/failed jobs older than specified days';

