import os
import shutil
import subprocess
from config import Config


def _resolve_claude():
    npm_bin = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "npm")
    augmented = npm_bin + os.pathsep + os.environ.get("PATH", "")
    found = shutil.which("claude", path=augmented)
    if found:
        return found
    cmd_path = os.path.join(npm_bin, "claude.cmd")
    if os.path.exists(cmd_path):
        return cmd_path
    return "claude"


CLAUDE_CMD = _resolve_claude()


def _run_claude(prompt, timeout):
    result = subprocess.run(
        [CLAUDE_CMD, "-p"],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI error: {result.stderr[:300]}")
    output = result.stdout.strip()
    try:
        output = output.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return output


def call_claude(prompt):
    """Full timeout (120s) — for research summarisation."""
    return _run_claude(prompt, Config.CLAUDE_TIMEOUT)


def call_claude_fast(prompt):
    """Fast timeout (60s) — for planning calls, prompt/skill gen."""
    return _run_claude(prompt, Config.CLAUDE_FAST_TIMEOUT)
