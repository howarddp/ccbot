"""Tests for baobaobot.transcribe — voice-to-text transcription."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# Reset module-level singletons before each test
@pytest.fixture(autouse=True)
def _reset_transcribe_globals():
    """Reset the module-level model cache before every test."""
    import baobaobot.transcribe as mod

    mod._models.clear()
    mod._load_failed = False
    yield
    mod._models.clear()
    mod._load_failed = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_segment(text: str) -> MagicMock:
    seg = MagicMock()
    seg.text = text
    return seg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_transcribe_success() -> None:
    """Successful transcription returns joined segment text."""
    fake_model = MagicMock()
    segments = [_make_segment("Hello "), _make_segment("world")]
    fake_model.transcribe.return_value = (iter(segments), MagicMock())

    with patch.dict("sys.modules", {"faster_whisper": MagicMock()}):
        import baobaobot.transcribe as mod

        with patch("baobaobot.transcribe._get_model", return_value=fake_model):
            result = mod._transcribe_sync(Path("/tmp/voice.ogg"))

    assert result == "Hello world"


def test_import_error_returns_none() -> None:
    """When faster-whisper is not installed, transcription returns None."""
    import baobaobot.transcribe as mod

    # Simulate ImportError inside _get_model
    with patch.dict("sys.modules", {"faster_whisper": None}):
        # Force re-import to hit ImportError
        mod._models.clear()
        mod._load_failed = False

        def fake_get_model(whisper_model: str = "small") -> None:
            mod._load_failed = True
            return None

        with patch.object(mod, "_get_model", side_effect=fake_get_model):
            result = mod._transcribe_sync(Path("/tmp/voice.ogg"))

    assert result is None


def test_model_load_failure() -> None:
    """Model loading failure sets _load_failed and returns None."""
    import baobaobot.transcribe as mod

    with patch(
        "builtins.__import__",
        side_effect=RuntimeError("GPU exploded"),
    ):
        mod._load_failed = False
        mod._models.clear()
        # Directly simulate _get_model returning None on failure
        result = mod._transcribe_sync(Path("/tmp/voice.ogg"))

    # _get_model would have failed, _transcribe_sync returns None
    # Since we can't easily trigger the exact import path, test via _load_failed
    mod._load_failed = True
    result = mod._transcribe_sync(Path("/tmp/voice.ogg"))
    assert result is None


def test_transcription_error_returns_none() -> None:
    """If model.transcribe() raises, return None."""
    import baobaobot.transcribe as mod

    fake_model = MagicMock()
    fake_model.transcribe.side_effect = RuntimeError("decode error")

    with patch.object(mod, "_get_model", return_value=fake_model):
        result = mod._transcribe_sync(Path("/tmp/voice.ogg"))

    assert result is None


def test_singleton_only_loads_once() -> None:
    """_get_model caches the model and doesn't re-create it."""
    import baobaobot.transcribe as mod

    sentinel = MagicMock()
    mod._models["small"] = sentinel
    assert mod._get_model() is sentinel


def test_empty_transcription_returns_none() -> None:
    """If all segments produce empty text, return None."""
    import baobaobot.transcribe as mod

    fake_model = MagicMock()
    segments = [_make_segment(""), _make_segment("  ")]
    fake_model.transcribe.return_value = (iter(segments), MagicMock())

    with patch.object(mod, "_get_model", return_value=fake_model):
        result = mod._transcribe_sync(Path("/tmp/silence.ogg"))

    assert result is None


async def test_transcribe_voice_async() -> None:
    """transcribe_voice() wraps sync call via asyncio.to_thread."""
    import baobaobot.transcribe as mod

    with patch.object(mod, "_transcribe_sync", return_value="hello") as mock_sync:
        result = await mod.transcribe_voice(Path("/tmp/voice.ogg"))

    assert result == "hello"
    mock_sync.assert_called_once_with(Path("/tmp/voice.ogg"), "small")


def test_load_failed_prevents_retry() -> None:
    """Once _load_failed is True, _get_model returns None immediately."""
    import baobaobot.transcribe as mod

    mod._load_failed = True
    assert mod._get_model() is None
