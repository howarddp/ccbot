"""Tests for baobaobot.transcript_parser — pure logic, no I/O."""

import pytest

from baobaobot.transcript_parser import (
    ParsedMessage,
    TranscriptParser,
    _NO_NOTIFY_TAG,
)

EXPQUOTE_START = TranscriptParser.EXPANDABLE_QUOTE_START
EXPQUOTE_END = TranscriptParser.EXPANDABLE_QUOTE_END


# ── parse_line ───────────────────────────────────────────────────────────


class TestParseLine:
    @pytest.mark.parametrize(
        "line, expected",
        [
            ('{"type": "user"}', {"type": "user"}),
            ("not-json", None),
            ("", None),
            ("   \t  ", None),
        ],
        ids=["valid_json", "invalid_json", "empty", "whitespace"],
    )
    def test_parse_line(self, line: str, expected: dict | None):
        assert TranscriptParser.parse_line(line) == expected


# ── extract_text_only ────────────────────────────────────────────────────


class TestExtractTextOnly:
    @pytest.mark.parametrize(
        "content, expected",
        [
            ("plain string", "plain string"),
            (
                [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}],
                "hello\nworld",
            ),
            (
                [
                    {"type": "text", "text": "keep"},
                    {"type": "tool_use", "name": "Read"},
                ],
                "keep",
            ),
            ([], ""),
            (42, ""),
        ],
        ids=["string", "text_blocks", "mixed", "empty_list", "non_list_non_string"],
    )
    def test_extract_text_only(self, content: list | str | int, expected: str):
        assert TranscriptParser.extract_text_only(content) == expected


# ── format_tool_use_summary ──────────────────────────────────────────────


class TestFormatToolUseSummary:
    @pytest.mark.parametrize(
        "name, input_data, expected",
        [
            ("Read", {"file_path": "src/main.py"}, "**Read**(src/main.py)"),
            ("Write", {"file_path": "out.txt"}, "**Write**(out.txt)"),
            ("Bash", {"command": "ls -la"}, "**Bash**(ls -la)"),
            ("Grep", {"pattern": "TODO"}, "**Grep**(TODO)"),
            ("Glob", {"pattern": "*.py"}, "**Glob**(*.py)"),
            ("Task", {"description": "analyze code"}, "**Task**(analyze code)"),
            (
                "WebFetch",
                {"url": "https://example.com"},
                "**WebFetch**(https://example.com)",
            ),
            ("WebSearch", {"query": "python async"}, "**WebSearch**(python async)"),
            ("TodoWrite", {"todos": [1, 2, 3]}, "**TodoWrite**(3 item(s))"),
            ("TodoRead", {}, "**TodoRead**"),
            (
                "AskUserQuestion",
                {"questions": [{"question": "Continue?"}]},
                "**AskUserQuestion**(Continue?)",
            ),
            ("ExitPlanMode", {}, "**ExitPlanMode**"),
            ("Skill", {"skill": "code-review"}, "**Skill**(code-review)"),
            (
                "CustomTool",
                {"first_key": "value1"},
                "**CustomTool**(value1)",
            ),
        ],
        ids=[
            "Read",
            "Write",
            "Bash",
            "Grep",
            "Glob",
            "Task",
            "WebFetch",
            "WebSearch",
            "TodoWrite",
            "TodoRead",
            "AskUserQuestion",
            "ExitPlanMode",
            "Skill",
            "unknown_tool",
        ],
    )
    def test_tool_summary(self, name: str, input_data: dict, expected: str):
        assert TranscriptParser.format_tool_use_summary(name, input_data) == expected

    def test_non_dict_input(self):
        assert (
            TranscriptParser.format_tool_use_summary("Read", "not a dict") == "**Read**"
        )

    def test_truncation_at_200_chars(self):
        long_value = "x" * 250
        result = TranscriptParser.format_tool_use_summary(
            "Bash", {"command": long_value}
        )
        assert len(long_value) > 200
        assert result == f"**Bash**({'x' * 200}…)"


# ── extract_tool_result_text ─────────────────────────────────────────────


