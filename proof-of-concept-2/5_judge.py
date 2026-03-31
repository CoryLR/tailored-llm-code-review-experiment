"""Run the judge agent to evaluate reviewer output against human comments.

For each test PR and condition (generic/tuned), compares the reviewer's
comments against the human review comments and produces match verdicts
and novel comment assessments.

Usage:
    python proof-of-concept-2/5_judge.py
    python proof-of-concept-2/5_judge.py --condition generic
    python proof-of-concept-2/5_judge.py --data-dir proof-of-concept-2/data/storybook-backup-append-system-prompt
"""

import json
import os
import re
import subprocess
import sys
import time

sys.stdout.reconfigure(line_buffering=True)
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
POC2_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DATA_DIR = POC2_DIR / "data" / "storybook"

JUDGE_PROMPT = POC2_DIR / "prompts" / "judge_system.md"

MODEL = "claude-opus-4-6"
MAX_BUDGET = "1.00"
SUBPROCESS_TIMEOUT = 300  # 5 minutes

RESULT_MARKER = "===FINAL_JUDGE_OUTPUT_BEGIN==="

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def save_json(data: object, filepath: Path) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(data, indent=2, default=str))
    print(f"    Saved {filepath.relative_to(REPO_ROOT)}")


def extract_json_from_text(text: str) -> dict | None:
    """Extract the judge JSON from agent output.

    Looks for the marker first, then falls back to last fenced block.
    """
    # Strategy 1: find marker, parse fenced block after it
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

    # Strategy 2: last fenced json block
    pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
    matches = list(re.finditer(pattern, text, re.DOTALL))
    for match in reversed(matches):
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            continue

    # Strategy 3: raw JSON at end
    text = text.strip()
    if text.endswith("}"):
        brace_idx = text.rfind("{")
        if brace_idx != -1:
            try:
                return json.loads(text[brace_idx:])
            except json.JSONDecodeError:
                pass

    return None


def format_comments_for_judge(
    human_comments: list[dict],
    reviewer_comments: dict,
) -> str:
    """Format both comment sets into the judge's user prompt."""
    lines = []

    lines.append("## Human Reviewer Comments\n")
    for i, c in enumerate(human_comments):
        loc = ""
        if c.get("file_path") and c.get("line_number"):
            loc = f" [{c['file_path']}:{c['line_number']}]"
        resolved = " (resolved)" if c.get("is_resolved") else ""
        lines.append(f"### Human Comment {i}{loc}{resolved}")
        lines.append(f"Category: {c.get('category', 'unknown')}")
        lines.append(f"Author: {c.get('author', 'unknown')}")
        lines.append(f"Body: {c['body']}")
        lines.append("")

    lines.append("---\n")
    lines.append("## AI Reviewer Comments\n")

    idx = 0
    for c in reviewer_comments.get("inline_comments", []):
        loc = ""
        if c.get("file_path") and c.get("line_number"):
            loc = f" [{c['file_path']}:{c['line_number']}]"
        category = c.get("category", "")
        importance = c.get("importance", "")
        lines.append(f"### Agent Comment {idx} (inline){loc}")
        if category:
            lines.append(f"Category: {category}")
        if importance:
            lines.append(f"Importance: {importance}")
        lines.append(f"Body: {c['comment']}")
        lines.append("")
        idx += 1

    for c in reviewer_comments.get("general_comments", []):
        category = c.get("category", "")
        importance = c.get("importance", "")
        lines.append(f"### Agent Comment {idx} (general)")
        if category:
            lines.append(f"Category: {category}")
        if importance:
            lines.append(f"Importance: {importance}")
        lines.append(f"Body: {c['comment']}")
        lines.append("")
        idx += 1

    return "\n".join(lines)


def run_judge(user_prompt: str) -> dict:
    """Invoke the judge agent via claude -p."""
    system_prompt = JUDGE_PROMPT.read_text()

    cmd = [
        "claude",
        "-p",
        "--output-format", "json",
        "--model", MODEL,
        "--system-prompt", system_prompt,
        "--tools", "",
        "--max-budget-usd", MAX_BUDGET,
    ]

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
        print(f"    ERROR: claude -p exited with code {result.returncode}")
        if result.stderr:
            print(f"    stderr: {result.stderr[:500]}")
        if result.stdout:
            print(f"    stdout: {result.stdout[:500]}")
        return {"error": True, "returncode": result.returncode}

    print(f"    Completed in {elapsed:.1f}s")

    raw_output = json.loads(result.stdout)
    cost = raw_output.get("total_cost_usd", 0)
    print(f"    Cost: ${cost:.2f}")
    return raw_output


