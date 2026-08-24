-- Migration 0001 — Blog-upload foundation (Phase 0)
--
-- Adds two columns to interview_calls so a session can record where it came
-- from and, for uploads, retain the raw post text.
--
--   * source_type  — 'voice' (a LiveKit voice interview, the default and only
--                    kind that exists today) vs 'blog_upload' (a post the
--                    expert uploaded). NOT NULL with a default of 'voice'.
--   * source_text  — the raw uploaded post, kept for audit/debugging and
--                    possible re-analysis. NULL for voice calls.
--
-- Backfill: existing rows are automatically treated as 'voice' via the column
-- default — the NOT NULL default is applied to every pre-existing row at ALTER
-- time, so no separate UPDATE/backfill statement is needed for historical
-- sessions. source_text stays NULL for those rows, which is correct (they have
-- no uploaded text).

alter table interview_calls
  add column if not exists source_type text not null default 'voice';

alter table interview_calls
  add column if not exists source_text text;
