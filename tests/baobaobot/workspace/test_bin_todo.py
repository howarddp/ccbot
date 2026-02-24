"""Tests for todo-add, todo-list, todo-get, todo-done, todo-update, todo-remove, todo-export."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

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


def extract_todo_id(result: subprocess.CompletedProcess) -> str:
    """Extract TODO ID from todo-add output."""
    for line in result.stdout.splitlines():
        if "TODO added" in line:
            return line.split(": ")[1].strip()
    raise ValueError(f"Could not extract TODO ID from: {result.stdout}")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a minimal workspace directory."""
    (tmp_path / "memory").mkdir()
    (tmp_path / "tmp").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# todo-add
# ---------------------------------------------------------------------------


class TestTodoAdd:
    def test_add_basic(self, workspace: Path):
        result = run_script("todo-add", ["Fix login bug"], workspace)
        assert result.returncode == 0
        assert "TODO added" in result.stdout
        assert "T" in result.stdout

    def test_add_with_type(self, workspace: Path):
        result = run_script("todo-add", ["Fix crash", "--type", "bug"], workspace)
        assert result.returncode == 0
        assert "bug" in result.stdout

    def test_add_with_deadline(self, workspace: Path):
        result = run_script(
            "todo-add", ["Review PR", "--deadline", "2026-03-01"], workspace
        )
        assert result.returncode == 0
        assert "2026-03-01" in result.stdout

    def test_add_with_user(self, workspace: Path):
        result = run_script(
            "todo-add", ["Task for Alice", "--user", "Alice"], workspace
        )
        assert result.returncode == 0
        # Verify via list
        list_result = run_script("todo-list", ["--user", "Alice"], workspace)
        assert "Task for Alice" in list_result.stdout

    def test_add_with_attachment(self, workspace: Path):
        # Create a test file to attach
        test_file = workspace / "tmp" / "test.txt"
        test_file.write_text("test content")
        result = run_script(
            "todo-add", ["With file", "--attach", str(test_file)], workspace
        )
        assert result.returncode == 0
        assert "1 file(s)" in result.stdout
        # Verify attachment was copied
        att_dir = workspace / "memory" / "attachments"
        assert att_dir.exists()
        files = list(att_dir.rglob("test.txt"))
        assert len(files) == 1

    def test_add_custom_type(self, workspace: Path):
        result = run_script(
            "todo-add", ["Custom type", "--type", "research"], workspace
        )
        assert result.returncode == 0
        assert "research" in result.stdout

    def test_id_format(self, workspace: Path):
        result = run_script("todo-add", ["Test ID format"], workspace)
        assert result.returncode == 0
        # Extract ID from output like "TODO added: T20260225-1"
        for line in result.stdout.splitlines():
            if "TODO added" in line:
                todo_id = line.split(": ")[1].strip()
                assert todo_id.startswith("T")
                assert "-" in todo_id
                break

    def test_id_auto_increment(self, workspace: Path):
        r1 = run_script("todo-add", ["First"], workspace)
        r2 = run_script("todo-add", ["Second"], workspace)
        assert r1.returncode == 0
        assert r2.returncode == 0
        # Both should have IDs, second should have higher number
        id1 = extract_todo_id(r1)
        id2 = extract_todo_id(r2)
        # Same date prefix, different sequence
        assert id1[:-1] == id2[:-2] or id1.rsplit("-", 1)[0] == id2.rsplit("-", 1)[0]
        n1 = int(id1.rsplit("-", 1)[1])
        n2 = int(id2.rsplit("-", 1)[1])
        assert n2 > n1

    def test_add_missing_title(self, workspace: Path):
        result = run_script("todo-add", [], workspace)
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# todo-list
# ---------------------------------------------------------------------------


