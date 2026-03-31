"""Run the reviewer agent on test PRs under both conditions.

For each test PR, selects the revision with the most human comments,
checks out the subject repo to that commit, and runs the reviewer
agent twice: once with the generic prompt and once with the tuned prompt.

Usage:
    python proof-of-concept-2/review.py
    python proof-of-concept-2/review.py --condition generic
    python proof-of-concept-2/review.py --condition tuned
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
POC2_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SUBJECT_REPO = REPO_ROOT / "subject-repos" / "storybook"
DATA_DIR = POC2_DIR / "data" / "storybook"
TEST_DIR = DATA_DIR / "test"
REVIEWS_DIR = DATA_DIR / "reviews"
SPLIT_FILE = DATA_DIR / "split.json"
MANIFEST_FILE = DATA_DIR / "manifest.json"

GENERIC_PROMPT = POC2_DIR / "prompts" / "reviewer_system.md"
TUNED_PROMPT = DATA_DIR / "optimization" / "tuned_prompt.md"

MODEL = "claude-sonnet-4-6"
MAX_BUDGET = "10.00"
SUBPROCESS_TIMEOUT = 1800  # 30 minutes (safety net; budget cap is the real limit)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def save_json(data: object, filepath: Path) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(data, indent=2, default=str))
    print(f"    Saved {filepath.relative_to(REPO_ROOT)}")


@contextmanager
def checkout_subject_repo(target_sha: str):
    """Context manager: checkout target SHA, restore original ref on exit."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=SUBJECT_REPO, capture_output=True, text=True, check=True,
    )
    original_sha = result.stdout.strip()
    print(f"    Subject repo at: {original_sha[:12]}")
    print(f"    Checking out: {target_sha[:12]}")

    subprocess.run(
        ["git", "checkout", target_sha],
        cwd=SUBJECT_REPO, capture_output=True, text=True, check=True,
    )
    try:
        yield
    finally:
        print(f"    Restoring to: {original_sha[:12]}")
        subprocess.run(
            ["git", "checkout", original_sha],
            cwd=SUBJECT_REPO, capture_output=True, text=True, check=True,
        )


RESULT_MARKER = "===FINAL_REVIEW_OUTPUT_BEGIN==="


def extract_json_from_text(text: str) -> dict | None:
    """Extract the review JSON from agent output.

    Looks for the REVIEW_RESULT_JSON marker first, then falls back
    to generic extraction for robustness.
    """
    # Strategy 1: find marker, then parse the fenced block after it
    marker_idx = text.find(RESULT_MARKER)
    if marker_idx != -1:
        after_marker = text[marker_idx + len(RESULT_MARKER):]
        pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
        match = re.search(pattern, after_marker, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

    # Strategy 2: find the LAST fenced json block (most likely the final output)
    pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
    matches = list(re.finditer(pattern, text, re.DOTALL))
    for match in reversed(matches):
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            continue

    # Strategy 3: raw JSON at the very end
    text = text.strip()
    if text.endswith("}"):
        # Find the last { that could start the object
        brace_idx = text.rfind("{")
        if brace_idx != -1:
            try:
                return json.loads(text[brace_idx:])
            except json.JSONDecodeError:
                pass

    return None


def run_reviewer(user_prompt: str, system_prompt: str) -> dict:
    """Invoke the reviewer agent via claude -p."""
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

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            input=user_prompt,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
            cwd=REPO_ROOT,
            env=env,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"    TIMEOUT after {elapsed:.0f}s")
        return {"error": True, "timeout": True}
    elapsed = time.time() - start

    if result.returncode != 0:
        print(f"    ERROR: claude -p exited with code {result.returncode}")
        if result.stderr:
            print(f"    stderr: {result.stderr[:500]}")
        return {"error": True, "returncode": result.returncode}

    print(f"    Completed in {elapsed:.1f}s")

    raw_output = json.loads(result.stdout)
    cost = raw_output.get("total_cost_usd", 0)
    print(f"    Cost: ${cost:.2f}")
    return raw_output