class TestExtractToolResultText:
    @pytest.mark.parametrize(
        "content, expected",
        [
            ("raw string", "raw string"),
            (
                [{"type": "text", "text": "line1"}, {"type": "text", "text": "line2"}],
                "line1\nline2",
            ),
            (
                [{"type": "text", "text": "keep"}, {"type": "image", "data": "..."}],
                "keep",
            ),
            (None, ""),
        ],
        ids=["string", "text_blocks", "mixed", "none"],
    )
    def test_extract_tool_result_text(self, content: str | list | None, expected: str):
        assert TranscriptParser.extract_tool_result_text(content) == expected


# ── parse_message ────────────────────────────────────────────────────────


class TestParseMessage:
    def test_user_text(self):
        data = {
            "type": "user",
            "message": {"content": [{"type": "text", "text": "hello"}]},
        }
        result = TranscriptParser.parse_message(data)
        assert result == ParsedMessage(message_type="user", text="hello")

    def test_assistant_text(self):
        data = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "hi there"}]},
        }
        result = TranscriptParser.parse_message(data)
        assert result == ParsedMessage(message_type="assistant", text="hi there")

    def test_local_command_with_stdout(self):
        data = {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "<command-name>/help</command-name>"
                            "<local-command-stdout>Available commands</local-command-stdout>"
                        ),
                    }
                ]
            },
        }
        result = TranscriptParser.parse_message(data)
        assert result is not None
        assert result.message_type == "local_command"
        assert result.text == "Available commands"
        assert result.tool_name == "/help"

    def test_local_command_invoke(self):
        data = {
            "type": "user",
            "message": {
                "content": [
                    {"type": "text", "text": "<command-name>/clear</command-name>"}
                ]
            },
        }
        result = TranscriptParser.parse_message(data)
        assert result is not None
        assert result.message_type == "local_command_invoke"
        assert result.text == ""
        assert result.tool_name == "/clear"

    def test_non_user_assistant_returns_none(self):
        data = {
            "type": "summary",
            "message": {"content": "summary text"},
        }
        assert TranscriptParser.parse_message(data) is None

    def test_string_content(self):
        data = {
            "type": "assistant",
            "message": {"content": "plain response"},
        }
        result = TranscriptParser.parse_message(data)
        assert result == ParsedMessage(message_type="assistant", text="plain response")


# ── _format_edit_diff ────────────────────────────────────────────────────


class TestFormatEditDiff:
    @pytest.mark.parametrize(
        "old, new, check",
        [
            (
                "hello",
                "world",
                lambda r: "-hello" in r and "+world" in r,
            ),
            (
                "line1\nline2\nline3",
                "line1\nchanged\nline3",
                lambda r: "-line2" in r and "+changed" in r,
            ),
            (
                "same",
                "same",
                lambda r: r == "",
            ),
        ],
        ids=["single_line", "multi_line", "identical"],
    )
    def test_format_edit_diff(self, old: str, new: str, check):
        result = TranscriptParser._format_edit_diff(old, new)
        assert check(result), f"Check failed for ({old!r}, {new!r}): {result!r}"


# ── _format_tool_result_text ─────────────────────────────────────────────


class TestFormatToolResultText:
    @pytest.mark.parametrize(
        "text, tool_name, check",
        [
            (
                "line1\nline2\nline3",
                "Read",
                lambda r: r == "  ⎿  Read 3 lines",
            ),
            (
                "line1\nline2",
                "Write",
                lambda r: r == "  ⎿  Wrote 2 lines",
            ),
            (
                "output line",
                "Bash",
                lambda r: (
                    r.startswith("  ⎿  Output 1 lines")
                    and EXPQUOTE_START in r
                    and EXPQUOTE_END in r
                ),
            ),
            (
                "file1.py\nfile2.py\n",
                "Grep",
                lambda r: "Found 2 matches" in r and EXPQUOTE_START in r,
            ),
            (
                "a.py\nb.py\nc.py",
                "Glob",
                lambda r: "Found 3 files" in r and EXPQUOTE_START in r,
            ),
            (
                "agent says hello",
                "Task",
                lambda r: "Agent output 1 lines" in r and EXPQUOTE_START in r,
            ),
            (
                "page content here",
                "WebFetch",
                lambda r: (
                    f"Fetched {len('page content here')} characters" in r
                    and EXPQUOTE_START in r
                ),
            ),
            (
                "",
                "Read",
                lambda r: r == "",
            ),
        ],
        ids=["Read", "Write", "Bash", "Grep", "Glob", "Task", "WebFetch", "empty"],
    )
    def test_format_tool_result_text(self, text: str, tool_name: str, check):
        result = TranscriptParser._format_tool_result_text(text, tool_name)
        assert check(result), f"Failed check for {tool_name!r}: {result!r}"


