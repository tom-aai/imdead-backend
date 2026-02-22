"""
Tests for the CaptionGenerator service.

Verifies font detection, ASS subtitle generation, and ffmpeg command
construction – everything except the actual ffmpeg execution so these
tests run fast and without external dependencies.
"""

import json
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from services.caption_generator import CaptionGenerator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def generator():
    return CaptionGenerator()


@pytest.fixture
def sample_word_timestamps():
    """Minimal word timestamps similar to what Cartesia produces."""
    return [
        {"word": "Hello", "start": 0.0, "end": 0.3},
        {"word": "world", "start": 0.35, "end": 0.7},
        {"word": "this", "start": 0.75, "end": 1.0},
        {"word": "is", "start": 1.05, "end": 1.2},
        {"word": "a", "start": 1.25, "end": 1.35},
        {"word": "test", "start": 1.4, "end": 1.8},
    ]


@pytest.fixture
def timestamps_file(sample_word_timestamps):
    """Write sample timestamps to a temp JSON file and yield its path."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    json.dump(sample_word_timestamps, f)
    f.close()
    yield f.name
    os.unlink(f.name)


# ---------------------------------------------------------------------------
# Font detection
# ---------------------------------------------------------------------------

class TestFontDetection:
    """_get_font_info must always find a usable font."""

    def test_bundled_font_exists(self, generator):
        """The bundled LiberationSans-Bold.ttf must be present in the repo."""
        assert os.path.exists(generator._BUNDLED_FONT_FILE), (
            f"Bundled font not found at {generator._BUNDLED_FONT_FILE}. "
            "Did you forget to commit fonts/LiberationSans-Bold.ttf?"
        )

    def test_bundled_font_is_valid_ttf(self, generator):
        """The bundled file should be a real TrueType font, not an HTML page."""
        with open(generator._BUNDLED_FONT_FILE, "rb") as f:
            magic = f.read(4)
        # TrueType files start with 0x00010000 or 'true' or 'OTTO'
        valid_magic = (
            magic == b"\x00\x01\x00\x00"
            or magic == b"true"
            or magic == b"OTTO"
        )
        assert valid_magic, (
            f"Bundled font does not look like a TrueType file (magic: {magic!r})"
        )

    def test_get_font_info_returns_bundled_font(self, generator):
        """_get_font_info should find the bundled font and return its info."""
        font_path, font_name = generator._get_font_info()

        assert os.path.exists(font_path)
        assert font_name  # must be a non-empty string
        # On any machine with the repo checked out, the bundled font
        # should be the first match.
        assert "LiberationSans-Bold" in font_path
        assert font_name == "Liberation Sans"

    def test_get_fonts_dir(self, generator):
        """_get_fonts_dir should return the directory containing the font."""
        fonts_dir = generator._get_fonts_dir()
        assert os.path.isdir(fonts_dir)
        # Should contain at least our bundled font
        assert any(f.endswith(".ttf") for f in os.listdir(fonts_dir))

    def test_get_font_path_compat(self, generator):
        """The compatibility wrapper should still return a valid path."""
        path = generator._get_font_path()
        assert os.path.exists(path)


# ---------------------------------------------------------------------------
# ASS subtitle generation
# ---------------------------------------------------------------------------

class TestASSGeneration:
    """Verify the generated ASS subtitle content is well-formed."""

    def test_ass_header_present(self, generator, sample_word_timestamps):
        ass = generator._generate_ass_subtitles(sample_word_timestamps)

        assert "[Script Info]" in ass
        assert "[V4+ Styles]" in ass
        assert "[Events]" in ass

    def test_ass_uses_correct_font_name(self, generator, sample_word_timestamps):
        """The ASS style lines must reference the font by family name."""
        ass = generator._generate_ass_subtitles(sample_word_timestamps)
        _, font_name = generator._get_font_info()

        # Both White and Yellow styles should reference the font
        assert f"Style: White,{font_name}," in ass
        assert f"Style: Yellow,{font_name}," in ass

    def test_dialogue_lines_generated(self, generator, sample_word_timestamps):
        ass = generator._generate_ass_subtitles(sample_word_timestamps)
        dialogue_lines = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
        # Should have at least one base subtitle + per-word overlays
        assert len(dialogue_lines) >= len(sample_word_timestamps)

    def test_timestamps_are_formatted(self, generator):
        assert generator._format_ass_time(0.0) == "0:00:00.00"
        assert generator._format_ass_time(65.5) == "0:01:05.50"
        assert generator._format_ass_time(3723.5) == "1:02:03.50"


# ---------------------------------------------------------------------------
# ffmpeg command construction
# ---------------------------------------------------------------------------

class TestFFmpegCommand:
    """Verify the ffmpeg commands include fontsdir for font discovery."""

    @patch("services.caption_generator.subprocess.run")
    def test_add_captions_passes_fontsdir(
        self, mock_run, generator, timestamps_file, tmp_path
    ):
        """The subtitles filter must include fontsdir=<fonts_dir>."""
        # Create a dummy video file
        video_path = str(tmp_path / "input.mp4")
        with open(video_path, "wb") as f:
            f.write(b"\x00" * 100)

        output_path = str(tmp_path / "output.mp4")

        # Mock ffprobe to return valid video info
        probe_response = MagicMock()
        probe_response.returncode = 0
        probe_response.stdout = json.dumps({
            "streams": [{"width": 1280, "height": 720}],
            "format": {"duration": "5.0"},
        })

        # Mock ffmpeg to "succeed" and create a dummy output
        ffmpeg_response = MagicMock()
        ffmpeg_response.returncode = 0
        ffmpeg_response.stderr = ""

        mock_run.side_effect = [probe_response, ffmpeg_response]

        # Create dummy output so the size check passes
        with open(output_path, "wb") as f:
            f.write(b"\x00" * 100)

        generator.add_captions_to_video(
            video_path=video_path,
            audio_path="/dev/null",
            timestamps_path=timestamps_file,
            output_path=output_path,
        )

        # The second call to subprocess.run should be ffmpeg
        ffmpeg_call = mock_run.call_args_list[1]
        cmd = ffmpeg_call[0][0]  # positional arg: the command list
        vf_flag_idx = cmd.index("-vf")
        vf_value = cmd[vf_flag_idx + 1]

        fonts_dir = generator._get_fonts_dir()
        assert f"fontsdir={fonts_dir}" in vf_value, (
            f"Expected fontsdir in -vf filter, got: {vf_value}"
        )

    @patch("services.caption_generator.subprocess.run")
    def test_simple_captions_passes_fontsdir(
        self, mock_run, generator, timestamps_file, tmp_path
    ):
        """add_simple_captions_to_video must also include fontsdir."""
        video_path = str(tmp_path / "input.mp4")
        with open(video_path, "wb") as f:
            f.write(b"\x00" * 100)

        output_path = str(tmp_path / "output.mp4")

        probe_response = MagicMock()
        probe_response.returncode = 0
        probe_response.stdout = json.dumps({
            "streams": [{"width": 1280, "height": 720}],
        })

        ffmpeg_response = MagicMock()
        ffmpeg_response.returncode = 0
        ffmpeg_response.stderr = ""

        mock_run.side_effect = [probe_response, ffmpeg_response]

        with open(output_path, "wb") as f:
            f.write(b"\x00" * 100)

        generator.add_simple_captions_to_video(
            video_path=video_path,
            timestamps_path=timestamps_file,
            output_path=output_path,
        )

        ffmpeg_call = mock_run.call_args_list[1]
        cmd = ffmpeg_call[0][0]
        vf_flag_idx = cmd.index("-vf")
        vf_value = cmd[vf_flag_idx + 1]

        fonts_dir = generator._get_fonts_dir()
        assert f"fontsdir={fonts_dir}" in vf_value
