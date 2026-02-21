"""Tests for bin/memory-save script."""

import subprocess
import sys
from datetime import date
from pathlib import Path

BIN_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "src"
    / "baobaobot"
    / "workspace"
    / "bin"
)


def _daily_file(workspace: Path, date_str: str) -> Path:
    """Helper to get the daily file path in new directory structure."""
    year_month = date_str[:7]
    return workspace / "memory" / "daily" / year_month / f"{date_str}.md"


def run_script(args: list[str], workspace: Path) -> subprocess.CompletedProcess[str]:
    """Run memory-save as a subprocess."""
    script = BIN_DIR / "memory-save"
    return subprocess.run(
        [sys.executable, str(script), *args, "--workspace", str(workspace)],
        capture_output=True,
        text=True,
    )


class TestMemorySaveText:
    """Tests for text mode (new)."""

    def test_saves_text_to_daily(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        (ws / "memory").mkdir(parents=True)

        result = run_script(["learned something important"], ws)
        assert result.returncode == 0
        assert "Saved to daily" in result.stdout

        today = date.today().isoformat()
        daily = _daily_file(ws, today).read_text()
        assert "learned something important" in daily

    def test_saves_text_with_user(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        (ws / "memory").mkdir(parents=True)

        result = run_script(["TODO: fix that bug", "--user", "Alice"], ws)
        assert result.returncode == 0

        today = date.today().isoformat()
        daily = _daily_file(ws, today).read_text()
        assert "[Alice]" in daily
        assert "TODO: fix that bug" in daily

    def test_saves_text_to_experience(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        (ws / "memory").mkdir(parents=True)

        result = run_script(["-e", "project-notes", "uses register(api) pattern"], ws)
        assert result.returncode == 0
        assert "Saved to experience" in result.stdout

        exp_file = ws / "memory" / "experience" / "project-notes.md"
        assert exp_file.exists()
        content = exp_file.read_text()
        assert "uses register(api) pattern" in content


class TestMemorySaveAttachment:
    """Tests for attachment mode (file auto-detection)."""

    def test_saves_image(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        (ws / "memory").mkdir(parents=True)
        src = tmp_path / "photo.jpg"
        src.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

        result = run_script([str(src), "nice photo"], ws)
        assert result.returncode == 0
        assert "Saved to memory" in result.stdout

        # Attachment file exists in date subdirectory with original name
        today = date.today().isoformat()
        att_file = ws / "memory" / "attachments" / today / "photo.jpg"
        assert att_file.exists()
        assert att_file.read_bytes() == b"\xff\xd8\xff\xe0fake-jpeg"

        # Daily memory updated with image syntax
        daily = _daily_file(ws, today).read_text()
        assert "![nice photo](" in daily

    def test_saves_non_image(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        (ws / "memory").mkdir(parents=True)
        src = tmp_path / "report.pdf"
        src.write_bytes(b"%PDF-1.4")

        result = run_script([str(src), "monthly report"], ws)
        assert result.returncode == 0

        today = date.today().isoformat()
        daily = _daily_file(ws, today).read_text()
        assert "[monthly report](" in daily
        assert "![monthly report]" not in daily

    def test_with_user(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        (ws / "memory").mkdir(parents=True)
        src = tmp_path / "data.csv"
        src.write_bytes(b"a,b\n1,2")

        result = run_script([str(src), "data file", "--user", "Alice"], ws)
        assert result.returncode == 0

        today = date.today().isoformat()
        daily = _daily_file(ws, today).read_text()
        assert "[Alice]" in daily

    def test_saves_attachment_to_experience(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        (ws / "memory").mkdir(parents=True)
        src = tmp_path / "diagram.png"
        src.write_bytes(b"png-data")

        result = run_script(["-e", "project-arch", str(src), "system diagram"], ws)
        assert result.returncode == 0
        assert "Saved to memory" in result.stdout

        # Attachment file was created
        today = date.today().isoformat()
        att_file = ws / "memory" / "attachments" / today / "diagram.png"
        assert att_file.exists()

        # Experience file was created with image reference
        exp_file = ws / "memory" / "experience" / "project-arch.md"
        assert exp_file.exists()
        content = exp_file.read_text()
        assert "![system diagram](" in content

    def test_nonexistent_path_treated_as_text(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        (ws / "memory").mkdir(parents=True)

        result = run_script(["/nonexistent/file.txt", "missing"], ws)
        # Should be treated as text mode (not a file), saved to daily
        assert result.returncode == 0
        assert "Saved to daily" in result.stdout
        # Should warn about path-like text
        assert "looks like a file path" in result.stderr

        today = date.today().isoformat()
        daily = _daily_file(ws, today).read_text()
        assert "/nonexistent/file.txt" in daily

    def test_workspace_not_found(self, tmp_path: Path) -> None:
        src = tmp_path / "file.txt"
        src.write_bytes(b"data")

        result = run_script(
            [str(src), "desc", "--workspace", str(tmp_path / "nope")],
            tmp_path / "nope",
        )
        assert result.returncode != 0

    def test_creates_attachments_dir(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        (ws / "memory").mkdir(parents=True)
        assert not (ws / "memory" / "attachments").exists()

        src = tmp_path / "img.png"
        src.write_bytes(b"png-data")

        result = run_script([str(src), "test"], ws)
        assert result.returncode == 0
        today = date.today().isoformat()
        assert (ws / "memory" / "attachments" / today).is_dir()
