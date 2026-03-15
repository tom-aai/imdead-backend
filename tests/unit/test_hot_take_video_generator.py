"""
Unit tests for HotTakeVideoGenerationService.

Tests cover:
- _get_hot_take_data selection of cartoon vs photorealistic avatar based on use_hedra_photo_avatar
"""

import pytest
from unittest.mock import MagicMock

from services.hot_take_video_generator import HotTakeVideoGenerationService


class TestGetHotTakeDataAvatarSelection:
    """Test _get_hot_take_data returns correct hedra_avatar_id based on use_hedra_photo_avatar."""

    @pytest.fixture
    def mock_supabase(self):
        mock = MagicMock()
        return mock

    def test_uses_photo_avatar_when_flag_true(self, mock_supabase):
        """When use_hedra_photo_avatar is True and hedra_photo_avatar_id exists, return photo avatar ID."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {
                "hot_take_1": "My hot take text",
                "hedra_avatar_id": "hedra-cartoon-123",
                "hedra_photo_avatar_id": "hedra-photo-456",
                "use_hedra_photo_avatar": True,
            }
        ]
        service = HotTakeVideoGenerationService(mock_supabase, hedra_api_key="test-key")
        hot_take_text, hedra_avatar_id = service._get_hot_take_data("session-123")
        assert hot_take_text == "My hot take text"
        assert hedra_avatar_id == "hedra-photo-456"

    def test_uses_cartoon_avatar_when_flag_false(self, mock_supabase):
        """When use_hedra_photo_avatar is False, return cartoon hedra_avatar_id."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {
                "hot_take_1": "Another hot take",
                "hedra_avatar_id": "hedra-cartoon-123",
                "hedra_photo_avatar_id": "hedra-photo-456",
                "use_hedra_photo_avatar": False,
            }
        ]
        service = HotTakeVideoGenerationService(mock_supabase, hedra_api_key="test-key")
        hot_take_text, hedra_avatar_id = service._get_hot_take_data("session-123")
        assert hedra_avatar_id == "hedra-cartoon-123"

    def test_uses_cartoon_avatar_when_photo_id_missing(self, mock_supabase):
        """When use_hedra_photo_avatar is True but hedra_photo_avatar_id is null, fall back to cartoon."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {
                "hot_take_1": "Hot take",
                "hedra_avatar_id": "hedra-cartoon-123",
                "hedra_photo_avatar_id": None,
                "use_hedra_photo_avatar": True,
            }
        ]
        service = HotTakeVideoGenerationService(mock_supabase, hedra_api_key="test-key")
        _, hedra_avatar_id = service._get_hot_take_data("session-123")
        assert hedra_avatar_id == "hedra-cartoon-123"

    def test_uses_cartoon_avatar_when_use_hedra_photo_avatar_missing(
        self, mock_supabase
    ):
        """When use_hedra_photo_avatar is missing (e.g. old row), default to cartoon."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {
                "hot_take_1": "Hot take",
                "hedra_avatar_id": "hedra-cartoon-123",
                "hedra_photo_avatar_id": "hedra-photo-456",
            }
        ]
        service = HotTakeVideoGenerationService(mock_supabase, hedra_api_key="test-key")
        _, hedra_avatar_id = service._get_hot_take_data("session-123")
        assert hedra_avatar_id == "hedra-cartoon-123"
