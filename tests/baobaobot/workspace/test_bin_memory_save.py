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


def run_script(args: list[str], workspace: Path) -> subprocess.CompletedProcess[str]:
    """Run memory-save as a subprocess."""
    script = BIN_DIR / "memory-save"
    return subprocess.run(
        [sys.executable, str(script), *args, "--workspace", str(workspace)],
        capture_output=True,
        text=True,
    )


class TestMemorySave:
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
        daily = (ws / "memory" / f"{today}.md").read_text()
        assert "![nice photo](" in daily

    def test_saves_non_image(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        (ws / "memory").mkdir(parents=True)
        src = tmp_path / "report.pdf"
        src.write_bytes(b"%PDF-1.4")

        result = run_script([str(src), "monthly report"], ws)
        assert result.returncode == 0

        today = date.today().isoformat()
        daily = (ws / "memory" / f"{today}.md").read_text()
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
        daily = (ws / "memory" / f"{today}.md").read_text()
        assert "[Alice]" in daily

    def test_file_not_found(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        (ws / "memory").mkdir(parents=True)

        result = run_script(["/nonexistent/file.txt", "missing"], ws)
        assert result.returncode != 0
        assert "not found" in result.stderr.lower()

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
