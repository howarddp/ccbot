"""Root conftest — sets env vars BEFORE any baobaobot module is imported."""

import os
import tempfile

# Force-set BAOBAOBOT_DIR to a temp directory for test isolation
os.environ["BAOBAOBOT_DIR"] = tempfile.mkdtemp(prefix="baobaobot-test-")
