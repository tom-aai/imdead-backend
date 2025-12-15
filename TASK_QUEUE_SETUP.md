# Task Queue Setup Guide

This guide walks you through setting up the Supabase database-backed task queue for background job processing.

## Overview

The task queue system allows your Flask app to return immediately when post-call orchestration is triggered, while a separate worker process handles the actual job execution asynchronously.

**Architecture:**
```
LiveKit Agent → Flask API → Insert job to Supabase → Return 202 Accepted
                                    ↓
                          Background Worker polls
                                    ↓
                          Process job → Update status
```

## Step 1: Create Database Table in Supabase

1. **Go to your Supabase project dashboard**
   - Navigate to: https://supabase.com/dashboard/project/YOUR_PROJECT_ID

2. **Open the SQL Editor**
   - Click on "SQL Editor" in the left sidebar
   - Click "New query"

3. **Run the migration SQL**
   - Copy the entire contents of `migrations/create_post_call_jobs_table.sql`
   - Paste it into the SQL editor
   - Click "Run" or press `Cmd+Enter` (Mac) / `Ctrl+Enter` (Windows/Linux)

4. **Enable Row Level Security (RLS)** - **IMPORTANT for security!**
   - Open a new query in SQL Editor
   - Copy the entire contents of `migrations/enable_rls_post_call_jobs.sql`
   - Paste it into the SQL editor
   - Click "Run"
   
   This enables RLS and creates policies that:
   - ✅ Allow your backend/worker (using service key) to do everything
   - ✅ Allow authenticated users to read only their own jobs
   - ❌ Block anonymous access to job data

5. **Verify the table was created**
   - Go to "Table Editor" in the left sidebar
   - You should see a new table called `post_call_jobs`
   - It should have the following columns:
     - `id` (uuid, primary key)
     - `session_id` (text)
     - `status` (text) - one of: pending, processing, completed, failed
     - `attempts` (int)
     - `max_attempts` (int)
     - `error` (text, nullable)
     - `result` (jsonb, nullable)
     - `created_at` (timestamptz)
     - `started_at` (timestamptz, nullable)
     - `completed_at` (timestamptz, nullable)
   - **Check that RLS is enabled**: The table should show a shield icon 🛡️ next to it

## Step 2: Verify Database Functions

The migration also creates three helpful PostgreSQL functions:

### 1. `claim_pending_job()`
- Atomically claims the next pending job for processing
- Uses `FOR UPDATE SKIP LOCKED` to prevent race conditions
- Called by the worker to get the next job

**Test it:**
```sql
-- This should return empty initially
SELECT * FROM claim_pending_job();
```

### 2. `reset_stuck_jobs(timeout_minutes INT)`
- Resets jobs that have been stuck in "processing" state too long
- Useful for recovering from worker crashes
- Default timeout: 30 minutes

**Test it:**
```sql
-- Reset any jobs stuck for more than 30 minutes
SELECT reset_stuck_jobs(30);
```

### 3. `cleanup_old_jobs(days_to_keep INT)`
- Deletes old completed/failed jobs
- Keeps your table clean and performant
- Default: keeps last 7 days

**Test it:**
```sql
-- Clean up jobs older than 7 days
SELECT cleanup_old_jobs(7);
```

## Step 3: Test the Queue Manually

Before deploying, let's test the queue locally:

### 3.1 Create a test job

**Option A: Use a test session_id (recommended for testing)**
```sql
INSERT INTO post_call_jobs (session_id, status, attempts, max_attempts)
VALUES ('test-session-123', 'pending', 0, 3);
```

**Option B: Use a real session_id from your interview_calls table**
```sql
-- First, find a real session_id
SELECT session_id FROM interview_calls LIMIT 1;

-- Then insert using that session_id
INSERT INTO post_call_jobs (session_id, status, attempts, max_attempts)
VALUES ('your-real-session-id-here', 'pending', 0, 3);
```

### 3.2 Verify it appears in the queue

```sql
SELECT * FROM post_call_jobs WHERE status = 'pending';
```

### 3.3 Claim the job (simulating worker behavior)

```sql
SELECT * FROM claim_pending_job();
```

### 3.4 Check the job status changed

