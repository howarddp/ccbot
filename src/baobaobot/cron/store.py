"""JSON persistence for per-workspace cron stores."""

import json
import logging
from pathlib import Path

from ..utils import atomic_write_json
from .types import CronStoreFile

logger = logging.getLogger(__name__)

CRON_DIR = "cron"
JOBS_FILE = "jobs.json"


def store_path(workspace_dir: Path) -> Path:
    """Return the path to cron/jobs.json for a workspace."""
    return workspace_dir / CRON_DIR / JOBS_FILE


def load_store(workspace_dir: Path) -> CronStoreFile:
    """Load cron store from workspace. Returns empty store if not found."""
    path = store_path(workspace_dir)
    if not path.is_file():
        return CronStoreFile()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return CronStoreFile.from_dict(data)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load cron store %s: %s", path, e)
        return CronStoreFile()


def save_store(workspace_dir: Path, store: CronStoreFile) -> None:
    """Atomically write cron store to workspace."""
    path = store_path(workspace_dir)
    atomic_write_json(path, store.to_dict())


def store_mtime(workspace_dir: Path) -> float:
    """Return mtime of cron store file, or 0.0 if not found."""
    path = store_path(workspace_dir)
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