# ── parse_entries ────────────────────────────────────────────────────────


class TestParseEntries:
    def test_assistant_text(self, make_jsonl_entry, make_text_block):
        entries = [make_jsonl_entry("assistant", [make_text_block("Hello!")])]
        result, pending, _ = TranscriptParser.parse_entries(entries)
        assert len(result) == 1
        assert result[0].role == "assistant"
        assert result[0].text == "Hello!"
        assert result[0].content_type == "text"

    def test_user_text(self, make_jsonl_entry, make_text_block):
        entries = [make_jsonl_entry("user", [make_text_block("Hi bot")])]
        result, pending, _ = TranscriptParser.parse_entries(entries)
        assert len(result) == 1
        assert result[0].role == "user"
        assert result[0].text == "Hi bot"

    def test_tool_use_and_result_pairing(
        self,
        make_jsonl_entry,
        make_text_block,
        make_tool_use_block,
        make_tool_result_block,
    ):
        entries = [
            make_jsonl_entry(
                "assistant",
                [make_tool_use_block("t1", "Read", {"file_path": "app.py"})],
            ),
            make_jsonl_entry(
                "user",
                [make_tool_result_block("t1", "file contents line1\nline2\nline3")],
            ),
        ]
        result, pending, _ = TranscriptParser.parse_entries(entries)
        tool_use_entries = [e for e in result if e.content_type == "tool_use"]
        tool_result_entries = [e for e in result if e.content_type == "tool_result"]
        assert len(tool_use_entries) == 1
        assert tool_use_entries[0].tool_use_id == "t1"
        assert "**Read**" in tool_use_entries[0].text
        assert len(tool_result_entries) == 1
        assert tool_result_entries[0].tool_use_id == "t1"
        assert not pending

    def test_thinking_block(self, make_jsonl_entry, make_thinking_block):
        entries = [
            make_jsonl_entry("assistant", [make_thinking_block("reasoning here")])
        ]
        result, pending, _ = TranscriptParser.parse_entries(entries)
        assert len(result) == 1
        assert result[0].content_type == "thinking"
        assert EXPQUOTE_START in result[0].text
        assert EXPQUOTE_END in result[0].text
        assert "reasoning here" in result[0].text

    def test_local_command_with_stdout(self, make_jsonl_entry, make_text_block):
        xml = (
            "<command-name>/status</command-name>"
            "<local-command-stdout>all good</local-command-stdout>"
        )
        entries = [make_jsonl_entry("user", [make_text_block(xml)])]
        result, pending, _ = TranscriptParser.parse_entries(entries)
        assert len(result) == 1
        assert result[0].content_type == "local_command"
        assert "/status" in result[0].text
        assert "all good" in result[0].text

    def test_exit_plan_mode_emits_plan(self, make_jsonl_entry, make_tool_use_block):
        block = make_tool_use_block(
            "t1", "ExitPlanMode", {"plan": "Step 1: do X\nStep 2: do Y"}
        )
        entries = [make_jsonl_entry("assistant", [block])]
        result, pending, _ = TranscriptParser.parse_entries(entries)
        texts = [e for e in result if e.content_type == "text"]
        tool_uses = [e for e in result if e.content_type == "tool_use"]
        assert len(texts) == 1
        assert "Step 1: do X" in texts[0].text
        assert len(tool_uses) >= 1

    def test_edit_tool_diff_stats(
        self,
        make_jsonl_entry,
        make_tool_use_block,
        make_tool_result_block,
    ):
        edit_input = {
            "file_path": "main.py",
            "old_string": "old line",
            "new_string": "new line",
        }
        entries = [
            make_jsonl_entry(
                "assistant",
                [make_tool_use_block("t1", "Edit", edit_input)],
            ),
            make_jsonl_entry(
                "user",
                [make_tool_result_block("t1", "OK")],
            ),
        ]
        result, pending, _ = TranscriptParser.parse_entries(entries)
        tool_result_entries = [e for e in result if e.content_type == "tool_result"]
        assert len(tool_result_entries) == 1
        tr = tool_result_entries[0]
        assert "Added" in tr.text
        assert "removed" in tr.text
        assert EXPQUOTE_START in tr.text

    def test_error_tool_result(
        self,
        make_jsonl_entry,
        make_tool_use_block,
        make_tool_result_block,
    ):
        entries = [
            make_jsonl_entry(
                "assistant",
                [make_tool_use_block("t1", "Bash", {"command": "rm -rf /"})],
            ),
            make_jsonl_entry(
                "user",
                [make_tool_result_block("t1", "Permission denied", is_error=True)],
            ),
        ]
        result, pending, _ = TranscriptParser.parse_entries(entries)
        tool_result_entries = [e for e in result if e.content_type == "tool_result"]
        assert len(tool_result_entries) == 1
        assert "Error: Permission denied" in tool_result_entries[0].text

    def test_interrupted_tool_result(
        self,
        make_jsonl_entry,
        make_tool_use_block,
        make_tool_result_block,
    ):
        entries = [
            make_jsonl_entry(
                "assistant",
                [make_tool_use_block("t1", "Read", {"file_path": "x.py"})],
            ),
            make_jsonl_entry(
                "user",
                [make_tool_result_block("t1", TranscriptParser._INTERRUPTED_TEXT)],
            ),
        ]
        result, pending, _ = TranscriptParser.parse_entries(entries)
        tool_result_entries = [e for e in result if e.content_type == "tool_result"]
        assert len(tool_result_entries) == 1
        assert "Interrupted" in tool_result_entries[0].text

    def test_pending_tools_carry_over(self, make_jsonl_entry, make_tool_use_block):
        entries = [
            make_jsonl_entry(
                "assistant",
                [make_tool_use_block("t1", "Read", {"file_path": "a.py"})],
            ),
        ]
        result, pending, _ = TranscriptParser.parse_entries(entries, pending_tools={})
        assert "t1" in pending
        flushed = [
            e for e in result if e.content_type == "tool_use" and e.tool_use_id == "t1"
        ]
        assert len(flushed) == 1

    def test_pending_tools_flushed_without_carry_over(
        self, make_jsonl_entry, make_tool_use_block
    ):
        entries = [
            make_jsonl_entry(
                "assistant",
                [make_tool_use_block("t1", "Read", {"file_path": "a.py"})],
            ),
        ]
        result, pending, _ = TranscriptParser.parse_entries(entries, pending_tools=None)
        tool_entries = [e for e in result if e.tool_use_id == "t1"]
        assert len(tool_entries) == 2
        assert tool_entries[0].content_type == "tool_use"
        assert tool_entries[1].content_type == "tool_use"

    def test_system_tag_filtered(self, make_jsonl_entry, make_text_block):
        entries = [
            make_jsonl_entry(
                "user",
                [
                    make_text_block(
                        "<system-reminder>secret instructions</system-reminder>"
                    )
                ],
            ),
        ]
        result, pending, _ = TranscriptParser.parse_entries(entries)
        user_entries = [e for e in result if e.role == "user"]
        assert len(user_entries) == 0