def parse_judge_output(raw_output: dict) -> dict | None:
    """Extract judge evaluation from raw output.

    Returns parsed dict on success, None on parse failure (caller should retry).
    """
    if raw_output.get("error"):
        return None

    result_text = raw_output.get("result", "")
    parsed = extract_json_from_text(result_text)

    if parsed is None:
        print("    WARNING: Could not parse judge JSON output")
        print(f"    Raw (first 300 chars): {result_text[:300]}")
        return None

    summary = parsed.get("summary", {})
    full = summary.get("full_matches", "?")
    partial = summary.get("partial_matches", "?")
    no = summary.get("no_matches", "?")
    novel = len(parsed.get("novel_agent_comments", []))
    print(f"    Results: {full} full, {partial} partial, {no} no match, {novel} novel")
    return parsed


def load_evaluation_human_comments(pr_number: int, test_dir: Path) -> list[dict] | None:
    """Load human comments for the evaluation revision of a test PR.

    Picks the revision with the most comments, matching review.py's
    select_evaluation_revision logic.
    """
    pr_dir = test_dir / f"pr_{pr_number}"
    if not pr_dir.exists():
        return None

    best_comments = None
    best_count = 0

    for rev_dir in sorted(pr_dir.glob("rev_*")):
        comments_file = rev_dir / "human_comments.json"
        if not comments_file.exists():
            continue
        comments = json.loads(comments_file.read_text())
        if len(comments) > best_count:
            best_count = len(comments)
            best_comments = comments

    return best_comments


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    conditions = ["generic", "tuned"]
    if "--condition" in sys.argv:
        idx = sys.argv.index("--condition")
        if idx + 1 < len(sys.argv):
            cond = sys.argv[idx + 1]
            if cond in conditions:
                conditions = [cond]
            else:
                print(f"ERROR: Unknown condition '{cond}'.")
                sys.exit(1)

    # Allow overriding the data directory (e.g., to judge a backup dataset)
    data_dir = DEFAULT_DATA_DIR
    if "--data-dir" in sys.argv:
        idx = sys.argv.index("--data-dir")
        if idx + 1 < len(sys.argv):
            data_dir = Path(sys.argv[idx + 1])
            if not data_dir.is_absolute():
                data_dir = REPO_ROOT / data_dir

    test_dir = data_dir / "test"
    reviews_dir = data_dir / "reviews"
    judgments_dir = data_dir / "judgments"
    split_file = data_dir / "split.json"

    print("PoC #2: Running judge agent on reviewer outputs")
    print(f"  Data dir: {data_dir.relative_to(REPO_ROOT)}")
    print(f"  Conditions: {conditions}")
    print(f"  Model: {MODEL}")
    print()

    split = json.loads(split_file.read_text())
    test_prs = split["test"]

    total_cost = 0.0

    for pr_number in test_prs:
        human_comments = load_evaluation_human_comments(pr_number, test_dir)
        if not human_comments:
            print(f"  PR #{pr_number}: no human comments found, skipping")
            continue

        print(f"  PR #{pr_number} ({len(human_comments)} human comments)")

        for condition in conditions:
            reviewer_file = reviews_dir / condition / f"pr_{pr_number}.json"
            output_file = judgments_dir / condition / f"pr_{pr_number}.json"

            if not reviewer_file.exists():
                print(f"    [{condition}] No reviewer output, skipping")
                continue

            if output_file.exists():
                print(f"    [{condition}] Already exists, skipping")
                continue

            reviewer_comments = json.loads(reviewer_file.read_text())
            if reviewer_comments.get("parse_error"):
                print(f"    [{condition}] Reviewer had parse error, skipping")
                continue

            # Build judge prompt
            user_prompt = format_comments_for_judge(human_comments, reviewer_comments)

            # Run judge with retry
            MAX_ATTEMPTS = 2
            parsed = None
            raw_output = None

            for attempt in range(1, MAX_ATTEMPTS + 1):
                label = f"[{condition}]"
                if attempt > 1:
                    label += f" (retry {attempt}/{MAX_ATTEMPTS})"
                print(f"    {label} Running judge...")

                raw_output = run_judge(user_prompt)
                parsed = parse_judge_output(raw_output)

                if parsed is not None:
                    break
                if attempt < MAX_ATTEMPTS:
                    print(f"    Parse failed, retrying...")

            if parsed is None:
                print(f"    ERROR: Failed to parse after {MAX_ATTEMPTS} attempts")
                parsed = {"parse_error": True}

            save_json(raw_output, judgments_dir / condition / f"pr_{pr_number}_raw.json")
            save_json(parsed, output_file)

            cost = (raw_output or {}).get("total_cost_usd", 0) or 0
            total_cost += cost
            print()

    print(f"Total judge cost: ${total_cost:.2f}")


if __name__ == "__main__":
    main()
