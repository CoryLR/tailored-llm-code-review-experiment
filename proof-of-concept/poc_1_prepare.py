"""Prepare inputs for the reviewer and judge agents.

Extracts the diff and human review comments for a specific MR revision,
then builds the full reviewer prompt. No API calls; reads from local git
repo and cached discussion data only.
"""

import json
import subprocess
import sys

sys.stdout.reconfigure(line_buffering=True)
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
POC_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Configuration: MR and revision to evaluate
# ---------------------------------------------------------------------------

PROJECT_ID = 7071551  # gitlab-org/gitlab-ui
MR_IID = 5074
MR_TITLE = "Resolve \"Remove `BNav` from `GlNav`\""
MR_DESCRIPTION = (
    "Refactor GlNav to remove the dependency on the Bootstrap Vue BNav "
    "component. Adds prop controls, default values, documentation, and "
    "cleans up migrated specs."
)
MR_AUTHOR = "thutterer"

# The revision to evaluate: an intermediate commit where human reviewers
# left feedback that the author subsequently addressed in later commits.
# This is NOT the final merged commit; it's the state of the code that
# the human reviewer was actually looking at when they wrote their comments.
BASE_SHA = "9b4547e180dbd5b1468d75f3a5d9b84b40599d21"
HEAD_SHA = "7d6d756846c2e8cf3f9fdbfc9b003c8003d5be85"
MERGE_SHA = "5865688af946eef56d8ecc771dc04ceac81e761b"

# Only include human comments made against this exact head_sha.
# Comments on other revisions reference different code states and would
# produce invalid line number comparisons.
EVAL_HEAD_SHA = HEAD_SHA

# Known bot/automation usernames that don't contain "bot" in the name
# (e.g., deactivated CI accounts). Added to the bot filter.
KNOWN_BOT_USERNAMES = {"ghost1"}

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SUBJECT_REPO = REPO_ROOT / "subject-repos" / "gitlab-ui"
CACHED_DISCUSSIONS = (
    POC_DIR / "results" / "0_candidate_search"
    / f"discussions_{MR_IID}.json"
)
DATA_DIR = POC_DIR / "results" / f"mr_{MR_IID}"
REVIEWER_SYSTEM_PROMPT = REPO_ROOT / "prompts" / "reviewer_system.txt"


def save_text(content: str, filename: str) -> Path:
    """Save text content to the data directory."""
    filepath = DATA_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content)
    print(f"  Saved {filepath.relative_to(REPO_ROOT)}")
    return filepath


def save_json(data: object, filename: str) -> Path:
    """Save data as JSON to the data directory."""
    filepath = DATA_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(data, indent=2, default=str))
    print(f"  Saved {filepath.relative_to(REPO_ROOT)}")
    return filepath


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


def generate_diff() -> tuple[str, str]:
    """Generate the unified diff and diff summary."""
    print("Step 1: Generating diff...")

    diff = run_git(["diff", f"{BASE_SHA}..{HEAD_SHA}"])
    save_text(diff, "1_diff.patch")

    diff_stat = run_git(["diff", "--stat", f"{BASE_SHA}..{HEAD_SHA}"])
    save_text(diff_stat, "1_diff_summary.txt")

    line_count = len(diff.splitlines())
    print(f"  Diff: {line_count} lines")
    return diff, diff_stat