def parse_reviewer_output(raw_output: dict) -> dict | None:
    """Extract structured comments from raw reviewer output.

    Returns parsed dict on success, None on parse failure (caller should retry).
    """
    if raw_output.get("error"):
        return None

    result_text = raw_output.get("result", "")
    parsed = extract_json_from_text(result_text)

    if parsed is None:
        print("    WARNING: Could not parse reviewer JSON output")
        print(f"    Raw (first 300 chars): {result_text[:300]}")
        return None

    if "inline_comments" not in parsed:
        parsed["inline_comments"] = []
    if "general_comments" not in parsed:
        parsed["general_comments"] = []

    inline = len(parsed["inline_comments"])
    general = len(parsed["general_comments"])
    print(f"    Output: {inline} inline + {general} general comments")
    return parsed


def select_evaluation_revision(pr_number: int) -> tuple[str, Path] | None:
    """Pick the revision with the most human comments for a test PR.

    Each PR is evaluated once. When a PR has multiple revisions with
    comments, we pick the one with the most to maximize ground truth.
    """
    pr_dir = TEST_DIR / f"pr_{pr_number}"
    if not pr_dir.exists():
        return None

    best_rev = None
    best_count = 0
    best_dir = None

    for rev_dir in sorted(pr_dir.glob("rev_*")):
        comments_file = rev_dir / "human_comments.json"
        if not comments_file.exists():
            continue
        comments = json.loads(comments_file.read_text())
        if len(comments) > best_count:
            best_count = len(comments)
            best_rev = rev_dir.name.replace("rev_", "")
            best_dir = rev_dir

    if best_rev:
        return best_rev, best_dir
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # Parse optional --condition flag
    conditions = ["generic", "tuned"]
    if "--condition" in sys.argv:
        idx = sys.argv.index("--condition")
        if idx + 1 < len(sys.argv):
            cond = sys.argv[idx + 1]
            if cond in conditions:
                conditions = [cond]
            else:
                print(f"ERROR: Unknown condition '{cond}'. Use 'generic' or 'tuned'.")
                sys.exit(1)

    print("PoC #2: Running reviewer agent on test PRs")
    print(f"  Conditions: {conditions}")
    print(f"  Model: {MODEL}")
    print(f"  Budget per run: ${MAX_BUDGET}")
    print()

    # Load test PR numbers
    split = json.loads(SPLIT_FILE.read_text())
    test_prs = split["test"]

    # Load prompts
    generic_system = GENERIC_PROMPT.read_text()
    if not TUNED_PROMPT.exists():
        print(f"ERROR: Tuned prompt not found at {TUNED_PROMPT}")
        print("  Run optimize.py first.")
        sys.exit(1)
    tuned_system = TUNED_PROMPT.read_text()

    prompts = {
        "generic": generic_system,
        "tuned": tuned_system,
    }

    total_cost = 0.0

    for pr_number in test_prs:
        result = select_evaluation_revision(pr_number)
        if not result:
            print(f"  PR #{pr_number}: no usable revision, skipping")
            continue

        rev_sha, rev_dir = result
        user_prompt = (rev_dir / "reviewer_prompt.txt").read_text()
        comments = json.loads((rev_dir / "human_comments.json").read_text())

        print(f"  PR #{pr_number}, revision {rev_sha[:12]} ({len(comments)} human comments)")

        with checkout_subject_repo(rev_sha):
            for condition in conditions:
                output_file = REVIEWS_DIR / condition / f"pr_{pr_number}.json"

                # Skip if already done
                if output_file.exists():
                    print(f"    [{condition}] Already exists, skipping")
                    continue

                MAX_ATTEMPTS = 2
                for attempt in range(1, MAX_ATTEMPTS + 1):
                    label = f"[{condition}]"
                    if attempt > 1:
                        label += f" (retry {attempt}/{MAX_ATTEMPTS})"
                    print(f"    {label} Running reviewer...")

                    raw_output = run_reviewer(user_prompt, prompts[condition])
                    parsed = parse_reviewer_output(raw_output)

                    if parsed is not None:
                        break
                    if attempt < MAX_ATTEMPTS:
                        print(f"    Parse failed, retrying...")

                if parsed is None:
                    print(f"    ERROR: Failed to parse after {MAX_ATTEMPTS} attempts")
                    parsed = {"inline_comments": [], "general_comments": [],
                              "parse_error": True}

                # Save both raw and parsed
                save_json(raw_output, REVIEWS_DIR / condition / f"pr_{pr_number}_raw.json")
                save_json(parsed, output_file)

                cost = raw_output.get("total_cost_usd", 0) or 0
                total_cost += cost
                print()

        print()

    print(f"Total reviewer cost: ${total_cost:.2f}")


if __name__ == "__main__":
    main()
