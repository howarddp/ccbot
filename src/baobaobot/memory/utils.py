"""Shared memory utilities — frontmatter parsing, tag extraction.

Single source of truth for frontmatter/tag regex patterns and parsing
functions used by both the in-process MemoryDB and standalone bin scripts.
"""

import re

# Regex to strip YAML frontmatter (--- ... ---) from the beginning of a file
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?", re.DOTALL)

# Extract tags: [tag1, tag2] from frontmatter
_TAGS_BRACKET_RE = re.compile(r"^tags:\s*\[([^\]]*)\]", re.MULTILINE)

# Inline tags: #word (not preceded by word char — avoids headings and anchors).
# Matches mixed-case; callers normalize to lowercase.
_INLINE_TAG_RE = re.compile(r"(?:^|(?<=\s))#([a-zA-Z][a-zA-Z0-9/-]*)")

# Attachment references: ![desc](path) for images, [desc](path) for files
# Captures (description, path) — prefix '!' indicates image
ATTACHMENT_RE = re.compile(r"!?\[([^\]]+)\]\(([^)]+)\)")


def strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter block from the beginning of text."""
    return FRONTMATTER_RE.sub("", text)


def parse_tags(text: str) -> list[str]:
    """Extract tags from YAML frontmatter and inline #tags.

    Parses:
      - Frontmatter ``tags: [tag1, tag2]`` (with or without # prefix)
      - Inline ``#tag`` occurrences in the body (case-insensitive)

    Tags are normalized to lowercase.

    Returns:
        Sorted list of unique lowercase tag names (without # prefix).
    """
    tags: set[str] = set()

    # Parse frontmatter tags
    fm_match = FRONTMATTER_RE.match(text)
    if fm_match:
        bracket_match = _TAGS_BRACKET_RE.search(fm_match.group(0))
        if bracket_match:
            for t in bracket_match.group(1).split(","):
                t = t.strip().strip('"').strip("'").lstrip("#").lower()
                if t:
                    tags.add(t)

    # Parse inline #tags from body (after frontmatter)
    body = strip_frontmatter(text)
    for m in _INLINE_TAG_RE.finditer(body):
        tags.add(m.group(1).lower())

    return sorted(tags)
