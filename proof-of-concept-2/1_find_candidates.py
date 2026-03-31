"""Find evaluation-eligible PR revisions from collected Storybook data.

For each collected PR, checks which review comment commit SHAs are
reachable in the local clone and identifies revisions with usable
human comments (reachable, non-final commits). Selects 5 PRs and
splits them into tuning (2 oldest) and test (3 newest) sets.

Usage:
    python proof-of-concept-2/find_candidates.py
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

# How many PRs to select and how to split them
TARGET_PRS = 8     # use all eligible PRs
TUNING_COUNT = 5   # oldest N become tuning set
# Remaining (TARGET_PRS - TUNING_COUNT) become test set

# Known bot usernames (beyond the "bot" substring heuristic)
KNOWN_BOTS = {"dependabot", "renovate", "codecov", "storybook-bot", "github-actions"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_git(args: list[str]) -> subprocess.CompletedProcess:
    """Run a git command in the subject repo."""
    return subprocess.run(
        ["git"] + args,
        cwd=SUBJECT_REPO,
        capture_output=True,
        text=True,
    )


def is_commit_reachable(sha: str) -> bool:
    """Check if a commit SHA exists in the local clone."""
    result = run_git(["cat-file", "-t", sha])
    return result.returncode == 0


def get_final_branch_commit(merge_sha: str) -> str | None:
    """Get the branch tip (second parent of merge commit)."""
    result = run_git(["rev-parse", f"{merge_sha}^2"])
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def is_bot(username: str) -> bool:
    """Check if a username looks like a bot."""
    lower = username.lower()
    if lower in KNOWN_BOTS:
        return True
    if "bot" in lower or "[bot]" in lower:
        return True
    return False


def analyze_pr(pr_data: dict) -> dict:
    """Analyze a single PR for evaluation eligibility.

    Groups review comments by commit_sha, checks reachability,
    excludes bot/author comments and final-commit comments.
    """
    pr_number = pr_data["number"]
    pr_author = pr_data["author"]
    merge_sha = pr_data["merge_commit_sha"]

    # Get the final branch commit (to exclude final-revision comments)
    final_commit = get_final_branch_commit(merge_sha)

    # Group thread-starting comments by commit_sha
    # Only include non-bot, non-author, inline comments
    sha_groups: dict[str, list[dict]] = {}
    skipped_bot = 0
    skipped_author = 0
    skipped_no_commit = 0

    for thread in pr_data.get("review_threads", []):
        comments = thread.get("comments", [])
        if not comments:
            continue
        comment = comments[0]  # thread starter only

        author = comment.get("author", "ghost")
        if is_bot(author):
            skipped_bot += 1
            continue
        if author == pr_author:
            skipped_author += 1
            continue

        commit_sha = comment.get("commit_sha")
        if not commit_sha:
            skipped_no_commit += 1
            continue

        if commit_sha not in sha_groups:
            sha_groups[commit_sha] = []
        sha_groups[commit_sha].append({
            "file_path": comment.get("file_path"),
            "line_number": comment.get("line_number"),
            "author": author,
            "body_preview": (comment.get("body") or "")[:80],
            "is_resolved": thread.get("is_resolved", False),
            "is_outdated": thread.get("is_outdated", False),
        })

    # Check reachability and classify each revision
    revisions = []
    total_usable = 0

    for sha, comments in sorted(sha_groups.items(), key=lambda x: -len(x[1])):
        reachable = is_commit_reachable(sha)
        is_final = (sha == final_commit) if final_commit else False
        usable = reachable and not is_final

        revision = {
            "commit_sha": sha,
            "comment_count": len(comments),
            "reachable": reachable,
            "is_final_commit": is_final,
            "usable": usable,
            "comments": comments,
        }
        revisions.append(revision)

        if usable:
            total_usable += len(comments)

    return {
        "pr_number": pr_number,
        "title": pr_data["title"],
        "author": pr_author,
        "merged_at": pr_data["merged_at"],
        "merge_commit_sha": merge_sha,
        "final_branch_commit": final_commit,
        "total_threads": len(pr_data.get("review_threads", [])),
        "skipped_bot": skipped_bot,
        "skipped_author": skipped_author,
        "skipped_no_commit": skipped_no_commit,
        "revisions": revisions,
        "total_usable_comments": total_usable,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("PoC #2: Finding evaluation-eligible PR revisions")
    print(f"  Subject repo: {SUBJECT_REPO}")
    print(f"  Collected data: {COLLECTED_DIR}")
    print()

    if not SUBJECT_REPO.exists():
        print(f"ERROR: Subject repo not found at {SUBJECT_REPO}")
        sys.exit(1)

    # Load all collected PRs
    pr_files = sorted(COLLECTED_DIR.glob("pr_*.json"))
    if not pr_files:
        print(f"ERROR: No collected PR files in {COLLECTED_DIR}")
        print("  Run collect_prs.py first.")
        sys.exit(1)

    print(f"  Found {len(pr_files)} collected PRs")
    print()

    # Analyze each PR
    candidates = []
    for pr_file in pr_files:
        pr_data = json.loads(pr_file.read_text())
        pr_number = pr_data["number"]
        print(f"  PR #{pr_number}: {pr_data['title'][:60]}")

        analysis = analyze_pr(pr_data)

        usable = analysis["total_usable_comments"]
        total = analysis["total_threads"]
        print(f"    {usable} usable comments (of {total} threads)")

        for rev in analysis["revisions"]:
            status = "USABLE" if rev["usable"] else "skip"
            reason = ""
            if not rev["reachable"]:
                reason = " (unreachable)"
            elif rev["is_final_commit"]:
                reason = " (final commit)"
            print(
                f"    {rev['commit_sha'][:12]}: "
                f"{rev['comment_count']} comments, {status}{reason}"
            )

        if usable > 0:
            candidates.append(analysis)
        print()

    # Sort by merged_at for temporal split
    candidates.sort(key=lambda c: c["merged_at"])

    print(f"Eligible PRs: {len(candidates)} (of {len(pr_files)} collected)")

    if len(candidates) < TARGET_PRS:
        print(
            f"WARNING: Only {len(candidates)} eligible PRs,"
            f" wanted {TARGET_PRS}. Using all available."
        )
        selected = candidates
    else:
        # Take the TARGET_PRS with the most usable comments
        by_comments = sorted(
            candidates, key=lambda c: c["total_usable_comments"], reverse=True
        )
        selected = by_comments[:TARGET_PRS]
        # Re-sort selected by merge date for temporal split
        selected.sort(key=lambda c: c["merged_at"])

    tuning = selected[:TUNING_COUNT]
    test = selected[TUNING_COUNT:]

    print(f"\nSelected {len(selected)} PRs:")
    print(f"  Tuning set ({len(tuning)}):")
    for c in tuning:
        print(
            f"    PR #{c['pr_number']}: {c['total_usable_comments']} comments"
            f" (merged {c['merged_at'][:10]})"
        )
    print(f"  Test set ({len(test)}):")
    for c in test:
        print(
            f"    PR #{c['pr_number']}: {c['total_usable_comments']} comments"
            f" (merged {c['merged_at'][:10]})"
        )

    # Save candidates analysis
    candidates_file = DATA_DIR / "candidates.json"
    candidates_file.parent.mkdir(parents=True, exist_ok=True)
    candidates_file.write_text(json.dumps(candidates, indent=2, default=str))
    print(f"\n  Saved candidates: {candidates_file}")

    # Save split
    split_data = {
        "tuning": [c["pr_number"] for c in tuning],
        "test": [c["pr_number"] for c in test],
        "all_selected": [c["pr_number"] for c in selected],
    }
    split_file = DATA_DIR / "split.json"
    split_file.write_text(json.dumps(split_data, indent=2))
    print(f"  Saved split: {split_file}")


if __name__ == "__main__":
    main()
