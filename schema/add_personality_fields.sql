-- Add the three personality/expertise fields to interview_call_analysis table

ALTER TABLE interview_call_analysis 
ADD COLUMN myers_briggs_code TEXT NOT NULL DEFAULT '',
ADD COLUMN personality TEXT NOT NULL DEFAULT '',
ADD COLUMN expertise_description TEXT NOT NULL DEFAULT '';

-- Remove the default values after adding (optional - keeps schema clean)
ALTER TABLE interview_call_analysis 
ALTER COLUMN myers_briggs_code DROP DEFAULT,
ALTER COLUMN personality DROP DEFAULT,
ALTER COLUMN expertise_description DROP DEFAULT;