# ── [NO_NOTIFY] tag handling ────────────────────────────────────────────


class TestNoNotifyTag:
    """Tests for [NO_NOTIFY] prefix tag detection, stripping, and flagging."""

    def test_user_message_with_no_notify(self, make_jsonl_entry, make_text_block):
        """User message with [NO_NOTIFY] → no_notify=True, tag stripped."""
        entries = [
            make_jsonl_entry(
                "user",
                [make_text_block("[NO_NOTIFY] [System] Auto-summary check")],
            ),
        ]
        result, _, _ = TranscriptParser.parse_entries(entries)
        assert len(result) == 1
        assert result[0].no_notify is True
        assert result[0].text == "[System] Auto-summary check"
        assert _NO_NOTIFY_TAG not in result[0].text

    def test_user_message_without_no_notify(self, make_jsonl_entry, make_text_block):
        """User message without tag → no_notify=False, text unchanged."""
        entries = [
            make_jsonl_entry("user", [make_text_block("Hello bot")]),
        ]
        result, _, _ = TranscriptParser.parse_entries(entries)
        assert len(result) == 1
        assert result[0].no_notify is False
        assert result[0].text == "Hello bot"

    def test_assistant_text_with_no_notify(self, make_jsonl_entry, make_text_block):
        """Assistant text with [NO_NOTIFY] → no_notify=True, tag stripped."""
        entries = [
            make_jsonl_entry(
                "assistant",
                [make_text_block("[NO_NOTIFY] No summary needed.")],
            ),
        ]
        result, _, _ = TranscriptParser.parse_entries(entries)
        assert len(result) == 1
        assert result[0].no_notify is True
        assert result[0].text == "No summary needed."
        assert _NO_NOTIFY_TAG not in result[0].text

    def test_assistant_text_without_no_notify(self, make_jsonl_entry, make_text_block):
        """Assistant text without tag → no_notify=False."""
        entries = [
            make_jsonl_entry(
                "assistant",
                [make_text_block("Here is your summary: ...")],
            ),
        ]
        result, _, _ = TranscriptParser.parse_entries(entries)
        assert len(result) == 1
        assert result[0].no_notify is False

    def test_assistant_thinking_plus_no_notify_text(
        self, make_jsonl_entry, make_text_block, make_thinking_block
    ):
        """Assistant with thinking + [NO_NOTIFY] text → both entries no_notify=True."""
        entries = [
            make_jsonl_entry(
                "assistant",
                [
                    make_thinking_block("Let me check..."),
                    make_text_block("[NO_NOTIFY] No summary needed."),
                ],
            ),
        ]
        result, _, _ = TranscriptParser.parse_entries(entries)
        assert len(result) == 2
        thinking_entry = [e for e in result if e.content_type == "thinking"][0]
        text_entry = [e for e in result if e.content_type == "text"][0]
        assert thinking_entry.no_notify is True
        assert text_entry.no_notify is True
        assert text_entry.text == "No summary needed."

    def test_assistant_thinking_without_no_notify_text(
        self, make_jsonl_entry, make_text_block, make_thinking_block
    ):
        """Assistant with thinking + normal text → both entries no_notify=False."""
        entries = [
            make_jsonl_entry(
                "assistant",
                [
                    make_thinking_block("Let me write the summary..."),
                    make_text_block(
                        "Summary written to memory/summaries/2026-02-20.md"
                    ),
                ],
            ),
        ]
        result, _, _ = TranscriptParser.parse_entries(entries)
        assert len(result) == 2
        for entry in result:
            assert entry.no_notify is False

    def test_no_notify_only_tag_no_content(self, make_jsonl_entry, make_text_block):
        """[NO_NOTIFY] with no other content → stripped to empty, no entry produced."""
        entries = [
            make_jsonl_entry(
                "assistant",
                [make_text_block("[NO_NOTIFY]")],
            ),
        ]
        result, _, _ = TranscriptParser.parse_entries(entries)
        text_entries = [e for e in result if e.content_type == "text"]
        assert len(text_entries) == 0

    def test_no_notify_user_only_tag(self, make_jsonl_entry, make_text_block):
        """User message with only [NO_NOTIFY] → stripped to empty, no entry."""
        entries = [
            make_jsonl_entry("user", [make_text_block("[NO_NOTIFY]")]),
        ]
        result, _, _ = TranscriptParser.parse_entries(entries)
        assert len(result) == 0

    def test_no_notify_mid_text_not_detected(self, make_jsonl_entry, make_text_block):
        """[NO_NOTIFY] not at start → no_notify=False, text unchanged."""
        entries = [
            make_jsonl_entry(
                "assistant",
                [make_text_block("Some text [NO_NOTIFY] here")],
            ),
        ]
        result, _, _ = TranscriptParser.parse_entries(entries)
        assert len(result) == 1
        assert result[0].no_notify is False
        assert result[0].text == "Some text [NO_NOTIFY] here"

    def test_no_notify_cron_summary_prompt(self, make_jsonl_entry, make_text_block):
        """Simulates actual cron summary prompt with [NO_NOTIFY] prefix."""
        prompt = (
            "[NO_NOTIFY] [System] Auto-summary check: "
            "Review recent conversation and classify..."
        )
        entries = [make_jsonl_entry("user", [make_text_block(prompt)])]
        result, _, _ = TranscriptParser.parse_entries(entries)
        assert len(result) == 1
        assert result[0].no_notify is True
        assert result[0].text.startswith("[System]")

    def test_system_prefix_implicit_no_notify(self, make_jsonl_entry, make_text_block):
        """[System] prefix in user message → implicit no_notify (Claude Code strips [NO_NOTIFY])."""
        prompt = (
            "[System] Auto-summary check: Review recent conversation and classify..."
        )
        entries = [make_jsonl_entry("user", [make_text_block(prompt)])]
        result, _, nn = TranscriptParser.parse_entries(entries)
        assert len(result) == 1
        assert result[0].no_notify is True
        assert result[0].text.startswith("[System]")
        # no_notify_active should be True for carry-over
        assert nn is True

    def test_system_prefix_propagates_to_assistant(
        self, make_jsonl_entry, make_text_block
    ):
        """[System] user message sets no_notify_active, suppressing subsequent assistant."""
        entries = [
            make_jsonl_entry(
                "user",
                [make_text_block("[System] Auto-summary check: ...")],
            ),
            make_jsonl_entry(
                "assistant",
                [make_text_block("[NO_NOTIFY] No summary needed.")],
            ),
        ]
        result, _, _ = TranscriptParser.parse_entries(entries)
        for entry in result:
            assert entry.no_notify is True

    def test_normal_user_after_system_resets_no_notify(
        self, make_jsonl_entry, make_text_block
    ):
        """Normal user message after [System] message resets no_notify_active."""
        entries = [
            make_jsonl_entry(
                "user",
                [make_text_block("[System] Auto-summary check: ...")],
            ),
            make_jsonl_entry(
                "assistant",
                [make_text_block("[NO_NOTIFY] No summary needed.")],
            ),
            make_jsonl_entry("user", [make_text_block("Hello bot")]),
        ]
        result, _, nn = TranscriptParser.parse_entries(entries)
        user_entries = [e for e in result if e.role == "user"]
        assert user_entries[0].no_notify is True  # [System] message
        assert user_entries[1].no_notify is False  # Normal message
        assert nn is False


