"""Run the optimizer agent on tuning PRs to generate a project-tuned prompt.

Processes tuning PRs sequentially, one MR at a time. For each MR, the
optimizer reads all human review comments (across all revisions) plus
the MR diff, and extracts generalizable project-specific rules to add
to the reviewer prompt.

The optimizer agent runs via claude -p with no tools (text analysis only).

Usage:
    python proof-of-concept-2/optimize.py
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

DATA_DIR = POC2_DIR / "data" / "storybook"
SPLIT_FILE = DATA_DIR / "split.json"
COLLECTED_DIR = DATA_DIR / "collected"
TUNING_DIR = DATA_DIR / "tuning"
OPT_DIR = DATA_DIR / "optimization"

REVIEWER_PROMPT = POC2_DIR / "prompts" / "reviewer_system.md"
OPTIMIZER_PROMPT = POC2_DIR / "prompts" / "optimizer_system.md"

MODEL = "claude-sonnet-4-6"
MAX_BUDGET = "1.00"
SUBPROCESS_TIMEOUT = 300  # 5 minutes

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def save_json(data: object, filepath: Path) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(data, indent=2, default=str))
    print(f"  Saved {filepath.relative_to(REPO_ROOT)}")


def save_text(content: str, filepath: Path) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content)
    print(f"  Saved {filepath.relative_to(REPO_ROOT)}")


RESULT_MARKER = "===FINAL_OPTIMIZER_OUTPUT_BEGIN==="


def extract_json_from_text(text: str) -> dict | None:
    """Extract the optimizer JSON from agent output.

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


def collect_mr_comments(pr_number: int) -> tuple[list[dict], str, str]:
    """Collect all human comments and the latest diff for a tuning PR.

    Returns (all_comments, diff_text, pr_description).
    Comments are collected across all revisions of this PR.
    The diff from the latest (largest) revision is used for context.
    """
    pr_dir = TUNING_DIR / f"pr_{pr_number}"
    rev_dirs = sorted(pr_dir.glob("rev_*"))

    all_comments = []
    latest_diff = ""
    latest_diff_lines = 0

    for rev_dir in rev_dirs:
        comments_file = rev_dir / "human_comments.json"
        if comments_file.exists():
            comments = json.loads(comments_file.read_text())
            all_comments.extend(comments)

        diff_file = rev_dir / "diff.patch"
        if diff_file.exists():
            diff = diff_file.read_text()
            diff_lines = len(diff.splitlines())
            if diff_lines > latest_diff_lines:
                latest_diff = diff
                latest_diff_lines = diff_lines

    # Get PR description from collected data
    pr_file = COLLECTED_DIR / f"pr_{pr_number}.json"
    pr_data = json.loads(pr_file.read_text())
    pr_title = pr_data["title"]
    pr_body = pr_data.get("body") or "(no description)"

    return all_comments, latest_diff, f"Title: {pr_title}\nDescription: {pr_body}"


def build_optimizer_user_prompt(
    current_rules: list[dict],
    comments: list[dict],
    diff: str,
    pr_description: str,
) -> str:
    """Build the user prompt for the optimizer agent."""
    # Format current rules
    if current_rules:
        rules_text = json.dumps(current_rules, indent=2)
    else:
        rules_text = "(no rules yet)"

    # Format comments
    comment_lines = []
    for i, c in enumerate(comments):
        loc = ""
        if c.get("file_path") and c.get("line_number"):
            loc = f" [{c['file_path']}:{c['line_number']}]"
        comment_lines.append(f"### Comment {i}{loc}")
        comment_lines.append(c["body"])
        comment_lines.append("")
    comments_text = "\n".join(comment_lines)

    # Truncate diff if very long (keep optimizer focused on comments)
    diff_lines = diff.splitlines()
    if len(diff_lines) > 500:
        diff = "\n".join(diff_lines[:500]) + f"\n\n... (truncated, {len(diff_lines)} lines total)"

    return f"""## Current Project-Specific Rules

{rules_text}

## Merge Request Context

{pr_description}

### Diff (for context)
{diff}

## Human Review Comments

{comments_text}

## Task

Analyze the human comments above. For each one, determine if it reflects
a generalizable project pattern that should become a rule in the reviewer
prompt, or if it is specific to this particular MR only. Update the rules
accordingly."""


def run_optimizer(user_prompt: str) -> dict:
    """Invoke the optimizer agent via claude -p."""
    system_prompt = OPTIMIZER_PROMPT.read_text()

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
    print(f"  Budget: ${MAX_BUDGET}")
    print(f"  Running optimizer agent...")

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
        if result.stdout:
            print(f"  stdout: {result.stdout[:500]}")
        sys.exit(1)

    print(f"  Completed in {elapsed:.1f}s")

    raw_output = json.loads(result.stdout)
    return raw_output


