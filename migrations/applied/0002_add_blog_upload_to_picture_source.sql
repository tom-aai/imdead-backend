-- Migration 0002 — Add 'blog_upload' to the picture_source enum
--
-- The blog-upload flow inserts a user_avatar_images row with
-- picture_source = 'blog_upload' (app.py, blog_upload_orchestrator_endpoint).
-- The picture_source enum did not include that value, so Postgres rejected the
-- insert with 22P02 ("invalid input value for enum picture_source") and the
-- endpoint returned 500. This adds the missing value.
--
-- Note: ALTER TYPE ... ADD VALUE cannot run inside a transaction block, so
-- this statement must be executed on its own (not wrapped in BEGIN/COMMIT).

alter type picture_source add value if not exists 'blog_upload';
