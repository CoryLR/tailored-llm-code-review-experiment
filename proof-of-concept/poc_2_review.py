"""Run the reviewer agent on a target MR revision via claude -p.

Checks out the subject repo to the evaluation commit, runs the reviewer
agent with read-only tool access, then restores the repo to its
original HEAD.
"""

import json
import os
import re
import subprocess
import sys
import time

sys.stdout.reconfigure(line_buffering=True)
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
POC_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MR_IID = 5074
HEAD_SHA = "7d6d756846c2e8cf3f9fdbfc9b003c8003d5be85"

# Paths
SUBJECT_REPO = REPO_ROOT / "subject-repos" / "gitlab-ui"
DATA_DIR = POC_DIR / "results" / f"mr_{MR_IID}"
REVIEWER_SYSTEM_PROMPT = REPO_ROOT / "prompts" / "reviewer_system.txt"

# Agent config
MODEL = "claude-sonnet-4-6"
MAX_BUDGET = "2.00"
SUBPROCESS_TIMEOUT = 600  # 10 minutes


def save_json(data: object, filename: str) -> Path:
    """Save data as JSON to the data directory."""
    filepath = DATA_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(data, indent=2, default=str))
    print(f"  Saved {filepath.relative_to(REPO_ROOT)}")
    return filepath


@contextmanager
def checkout_subject_repo(target_sha: str):
    """Context manager: checkout target SHA, restore original ref on exit."""
    # Save current ref
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=SUBJECT_REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    original_sha = result.stdout.strip()
    print(f"  Subject repo at: {original_sha[:12]}")

    # Checkout target
    print(f"  Checking out: {target_sha[:12]}")
    subprocess.run(
        ["git", "checkout", target_sha],
        cwd=SUBJECT_REPO,
        capture_output=True,
        text=True,
        check=True,
    )

    try:
        yield
    finally:
        # Restore original HEAD
        print(f"  Restoring subject repo to: {original_sha[:12]}")
        subprocess.run(
            ["git", "checkout", original_sha],
            cwd=SUBJECT_REPO,
            capture_output=True,
            text=True,
            check=True,
        )


def extract_json_from_text(text: str) -> dict | None:
    """Extract a JSON object from text, handling markdown fences."""
    # Try direct parse first
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # Try stripping markdown code fences
    pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    return None


def run_reviewer() -> dict:
    """Invoke the reviewer agent via claude -p.

    Passes the user prompt via stdin to avoid command-line length limits.
    """
    system_prompt = REVIEWER_SYSTEM_PROMPT.read_text()
    user_prompt_path = DATA_DIR / "1_reviewer_prompt.txt"

    if not user_prompt_path.exists():
        print(f"ERROR: Reviewer prompt not found at {user_prompt_path}")
        print("  Run poc_1_prepare.py first.")
        sys.exit(1)

    user_prompt = user_prompt_path.read_text()

    cmd = [
        "claude",
        "-p",
        "--output-format", "json",
        "--model", MODEL,
        "--system-prompt", system_prompt,
        "--add-dir", str(SUBJECT_REPO),
        "--dangerously-skip-permissions",
        "--allowed-tools", "Read", "Glob", "Grep",
        "--max-budget-usd", MAX_BUDGET,
    ]

    print(f"  Model: {MODEL}")
    print(f"  Budget cap: ${MAX_BUDGET}")
    print(f"  Subject repo: {SUBJECT_REPO}")
    print(f"  Timeout: {SUBPROCESS_TIMEOUT}s")
    print("  Running reviewer agent...")

    # Remove CLAUDECODE env var so claude -p doesn't refuse to run
    # when invoked from within a Claude Code session.
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    start = time.time()
    result = subprocess.run(
        cmd,
        input=user_prompt,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT,
        cwd=REPO_ROOT,
        env=env,
    )
    elapsed = time.time() - start

    if result.returncode != 0:
        print(f"  ERROR: claude -p exited with code {result.returncode}")
        if result.stderr:
            print(f"  stderr: {result.stderr[:500]}")
        sys.exit(1)

    print(f"  Completed in {elapsed:.1f}s")

    # Parse the raw JSON output from claude -p
    raw_output = json.loads(result.stdout)
    save_json(raw_output, "2_reviewer_raw_output.json")

    duration = raw_output.get("duration_ms", 0)
    print(f"  Duration: {duration / 1000:.1f}s")

    return raw_output


def parse_reviewer_output(raw_output: dict) -> dict:
    """Extract and validate the reviewer's JSON comments from raw output."""
    result_text = raw_output.get("result", "")

    parsed = extract_json_from_text(result_text)
    if parsed is None:
        print("  WARNING: Could not parse JSON from reviewer output.")
        print(f"  Raw result text (first 500 chars): {result_text[:500]}")
        # Save what we got anyway
        save_json({"parse_error": True, "raw_result": result_text}, "2_reviewer_comments.json")
        return {"inline_comments": [], "general_comments": []}

    # Validate structure
    if "inline_comments" not in parsed:
        parsed["inline_comments"] = []
    if "general_comments" not in parsed:
        parsed["general_comments"] = []

    save_json(parsed, "2_reviewer_comments.json")

    inline_count = len(parsed["inline_comments"])
    general_count = len(parsed["general_comments"])
    print(f"  Parsed {inline_count} inline + {general_count} general comments")

    return parsed


def main() -> None:
    print(f"PoC Step 2: Running reviewer agent on MR !{MR_IID}")
    print()

    with checkout_subject_repo(HEAD_SHA):
        print()
        raw_output = run_reviewer()
        print()
        parse_reviewer_output(raw_output)

    print()
    print("Done. Reviewer output saved to:")
    print(f"  {DATA_DIR.relative_to(REPO_ROOT)}/")


if __name__ == "__main__":
    main()