```sql
SELECT * FROM post_call_jobs WHERE session_id = 'test-session-123';
-- Status should now be 'processing' and started_at should be set
```

### 3.5 Clean up test data

```sql
DELETE FROM post_call_jobs WHERE session_id = 'test-session-123';
```

## Step 4: Set Up Environment Variables

Make sure these environment variables are set in your `.env.local` file:

```bash
# Supabase (required for both Flask and Worker)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-key-here

# API Keys (required for Worker)
ANTHROPIC_API_KEY=your-anthropic-key
HEDRA_API_KEY=your-hedra-key
CARTESIA_API_KEY=your-cartesia-key

# Worker Configuration (optional, defaults shown)
WORKER_POLL_INTERVAL=5  # seconds between polling
STUCK_JOB_TIMEOUT=30    # minutes before resetting stuck jobs
```

## Step 5: Test Locally

### 5.1 Start the Flask app

```bash
cd expert-avatar-backend
python app.py
```

### 5.2 Start the worker (in a separate terminal)

**Option A: Using Python directly (recommended for local dev)**
```bash
cd expert-avatar-backend
python worker.py
```

**Option B: Using uv (if you prefer)**
```bash
cd expert-avatar-backend
uv run worker.py
```

**Note:** The worker now loads `.env.local` first (just like the Flask app), so your environment variables will be picked up correctly.

You should see:
```
2025-12-14 18:00:00,000 - __main__ - INFO - Starting post-call orchestration worker...
2025-12-14 18:00:00,001 - __main__ - INFO - Worker initialized with ID: worker-12345
2025-12-14 18:00:00,002 - __main__ - INFO - Worker worker-12345 started (poll interval: 5s)
```

### 5.3 Trigger a job

Send a POST request to the Flask app:

```bash
curl -X POST http://localhost:5001/post-call-orchestrator \
  -H "Content-Type: application/json" \
  -d '{"session_id": "your-test-session-id"}'
```

Expected response:
```json
{
  "status": "queued",
  "message": "Post-call orchestration job enqueued",
  "session_id": "your-test-session-id",
  "job_id": "uuid-here"
}
```

### 5.4 Check job status

```bash
curl http://localhost:5001/jobs/<job_id>
```

Expected response:
```json
{
  "job_id": "uuid-here",
  "session_id": "your-test-session-id",
  "status": "processing",
  "attempts": 1,
  "max_attempts": 3,
  "created_at": "2025-12-14T18:00:00.000Z",
  "started_at": "2025-12-14T18:00:05.000Z",
  "completed_at": null
}
```

### 5.5 Watch the worker logs

The worker should pick up the job and start processing:
```
2025-12-14 18:00:05,000 - __main__ - INFO - Claimed job uuid-here for session your-test-session-id
2025-12-14 18:00:05,001 - __main__ - INFO - Processing job uuid-here for session your-test-session-id (attempt 1)
2025-12-14 18:00:05,002 - services.post_call_orchestrator - INFO - Starting post-call orchestration for session your-test-session-id
...
2025-12-14 18:05:00,000 - __main__ - INFO - Job uuid-here completed successfully
```

## Step 6: Deploy to Render

### 6.1 Connect your GitHub repository to Render

1. Go to https://render.com/dashboard
2. Click "New +" and select "Blueprint"
3. Connect your GitHub repository
4. Render will automatically detect the `render.yaml` file

### 6.2 Configure environment variables

In the Render dashboard, set these environment variables for **both** the web service and worker:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `ANTHROPIC_API_KEY`
- `HEDRA_API_KEY`
- `CARTESIA_API_KEY`

### 6.3 Deploy

Render will automatically:
1. Build your Flask API (web service)
2. Build your background worker (worker service)
3. Start both services
4. Provide you with a public URL for your API

### 6.4 Verify deployment

Check the logs for both services in the Render dashboard:

**Web Service logs:**
```
Starting gunicorn...
Supabase client initialized
```

**Worker logs:**
```
Starting post-call orchestration worker...
Worker initialized with ID: worker-render-xyz
Worker worker-render-xyz started (poll interval: 5s)
```

## Step 7: Monitor and Maintain

### Check job queue health

Run this query periodically to see queue status:

