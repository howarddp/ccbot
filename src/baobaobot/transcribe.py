"""Voice-to-text transcription using faster-whisper.

Lazy-loads the WhisperModel singleton on first use.  If ``faster-whisper``
is not installed the module degrades gracefully — ``transcribe_voice()``
returns *None* and the caller falls back to the default file-only flow.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from .config import config

logger = logging.getLogger(__name__)

_model: object | None = None
_model_lock = threading.Lock()
_load_failed = False


def _get_model() -> object | None:
    """Return the cached WhisperModel, creating it on first call."""
    global _model, _load_failed  # noqa: PLW0603
    if _load_failed:
        return None
    if _model is not None:
        return _model
    with _model_lock:
        # Double-checked locking
        if _model is not None:
            return _model
        if _load_failed:
            return None
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]

            _model = WhisperModel(config.whisper_model, compute_type="int8")
            logger.info("Loaded whisper model: %s", config.whisper_model)
            return _model
        except ImportError:
            logger.info("faster-whisper not installed — voice transcription disabled")
            _load_failed = True
            return None
        except Exception:
            logger.exception("Failed to load whisper model")
            _load_failed = True
            return None


def _transcribe_sync(path: Path) -> str | None:
    """Synchronous transcription.  Returns text or *None* on failure."""
    model = _get_model()
    if model is None:
        return None
    try:
        segments, _info = model.transcribe(str(path), vad_filter=True)  # type: ignore[union-attr]
        text = "".join(seg.text for seg in segments).strip()
        return text if text else None
    except Exception:
        logger.exception("Transcription failed for %s", path)
        return None


async def transcribe_voice(path: Path) -> str | None:
    """Transcribe an audio file without blocking the event loop."""
    return await asyncio.to_thread(_transcribe_sync, path)
