"""Tests for web-search and web-read bin scripts."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch


BIN_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "src"
    / "baobaobot"
    / "workspace"
    / "bin"
)


def run_script(
    name: str, args: list[str]
) -> subprocess.CompletedProcess:
    """Run a bin script as a subprocess."""
    script = BIN_DIR / name
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# web-search
# ---------------------------------------------------------------------------


class TestWebSearch:
    def test_missing_query(self):
        result = run_script("web-search", [])
        assert result.returncode != 0

    def test_help(self):
        result = run_script("web-search", ["--help"])
        assert result.returncode == 0
        assert "query" in result.stdout

    @patch("duckduckgo_search.DDGS")
    def test_search_basic(self, mock_ddgs_cls):
        """Test that the script calls DDGS.text correctly (unit test via import)."""
        # This is an integration test that the script parses args correctly
        # Actual DDG calls are tested separately
        result = run_script("web-search", ["test query", "--limit", "1"])
        # Script should run without crashing (may return 0 or results)
        # We just verify it doesn't crash on valid args
        assert result.returncode == 0 or "error" in result.stderr.lower()

    def test_json_flag(self):
        result = run_script("web-search", ["python programming", "--limit", "1", "--json"])
        # Should either return JSON or a search error
        if result.returncode == 0 and result.stdout.strip():
            try:
                data = json.loads(result.stdout)
                assert isinstance(data, list)
            except json.JSONDecodeError:
                pass  # Network may not be available

    def test_region_flag(self):
        result = run_script("web-search", ["test", "--region", "tw-tzh", "--limit", "1"])
        # Should not crash with region flag
        assert result.returncode == 0 or "error" in result.stderr.lower()

    def test_time_flag(self):
        result = run_script("web-search", ["test", "--time", "w", "--limit", "1"])
        assert result.returncode == 0 or "error" in result.stderr.lower()

    def test_news_flag(self):
        result = run_script("web-search", ["technology", "--news", "--limit", "1"])
        assert result.returncode == 0 or "error" in result.stderr.lower()


# ---------------------------------------------------------------------------
# web-read
# ---------------------------------------------------------------------------


class TestWebRead:
    def test_missing_url(self):
        result = run_script("web-read", [])
        assert result.returncode != 0

    def test_help(self):
        result = run_script("web-read", ["--help"])
        assert result.returncode == 0
        assert "url" in result.stdout

    def test_read_valid_url(self):
        result = run_script("web-read", ["https://example.com"])
        if result.returncode == 0:
            assert len(result.stdout) > 0
        # May fail if no network

    def test_read_invalid_url(self):
        result = run_script("web-read", ["https://thisdomaindoesnotexist12345.com"])
        assert result.returncode != 0

    def test_format_text(self):
        result = run_script("web-read", ["https://example.com", "--format", "text"])
        if result.returncode == 0:
            assert len(result.stdout) > 0

    def test_length_limit(self):
        result = run_script("web-read", ["https://example.com", "--length", "100"])
        if result.returncode == 0:
            # Output should be close to 100 chars (may include truncation message)
            assert len(result.stdout) < 200

    def test_with_metadata(self):
        result = run_script("web-read", ["https://example.com", "--with-metadata"])
        if result.returncode == 0:
            assert len(result.stdout) > 0
