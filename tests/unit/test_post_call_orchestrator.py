"""
Unit tests for PostCallOrchestrator service.

Tests cover:
- Task orchestration and coordination
- Retry logic with exponential backoff
- Error handling and partial failures
- Task execution flow
- Service integration coordination
"""

import pytest
import asyncio
from unittest.mock import MagicMock, Mock, patch, AsyncMock

from services.post_call_orchestrator import PostCallOrchestrator


class TestPostCallOrchestratorInit:
    """Test PostCallOrchestrator initialization."""

    def test_init_with_all_services(self, mock_supabase):
        """Test initialization with all API keys."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="anthropic-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        assert orchestrator.supabase == mock_supabase
        assert orchestrator.transcript_processor is not None
        assert orchestrator.profile_aggregator is not None
        assert orchestrator.cartoon_generator is not None
        assert orchestrator.hedra_avatar_generator is not None
        assert orchestrator.voice_cloning_service is not None


class TestProcessCallCompletion:
    """Test the main process_call_completion method."""

    @pytest.mark.asyncio
    async def test_successful_orchestration(self, mock_supabase):
        """Test successful execution of all tasks."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        # Mock all task methods
        with patch.object(
            orchestrator, "_process_transcript", return_value={"success": True}
        ):
            with patch.object(
                orchestrator, "_aggregate_profile", return_value={"success": True}
            ):
                with patch.object(
                    orchestrator,
                    "_cartoonize_image",
                    return_value={"cartoonized_picture_id": "cartoon.png"},
                ):
                    with patch.object(
                        orchestrator,
                        "_generate_hedra_avatar",
                        return_value={
                            "hedra_id": "123",
                            "hedra_avatar_id": "hedra-123",
                            "hedra_photo_avatar_id": "hedra-photo-456",
                        },
                    ):
                        with patch.object(
                            orchestrator,
                            "_process_voice_recording",
                            return_value={"voice_id": "voice-123"},
                        ):
                            result = await orchestrator.process_call_completion(
                                "session-123"
                            )

                            assert result["status"] == "success"
                            assert result["session_id"] == "session-123"
                            assert len(result["tasks"]) == 5
                            assert "transcript_processing" in result["tasks"]
                            assert "profile_aggregation" in result["tasks"]
                            assert "image_cartoonization" in result["tasks"]
                            assert "hedra_avatar_generation" in result["tasks"]
                            assert "voice_cloning" in result["tasks"]
                            # Hedra generation now produces both cartoon and photorealistic asset IDs
                            hedra_result = result["tasks"]["hedra_avatar_generation"][
                                "result"
                            ]
                            assert (
                                hedra_result.get("hedra_photo_avatar_id")
                                == "hedra-photo-456"
                            )

    @pytest.mark.asyncio
    async def test_orchestration_partial_failure(self, mock_supabase):
        """Test that orchestration continues even if some tasks fail."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        # Mock tasks - some succeed, some fail
        with patch.object(
            orchestrator, "_process_transcript", return_value={"success": True}
        ):
            with patch.object(
                orchestrator,
                "_aggregate_profile",
                side_effect=Exception("Profile error"),
            ):
                with patch.object(
                    orchestrator,
                    "_cartoonize_image",
                    return_value={"cartoonized_picture_id": "cartoon.png"},
                ):
                    with patch.object(
                        orchestrator,
                        "_generate_hedra_avatar",
                        return_value={"hedra_id": "123"},
                    ):
                        with patch.object(
                            orchestrator,
                            "_process_voice_recording",
                            return_value={"voice_id": "voice-123"},
                        ):
                            result = await orchestrator.process_call_completion(
                                "session-123"
                            )

                            # Should still be successful overall
                            assert result["status"] == "success"

                            # But profile_aggregation task should show error
                            profile_task = result["tasks"]["profile_aggregation"]
                            assert profile_task["status"] == "error"
                            assert "Profile error" in profile_task["error"]

    @pytest.mark.asyncio
    async def test_orchestration_complete_failure(self, mock_supabase):
        """Test error handling when orchestration fails completely."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        # Mock first task to raise an exception
        with patch.object(
            orchestrator, "_process_transcript", side_effect=Exception("Critical error")
        ):
            result = await orchestrator.process_call_completion("session-123")

            # Should catch the error
            assert (
                result["status"] == "error"
                or result["tasks"]["transcript_processing"]["status"] == "error"
            )