# ── Stateful [NO_NOTIFY] propagation ──────────────────────────────────


class TestNoNotifyStateful:
    """Tests for stateful [NO_NOTIFY] propagation across entries.

    After a [NO_NOTIFY] user message, all subsequent assistant entries
    (text, thinking, tool_use, tool_result) should be no_notify=True
    until the next non-[NO_NOTIFY] user message.
    """

    def test_tool_use_after_no_notify_user(
        self, make_jsonl_entry, make_text_block, make_tool_use_block
    ):
        """tool_use after [NO_NOTIFY] user message → no_notify=True."""
        entries = [
            make_jsonl_entry(
                "user", [make_text_block("[NO_NOTIFY] Auto-summary check")]
            ),
            make_jsonl_entry(
                "assistant",
                [make_tool_use_block("t1", "Bash", {"command": "curl http://wttr.in"})],
            ),
        ]
        # Use carry-over mode (pending_tools={}) like session_monitor
        result, pending, nn = TranscriptParser.parse_entries(entries, pending_tools={})
        # User entry should be no_notify
        user_entries = [e for e in result if e.role == "user"]
        assert len(user_entries) == 1
        assert user_entries[0].no_notify is True

        # tool_use should inherit no_notify
        tool_entries = [e for e in result if e.content_type == "tool_use"]
        assert len(tool_entries) == 1
        assert tool_entries[0].no_notify is True
        assert nn is True

    def test_tool_result_after_no_notify_user(
        self,
        make_jsonl_entry,
        make_text_block,
        make_tool_use_block,
        make_tool_result_block,
    ):
        """tool_result after [NO_NOTIFY] user message → no_notify=True."""
        entries = [
            make_jsonl_entry(
                "user", [make_text_block("[NO_NOTIFY] Auto-summary check")]
            ),
            make_jsonl_entry(
                "assistant",
                [make_tool_use_block("t1", "Bash", {"command": "curl http://wttr.in"})],
            ),
            make_jsonl_entry(
                "user",
                [make_tool_result_block("t1", "HTTP 200 OK\nweather data...")],
            ),
        ]
        result, _, nn = TranscriptParser.parse_entries(entries)
        tool_result_entries = [e for e in result if e.content_type == "tool_result"]
        assert len(tool_result_entries) == 1
        assert tool_result_entries[0].no_notify is True
        assert nn is True

    def test_assistant_text_after_no_notify_user(
        self, make_jsonl_entry, make_text_block
    ):
        """Assistant text after [NO_NOTIFY] user → no_notify=True (inherited)."""
        entries = [
            make_jsonl_entry(
                "user", [make_text_block("[NO_NOTIFY] Auto-summary check")]
            ),
            make_jsonl_entry(
                "assistant",
                [make_text_block("[NO_NOTIFY] No summary needed.")],
            ),
        ]
        result, _, nn = TranscriptParser.parse_entries(entries)
        assistant_entries = [e for e in result if e.role == "assistant"]
        assert len(assistant_entries) == 1
        assert assistant_entries[0].no_notify is True
        assert assistant_entries[0].text == "No summary needed."

    def test_normal_user_resets_no_notify(self, make_jsonl_entry, make_text_block):
        """Normal user message after [NO_NOTIFY] → resets no_notify_active."""
        entries = [
            make_jsonl_entry("user", [make_text_block("[NO_NOTIFY] Silent prompt")]),
            make_jsonl_entry(
                "assistant", [make_text_block("[NO_NOTIFY] Silent reply")]
            ),
            make_jsonl_entry("user", [make_text_block("Hello, this is normal")]),
            make_jsonl_entry("assistant", [make_text_block("Normal reply")]),
        ]
        result, _, nn = TranscriptParser.parse_entries(entries)
        # Last assistant should NOT be no_notify
        normal_reply = [
            e for e in result if e.role == "assistant" and e.text == "Normal reply"
        ]
        assert len(normal_reply) == 1
        assert normal_reply[0].no_notify is False
        assert nn is False

    def test_no_notify_carry_over_state(self, make_jsonl_entry, make_text_block):
        """parse_entries returns no_notify_active for carry-over across calls."""
        # First batch: [NO_NOTIFY] user, no response yet
        entries1 = [
            make_jsonl_entry("user", [make_text_block("[NO_NOTIFY] Auto-summary")]),
        ]
        result1, pending1, nn1 = TranscriptParser.parse_entries(
            entries1, pending_tools={}, no_notify_active=False
        )
        assert nn1 is True

        # Second batch: assistant responds (should inherit state)
        entries2 = [
            make_jsonl_entry("assistant", [make_text_block("No summary needed.")]),
        ]
        result2, _, nn2 = TranscriptParser.parse_entries(
            entries2, pending_tools=pending1, no_notify_active=nn1
        )
        assert len(result2) == 1
        assert result2[0].no_notify is True
        assert nn2 is True

    def test_full_cron_scenario(
        self,
        make_jsonl_entry,
        make_text_block,
        make_thinking_block,
        make_tool_use_block,
        make_tool_result_block,
    ):
        """Full cron summary scenario: prompt → thinking → tool → reply."""
        entries = [
            # Cron sends [NO_NOTIFY] prompt
            make_jsonl_entry(
                "user",
                [make_text_block("[NO_NOTIFY] [System] Auto-summary check")],
            ),
            # Claude thinks, uses a tool, then replies
            make_jsonl_entry(
                "assistant",
                [
                    make_thinking_block("Let me check for activity..."),
                    make_tool_use_block("t1", "Bash", {"command": "cat recent.log"}),
                ],
            ),
            # Tool result
            make_jsonl_entry(
                "user",
                [make_tool_result_block("t1", "no recent activity")],
            ),
            # Final reply
            make_jsonl_entry(
                "assistant",
                [make_text_block("[NO_NOTIFY] No summary needed.")],
            ),
        ]
        result, _, nn = TranscriptParser.parse_entries(entries)
        # ALL entries should be no_notify
        for entry in result:
            assert entry.no_notify is True, (
                f"Entry {entry.content_type} '{entry.text[:30]}' should be no_notify"
            )

    def test_no_notify_initial_state_false(self, make_jsonl_entry, make_text_block):
        """Default no_notify_active=False → normal messages are not suppressed."""
        entries = [
            make_jsonl_entry("user", [make_text_block("Hello")]),
            make_jsonl_entry("assistant", [make_text_block("Hi there!")]),
        ]
        result, _, nn = TranscriptParser.parse_entries(entries)
        for entry in result:
            assert entry.no_notify is False
        assert nn is False
