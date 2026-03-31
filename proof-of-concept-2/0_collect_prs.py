"""Collect Storybook PR data from GitHub GraphQL for PoC #2.

Fetches merged pull requests with inline review threads from
storybookjs/storybook. Saves per-PR JSON files with metadata,
merge commit info, and review threads with comments.

Filters: non-squash merges only, minimum review thread count.
Collected data is immutable (never modified after fetch).

Usage:
    python proof-of-concept-2/collect_prs.py
"""

import json
import os
import sys
import time

sys.stdout.reconfigure(line_buffering=True)
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
POC2_DIR = Path(__file__).resolve().parent
load_dotenv(REPO_ROOT / ".env")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

PROJECT_OWNER = "storybookjs"
PROJECT_NAME = "storybook"

# Rate limiting
DELAY = 1.0  # seconds between GraphQL calls

# Collection limits
PAGE_SIZE = 50   # PRs per page (max 100 for GraphQL)
MAX_PRS = 10     # collect up to 10 qualifying PRs

# Trial mode: collect fewer PRs for testing
TRIAL_MODE = False
TRIAL_MAX_PRS = 3

# Filters
MIN_REVIEW_THREADS = 2  # higher than pipeline default; ensures enough comments

# Output directory
DATA_DIR = POC2_DIR / "data" / "storybook" / "collected"

# ---------------------------------------------------------------------------
# GraphQL queries (adapted from pipeline/collect_github.py)
# ---------------------------------------------------------------------------

QUERY_LIST_PRS = """
query($owner: String!, $name: String!, $first: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(
      states: MERGED
      first: $first
      after: $after
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        mergedAt
        mergeCommit {
          oid
          parents(first: 3) { totalCount }
        }
        reviewThreads { totalCount }
      }
    }
  }
  rateLimit { remaining resetAt cost }
}
"""

