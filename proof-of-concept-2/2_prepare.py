"""Prepare inputs for reviewer and judge agents.

For each selected PR revision, extracts the diff at the review-time
commit and the human review comments for that revision. Also builds
the reviewer user prompt with MR description from the collected data.

Usage:
    python proof-of-concept-2/prepare.py
"""

import json
import subprocess
import sys

sys.stdout.reconfigure(line_buffering=True)
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
POC2_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SUBJECT_REPO = REPO_ROOT / "subject-repos" / "storybook"
COLLECTED_DIR = POC2_DIR / "data" / "storybook" / "collected"
DATA_DIR = POC2_DIR / "data" / "storybook"
CANDIDATES_FILE = DATA_DIR / "candidates.json"
SPLIT_FILE = DATA_DIR / "split.json"
REVIEWER_PROMPT = POC2_DIR / "prompts" / "reviewer_system.md"

# Known bot usernames
KNOWN_BOTS = {"dependabot", "renovate", "codecov", "storybook-bot", "github-actions"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_git(args: list[str]) -> str:
    """Run a git command in the subject repo and return stdout."""
    result = subprocess.run(
        ["git"] + args,
        cwd=SUBJECT_REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def save_json(data: object, filepath: Path) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(data, indent=2, default=str))
    print(f"    Saved {filepath.relative_to(REPO_ROOT)}")


def save_text(content: str, filepath: Path) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content)
    print(f"    Saved {filepath.relative_to(REPO_ROOT)}")


def is_bot(username: str) -> bool:
    lower = username.lower()
    return lower in KNOWN_BOTS or "bot" in lower or "[bot]" in lower


# ---------------------------------------------------------------------------
# Preparation
# ---------------------------------------------------------------------------


def get_merge_base(merge_sha: str, head_sha: str) -> str:
    """Compute the diff base for a revision.

    Uses the merge commit's first parent (main branch) as one side
    and finds the merge base with the review-time head commit.
    """
    main_parent = run_git(["rev-parse", f"{merge_sha}^1"]).strip()
    base = run_git(["merge-base", main_parent, head_sha]).strip()
    return base


def extract_human_comments(
    pr_data: dict, target_sha: str
) -> list[dict]:
    """Extract human review comments for a specific commit SHA.

    Takes only thread-starting comments. Filters out bots and
    PR author self-comments.
    """
    pr_author = pr_data["author"]
    comments = []

    for thread in pr_data.get("review_threads", []):
        thread_comments = thread.get("comments", [])
        if not thread_comments:
            continue
        comment = thread_comments[0]  # thread starter

        # Filter
        author = comment.get("author", "ghost")
        if is_bot(author):
            continue
        if author == pr_author:
            continue

        commit_sha = comment.get("commit_sha")
        if commit_sha != target_sha:
            continue

        comments.append({
            "body": comment["body"],
            "author": author,
            "file_path": comment.get("file_path"),
            "line_number": comment.get("line_number"),
            "is_resolved": thread.get("is_resolved", False),
            "is_outdated": thread.get("is_outdated", False),
            "category": "inline" if comment.get("file_path") else "general",
        })

    return comments


def build_reviewer_prompt(
    pr_data: dict, diff: str, diff_stat: str
) -> str:
    """Build the user prompt for the reviewer agent."""
    title = pr_data["title"]
    body = pr_data.get("body") or "(no description)"

    return f"""Review the following pull request diff for the Storybook project (storybookjs/storybook), a UI component development environment for React, Vue, Angular, and other frameworks.

PR Title: {title}
PR Description: {body}

The project codebase is available for you to explore for context.

## Files Changed
{diff_stat}

## Diff
{diff}"""


