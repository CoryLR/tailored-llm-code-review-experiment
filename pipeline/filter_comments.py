"""Filter collected PR/MR data through Stage 1 and Stage 2.

Stage 1: Bot and structural noise removal
  - Bot authors (username patterns, known bot accounts)
  - Short comments (fewer than MIN_WORDS words)
  - Non-substantive patterns (LGTM, +1, pure emoji, etc.)
  - MR/PR author self-comments (not reviewer feedback)

Stage 2: Resolution signal tagging
  - Tags each surviving comment with a normalized resolution_signal
  - Preserves the original is_resolved / is_outdated fields
  - Adds computed fields for downstream use

Input:  per-PR/MR JSON files from data/collected/{project_slug}/prs/ or mrs/
Output: filtered per-PR/MR JSON files in data/filtered/{project_slug}/

Both GitHub and GitLab collected data use the same normalized format
(see collect_github.py and collect_gitlab.py), so this script handles
both platforms uniformly.

Usage:
    python pipeline/filter_comments.py

Or override the project slug via command line:
    python pipeline/filter_comments.py github--django--django
"""

import json
import re
import sys

sys.stdout.reconfigure(line_buffering=True)
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Project to filter (directory name under data/collected/)
# Can be overridden via command line arg.
PROJECT_SLUG = "gitlab--gitlab-org--gitlab-ui"

# Directories
COLLECTED_DIR = REPO_ROOT / "data" / "collected"
FILTERED_DIR = REPO_ROOT / "data" / "filtered"

# Trial mode: limit the number of PR/MR files to process
TRIAL_MODE = True
TRIAL_MAX_FILES = 5

# ---------------------------------------------------------------------------
# Stage 1: Bot and structural noise removal
# ---------------------------------------------------------------------------

# Minimum word count for a comment to be considered substantive
MIN_WORDS = 3

# Username substrings that indicate a bot account (case-insensitive)
BOT_USERNAME_PATTERNS = [
    "bot",
    "[bot]",
    "-bot",
    "_bot",
]

# Known bot accounts that don't match the patterns above
KNOWN_BOT_USERNAMES = {
    "dependabot",
    "renovate",
    "codecov",
    "coveralls",
    "greenkeeper",
    "snyk-bot",
    "ghost",
    "ghost1",
    "danger-bot",
    "gitlab-bot",
    "review-bot",
}

# Regex patterns for non-substantive comments (case-insensitive).
# These match the ENTIRE comment body (after stripping whitespace).
# Comments matching any of these are removed.
NON_SUBSTANTIVE_PATTERNS = [
    r"^lgtm\.?$",
    r"^\+1$",
    r"^:\+1:$",
    r"^:thumbsup:$",
    r"^looks good( to me)?\.?!?$",
    r"^ship it\.?!?$",
    r"^ack\.?$",
    r"^nack\.?$",
    r"^thanks\.?!?$",
    r"^thank you\.?!?$",
    r"^done\.?$",
    r"^fixed\.?$",
    r"^addressed\.?$",
    r"^will do\.?$",
    r"^good catch\.?!?$",
    r"^nice\.?!?$",
    r"^great\.?!?$",
    # Pure emoji (one or more emoji characters with optional whitespace)
    r"^[\U0001f300-\U0001f9ff\s]+$",
]

# Pre-compile patterns for performance
_NON_SUBSTANTIVE_RE = [
    re.compile(p, re.IGNORECASE) for p in NON_SUBSTANTIVE_PATTERNS
]

# ---------------------------------------------------------------------------
# End of configuration
# ---------------------------------------------------------------------------


def load_json(filepath: Path) -> dict | list:
    """Load and parse a JSON file."""
    return json.loads(filepath.read_text())