QUERY_PR_DETAILS = """
query($owner: String!, $name: String!, $number: Int!, $threadCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number
      title
      body
      author { login }
      mergedAt
      mergeCommit {
        oid
        parents(first: 3) {
          totalCount
          nodes { oid }
        }
      }
      reviewThreads(first: 50, after: $threadCursor) {
        totalCount
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          isOutdated
          isCollapsed
          comments(first: 20) {
            nodes {
              id
              body
              author { login }
              path
              line
              startLine
              outdated
              createdAt
              commit { oid }
            }
          }
        }
      }
    }
  }
  rateLimit { remaining resetAt cost }
}
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

api_call_count = 0
start_time = 0.0


def graphql(query: str, variables: dict | None = None) -> dict:
    """Execute a GitHub GraphQL query with rate limiting."""
    global api_call_count
    time.sleep(DELAY)
    api_call_count += 1

    resp = httpx.post(
        "https://api.github.com/graphql",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        json={"query": query, "variables": variables or {}},
        timeout=30,
    )
    resp.raise_for_status()

    data = resp.json()
    if "errors" in data:
        error_msgs = "; ".join(e.get("message", "?") for e in data["errors"])
        raise RuntimeError(f"GraphQL errors: {error_msgs}")
    return data["data"]


def save_json(data: object, filepath: Path) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(data, indent=2, default=str))


def log(msg: str) -> None:
    elapsed = time.time() - start_time
    minutes, seconds = divmod(int(elapsed), 60)
    print(f"  [{minutes:02d}:{seconds:02d}] {msg}")


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def fetch_pr_list(max_prs: int) -> list[dict]:
    """Fetch paginated list of merged PRs, filtering for non-squash with reviews."""
    all_prs: list[dict] = []
    cursor = None
    page = 0

    while True:
        page += 1
        log(f"Fetching PR list page {page}...")

        data = graphql(QUERY_LIST_PRS, {
            "owner": PROJECT_OWNER,
            "name": PROJECT_NAME,
            "first": PAGE_SIZE,
            "after": cursor,
        })

        pr_data = data["repository"]["pullRequests"]
        prs = pr_data["nodes"]
        page_info = pr_data["pageInfo"]

        for pr in prs:
            mc = pr.get("mergeCommit")
            thread_count = pr["reviewThreads"]["totalCount"]

            # Skip squash/rebase merges (1-parent commits)
            if mc and mc["parents"]["totalCount"] < 2:
                continue
            # Skip PRs without enough review threads
            if thread_count < MIN_REVIEW_THREADS:
                continue

            all_prs.append(pr)
            if len(all_prs) >= max_prs:
                log(f"Reached target ({max_prs} PRs)")
                return all_prs

        rate = data.get("rateLimit", {})
        remaining = rate.get("remaining", "?")
        log(
            f"  Page {page}: {len(prs)} PRs scanned,"
            f" {len(all_prs)} qualifying so far"
            f" (rate limit: {remaining} remaining)"
        )

        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]

    return all_prs


def fetch_pr_details(pr_number: int) -> dict:
    """Fetch full PR details including all review threads (paginated)."""
    all_threads: list[dict] = []
    cursor = None
    pr_metadata = None

    while True:
        data = graphql(QUERY_PR_DETAILS, {
            "owner": PROJECT_OWNER,
            "name": PROJECT_NAME,
            "number": pr_number,
            "threadCursor": cursor,
        })

        pr = data["repository"]["pullRequest"]
        if pr_metadata is None:
            pr_metadata = {
                "platform": "github",
                "project": f"{PROJECT_OWNER}/{PROJECT_NAME}",
                "number": pr["number"],
                "title": pr["title"],
                "body": pr["body"],
                "author": (pr.get("author") or {}).get("login", "ghost"),
                "merged_at": pr["mergedAt"],
                "merge_commit_sha": pr["mergeCommit"]["oid"],
                "merge_commit_parent_count": (
                    pr["mergeCommit"]["parents"]["totalCount"]
                ),
                "merge_commit_parent_shas": [
                    p["oid"]
                    for p in pr["mergeCommit"]["parents"]["nodes"]
                ],
            }

        threads_data = pr["reviewThreads"]
        all_threads.extend(threads_data["nodes"])

        if not threads_data["pageInfo"]["hasNextPage"]:
            break
        cursor = threads_data["pageInfo"]["endCursor"]

    # Normalize threads
    normalized_threads = []
    for thread in all_threads:
        comments = thread["comments"]["nodes"]
        normalized_comments = []
        for comment in comments:
            normalized_comments.append({
                "id": comment["id"],
                "author": (
                    (comment.get("author") or {}).get("login", "ghost")
                ),
                "body": comment["body"],
                "file_path": comment.get("path"),
                "line_number": comment.get("line"),
                "start_line": comment.get("startLine"),
                "commit_sha": (
                    (comment.get("commit") or {}).get("oid")
                ),
                "created_at": comment["createdAt"],
                "is_outdated": comment.get("outdated", False),
            })

        normalized_threads.append({
            "is_resolved": thread["isResolved"],
            "is_outdated": thread["isOutdated"],
            "is_collapsed": thread["isCollapsed"],
            "comments": normalized_comments,
        })

    pr_metadata["review_threads"] = normalized_threads
    pr_metadata["review_thread_count"] = len(normalized_threads)
    return pr_metadata


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global start_time
    start_time = time.time()

    max_prs = TRIAL_MAX_PRS if TRIAL_MODE else MAX_PRS

    print(f"PoC #2: Collecting Storybook PR data")
    print(f"  Target: {PROJECT_OWNER}/{PROJECT_NAME}")
    print(f"  Output: {DATA_DIR}")
    print(f"  Max PRs: {max_prs}")
    print(f"  Min review threads: {MIN_REVIEW_THREADS}")
    if TRIAL_MODE:
        print(f"  TRIAL MODE (set TRIAL_MODE=False for full collection)")
    print()

    if not GITHUB_TOKEN:
        print("ERROR: GITHUB_TOKEN not set.")
        sys.exit(1)

    # Step 1: Get filtered PR list
    log("Step 1: Fetching PR list...")
    pr_list = fetch_pr_list(max_prs)
    log(f"Found {len(pr_list)} qualifying PRs")
    print()

    # Step 2: Fetch details for each
    log("Step 2: Fetching PR details...")
    collected = 0
    skipped = 0

    for i, pr_summary in enumerate(pr_list):
        pr_number = pr_summary["number"]
        pr_file = DATA_DIR / f"pr_{pr_number}.json"

        # Resume support: skip already-collected PRs
        if pr_file.exists():
            skipped += 1
            log(f"  [{i + 1}/{len(pr_list)}] PR #{pr_number}: already exists, skipping")
            continue

        log(
            f"  [{i + 1}/{len(pr_list)}] PR #{pr_number}:"
            f" {pr_summary.get('title', '?')[:60]}"
        )

        try:
            details = fetch_pr_details(pr_number)
            save_json(details, pr_file)
            thread_count = details["review_thread_count"]
            log(f"    Saved ({thread_count} review threads)")
            collected += 1
        except (httpx.HTTPError, RuntimeError) as e:
            log(f"    ERROR: {e}")
            continue

    print()
    log(f"Done. Collected {collected}, skipped {skipped} (already exist)")

    elapsed = time.time() - start_time
    print(f"\nTotal API calls: {api_call_count}")
    print(f"Elapsed: {elapsed:.0f}s ({elapsed / 60:.1f} min)")


if __name__ == "__main__":
    main()