def prepare_revision(
    pr_data: dict,
    candidate: dict,
    revision: dict,
    output_dir: Path,
    role: str,
) -> dict | None:
    """Prepare inputs for one PR revision. Returns summary or None on error."""
    head_sha = revision["commit_sha"]
    merge_sha = pr_data["merge_commit_sha"]
    pr_number = pr_data["number"]

    print(f"    Revision {head_sha[:12]} ({revision['comment_count']} comments)")

    # Compute diff base
    try:
        base_sha = get_merge_base(merge_sha, head_sha)
    except subprocess.CalledProcessError as e:
        print(f"      ERROR computing merge base: {e}")
        return None

    # Generate diff
    try:
        diff = run_git(["diff", f"{base_sha}..{head_sha}"])
        diff_stat = run_git(["diff", "--stat", f"{base_sha}..{head_sha}"])
    except subprocess.CalledProcessError as e:
        print(f"      ERROR generating diff: {e}")
        return None

    diff_lines = len(diff.splitlines())
    print(f"      Diff: {diff_lines} lines")

    # Extract human comments for this revision
    comments = extract_human_comments(pr_data, head_sha)
    inline = sum(1 for c in comments if c["category"] == "inline")
    general = len(comments) - inline
    print(f"      Comments: {len(comments)} ({inline} inline, {general} general)")

    if not comments:
        print("      WARNING: No comments for this revision after filtering")

    # Build reviewer prompt
    reviewer_prompt = build_reviewer_prompt(pr_data, diff, diff_stat)

    # Save everything
    rev_dir = output_dir / f"rev_{head_sha[:12]}"
    save_json(comments, rev_dir / "human_comments.json")
    save_text(reviewer_prompt, rev_dir / "reviewer_prompt.txt")

    return {
        "pr_number": pr_number,
        "head_sha": head_sha,
        "base_sha": base_sha,
        "diff_lines": diff_lines,
        "human_comment_count": len(comments),
        "role": role,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("PoC #2: Preparing inputs for reviewer and judge agents")
    print(f"  Subject repo: {SUBJECT_REPO}")
    print()

    # Load split
    if not SPLIT_FILE.exists():
        print(f"ERROR: Split file not found at {SPLIT_FILE}")
        print("  Run find_candidates.py first.")
        sys.exit(1)

    split = json.loads(SPLIT_FILE.read_text())
    tuning_prs = set(split["tuning"])
    test_prs = set(split["test"])
    all_selected = split["all_selected"]

    # Load candidates for revision info
    candidates = json.loads(CANDIDATES_FILE.read_text())
    candidates_by_pr = {c["pr_number"]: c for c in candidates}

    print(f"  Tuning PRs: {sorted(tuning_prs)}")
    print(f"  Test PRs: {sorted(test_prs)}")
    print()

    # Also save a copy of the reviewer system prompt for reproducibility
    system_prompt = REVIEWER_PROMPT.read_text()
    save_text(system_prompt, DATA_DIR / "reviewer_system_prompt_used.txt")
    print()

    prepared = []

    for pr_number in all_selected:
        role = "tuning" if pr_number in tuning_prs else "test"
        print(f"  PR #{pr_number} ({role}):")

        # Load collected PR data
        pr_file = COLLECTED_DIR / f"pr_{pr_number}.json"
        if not pr_file.exists():
            print(f"    ERROR: Collected data not found at {pr_file}")
            continue
        pr_data = json.loads(pr_file.read_text())

        # Get usable revisions from candidates
        candidate = candidates_by_pr.get(pr_number)
        if not candidate:
            print(f"    ERROR: No candidate analysis for PR #{pr_number}")
            continue

        usable_revisions = [
            r for r in candidate["revisions"] if r["usable"]
        ]

        if not usable_revisions:
            print(f"    WARNING: No usable revisions")
            continue

        output_dir = DATA_DIR / role / f"pr_{pr_number}"

        for revision in usable_revisions:
            summary = prepare_revision(
                pr_data, candidate, revision, output_dir, role
            )
            if summary:
                prepared.append(summary)
        print()

    # Save preparation manifest
    manifest = {
        "tuning_prs": sorted(tuning_prs),
        "test_prs": sorted(test_prs),
        "prepared_revisions": prepared,
        "total_tuning_comments": sum(
            p["human_comment_count"] for p in prepared if p["role"] == "tuning"
        ),
        "total_test_comments": sum(
            p["human_comment_count"] for p in prepared if p["role"] == "test"
        ),
    }
    save_json(manifest, DATA_DIR / "manifest.json")

    print(f"\nSummary:")
    print(f"  Prepared {len(prepared)} revisions")
    print(f"  Tuning: {manifest['total_tuning_comments']} human comments")
    print(f"  Test: {manifest['total_test_comments']} human comments")


if __name__ == "__main__":
    main()
