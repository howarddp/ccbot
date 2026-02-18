"""Tests for cron-add, cron-list, cron-remove bin scripts."""

import json
import subprocess
import sys
import time
from pathlib import Path

BIN_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "src"
    / "baobaobot"
    / "workspace"
    / "bin"
)


def run_script(
    name: str, args: list[str], workspace: Path
) -> subprocess.CompletedProcess:
    """Run a bin script as a subprocess."""
    script = BIN_DIR / name
    return subprocess.run(
        [sys.executable, str(script), *args, "--workspace", str(workspace)],
        capture_output=True,
        text=True,
    )


def load_jobs(workspace: Path) -> list[dict]:
    """Load jobs from cron/jobs.json."""
    path = workspace / "cron" / "jobs.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("jobs", [])


# ---------------------------------------------------------------------------
# cron-add
# ---------------------------------------------------------------------------


class TestCronAdd:
    def test_add_at_schedule(self, tmp_path: Path):
        """at: schedule creates a one-shot job with delete_after_run=True."""
        (tmp_path / "memory").mkdir()
        result = run_script(
            "cron-add", ["at:2099-12-31T23:59", "test message"], tmp_path
        )
        assert result.returncode == 0
        assert "Job added" in result.stdout

        jobs = load_jobs(tmp_path)
        assert len(jobs) == 1
        job = jobs[0]
        assert job["schedule"]["kind"] == "at"
        assert job["schedule"]["at"] == "2099-12-31T23:59"
        assert job["message"] == "test message"
        assert job["delete_after_run"] is True
        assert job["enabled"] is True
        assert job["state"]["next_run_at"] is not None

    def test_add_every_schedule(self, tmp_path: Path):
        """every: schedule creates a repeating job."""
        (tmp_path / "memory").mkdir()
        result = run_script("cron-add", ["every:30m", "check status"], tmp_path)
        assert result.returncode == 0

        jobs = load_jobs(tmp_path)
        assert len(jobs) == 1
        job = jobs[0]
        assert job["schedule"]["kind"] == "every"
        assert job["schedule"]["every_seconds"] == 1800
        assert job["delete_after_run"] is False

    def test_add_cron_schedule(self, tmp_path: Path):
        """Cron expression schedule."""
        (tmp_path / "memory").mkdir()
        result = run_script(
            "cron-add", ["0 9 * * *", "morning", "--name", "wake-up"], tmp_path
        )
        assert result.returncode == 0

        jobs = load_jobs(tmp_path)
        assert len(jobs) == 1
        job = jobs[0]
        assert job["schedule"]["kind"] == "cron"
        assert job["schedule"]["expr"] == "0 9 * * *"
        assert job["name"] == "wake-up"

    def test_add_cron_with_tz(self, tmp_path: Path):
        """Cron expression with timezone."""
        (tmp_path / "memory").mkdir()
        result = run_script(
            "cron-add", ["0 9 * * *", "morning", "--tz", "Asia/Taipei"], tmp_path
        )
        assert result.returncode == 0

        jobs = load_jobs(tmp_path)
        assert jobs[0]["schedule"]["tz"] == "Asia/Taipei"

    def test_add_multiple_jobs(self, tmp_path: Path):
        """Multiple adds append to the same jobs.json."""
        (tmp_path / "memory").mkdir()
        run_script("cron-add", ["every:1h", "job1"], tmp_path)
        run_script("cron-add", ["every:2h", "job2"], tmp_path)
        run_script("cron-add", ["at:2099-01-01T00:00", "job3"], tmp_path)

        jobs = load_jobs(tmp_path)
        assert len(jobs) == 3
        # All have unique IDs
        ids = [j["id"] for j in jobs]
        assert len(set(ids)) == 3

    def test_add_invalid_schedule(self, tmp_path: Path):
        """Invalid schedule string returns error."""
        (tmp_path / "memory").mkdir()
        result = run_script("cron-add", ["invalid-format", "msg"], tmp_path)
        assert result.returncode != 0
        assert "Unrecognized schedule format" in result.stderr

    def test_add_next_run_at_computed(self, tmp_path: Path):
        """next_run_at is computed and stored."""
        (tmp_path / "memory").mkdir()
        before = time.time()
        run_script("cron-add", ["every:5m", "test"], tmp_path)
        after = time.time()

        jobs = load_jobs(tmp_path)
        next_run = jobs[0]["state"]["next_run_at"]
        assert next_run is not None
        # Should be roughly now + 300s
        assert before + 300 <= next_run <= after + 300 + 1

    def test_add_at_past_time(self, tmp_path: Path):
        """at: with past time → next_run_at is None."""
        (tmp_path / "memory").mkdir()
        run_script("cron-add", ["at:2000-01-01T00:00", "past"], tmp_path)

        jobs = load_jobs(tmp_path)
        assert jobs[0]["state"]["next_run_at"] is None

    def test_add_preserves_workspace_meta(self, tmp_path: Path):
        """Adding to existing jobs.json preserves workspace_meta."""
        cron_dir = tmp_path / "cron"
        cron_dir.mkdir()
        existing = {
            "version": 1,
            "workspace_meta": {"user_id": 42, "thread_id": 99, "chat_id": 7},
            "jobs": [],
        }
        (cron_dir / "jobs.json").write_text(json.dumps(existing))

        run_script("cron-add", ["every:1h", "new job"], tmp_path)

        data = json.loads((cron_dir / "jobs.json").read_text())
        assert data["workspace_meta"]["user_id"] == 42
        assert data["workspace_meta"]["thread_id"] == 99
        assert len(data["jobs"]) == 1

    def test_add_default_name_from_message(self, tmp_path: Path):
        """Default name is derived from message when --name not given."""
        (tmp_path / "memory").mkdir()
        run_script(
            "cron-add",
            ["every:1h", "a very long message that exceeds thirty characters limit"],
            tmp_path,
        )

        jobs = load_jobs(tmp_path)
        assert len(jobs[0]["name"]) <= 30

    def test_add_creates_cron_dir(self, tmp_path: Path):
        """cron/ directory is created if it doesn't exist."""
        (tmp_path / "memory").mkdir()
        assert not (tmp_path / "cron").exists()
        run_script("cron-add", ["every:1h", "test"], tmp_path)
        assert (tmp_path / "cron" / "jobs.json").is_file()