class TestRunWithRetry:
    """Test the _run_with_retry method."""

    @pytest.mark.asyncio
    async def test_successful_on_first_attempt(self, mock_supabase):
        """Test task succeeds on first attempt."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        mock_task = Mock(return_value={"result": "success"})

        result = await orchestrator._run_with_retry(
            "test_task", mock_task, max_retries=3
        )

        assert result["status"] == "success"
        assert result["result"] == {"result": "success"}
        assert result["attempts"] == 1
        mock_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_successful_after_retries(self, mock_supabase):
        """Test task succeeds after failing once."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        # Fail once, then succeed
        mock_task = Mock(
            side_effect=[Exception("Temporary error"), {"result": "success"}]
        )

        result = await orchestrator._run_with_retry(
            "test_task", mock_task, max_retries=3
        )

        assert result["status"] == "success"
        assert result["result"] == {"result": "success"}
        assert result["attempts"] == 2
        assert mock_task.call_count == 2

    @pytest.mark.asyncio
    async def test_failure_after_max_retries(self, mock_supabase):
        """Test task fails after exhausting retries."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        # Fail all attempts
        mock_task = Mock(side_effect=Exception("Persistent error"))

        result = await orchestrator._run_with_retry(
            "test_task", mock_task, max_retries=3
        )

        assert result["status"] == "error"
        assert "Persistent error" in result["error"]
        assert result["attempts"] == 3
        assert mock_task.call_count == 3

    @pytest.mark.asyncio
    async def test_async_task_execution(self, mock_supabase):
        """Test execution of async functions."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        # Create an async mock
        async def async_task():
            return {"result": "async_success"}

        result = await orchestrator._run_with_retry(
            "async_task", async_task, max_retries=3
        )

        assert result["status"] == "success"
        assert result["result"] == {"result": "async_success"}

    @pytest.mark.asyncio
    async def test_exponential_backoff(self, mock_supabase):
        """Test that retry delays use exponential backoff."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        mock_task = Mock(
            side_effect=[
                Exception("Error 1"),
                Exception("Error 2"),
                {"result": "success"},
            ]
        )

        with patch("asyncio.sleep") as mock_sleep:
            result = await orchestrator._run_with_retry(
                "test_task", mock_task, max_retries=3
            )

            assert result["status"] == "success"

            # Verify exponential backoff: 2^0=1, 2^1=2
            sleep_calls = [call.args[0] for call in mock_sleep.call_args_list]
            assert sleep_calls == [1, 2]  # 2^0 and 2^1


class TestTaskMethods:
    """Test individual task wrapper methods."""

    def test_process_transcript(self, mock_supabase):
        """Test _process_transcript calls TranscriptProcessor."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        with patch.object(
            orchestrator.transcript_processor,
            "process_transcript",
            return_value={"result": "ok"},
        ):
            result = orchestrator._process_transcript("session-123")

            assert result == {"result": "ok"}
            orchestrator.transcript_processor.process_transcript.assert_called_once_with(
                "session-123"
            )

    def test_aggregate_profile(self, mock_supabase):
        """Test _aggregate_profile calls ProfileAggregator."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        with patch.object(
            orchestrator.profile_aggregator,
            "aggregate_user_profile",
            return_value={"result": "ok"},
        ):
            result = orchestrator._aggregate_profile("session-123")

            assert result == {"result": "ok"}
            orchestrator.profile_aggregator.aggregate_user_profile.assert_called_once_with(
                "session-123"
            )

    def test_cartoonize_image(self, mock_supabase):
        """Test _cartoonize_image calls CartoonGenerator."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            google_api_key="google-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
        )

        # Mock the helper method to return picture and user data
        with patch.object(
            orchestrator,
            "_get_picture_and_user_from_session",
            return_value=("pic.jpg", "user-456"),
        ):
            with patch.object(
                orchestrator.cartoon_generator,
                "cartoonize_image",
                return_value={"result": "ok"},
            ):
                result = orchestrator._cartoonize_image("session-123")

                assert result == {"result": "ok"}
                orchestrator.cartoon_generator.cartoonize_image.assert_called_once_with(
                    user_id="user-456", picture_id="pic.jpg", session_id="session-123"
                )

    def test_generate_hedra_avatar(self, mock_supabase):
        """Test _generate_hedra_avatar calls HedraAvatarGenerator."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        # Mock database lookups
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {
                "picture_id": "pic.jpg",
                "user_id": "user-456",
                "cartoon_id": "cartoon.png",
            }
        ]

        with patch.object(
            orchestrator.hedra_avatar_generator,
            "generate_avatar",
            return_value={"result": "ok"},
        ):
            result = orchestrator._generate_hedra_avatar("session-123", "cartoon.png")

            assert result == {"result": "ok"}

    def test_process_voice_recording(self, mock_supabase):
        """Test _process_voice_recording calls VoiceCloningService."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        with patch.object(
            orchestrator.voice_cloning_service,
            "process_voice_recording",
            return_value={"result": "ok"},
        ):
            result = orchestrator._process_voice_recording("session-123")

            assert result == {"result": "ok"}
            orchestrator.voice_cloning_service.process_voice_recording.assert_called_once_with(
                "session-123"
            )


class TestGetPictureAndUserFromSession:
    """Test _get_picture_and_user_from_session method."""

    def test_successful_lookup(self, mock_supabase):
        """Test successful picture_id and user_id lookup."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"picture_id": "pic.jpg", "user_id": "user-456"}
        ]

        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        picture_id, user_id = orchestrator._get_picture_and_user_from_session(
            "session-123"
        )

        assert picture_id == "pic.jpg"
        assert user_id == "user-456"

    def test_session_not_found(self, mock_supabase):
        """Test handling when session not found."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []

        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        picture_id, user_id = orchestrator._get_picture_and_user_from_session(
            "nonexistent"
        )

        assert picture_id is None
        assert user_id is None


