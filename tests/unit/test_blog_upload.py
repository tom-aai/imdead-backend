"""
Unit tests for the blog-upload feature (Phase 1).

Covers:
- blog_normalizer.normalize_blog_post — canonical items shape, chunking
- POST /blog-upload-orchestrator — creates an interview_calls row and a
  blog_upload job for valid text; rejects too-short/too-long/missing input.
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from services.blog_normalizer import (
    normalize_blog_post,
    CHUNK_THRESHOLD_CHARS,
)
from services.post_call_orchestrator import PostCallOrchestrator


class TestNormalizeBlogPost:
    """Tests for the blog -> conversation.items[] normalizer."""

    def test_short_post_is_single_user_item(self):
        result = normalize_blog_post("This is a short post about my expertise.")

        assert result == {
            "items": [
                {
                    "role": "user",
                    "content": [{"text": "This is a short post about my expertise."}],
                }
            ]
        }

    def test_every_item_is_expert_attributed(self):
        text = "\n\n".join([f"Paragraph number {i} " + ("x" * 500) for i in range(10)])
        result = normalize_blog_post(text)

        assert len(result["items"]) > 1  # long enough to chunk
        assert all(item["role"] == "user" for item in result["items"])

    def test_long_post_is_paragraph_chunked(self):
        # Build a post well over the chunk threshold out of distinct paragraphs.
        paragraphs = [f"Paragraph {i}. " + ("word " * 100) for i in range(8)]
        text = "\n\n".join(paragraphs)
        assert len(text) > CHUNK_THRESHOLD_CHARS

        result = normalize_blog_post(text)

        # More than one chunk, and no paragraph content is lost.
        assert len(result["items"]) >= 2
        rejoined = " ".join(
            part["text"] for item in result["items"] for part in item["content"]
        )
        for i in range(8):
            assert f"Paragraph {i}." in rejoined

    def test_whitespace_only_yields_no_items(self):
        assert normalize_blog_post("   \n\n   ") == {"items": []}

    def test_content_shape_matches_interview_calls(self):
        """content is a list of {text: ...} dicts, as voice calls store."""
        result = normalize_blog_post("A" * 50)
        content = result["items"][0]["content"]
        assert isinstance(content, list)
        assert isinstance(content[0], dict)
        assert "text" in content[0]


class TestBlogUploadEndpoint:
    """Tests for POST /blog-upload-orchestrator."""

    API_KEY = "test-api-key"

    @pytest.fixture
    def client(self):
        from app import app

        app.config["TESTING"] = True
        return app.test_client()

    @pytest.fixture
    def auth_headers(self):
        return {"X-API-Key": self.API_KEY, "Content-Type": "application/json"}

    @pytest.fixture
    def mock_api_key(self):
        with patch("app.BACKEND_API_KEY", self.API_KEY):
            yield

    @pytest.fixture
    def mock_supabase(self):
        """
        Mock app.supabase so that:
        - selecting the latest interview_calls row returns prior metadata
        - inserting into interview_calls succeeds
        - inserting into post_call_jobs returns a job id
        """
        with patch("app.supabase") as mock:
            interview_calls = MagicMock()
            # latest-session select chain
            interview_calls.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
                {
                    "user_firstname": "Ada",
                    "user_lastname": "Lovelace",
                }
            ]
            # insert chain
            interview_calls.insert.return_value.execute.return_value.data = [
                {"session_id": "generated"}
            ]

            user_avatar_images = MagicMock()
            user_avatar_images.insert.return_value.execute.return_value.data = [
                {"id": "avatar-row-1"}
            ]

            post_call_jobs = MagicMock()
            post_call_jobs.insert.return_value.execute.return_value.data = [
                {"id": "job-123"}
            ]

            def table_side_effect(name):
                if name == "interview_calls":
                    return interview_calls
                if name == "user_avatar_images":
                    return user_avatar_images
                if name == "post_call_jobs":
                    return post_call_jobs
                return MagicMock()

            mock.table.side_effect = table_side_effect
            mock._interview_calls = interview_calls
            mock._user_avatar_images = user_avatar_images
            mock._post_call_jobs = post_call_jobs
            yield mock

    def test_valid_upload_creates_row_and_job(
        self, client, auth_headers, mock_api_key, mock_supabase
    ):
        text = "I have spent a decade building distributed systems. " * 10
        response = client.post(
            "/blog-upload-orchestrator",
            data=json.dumps(
                {
                    "user_id": "user-1",
                    "text": text,
                    "picture_id": "11111111-2222-3333-4444-555555555555",
                }
            ),
            headers=auth_headers,
        )

        assert response.status_code == 202
        body = json.loads(response.data)
        assert body["status"] == "queued"
        assert body["job_id"] == "job-123"
        assert body["session_id"]

        # An interview_calls row was inserted with the blog_upload source.
        call_row = mock_supabase._interview_calls.insert.call_args[0][0]
        assert call_row["source_type"] == "blog_upload"
        assert call_row["source_text"] == text.strip()
        assert call_row["user_id"] == "user-1"
        assert call_row["user_firstname"] == "Ada"
        # The captured picture is stored, normalized to the "{uuid}.png"
        # onboarding convention — not copied from a prior session.
        assert (
            call_row["picture_id"]
            == "11111111-2222-3333-4444-555555555555.png"
        )
        # Conversation is expert-attributed items.
        assert call_row["conversation"]["items"]
        assert all(
            item["role"] == "user" for item in call_row["conversation"]["items"]
        )
        # session_id on the row matches the returned session_id.
        assert call_row["session_id"] == body["session_id"]

        # A user_avatar_images row was seeded for the captured picture so the
        # later cartoonize UPDATE finds a row (D7).
        avatar_row = mock_supabase._user_avatar_images.insert.call_args[0][0]
        assert avatar_row["user_id"] == "user-1"
        assert avatar_row["picture_id"] == "11111111-2222-3333-4444-555555555555.png"
        assert avatar_row["picture_source"] == "blog_upload"

        # A blog_upload job was enqueued for the same session.
        job_row = mock_supabase._post_call_jobs.insert.call_args[0][0]
        assert job_row["job_type"] == "blog_upload"
        assert job_row["session_id"] == body["session_id"]
        assert job_row["status"] == "pending"

    def test_picture_id_with_extension_is_preserved(
        self, client, auth_headers, mock_api_key, mock_supabase
    ):
        """A picture_id that already has an extension is stored as-is."""
        text = "I have spent a decade building distributed systems. " * 10
        client.post(
            "/blog-upload-orchestrator",
            data=json.dumps(
                {"user_id": "user-1", "text": text, "picture_id": "pic-9.png"}
            ),
            headers=auth_headers,
        )
        call_row = mock_supabase._interview_calls.insert.call_args[0][0]
        assert call_row["picture_id"] == "pic-9.png"

    def test_missing_picture_id_returns_400(
        self, client, auth_headers, mock_api_key, mock_supabase
    ):
        response = client.post(
            "/blog-upload-orchestrator",
            data=json.dumps({"user_id": "user-1", "text": "x" * 300}),
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "picture_id" in json.loads(response.data)["error"]

    def test_missing_user_id_returns_400(
        self, client, auth_headers, mock_api_key, mock_supabase
    ):
        response = client.post(
            "/blog-upload-orchestrator",
            data=json.dumps({"text": "x" * 300}),
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "user_id" in json.loads(response.data)["error"]

    def test_missing_text_returns_400(
        self, client, auth_headers, mock_api_key, mock_supabase
    ):
        response = client.post(
            "/blog-upload-orchestrator",
            data=json.dumps({"user_id": "user-1"}),
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "text" in json.loads(response.data)["error"]

    def test_too_short_returns_400(
        self, client, auth_headers, mock_api_key, mock_supabase
    ):
        response = client.post(
            "/blog-upload-orchestrator",
            data=json.dumps(
                {"user_id": "user-1", "text": "too short", "picture_id": "pic-1"}
            ),
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "too short" in json.loads(response.data)["error"].lower()

    def test_too_long_returns_400(
        self, client, auth_headers, mock_api_key, mock_supabase
    ):
        response = client.post(
            "/blog-upload-orchestrator",
            data=json.dumps(
                {"user_id": "user-1", "text": "x" * 50001, "picture_id": "pic-1"}
            ),
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "too long" in json.loads(response.data)["error"].lower()

    def test_unauthorized_without_api_key(self, client, mock_api_key, mock_supabase):
        response = client.post(
            "/blog-upload-orchestrator",
            data=json.dumps({"user_id": "user-1", "text": "x" * 300}),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 401


class TestProcessBlogUpload:
    """Tests for PostCallOrchestrator.process_blog_upload (Phase 2)."""

    def _make_orchestrator(self, mock_supabase, conversation):
        """Build an orchestrator whose minimum-length guard reads `conversation`."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"conversation": conversation}
        ]
        return PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

    @pytest.mark.asyncio
    async def test_builds_avatar_and_enqueues_hot_take(self, mock_supabase):
        """A blog_upload job runs transcript analysis + profile aggregation,
        cartoonizes the captured picture, and enqueues a hot_take job — with
        no Hedra or voice-cloning work attempted (D5/D6)."""
        conversation = {
            "items": [
                {
                    "role": "user",
                    "content": [
                        {"text": "I have spent a decade building distributed systems and this is what I learned."}
                    ],
                }
            ]
        }
        orchestrator = self._make_orchestrator(mock_supabase, conversation)

        with patch.object(
            orchestrator, "_process_transcript", return_value={"status": "success"}
        ) as mock_transcript, patch.object(
            orchestrator, "_aggregate_profile", return_value={"status": "success"}
        ) as mock_profile, patch.object(
            orchestrator, "_cartoonize_image", return_value={"cartoon_id": "cartoon.png"}
        ) as mock_cartoon, patch.object(
            orchestrator, "_enqueue_hot_take_job", return_value={"status": "success", "job_id": "job-1"}
        ) as mock_hot_take, patch.object(
            orchestrator, "_generate_hedra_avatar"
        ) as mock_hedra, patch.object(
            orchestrator, "_process_voice_recording"
        ) as mock_voice:
            result = await orchestrator.process_blog_upload("blog-session-1")

        assert result["status"] == "success"
        assert result["session_id"] == "blog-session-1"

        # Transcript + profile ran and succeeded.
        mock_transcript.assert_called_once_with(session_id="blog-session-1")
        mock_profile.assert_called_once_with(session_id="blog-session-1")
        assert result["tasks"]["transcript_processing"]["status"] == "success"
        assert result["tasks"]["profile_aggregation"]["status"] == "success"

        # The captured picture was cartoonized (the avatar build).
        mock_cartoon.assert_called_once_with(session_id="blog-session-1")
        assert result["tasks"]["image_cartoonization"]["status"] == "success"

        # A hot_take job was enqueued after cartoonization succeeded.
        mock_hot_take.assert_called_once_with("blog-session-1")
        assert result["tasks"]["hot_take_enqueue"]["status"] == "success"

        # No Hedra avatar-asset step and no voice cloning.
        mock_hedra.assert_not_called()
        mock_voice.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_hot_take_when_cartoonization_fails(self, mock_supabase):
        """If cartoonization fails, the hot_take job is not enqueued (D6)."""
        conversation = {
            "items": [
                {
                    "role": "user",
                    "content": [
                        {"text": "A genuinely long enough post about my area of deep expertise here."}
                    ],
                }
            ]
        }
        orchestrator = self._make_orchestrator(mock_supabase, conversation)

        with patch.object(
            orchestrator, "_process_transcript", return_value={"status": "success"}
        ), patch.object(
            orchestrator, "_aggregate_profile", return_value={"status": "success"}
        ), patch.object(
            orchestrator, "_cartoonize_image", side_effect=Exception("cartoon boom")
        ), patch.object(
            orchestrator, "_enqueue_hot_take_job"
        ) as mock_hot_take:
            result = await orchestrator.process_blog_upload("blog-session-4")

        assert result["tasks"]["image_cartoonization"]["status"] == "error"
        assert result["tasks"]["hot_take_enqueue"]["status"] == "skipped"
        mock_hot_take.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_profile_when_transcript_fails(self, mock_supabase):
        """If transcript analysis fails, profile aggregation is skipped (not run).
        Cartoonization does not depend on the transcript, so it still runs."""
        conversation = {
            "items": [
                {
                    "role": "user",
                    "content": [
                        {"text": "A genuinely long enough post about my area of deep expertise here."}
                    ],
                }
            ]
        }
        orchestrator = self._make_orchestrator(mock_supabase, conversation)

        with patch.object(
            orchestrator,
            "_process_transcript",
            side_effect=Exception("LLM boom"),
        ), patch.object(orchestrator, "_aggregate_profile") as mock_profile, patch.object(
            orchestrator, "_cartoonize_image", return_value={"cartoon_id": "cartoon.png"}
        ), patch.object(
            orchestrator, "_enqueue_hot_take_job", return_value={"status": "success", "job_id": "job-1"}
        ):
            result = await orchestrator.process_blog_upload("blog-session-2")

        assert result["tasks"]["transcript_processing"]["status"] == "error"
        assert result["tasks"]["profile_aggregation"]["status"] == "skipped"
        mock_profile.assert_not_called()

    def test_enqueue_hot_take_job_inserts_expected_record(self, mock_supabase):
        """_enqueue_hot_take_job inserts a hot_take post_call_jobs row mirroring
        the /generate-hot-take-video endpoint."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [
            {"id": "hot-take-job-1"}
        ]

        with patch.object(
            orchestrator,
            "_get_picture_and_user_from_session",
            return_value=("pic.png", "user-9"),
        ):
            result = orchestrator._enqueue_hot_take_job("blog-session-1")

        assert result == {"status": "success", "job_id": "hot-take-job-1"}
        mock_supabase.table.assert_called_with("post_call_jobs")
        job_record = mock_supabase.table.return_value.insert.call_args[0][0]
        assert job_record["session_id"] == "blog-session-1"
        assert job_record["job_type"] == "hot_take"
        assert job_record["job_data"] == {"user_id": "user-9"}
        assert job_record["status"] == "pending"

    @pytest.mark.asyncio
    async def test_degenerate_post_is_skipped(self, mock_supabase):
        """A too-short/degenerate post is skipped, not analyzed (min-length guard)."""
        conversation = {"items": [{"role": "user", "content": [{"text": "hi"}]}]}
        orchestrator = self._make_orchestrator(mock_supabase, conversation)

        with patch.object(orchestrator, "_process_transcript") as mock_transcript:
            result = await orchestrator.process_blog_upload("blog-session-3")

        assert result["status"] == "skipped"
        assert result["reason"] == "transcript_too_short"
        mock_transcript.assert_not_called()