def extract_human_comments() -> list[dict]:
    """Extract human review comments from cached discussion data.

    For each discussion, takes only the first note (thread starter).
    Filters:
    - non-system notes only
    - not by the MR author (we want reviewer feedback, not author responses)
    - not by bots
    - only comments on the evaluation head_sha (so line numbers match
      the code state the reviewer agent will see)
    Classifies as inline (DiffNote with position) or general.
    """
    print("Step 2: Extracting human comments...")

    raw = json.loads(CACHED_DISCUSSIONS.read_text())
    comments = []
    skipped_other_sha = 0

    for discussion in raw:
        notes = discussion.get("notes", [])
        if not notes:
            continue

        note = notes[0]

        if note.get("system", False):
            continue

        author = note.get("author", {}).get("username", "")
        if author == MR_AUTHOR:
            continue
        # TODO: The "bot" substring filter is fragile (false positives on usernames containing "bot"). Consider an allowlist/blocklist approach or LLM-based classification for the full pipeline.
        if "bot" in author.lower() or author in KNOWN_BOT_USERNAMES:
            continue

        # For DiffNotes, only include comments on the evaluation revision
        if note.get("type") == "DiffNote" and note.get("position"):
            pos = note["position"]
            if pos.get("head_sha") != EVAL_HEAD_SHA:
                skipped_other_sha += 1
                continue

            comments.append({
                "body": note["body"],
                "author": author,
                "resolved": note.get("resolved", False),
                "note_type": note.get("type"),
                "category": "inline",
                "file_path": pos.get("new_path"),
                "line_number": pos.get("new_line"),
            })
        else:
            comments.append({
                "body": note["body"],
                "author": author,
                "resolved": note.get("resolved", False),
                "note_type": note.get("type"),
                "category": "general",
                "file_path": None,
                "line_number": None,
            })

    save_json(comments, "1_human_comments.json")
    inline = sum(1 for c in comments if c["category"] == "inline")
    general = len(comments) - inline
    print(f"  Found {len(comments)} human comments ({inline} inline, {general} general)")
    if skipped_other_sha:
        print(f"  Skipped {skipped_other_sha} comments on other revisions")
    return comments


def build_reviewer_prompt(diff: str, diff_stat: str) -> str:
    """Build the full user prompt for the reviewer agent."""
    print("Step 3: Building reviewer prompt...")

    system_prompt = REVIEWER_SYSTEM_PROMPT.read_text()

    user_prompt = f"""Review the following merge request diff for the gitlab-ui project (a Vue.js component library for GitLab).

MR Title: {MR_TITLE}
MR Description: {MR_DESCRIPTION}

The project codebase is available for you to explore for context.

## Files Changed
{diff_stat}

## Diff
{diff}"""

    save_text(user_prompt, "1_reviewer_prompt.txt")
    save_text(system_prompt, "1_reviewer_system_prompt_used.txt")

    token_estimate = len(user_prompt.split()) * 1.3
    print(f"  User prompt: ~{int(token_estimate)} estimated tokens")
    return user_prompt


def main() -> None:
    print(f"Preparing PoC inputs for MR !{MR_IID}")
    print(f"  Evaluation revision: {HEAD_SHA[:12]}")
    print(f"  Subject repo: {SUBJECT_REPO}")
    print(f"  Cached data: {CACHED_DISCUSSIONS}")
    print(f"  Output dir: {DATA_DIR}")
    print()

    # Verify prerequisites
    if not SUBJECT_REPO.exists():
        print(f"ERROR: Subject repo not found at {SUBJECT_REPO}")
        sys.exit(1)
    if not CACHED_DISCUSSIONS.exists():
        print(f"ERROR: Cached discussions not found at {CACHED_DISCUSSIONS}")
        sys.exit(1)
    if not REVIEWER_SYSTEM_PROMPT.exists():
        print(f"ERROR: Reviewer system prompt not found at {REVIEWER_SYSTEM_PROMPT}")
        sys.exit(1)

    # Verify commits are reachable
    print("Verifying commits are reachable...")
    try:
        run_git(["cat-file", "-t", BASE_SHA])
        run_git(["cat-file", "-t", HEAD_SHA])
        print("  Both base and head commits found.\n")
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Commit not reachable: {e}")
        sys.exit(1)

    diff, diff_stat = generate_diff()
    print()

    extract_human_comments()
    print()

    build_reviewer_prompt(diff, diff_stat)
    print()

    print("Done. All inputs saved to:")
    print(f"  {DATA_DIR.relative_to(REPO_ROOT)}/")


if __name__ == "__main__":
    main()