class TestTodoList:
    def test_list_empty(self, workspace: Path):
        result = run_script("todo-list", [], workspace)
        assert result.returncode == 0
        assert "No" in result.stdout

    def test_list_default_open(self, workspace: Path):
        run_script("todo-add", ["Open task"], workspace)
        # Mark a second one as done
        r = run_script("todo-add", ["Done task"], workspace)
        todo_id = extract_todo_id(r)
        run_script("todo-done", [todo_id], workspace)

        result = run_script("todo-list", [], workspace)
        assert "Open task" in result.stdout
        assert "Done task" not in result.stdout

    def test_list_status_all(self, workspace: Path):
        run_script("todo-add", ["Task A"], workspace)
        r = run_script("todo-add", ["Task B"], workspace)
        todo_id = extract_todo_id(r)
        run_script("todo-done", [todo_id], workspace)

        result = run_script("todo-list", ["--status", "all"], workspace)
        assert "Task A" in result.stdout
        assert "Task B" in result.stdout

    def test_list_type_filter(self, workspace: Path):
        run_script("todo-add", ["A bug", "--type", "bug"], workspace)
        run_script("todo-add", ["A task", "--type", "task"], workspace)

        result = run_script("todo-list", ["--type", "bug"], workspace)
        assert "A bug" in result.stdout
        assert "A task" not in result.stdout

    def test_list_overdue(self, workspace: Path):
        run_script("todo-add", ["Overdue", "--deadline", "2020-01-01"], workspace)
        run_script("todo-add", ["Future", "--deadline", "2099-12-31"], workspace)

        result = run_script("todo-list", ["--overdue"], workspace)
        assert "Overdue" in result.stdout
        assert "Future" not in result.stdout

    def test_list_json(self, workspace: Path):
        run_script("todo-add", ["JSON test"], workspace)
        result = run_script("todo-list", ["--json"], workspace)
        assert result.returncode == 0
        items = json.loads(result.stdout)
        assert isinstance(items, list)
        assert len(items) == 1
        assert items[0]["title"] == "JSON test"


# ---------------------------------------------------------------------------
# todo-get
# ---------------------------------------------------------------------------


class TestTodoGet:
    def test_get_existing(self, workspace: Path):
        r = run_script("todo-add", ["Detail test", "--content", "Some details"], workspace)
        todo_id = extract_todo_id(r)

        result = run_script("todo-get", [todo_id], workspace)
        assert result.returncode == 0
        assert "Detail test" in result.stdout
        assert "Some details" in result.stdout

    def test_get_nonexistent(self, workspace: Path):
        result = run_script("todo-get", ["T99999999-99"], workspace)
        assert result.returncode != 0
        assert "not found" in result.stderr


# ---------------------------------------------------------------------------
# todo-done
# ---------------------------------------------------------------------------


class TestTodoDone:
    def test_done_existing(self, workspace: Path):
        r = run_script("todo-add", ["Complete me"], workspace)
        todo_id = extract_todo_id(r)

        result = run_script("todo-done", [todo_id], workspace)
        assert result.returncode == 0
        assert "Marked as done" in result.stdout

        # Verify it's done
        list_result = run_script("todo-list", ["--status", "done"], workspace)
        assert "Complete me" in list_result.stdout

    def test_done_nonexistent(self, workspace: Path):
        result = run_script("todo-done", ["T99999999-99"], workspace)
        assert result.returncode != 0

    def test_done_already_done(self, workspace: Path):
        r = run_script("todo-add", ["Already done"], workspace)
        todo_id = extract_todo_id(r)
        run_script("todo-done", [todo_id], workspace)

        result = run_script("todo-done", [todo_id], workspace)
        assert result.returncode != 0
        assert "Already done" in result.stdout


# ---------------------------------------------------------------------------
# todo-update
# ---------------------------------------------------------------------------