def save_json(data: object, filepath: Path) -> None:
    """Save data as JSON."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(data, indent=2, default=str))


def log(msg: str) -> None:
    """Print a log message."""
    print(f"  {msg}")


def is_bot_author(username: str) -> bool:
    """Check if a username belongs to a bot account."""
    if not username:
        return False
    lower = username.lower()
    if lower in KNOWN_BOT_USERNAMES:
        return True
    return any(pattern in lower for pattern in BOT_USERNAME_PATTERNS)


def is_non_substantive(body: str) -> bool:
    """Check if a comment body matches a non-substantive pattern."""
    stripped = body.strip()
    return any(regex.match(stripped) for regex in _NON_SUBSTANTIVE_RE)


def word_count(text: str) -> int:
    """Count words in a string (simple whitespace split)."""
    return len(text.split())


def stage1_filter_thread(thread: dict, pr_author: str) -> dict | None:
    """Apply Stage 1 filters to a single review thread.

    Evaluates the first comment (thread starter) to decide whether to
    keep or drop the entire thread. If the thread starter passes all
    filters, the full thread (including replies) is preserved.

    Returns the thread (unmodified) if it passes, or None if filtered out.
    """
    comments = thread.get("comments", [])
    if not comments:
        return None

    starter = comments[0]
    author = starter.get("author", "")
    body = starter.get("body", "")

    # Filter: bot author
    if is_bot_author(author):
        return None

    # Filter: PR/MR author (self-comments are not reviewer feedback)
    if author and author == pr_author:
        return None

    # Filter: too short
    if word_count(body) < MIN_WORDS:
        return None

    # Filter: non-substantive pattern
    if is_non_substantive(body):
        return None

    return thread


def stage2_tag_resolution(thread: dict) -> dict:
    """Apply Stage 2 resolution tagging to a thread.

    Adds a computed 'resolution_signal' field based on available data.
    Does NOT filter anything; this is purely additive tagging.
    """
    is_resolved = thread.get("is_resolved")
    is_outdated = thread.get("is_outdated")

    if is_resolved is True:
        signal = "resolved"
    elif is_outdated is True:
        signal = "outdated"
    elif is_resolved is False:
        signal = "unresolved"
    else:
        signal = "unknown"

    # Check replies for acknowledgment patterns (heuristic)
    comments = thread.get("comments", [])
    has_acknowledgment = False
    if len(comments) > 1:
        for reply in comments[1:]:
            reply_body = reply.get("body", "").strip().lower()
            ack_phrases = [
                "fixed", "done", "addressed", "good catch",
                "will do", "applied", "updated",
            ]
            if any(phrase in reply_body for phrase in ack_phrases):
                has_acknowledgment = True
                break

    tagged = dict(thread)
    tagged["resolution_signal"] = signal
    tagged["has_acknowledgment"] = has_acknowledgment
    return tagged


def filter_pr_file(input_path: Path, output_path: Path) -> dict:
    """Process a single PR/MR file through Stage 1 and Stage 2.

    Returns a stats dict with counts for reporting.
    """
    pr_data = load_json(input_path)
    pr_author = pr_data.get("author", "")
    threads = pr_data.get("review_threads", [])

    stats = {
        "total_threads": len(threads),
        "stage1_kept": 0,
        "stage1_filtered_bot": 0,
        "stage1_filtered_author": 0,
        "stage1_filtered_short": 0,
        "stage1_filtered_nonsubstantive": 0,
        "stage1_filtered_empty": 0,
        "stage2_resolved": 0,
        "stage2_unresolved": 0,
        "stage2_outdated": 0,
        "stage2_unknown": 0,
        "stage2_acknowledged": 0,
    }

    filtered_threads = []
    for thread in threads:
        # Detailed Stage 1 stats (check each filter independently)
        comments = thread.get("comments", [])
        if not comments:
            stats["stage1_filtered_empty"] += 1
            continue

        starter = comments[0]
        author = starter.get("author", "")
        body = starter.get("body", "")

        if is_bot_author(author):
            stats["stage1_filtered_bot"] += 1
            continue
        if author and author == pr_author:
            stats["stage1_filtered_author"] += 1
            continue
        if word_count(body) < MIN_WORDS:
            stats["stage1_filtered_short"] += 1
            continue
        if is_non_substantive(body):
            stats["stage1_filtered_nonsubstantive"] += 1
            continue

        # Passed Stage 1
        stats["stage1_kept"] += 1

        # Stage 2: tag resolution
        tagged = stage2_tag_resolution(thread)
        signal = tagged["resolution_signal"]
        stats[f"stage2_{signal}"] += 1
        if tagged["has_acknowledgment"]:
            stats["stage2_acknowledged"] += 1

        filtered_threads.append(tagged)

    # Build output record
    output = dict(pr_data)
    output["review_threads"] = filtered_threads
    output["review_thread_count"] = len(filtered_threads)
    output["filter_stats"] = stats

    save_json(output, output_path)
    return stats


def find_pr_files(project_dir: Path) -> list[Path]:
    """Find all PR/MR JSON files in a collected project directory."""
    files = []
    # GitHub format: prs/pr_1234.json
    prs_dir = project_dir / "prs"
    if prs_dir.exists():
        files.extend(sorted(prs_dir.glob("pr_*.json")))
    # GitLab format: mrs/mr_1234.json
    mrs_dir = project_dir / "mrs"
    if mrs_dir.exists():
        files.extend(sorted(mrs_dir.glob("mr_*.json")))
    return files


def main() -> None:
    project_slug = sys.argv[1] if len(sys.argv) > 1 else PROJECT_SLUG
    input_dir = COLLECTED_DIR / project_slug
    output_dir = FILTERED_DIR / project_slug

    print("Comment Filtering Pipeline (Stages 1-2)")
    print(f"  Input: {input_dir}")
    print(f"  Output: {output_dir}")
    if TRIAL_MODE:
        print(f"  TRIAL MODE: processing up to {TRIAL_MAX_FILES} files\n")
    else:
        print()

    if not input_dir.exists():
        print(f"ERROR: Input directory not found: {input_dir}")
        print("  Run collect_github.py or collect_gitlab.py first.")
        sys.exit(1)

    pr_files = find_pr_files(input_dir)
    if not pr_files:
        print(f"ERROR: No PR/MR files found in {input_dir}")
        sys.exit(1)

    if TRIAL_MODE:
        pr_files = pr_files[:TRIAL_MAX_FILES]

    print(f"Processing {len(pr_files)} files...")

    # Aggregate stats across all files
    totals = {
        "files_processed": 0,
        "total_threads": 0,
        "stage1_kept": 0,
        "stage1_filtered": 0,
        "stage2_resolved": 0,
        "stage2_unresolved": 0,
        "stage2_outdated": 0,
        "stage2_unknown": 0,
        "stage2_acknowledged": 0,
    }

    for i, pr_file in enumerate(pr_files):
        # Mirror the input directory structure in the output
        relative = pr_file.relative_to(input_dir)
        output_file = output_dir / relative

        log(f"[{i + 1}/{len(pr_files)}] {pr_file.name}")

        stats = filter_pr_file(pr_file, output_file)

        totals["files_processed"] += 1
        totals["total_threads"] += stats["total_threads"]
        totals["stage1_kept"] += stats["stage1_kept"]
        totals["stage1_filtered"] += (
            stats["total_threads"] - stats["stage1_kept"]
        )
        totals["stage2_resolved"] += stats["stage2_resolved"]
        totals["stage2_unresolved"] += stats["stage2_unresolved"]
        totals["stage2_outdated"] += stats["stage2_outdated"]
        totals["stage2_unknown"] += stats["stage2_unknown"]
        totals["stage2_acknowledged"] += stats["stage2_acknowledged"]

        kept_pct = (
            stats["stage1_kept"] / stats["total_threads"] * 100
            if stats["total_threads"]
            else 0
        )
        log(
            f"  {stats['stage1_kept']}/{stats['total_threads']} threads kept"
            f" ({kept_pct:.0f}%)"
        )

    # Print summary
    print(f"\n{'=' * 60}")
    print("FILTERING SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Files processed: {totals['files_processed']}")
    print(f"  Total threads: {totals['total_threads']}")
    kept_pct = (
        totals["stage1_kept"] / totals["total_threads"] * 100
        if totals["total_threads"]
        else 0
    )
    print(
        f"  Stage 1 kept: {totals['stage1_kept']}"
        f" ({kept_pct:.0f}%)"
    )
    print(f"  Stage 1 filtered: {totals['stage1_filtered']}")
    print(f"\n  Stage 2 resolution signals:")
    print(f"    Resolved: {totals['stage2_resolved']}")
    print(f"    Unresolved: {totals['stage2_unresolved']}")
    print(f"    Outdated: {totals['stage2_outdated']}")
    print(f"    Unknown: {totals['stage2_unknown']}")
    print(f"    With acknowledgment reply: {totals['stage2_acknowledged']}")

    # Save summary
    save_json(totals, output_dir / "filter_summary.json")
    print(f"\n  Summary saved to {output_dir / 'filter_summary.json'}")


if __name__ == "__main__":
    main()
