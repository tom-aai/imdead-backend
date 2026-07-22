"""
Unit tests for HotTakeVideoGenerationService.

Tests cover:
- _get_hot_take_data selection of the cartoon vs photorealistic avatar image
  (in avatar-images-bucket) based on use_hedra_photo_avatar
- _resolve_image_path storage-path resolution
- Constructor requires a WaveSpeed API key
"""

import pytest
from unittest.mock import MagicMock

from services.hot_take_video_generator import HotTakeVideoGenerationService


def _make_supabase(analysis_row: dict, calls_row: dict | None):
    """
    Build a mock Supabase client that returns `analysis_row` for the
    interview_call_analysis query and `calls_row` (picture_id/user_id) for the
    interview_calls query.
    """
    analysis_query = MagicMock()
    analysis_query.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = (
        [analysis_row] if analysis_row is not None else []
    )

    calls_query = MagicMock()
    calls_query.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = (
        [calls_row] if calls_row is not None else []
    )

    supabase = MagicMock()

    def table(name):
        if name == "interview_call_analysis":
            return analysis_query
        if name == "interview_calls":
            return calls_query
        return MagicMock()

    supabase.table.side_effect = table
    return supabase


class TestGetHotTakeDataAvatarSelection:
    """_get_hot_take_data returns the correct avatar image storage path."""

    def test_uses_photo_when_flag_true_and_picture_present(self):
        supabase = _make_supabase(
            analysis_row={
                "hot_take_1": "My hot take text",
                "cartoon_id": "cartoon-abc.png",
                "use_hedra_photo_avatar": True,
            },
            calls_row={"picture_id": "photo-xyz.jpg", "user_id": "user-1"},
        )
        service = HotTakeVideoGenerationService(supabase, wavespeed_api_key="test-key")
        hot_take_text, image_path = service._get_hot_take_data("session-123")
        assert hot_take_text == "My hot take text"
        assert image_path == "user-1/photo-xyz.jpg"

    def test_uses_cartoon_when_flag_false(self):
        supabase = _make_supabase(
            analysis_row={
                "hot_take_1": "Another hot take",
                "cartoon_id": "cartoon-abc.png",
                "use_hedra_photo_avatar": False,
            },
            calls_row={"picture_id": "photo-xyz.jpg", "user_id": "user-1"},
        )
        service = HotTakeVideoGenerationService(supabase, wavespeed_api_key="test-key")
        _, image_path = service._get_hot_take_data("session-123")
        assert image_path == "user-1/cartoon-abc.png"

    def test_falls_back_to_cartoon_when_photo_missing(self):
        """use_hedra_photo_avatar True but no picture_id -> use the cartoon image."""
        supabase = _make_supabase(
            analysis_row={
                "hot_take_1": "Hot take",
                "cartoon_id": "cartoon-abc.png",
                "use_hedra_photo_avatar": True,
            },
            calls_row={"picture_id": None, "user_id": "user-1"},
        )
        service = HotTakeVideoGenerationService(supabase, wavespeed_api_key="test-key")
        _, image_path = service._get_hot_take_data("session-123")
        assert image_path == "user-1/cartoon-abc.png"

    def test_defaults_to_cartoon_when_flag_missing(self):
        """When use_hedra_photo_avatar is absent (old row), default to cartoon."""
        supabase = _make_supabase(
            analysis_row={
                "hot_take_1": "Hot take",
                "cartoon_id": "cartoon-abc.png",
            },
            calls_row={"picture_id": "photo-xyz.jpg", "user_id": "user-1"},
        )
        service = HotTakeVideoGenerationService(supabase, wavespeed_api_key="test-key")
        _, image_path = service._get_hot_take_data("session-123")
        assert image_path == "user-1/cartoon-abc.png"

    def test_falls_back_to_photo_when_no_cartoon(self):
        """No cartoon_id and flag off -> still use the original photo as a fallback."""
        supabase = _make_supabase(
            analysis_row={
                "hot_take_1": "Hot take",
                "cartoon_id": None,
                "use_hedra_photo_avatar": False,
            },
            calls_row={"picture_id": "photo-xyz.jpg", "user_id": "user-1"},
        )
        service = HotTakeVideoGenerationService(supabase, wavespeed_api_key="test-key")
        _, image_path = service._get_hot_take_data("session-123")
        assert image_path == "user-1/photo-xyz.jpg"

    def test_returns_none_path_when_no_images(self):
        supabase = _make_supabase(
            analysis_row={
                "hot_take_1": "Hot take",
                "cartoon_id": None,
                "use_hedra_photo_avatar": False,
            },
            calls_row={"picture_id": None, "user_id": "user-1"},
        )
        service = HotTakeVideoGenerationService(supabase, wavespeed_api_key="test-key")
        _, image_path = service._get_hot_take_data("session-123")
        assert image_path is None


class TestResolveImagePath:
    """_resolve_image_path prefixes bare filenames with the user id."""

    def test_bare_filename_gets_user_prefix(self):
        assert (
            HotTakeVideoGenerationService._resolve_image_path("pic.jpg", "user-1")
            == "user-1/pic.jpg"
        )

    def test_path_with_slash_used_as_is(self):
        assert (
            HotTakeVideoGenerationService._resolve_image_path("user-1/pic.jpg", "user-9")
            == "user-1/pic.jpg"
        )


class TestConstructor:
    def test_requires_wavespeed_api_key(self, monkeypatch):
        monkeypatch.delenv("WAVESPEED_API_KEY", raising=False)
        with pytest.raises(ValueError, match="WAVESPEED_API_KEY"):
            HotTakeVideoGenerationService(MagicMock())