def apply_rules_to_prompt(base_prompt: str, rules: list[dict]) -> str:
    """Replace the Project-Specific Rules section in the reviewer prompt."""
    marker = "## Project-Specific Rules"
    idx = base_prompt.find(marker)
    if idx == -1:
        # Append if marker not found
        return base_prompt + f"\n\n{marker}\n\n" + format_rules(rules)

    # Replace everything after the marker
    return base_prompt[:idx] + marker + "\n\n" + format_rules(rules)


def format_rules(rules: list[dict]) -> str:
    """Format rules into readable text grouped by category."""
    if not rules:
        return "(none)\n"

    by_category: dict[str, list[str]] = {}
    for rule in rules:
        cat = rule.get("category", "Uncategorized")
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(rule["rule"])

    lines = []
    for cat, cat_rules in by_category.items():
        lines.append(f"### {cat}")
        for r in cat_rules:
            lines.append(f"- {r}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("PoC #2: Running prompt optimizer on tuning PRs")
    print()

    if not SPLIT_FILE.exists():
        print(f"ERROR: Split file not found at {SPLIT_FILE}")
        sys.exit(1)

    split = json.loads(SPLIT_FILE.read_text())
    tuning_prs = split["tuning"]

    print(f"  Tuning PRs: {tuning_prs}")
    print()

    # Save initial (empty rules) prompt
    base_prompt = REVIEWER_PROMPT.read_text()
    save_text(base_prompt, OPT_DIR / "step_0_initial.txt")
    print()

    current_rules: list[dict] = []
    log_entries = []

    for step, pr_number in enumerate(tuning_prs, start=1):
        print(f"Step {step}: Processing tuning PR #{pr_number}")

        # Collect all comments and diff for this PR
        comments, diff, pr_desc = collect_mr_comments(pr_number)
        print(f"  Comments: {len(comments)}")
        print(f"  Diff: {len(diff.splitlines())} lines")

        if not comments:
            print("  No comments to analyze, skipping.")
            continue

        # Build optimizer prompt
        user_prompt = build_optimizer_user_prompt(
            current_rules, comments, diff, pr_desc
        )

        # Run optimizer (with retry on parse failure)
        MAX_ATTEMPTS = 2
        parsed = None
        raw_output = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            if attempt > 1:
                print(f"  Retry {attempt}/{MAX_ATTEMPTS}...")
            raw_output = run_optimizer(user_prompt)
            print()

            result_text = raw_output.get("result", "")
            parsed = extract_json_from_text(result_text)

            if parsed is not None:
                break
            print(f"  WARNING: Could not parse optimizer output (attempt {attempt})")
            print(f"  Raw (first 300 chars): {result_text[:300]}")

        if parsed is None:
            print(f"  ERROR: Failed to parse after {MAX_ATTEMPTS} attempts, skipping")
            log_entries.append({
                "step": step,
                "pr_number": pr_number,
                "parse_error": True,
                "raw_result": result_text[:2000],
            })
            continue

        # Update rules
        new_rules = parsed.get("updated_rules", [])
        analysis = parsed.get("analysis", [])
        changes = parsed.get("changes_summary", "")

        print(f"  Analysis: {len(analysis)} comments examined")
        generalizable = sum(1 for a in analysis if a.get("is_generalizable"))
        print(f"  Generalizable: {generalizable}")
        print(f"  Rules after this step: {len(new_rules)}")
        print(f"  Changes: {changes}")

        current_rules = new_rules

        # Save intermediate prompt
        tuned_prompt = apply_rules_to_prompt(base_prompt, current_rules)
        save_text(
            tuned_prompt,
            OPT_DIR / f"step_{step}_pr_{pr_number}.txt",
        )

        # Log
        log_entries.append({
            "step": step,
            "pr_number": pr_number,
            "comments_analyzed": len(analysis),
            "generalizable_count": generalizable,
            "rules_after": len(new_rules),
            "changes_summary": changes,
            "analysis": analysis,
            "updated_rules": new_rules,
            "cost_usd": raw_output.get("total_cost_usd"),
            "duration_ms": raw_output.get("duration_ms"),
        })
        print()

    # Save final tuned prompt
    final_prompt = apply_rules_to_prompt(base_prompt, current_rules)
    save_text(final_prompt, OPT_DIR / "tuned_prompt.md")

    # Save log
    save_json(log_entries, OPT_DIR / "log.json")

    # Print final rules
    print("\n" + "=" * 60)
    print("FINAL TUNED RULES:")
    print("=" * 60)
    if current_rules:
        print(format_rules(current_rules))
    else:
        print("(no rules extracted)")

    total_cost = sum(e.get("cost_usd", 0) or 0 for e in log_entries)
    print(f"\nTotal optimizer cost: ${total_cost:.2f}")


if __name__ == "__main__":
    main()
