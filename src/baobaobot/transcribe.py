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

logger = logging.getLogger(__name__)

_models: dict[str, object] = {}
_model_lock = threading.Lock()
_load_failed = False


def _get_model(whisper_model: str = "small") -> object | None:
    """Return the cached WhisperModel for *whisper_model*, creating it on first call."""
    global _load_failed  # noqa: PLW0603
    if _load_failed:
        return None
    cached = _models.get(whisper_model)
    if cached is not None:
        return cached
    with _model_lock:
        # Double-checked locking
        cached = _models.get(whisper_model)
        if cached is not None:
            return cached
        if _load_failed:
            return None
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]

            model = WhisperModel(whisper_model, compute_type="int8")
            _models[whisper_model] = model
            logger.info("Loaded whisper model: %s", whisper_model)
            return model
        except ImportError:
            logger.info("faster-whisper not installed — voice transcription disabled")
            _load_failed = True
            return None
        except Exception:
            logger.exception("Failed to load whisper model: %s", whisper_model)
            _load_failed = True
            return None


def _transcribe_sync(path: Path, whisper_model: str = "small") -> str | None:
    """Synchronous transcription.  Returns text or *None* on failure."""
    model = _get_model(whisper_model)
    if model is None:
        return None
    try:
        segments, _info = model.transcribe(str(path), vad_filter=True)  # type: ignore[union-attr]
        text = "".join(seg.text for seg in segments).strip()
        return text if text else None
    except Exception:
        logger.exception("Transcription failed for %s", path)
        return None


async def transcribe_voice(path: Path, whisper_model: str = "small") -> str | None:
    """Transcribe an audio file without blocking the event loop."""
    return await asyncio.to_thread(_transcribe_sync, path, whisper_model)
