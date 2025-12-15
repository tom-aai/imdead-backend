-- Remove the foreign key constraint from post_call_jobs table
-- This allows more flexible testing and prevents cascade delete issues

-- Drop the foreign key constraint if it exists
ALTER TABLE post_call_jobs 
DROP CONSTRAINT IF EXISTS fk_session;

-- Verify the constraint is removed
SELECT 
    conname AS constraint_name,
    contype AS constraint_type
FROM pg_constraint
WHERE conrelid = 'post_call_jobs'::regclass;

