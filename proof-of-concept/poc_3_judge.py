"""Run the judge agent to evaluate reviewer output against human comments.

Compares the AI reviewer's comments against the actual human review
comments for the target MR, producing match verdicts and recall metrics.
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
POC_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MR_IID = 5074

# Paths
DATA_DIR = POC_DIR / "results" / f"mr_{MR_IID}"
JUDGE_SYSTEM_PROMPT = REPO_ROOT / "prompts" / "judge_system.txt"

# Agent config
MODEL = "claude-sonnet-4-6"
MAX_BUDGET = "1.00"
SUBPROCESS_TIMEOUT = 180  # 3 minutes


def save_json(data: object, filename: str) -> Path:
    """Save data as JSON to the data directory."""
    filepath = DATA_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(data, indent=2, default=str))
    print(f"  Saved {filepath.relative_to(REPO_ROOT)}")
    return filepath


def save_text(content: str, filename: str) -> Path:
    """Save text content to the data directory."""
    filepath = DATA_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content)
    print(f"  Saved {filepath.relative_to(REPO_ROOT)}")
    return filepath


def extract_json_from_text(text: str) -> dict | None:
    """Extract a JSON object from text, handling markdown fences."""
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
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
        resolved = " (resolved)" if c.get("resolved") else ""
        lines.append(f"### Human Comment {i}{loc}{resolved}")
        lines.append(f"Category: {c['category']}")
        lines.append(f"Author: {c['author']}")
        lines.append(f"Body: {c['body']}")
        lines.append("")

    lines.append("---\n")
    lines.append("## AI Reviewer Comments\n")

    idx = 0
    for c in reviewer_comments.get("inline_comments", []):
        loc = f" [{c['file_path']}:{c['line_number']}]"
        lines.append(f"### Agent Comment {idx} (inline){loc}")
        lines.append(f"Body: {c['comment']}")
        lines.append("")
        idx += 1

    for c in reviewer_comments.get("general_comments", []):
        lines.append(f"### Agent Comment {idx} (general)")
        lines.append(f"Body: {c['comment']}")
        lines.append("")
        idx += 1

    return "\n".join(lines)


def run_judge(user_prompt: str) -> dict:
    """Invoke the judge agent via claude -p."""
    system_prompt = JUDGE_SYSTEM_PROMPT.read_text()

    cmd = [
        "claude",
        "-p",
        "--output-format", "json",
        "--model", MODEL,
        "--system-prompt", system_prompt,
        "--tools", "",
        "--max-budget-usd", MAX_BUDGET,
    ]

    print(f"  Model: {MODEL}")
    print(f"  Budget cap: ${MAX_BUDGET}")
    print(f"  Timeout: {SUBPROCESS_TIMEOUT}s")
    print("  Running judge agent...")

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

    raw_output = json.loads(result.stdout)
    save_json(raw_output, "3_judge_raw_output.json")

    duration = raw_output.get("duration_ms", 0)
    print(f"  Duration: {duration / 1000:.1f}s")

    return raw_output


def parse_judge_output(raw_output: dict) -> dict:
    """Extract and validate the judge's evaluation from raw output."""
    result_text = raw_output.get("result", "")

    parsed = extract_json_from_text(result_text)
    if parsed is None:
        print("  WARNING: Could not parse JSON from judge output.")
        print(f"  Raw result text (first 500 chars): {result_text[:500]}")
        save_json({"parse_error": True, "raw_result": result_text}, "3_judge_results.json")
        return {}

    save_json(parsed, "3_judge_results.json")
    return parsed


def generate_summary(judge_results: dict, reviewer_raw: dict, judge_raw: dict) -> str:
    """Generate a human-readable summary of the evaluation."""
    lines = []
    lines.append(f"Proof of Concept: Reviewer + Judge Evaluation for MR !{MR_IID}")
    lines.append("=" * 60)
    lines.append("")

    summary = judge_results.get("summary", {})
    if summary:
        lines.append("Match Results:")
        lines.append(f"  Total human comments: {summary.get('total_human_comments', '?')}")
        lines.append(f"  Full matches:         {summary.get('full_matches', '?')}")
        lines.append(f"  Partial matches:      {summary.get('partial_matches', '?')}")
        lines.append(f"  No matches:           {summary.get('no_matches', '?')}")
        lines.append(f"  Novel agent comments: {summary.get('novel_agent_comments', '?')}")
        lines.append("")
        lines.append(f"  Recall (full only):       {summary.get('recall_at_full', 0):.2%}")
        lines.append(f"  Recall (full + partial):  {summary.get('recall_at_partial', 0):.2%}")
    else:
        lines.append("(No summary data available)")

    lines.append("")
    lines.append("Novel Agent Comments:")
    for nc in judge_results.get("novel_agent_comments", []):
        assessment = nc.get("assessment", "?")
        explanation = nc.get("explanation", "")
        lines.append(f"  [{assessment}] Agent #{nc.get('agent_comment_index', '?')}: {explanation}")

    lines.append("")
    lines.append("Duration:")
    reviewer_dur = reviewer_raw.get("duration_ms", 0) / 1000
    judge_dur = judge_raw.get("duration_ms", 0) / 1000
    lines.append(f"  Reviewer: {reviewer_dur:.1f}s")
    lines.append(f"  Judge:    {judge_dur:.1f}s")
    lines.append(f"  Total:    {reviewer_dur + judge_dur:.1f}s")

    return "\n".join(lines)


def main() -> None:
    print(f"PoC Step 3: Running judge agent for MR !{MR_IID}")
    print()

    # Load inputs
    human_path = DATA_DIR / "1_human_comments.json"
    reviewer_path = DATA_DIR / "2_reviewer_comments.json"

    if not human_path.exists():
        print(f"ERROR: Human comments not found at {human_path}")
        print("  Run poc_1_prepare.py first.")
        sys.exit(1)
    if not reviewer_path.exists():
        print(f"ERROR: Reviewer comments not found at {reviewer_path}")
        print("  Run poc_2_review.py first.")
        sys.exit(1)

    human_comments = json.loads(human_path.read_text())
    reviewer_comments = json.loads(reviewer_path.read_text())

    if reviewer_comments.get("parse_error"):
        print("ERROR: Reviewer output had a parse error. Cannot judge.")
        sys.exit(1)

    inline_count = len(reviewer_comments.get("inline_comments", []))
    general_count = len(reviewer_comments.get("general_comments", []))
    print(f"  Human comments:   {len(human_comments)}")
    print(f"  Agent comments:   {inline_count} inline + {general_count} general")
    print()

    # Build judge prompt
    user_prompt = format_comments_for_judge(human_comments, reviewer_comments)
    save_text(user_prompt, "3_judge_prompt.txt")
    print()

    # Run judge
    raw_output = run_judge(user_prompt)
    print()

    judge_results = parse_judge_output(raw_output)
    print()

    # Load reviewer raw output for cost/duration
    reviewer_raw_path = DATA_DIR / "2_reviewer_raw_output.json"
    reviewer_raw = {}
    if reviewer_raw_path.exists():
        reviewer_raw = json.loads(reviewer_raw_path.read_text())

    # Generate and save summary
    summary = generate_summary(judge_results, reviewer_raw, raw_output)
    save_text(summary, "3_summary.txt")
    print()
    print(summary)


if __name__ == "__main__":
    main()