class TestTodoUpdate:
    def test_update_title(self, workspace: Path):
        r = run_script("todo-add", ["Old title"], workspace)
        todo_id = extract_todo_id(r)

        result = run_script("todo-update", [todo_id, "--title", "New title"], workspace)
        assert result.returncode == 0
        assert "New title" in result.stdout

    def test_update_deadline(self, workspace: Path):
        r = run_script("todo-add", ["Deadline test"], workspace)
        todo_id = extract_todo_id(r)

        result = run_script("todo-update", [todo_id, "--deadline", "2026-06-01"], workspace)
        assert result.returncode == 0
        assert "2026-06-01" in result.stdout

    def test_update_attach(self, workspace: Path):
        r = run_script("todo-add", ["Attach test"], workspace)
        todo_id = extract_todo_id(r)

        test_file = workspace / "tmp" / "update_file.txt"
        test_file.write_text("update content")

        result = run_script(
            "todo-update", [todo_id, "--attach", str(test_file)], workspace
        )
        assert result.returncode == 0
        assert "update_file.txt" in result.stdout

    def test_update_nonexistent(self, workspace: Path):
        result = run_script(
            "todo-update", ["T99999999-99", "--title", "Nope"], workspace
        )
        assert result.returncode != 0

    def test_update_nothing(self, workspace: Path):
        r = run_script("todo-add", ["No update"], workspace)
        todo_id = extract_todo_id(r)

        result = run_script("todo-update", [todo_id], workspace)
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# todo-remove
# ---------------------------------------------------------------------------


class TestTodoRemove:
    def test_remove_existing(self, workspace: Path):
        r = run_script("todo-add", ["Remove me"], workspace)
        todo_id = extract_todo_id(r)

        result = run_script("todo-remove", [todo_id], workspace)
        assert result.returncode == 0
        assert "Removed" in result.stdout

        # Verify it's gone
        get_result = run_script("todo-get", [todo_id], workspace)
        assert get_result.returncode != 0

    def test_remove_nonexistent(self, workspace: Path):
        result = run_script("todo-remove", ["T99999999-99"], workspace)
        assert result.returncode != 0

    def test_remove_one_of_many(self, workspace: Path):
        run_script("todo-add", ["Keep me"], workspace)
        r = run_script("todo-add", ["Delete me"], workspace)
        todo_id = extract_todo_id(r)

        run_script("todo-remove", [todo_id], workspace)

        result = run_script("todo-list", [], workspace)
        assert "Keep me" in result.stdout
        assert "Delete me" not in result.stdout


# ---------------------------------------------------------------------------
# todo-export
# ---------------------------------------------------------------------------


class TestTodoExport:
    def test_export_creates_file(self, workspace: Path):
        run_script("todo-add", ["Export test"], workspace)
        result = run_script("todo-export", [], workspace)
        assert result.returncode == 0
        assert "Exported" in result.stdout
        # Find the exported file
        export_files = list((workspace / "tmp").glob("todos-export-*.md"))
        assert len(export_files) == 1

    def test_export_markdown_format(self, workspace: Path):
        run_script("todo-add", ["MD format test", "--type", "bug"], workspace)
        run_script("todo-export", [], workspace)
        export_files = list((workspace / "tmp").glob("todos-export-*.md"))
        content = export_files[0].read_text()
        assert "## Open" in content
        assert "MD format test" in content
        assert "bug" in content

    def test_export_with_filters(self, workspace: Path):
        run_script("todo-add", ["Bug item", "--type", "bug"], workspace)
        run_script("todo-add", ["Task item", "--type", "task"], workspace)
        result = run_script("todo-export", ["--type", "bug"], workspace)
        assert result.returncode == 0
        export_files = sorted((workspace / "tmp").glob("todos-export-*.md"))
        content = export_files[-1].read_text()
        assert "Bug item" in content
        assert "Task item" not in content

    def test_export_empty(self, workspace: Path):
        result = run_script("todo-export", [], workspace)
        assert result.returncode == 0
        export_files = list((workspace / "tmp").glob("todos-export-*.md"))
        content = export_files[0].read_text()
        assert "No matching" in content