```sql
SELECT 
  status,
  COUNT(*) as count,
  AVG(EXTRACT(EPOCH FROM (completed_at - created_at))) as avg_duration_seconds
FROM post_call_jobs
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY status;
```

### Set up automatic cleanup (optional)

Create a Supabase cron job to clean up old jobs:

1. Go to Supabase Dashboard → Database → Extensions
2. Enable the `pg_cron` extension
3. Run this SQL:

```sql
-- Clean up jobs older than 7 days, every day at 2 AM
SELECT cron.schedule(
  'cleanup-old-jobs',
  '0 2 * * *',
  'SELECT cleanup_old_jobs(7);'
);
```

### Reset stuck jobs (if needed)

If the worker crashes or hangs, manually reset stuck jobs:

```sql
-- Reset jobs that have been processing for more than 30 minutes
SELECT reset_stuck_jobs(30);
```

## Troubleshooting

### RLS Errors: "new row violates row-level security policy"

If you get RLS errors when enqueueing jobs:

1. **Check you're using the service key**, not the anon key:
   ```python
   # In app.py and worker.py
   SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")  # ✅ Correct
   # NOT: os.getenv("SUPABASE_ANON_KEY")  # ❌ Wrong
   ```

2. **Verify RLS policies are set up correctly:**
   ```sql
   SELECT policyname, roles FROM pg_policies WHERE tablename = 'post_call_jobs';
   ```
   
   Should show policies for `service_role`, `authenticated`, and `anon`.

3. **If needed, temporarily disable RLS for debugging:**
   ```sql
   ALTER TABLE post_call_jobs DISABLE ROW LEVEL SECURITY;
   ```
   
   **⚠️ Remember to re-enable it after debugging!**

### Worker not picking up jobs

1. **Check worker is running:**
   ```bash
   # Locally
   ps aux | grep worker.py
   
   # On Render: Check worker service logs
   ```

2. **Check for stuck jobs:**
   ```sql
   SELECT * FROM post_call_jobs WHERE status = 'processing' AND started_at < NOW() - INTERVAL '30 minutes';
   ```
   
   If found, reset them:
   ```sql
   SELECT reset_stuck_jobs(30);
   ```

3. **Check for errors:**
   ```sql
   SELECT session_id, attempts, error 
   FROM post_call_jobs 
   WHERE status = 'failed' 
   ORDER BY created_at DESC 
   LIMIT 10;
   ```

### Jobs failing repeatedly

Check the `error` column in failed jobs:

```sql
SELECT session_id, attempts, error, created_at
FROM post_call_jobs
WHERE status = 'failed'
ORDER BY created_at DESC;
```

Common issues:
- Missing API keys
- Invalid session_id (no data in related tables)
- Network timeouts
- Rate limits

### High queue backlog

Check how many pending jobs:

```sql
SELECT COUNT(*) FROM post_call_jobs WHERE status = 'pending';
```

If the backlog is growing:
1. Scale up worker instances on Render (upgrade to paid plan for more workers)
2. Reduce `WORKER_POLL_INTERVAL` (increase polling frequency)
3. Check worker logs for performance issues

## API Endpoints Reference

### POST /post-call-orchestrator
Enqueue a post-call orchestration job.

**Request:**
```json
{
  "session_id": "abc123"
}
```

**Response:** `202 Accepted`
```json
{
  "status": "queued",
  "message": "Post-call orchestration job enqueued",
  "session_id": "abc123",
  "job_id": "uuid-here"
}
```

### GET /jobs/:job_id
Get the status of a job.

**Response:** `200 OK`
```json
{
  "job_id": "uuid-here",
  "session_id": "abc123",
  "status": "completed",
  "attempts": 1,
  "max_attempts": 3,
  "result": { ... },
  "created_at": "2025-12-14T18:00:00.000Z",
  "started_at": "2025-12-14T18:00:05.000Z",
  "completed_at": "2025-12-14T18:05:00.000Z"
}
```

## Next Steps

- ✅ Set up monitoring/alerting for failed jobs
- ✅ Add job queue metrics to your dashboard
- ✅ Consider adding webhook notifications when jobs complete
- ✅ Scale worker instances as your traffic grows

---

**Questions?** Check the worker logs first, then inspect the `post_call_jobs` table in Supabase.

