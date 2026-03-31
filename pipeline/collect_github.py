"""Collect PR data from a GitHub repository for the code review experiment.

Fetches merged pull requests and their inline review comments using the
GitHub GraphQL API. Outputs per-PR JSON files with metadata, merge commit
info, and review threads with comments.

This is phase 1 of data collection (API scraping). Phase 2 (local clone,
diff generation, commit reachability checking) runs separately.

Rate limiting: 1s delay between calls. GitHub allows 5,000 points/hour.
A typical full collection of 500 PRs with details requires ~600 calls
(~3 points each), well within the hourly limit.

Usage:
    python pipeline/collect_github.py

Or override the project via command line:
    python pipeline/collect_github.py django django
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
load_dotenv(REPO_ROOT / ".env")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# Project to collect data from (can be overridden via command line args)
PROJECT_OWNER = "django"
PROJECT_NAME = "django"

# Rate limiting
DELAY = 1.0  # seconds between GraphQL calls

# Collection limits
PAGE_SIZE = 50   # PRs per page in listing query (max 100 for GraphQL)
MAX_PRS = None   # None = collect all qualifying PRs; integer to cap

# Trial mode: when True, overrides MAX_PRS to a small number
TRIAL_MODE = True
TRIAL_MAX_PRS = 5

# Filters applied during collection
SKIP_SQUASH_REBASE = True   # skip PRs with 1-parent merge commits
SKIP_NO_REVIEWS = True      # skip PRs with 0 review threads
MIN_REVIEW_THREADS = 1      # minimum review threads to collect a PR

# ---------------------------------------------------------------------------
# End of configuration
# ---------------------------------------------------------------------------

# Output directory (computed from project; under data/, which is gitignored)
def output_dir(owner: str, name: str) -> Path:
    return REPO_ROOT / "data" / "collected" / f"github--{owner}--{name}"


# GraphQL: list recent merged PRs with basic metadata for filtering
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

# GraphQL: fetch detailed review threads and comments for a single PR
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
    """Save data as JSON."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(data, indent=2, default=str))


def log(msg: str) -> None:
    """Print a timestamped log message."""
    elapsed = time.time() - start_time
    minutes, seconds = divmod(int(elapsed), 60)
    print(f"  [{minutes:02d}:{seconds:02d}] {msg}")


def fetch_pr_list(
    owner: str, name: str, max_prs: int | None,
) -> list[dict]:
    """Fetch a paginated list of merged PRs with basic metadata.

    Returns only PRs that pass the configured filters (non-squash,
    has review threads, etc.).
    """
    all_prs: list[dict] = []
    cursor = None
    page = 0

    while True:
        page += 1
        log(f"Fetching PR list page {page}...")

        data = graphql(QUERY_LIST_PRS, {
            "owner": owner,
            "name": name,
            "first": PAGE_SIZE,
            "after": cursor,
        })

        pr_data = data["repository"]["pullRequests"]
        prs = pr_data["nodes"]
        page_info = pr_data["pageInfo"]

        for pr in prs:
            mc = pr.get("mergeCommit")
            thread_count = pr["reviewThreads"]["totalCount"]

            # Apply filters
            if SKIP_SQUASH_REBASE and mc:
                parent_count = mc["parents"]["totalCount"]
                if parent_count < 2:
                    continue
            if SKIP_NO_REVIEWS and thread_count < MIN_REVIEW_THREADS:
                continue

            all_prs.append(pr)

            if max_prs and len(all_prs) >= max_prs:
                log(f"Reached max PRs limit ({max_prs})")
                return all_prs

        rate = data.get("rateLimit", {})
        remaining = rate.get("remaining", "?")
        log(
            f"  Page {page}: {len(prs)} PRs fetched,"
            f" {len(all_prs)} qualifying so far"
            f" (rate limit: {remaining} remaining)"
        )

        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]

    return all_prs


def fetch_pr_details(owner: str, name: str, pr_number: int) -> dict:
    """Fetch full details for a single PR including all review threads.

    Handles pagination of review threads (>50 threads per PR).
    """
    all_threads: list[dict] = []
    cursor = None
    pr_metadata = None

    while True:
        data = graphql(QUERY_PR_DETAILS, {
            "owner": owner,
            "name": name,
            "number": pr_number,
            "threadCursor": cursor,
        })

        pr = data["repository"]["pullRequest"]
        if pr_metadata is None:
            pr_metadata = {
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

    # Normalize threads into a clean format
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


def collect_project(owner: str, name: str) -> None:
    """Orchestrate full data collection for a single GitHub project."""
    data_dir = output_dir(owner, name)

    max_prs = MAX_PRS
    if TRIAL_MODE:
        max_prs = TRIAL_MAX_PRS

    print(f"\nCollecting data for {owner}/{name}")
    print(f"  Output: {data_dir}")
    print(f"  Max PRs: {max_prs or 'unlimited'}")
    print(f"  Filters: skip_squash={SKIP_SQUASH_REBASE},"
          f" min_threads={MIN_REVIEW_THREADS}")
    print()

    # Step 1: Get filtered PR list
    log("Step 1: Fetching PR list...")
    pr_list = fetch_pr_list(owner, name, max_prs)
    log(f"Found {len(pr_list)} qualifying PRs")

    # Save the PR list for reference
    save_json(pr_list, data_dir / "pr_list.json")

    # Step 2: Fetch details for each qualifying PR
    log("Step 2: Fetching PR details...")
    collected = 0
    skipped = 0

    for i, pr_summary in enumerate(pr_list):
        pr_number = pr_summary["number"]
        pr_file = data_dir / "prs" / f"pr_{pr_number}.json"

        # Skip if already collected (supports resuming)
        if pr_file.exists():
            skipped += 1
            continue

        log(
            f"  [{i + 1}/{len(pr_list)}] PR #{pr_number}:"
            f" {pr_summary.get('title', '?')[:60]}"
        )

        try:
            details = fetch_pr_details(owner, name, pr_number)
            details["platform"] = "github"
            details["project"] = f"{owner}/{name}"
            save_json(details, pr_file)
            collected += 1
        except (httpx.HTTPError, RuntimeError) as e:
            log(f"    ERROR: {e}")
            continue

    log(f"Done. Collected {collected} PRs, skipped {skipped} (already exist)")

    # Save project metadata
    save_json({
        "platform": "github",
        "owner": owner,
        "name": name,
        "url": f"https://github.com/{owner}/{name}",
        "total_qualifying_prs": len(pr_list),
        "collected": collected,
        "skipped_existing": skipped,
        "api_calls": api_call_count,
    }, data_dir / "project.json")


def main() -> None:
    global start_time
    start_time = time.time()

    # Allow overriding project via command line
    owner = sys.argv[1] if len(sys.argv) > 1 else PROJECT_OWNER
    name = sys.argv[2] if len(sys.argv) > 2 else PROJECT_NAME

    print("GitHub Data Collection")
    if TRIAL_MODE:
        print(f"TRIAL MODE: collecting up to {TRIAL_MAX_PRS} PRs\n")
    else:
        print()

    if not GITHUB_TOKEN:
        print(
            "ERROR: GITHUB_TOKEN not set."
            " GitHub GraphQL requires authentication."
        )
        sys.exit(1)

    collect_project(owner, name)

    elapsed = time.time() - start_time
    print(f"\nTotal API calls: {api_call_count}")
    print(f"Elapsed: {elapsed:.0f}s ({elapsed / 60:.1f} min)")


if __name__ == "__main__":
    main()