class TestGetCartoonIdFromSession:
    """Test _get_cartoon_id_from_session method."""

    def test_successful_lookup(self, mock_supabase):
        """Test successful cartoon_id lookup."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"cartoon_id": "cartoon.png"}
        ]

        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        cartoon_id = orchestrator._get_cartoon_id_from_session("session-123")

        assert cartoon_id == "cartoon.png"

    def test_not_found_returns_none(self, mock_supabase):
        """Test returns None when cartoon_id not found."""
        mock_supabase.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []

        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        cartoon_id = orchestrator._get_cartoon_id_from_session("nonexistent")

        assert cartoon_id is None


class TestProcessAvatarUpdate:
    """Test the process_avatar_update method for avatar updates."""

    @pytest.mark.asyncio
    async def test_successful_avatar_update(self, mock_supabase):
        """Test successful execution of avatar update tasks."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        # Mock the task methods
        with patch.object(
            orchestrator,
            "_cartoonize_avatar_update",
            return_value={"cartoonized_picture_id": "cartoon.png"},
        ):
            with patch.object(
                orchestrator,
                "_generate_hedra_avatar_update",
                return_value={"hedra_avatar_id": "hedra-123"},
            ):
                result = await orchestrator.process_avatar_update(
                    session_id="session-123",
                    user_id="user-456",
                    picture_id="user-456/pic.png",
                )

                assert result["status"] == "success"
                assert result["session_id"] == "session-123"
                assert len(result["tasks"]) == 2
                assert "image_cartoonization" in result["tasks"]
                assert "hedra_avatar_generation" in result["tasks"]

    @pytest.mark.asyncio
    async def test_avatar_update_cartoonization_failure(self, mock_supabase):
        """Test avatar update fails when cartoonization fails."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        # Mock cartoonization to fail
        with patch.object(
            orchestrator,
            "_cartoonize_avatar_update",
            side_effect=Exception("Cartoonization error"),
        ):
            result = await orchestrator.process_avatar_update(
                session_id="session-123",
                user_id="user-456",
                picture_id="user-456/pic.png",
            )

            assert result["status"] == "error"
            assert "Cartoonization" in result["error"]

    @pytest.mark.asyncio
    async def test_avatar_update_hedra_failure(self, mock_supabase):
        """Test avatar update fails when Hedra generation fails."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        # Mock cartoonization success, Hedra failure
        with patch.object(
            orchestrator,
            "_cartoonize_avatar_update",
            return_value={"cartoonized_picture_id": "cartoon.png"},
        ):
            with patch.object(
                orchestrator,
                "_generate_hedra_avatar_update",
                side_effect=Exception("Hedra error"),
            ):
                result = await orchestrator.process_avatar_update(
                    session_id="session-123",
                    user_id="user-456",
                    picture_id="user-456/pic.png",
                )

                # Hedra failure is caught in _run_with_retry so the task will show error
                assert "hedra_avatar_generation" in result["tasks"]
                assert result["tasks"]["hedra_avatar_generation"]["status"] == "error"


class TestAvatarUpdateTaskMethods:
    """Test individual avatar update task wrapper methods."""

    def test_cartoonize_avatar_update(self, mock_supabase):
        """Test _cartoonize_avatar_update calls CartoonGenerator with correct params."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            google_api_key="google-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
        )

        with patch.object(
            orchestrator.cartoon_generator,
            "cartoonize_image",
            return_value={"result": "ok"},
        ):
            result = orchestrator._cartoonize_avatar_update(
                user_id="user-456",
                picture_id="user-456/pic.png",
                session_id="session-123",
            )

            assert result == {"result": "ok"}
            orchestrator.cartoon_generator.cartoonize_image.assert_called_once_with(
                user_id="user-456",
                picture_id="user-456/pic.png",
                session_id="session-123",
            )

    def test_generate_hedra_avatar_update(self, mock_supabase):
        """Test _generate_hedra_avatar_update calls HedraAvatarGenerator with correct params."""
        orchestrator = PostCallOrchestrator(
            supabase_client=mock_supabase,
            anthropic_api_key="test-key",
            hedra_api_key="hedra-key",
            cartesia_api_key="cartesia-key",
            google_api_key="google-key",
        )

        with patch.object(
            orchestrator.hedra_avatar_generator,
            "generate_avatar",
            return_value={"result": "ok"},
        ):
            result = orchestrator._generate_hedra_avatar_update(
                user_id="user-456",
                picture_id="user-456/pic.png",
                session_id="session-123",
            )

            assert result == {"result": "ok"}
            orchestrator.hedra_avatar_generator.generate_avatar.assert_called_once_with(
                user_id="user-456",
                picture_id="user-456/pic.png",
                session_id="session-123",
            )