# ---------------------------------------------------------------------------
# cron-list
# ---------------------------------------------------------------------------


class TestCronList:
    def test_list_empty(self, tmp_path: Path):
        """No jobs.json → friendly message."""
        (tmp_path / "memory").mkdir()
        result = run_script("cron-list", [], tmp_path)
        assert result.returncode == 0
        assert "No cron jobs" in result.stdout

    def test_list_with_jobs(self, tmp_path: Path):
        """Lists jobs in human-readable format."""
        (tmp_path / "memory").mkdir()
        run_script(
            "cron-add", ["every:1h", "hourly check", "--name", "hourly"], tmp_path
        )
        run_script(
            "cron-add",
            ["at:2099-06-15T10:00", "dentist", "--name", "dentist"],
            tmp_path,
        )

        result = run_script("cron-list", [], tmp_path)
        assert result.returncode == 0
        assert "hourly" in result.stdout
        assert "dentist" in result.stdout
        assert "Cron jobs (2)" in result.stdout

    def test_list_json_output(self, tmp_path: Path):
        """--json outputs valid JSON array."""
        (tmp_path / "memory").mkdir()
        run_script("cron-add", ["every:30m", "test"], tmp_path)

        result = run_script("cron-list", ["--json"], tmp_path)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) == 1

    def test_list_json_empty(self, tmp_path: Path):
        """--json with no jobs → empty array."""
        (tmp_path / "memory").mkdir()
        result = run_script("cron-list", ["--json"], tmp_path)
        assert result.returncode == 0
        assert json.loads(result.stdout) == []


# ---------------------------------------------------------------------------
# cron-remove
# ---------------------------------------------------------------------------


class TestCronRemove:
    def test_remove_existing(self, tmp_path: Path):
        """Remove an existing job by ID."""
        (tmp_path / "memory").mkdir()
        run_script("cron-add", ["every:1h", "to-remove"], tmp_path)
        jobs = load_jobs(tmp_path)
        job_id = jobs[0]["id"]

        result = run_script("cron-remove", [job_id], tmp_path)
        assert result.returncode == 0
        assert "Job removed" in result.stdout

        remaining = load_jobs(tmp_path)
        assert len(remaining) == 0

    def test_remove_nonexistent(self, tmp_path: Path):
        """Removing a nonexistent ID returns error."""
        (tmp_path / "memory").mkdir()
        run_script("cron-add", ["every:1h", "keep"], tmp_path)

        result = run_script("cron-remove", ["nonexistent"], tmp_path)
        assert result.returncode != 0
        assert "Job not found" in result.stderr

        # Original job still there
        assert len(load_jobs(tmp_path)) == 1

    def test_remove_one_of_many(self, tmp_path: Path):
        """Remove one job, others remain."""
        (tmp_path / "memory").mkdir()
        run_script("cron-add", ["every:1h", "job1"], tmp_path)
        run_script("cron-add", ["every:2h", "job2"], tmp_path)
        run_script("cron-add", ["every:3h", "job3"], tmp_path)

        jobs = load_jobs(tmp_path)
        target_id = jobs[1]["id"]

        run_script("cron-remove", [target_id], tmp_path)

        remaining = load_jobs(tmp_path)
        assert len(remaining) == 2
        remaining_ids = [j["id"] for j in remaining]
        assert target_id not in remaining_ids

    def test_remove_no_jobs_file(self, tmp_path: Path):
        """Remove when no jobs.json exists → error."""
        (tmp_path / "memory").mkdir()
        result = run_script("cron-remove", ["abc123"], tmp_path)
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# resolve_workspace (tested via scripts)
# ---------------------------------------------------------------------------


class TestResolveWorkspace:
    def test_workspace_flag(self, tmp_path: Path):
        """--workspace explicitly sets workspace."""
        (tmp_path / "memory").mkdir()
        run_script("cron-add", ["every:1h", "test"], tmp_path)
        # Verify it used the explicit workspace
        assert (tmp_path / "cron" / "jobs.json").is_file()

    def test_cwd_with_cron_dir(self, tmp_path: Path, monkeypatch):
        """Script detects workspace from cwd with cron/ dir."""
        (tmp_path / "cron").mkdir()
        monkeypatch.chdir(tmp_path)

        script = BIN_DIR / "cron-list"
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert result.returncode == 0

    def test_cwd_with_memory_dir(self, tmp_path: Path, monkeypatch):
        """Script detects workspace from cwd with memory/ dir."""
        (tmp_path / "memory").mkdir()
        monkeypatch.chdir(tmp_path)

        script = BIN_DIR / "cron-list"
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert result.returncode == 0

    def test_parent_dir_detection(self, tmp_path: Path, monkeypatch):
        """Script detects workspace from parent directory."""
        (tmp_path / "memory").mkdir()
        subdir = tmp_path / "projects" / "myproject"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)

        script = BIN_DIR / "cron-add"
        result = subprocess.run(
            [sys.executable, str(script), "every:1h", "test"],
            capture_output=True,
            text=True,
            cwd=str(subdir),
        )
        assert result.returncode == 0
        assert (tmp_path / "cron" / "jobs.json").is_file()
