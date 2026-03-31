"""Collect MR data from a GitLab repository for the code review experiment.

Fetches merged merge requests and their inline review comments using the
GitLab REST API. Outputs per-MR JSON files with metadata, squash status,
and discussion threads with comments.

This is phase 1 of data collection (API scraping). Phase 2 (local clone,
diff generation, commit reachability checking) runs separately.

Generalizes the patterns from the proof-of-concept scripts (poc_0 and
poc_1) into a reusable collection pipeline.

Rate limiting: 1s delay between calls for authenticated gitlab.com
(limit is 300 req/min). A full collection of 500 MRs with discussions
requires ~550 API calls.

Usage:
    python pipeline/collect_gitlab.py

Or override the project via command line:
    python pipeline/collect_gitlab.py gitlab-org/gitlab-ui
"""

import json
import os
import sys
import time
import urllib.parse

sys.stdout.reconfigure(line_buffering=True)
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN", "")
GITLAB_INSTANCE = "https://gitlab.com"

# Project to collect data from (can be overridden via command line arg)
PROJECT_PATH = "gitlab-org/gitlab-ui"

# Rate limiting (gitlab.com authenticated: 300 req/min)
DELAY = 1.0  # seconds between API calls

# Collection limits
PAGE_SIZE = 100  # MRs per page (GitLab REST max is 100)
MAX_MRS = None   # None = collect all qualifying MRs; integer to cap

# Trial mode: when True, overrides MAX_MRS to a small number
TRIAL_MODE = True
TRIAL_MAX_MRS = 5

# Filters
SKIP_SQUASH_MERGED = True  # skip MRs that were squash-merged
SKIP_NO_DISCUSSIONS = True # skip MRs with no discussion threads

# ---------------------------------------------------------------------------
# End of configuration
# ---------------------------------------------------------------------------

def output_dir(project_path: str) -> Path:
    slug = project_path.replace("/", "--")
    return REPO_ROOT / "data" / "collected" / f"gitlab--{slug}"


api_call_count = 0
start_time = 0.0


def api_get(
    path: str, params: dict | None = None,
) -> httpx.Response:
    """GET request to GitLab API with rate limiting."""
    global api_call_count

    time.sleep(DELAY)
    api_call_count += 1

    url = f"{GITLAB_INSTANCE}/api/v4{path}"
    headers = {}
    if GITLAB_TOKEN:
        headers["PRIVATE-TOKEN"] = GITLAB_TOKEN

    resp = httpx.get(url, headers=headers, params=params or {}, timeout=30)
    return resp


def save_json(data: object, filepath: Path) -> None:
    """Save data as JSON."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(data, indent=2, default=str))


def log(msg: str) -> None:
    """Print a timestamped log message."""
    elapsed = time.time() - start_time
    minutes, seconds = divmod(int(elapsed), 60)
    print(f"  [{minutes:02d}:{seconds:02d}] {msg}")


def lookup_project(project_path: str) -> dict | None:
    """Look up a GitLab project by its path and return metadata."""
    encoded = urllib.parse.quote(project_path, safe="")
    resp = api_get(f"/projects/{encoded}")
    if resp.status_code != 200:
        log(f"Project lookup failed: HTTP {resp.status_code}")
        return None
    return resp.json()


def fetch_mr_list(
    project_id: int, max_mrs: int | None,
) -> list[dict]:
    """Fetch a paginated list of merged MRs.

    Returns only MRs that pass configured filters (non-squash, etc.).
    """
    all_mrs: list[dict] = []
    page = 0

    while True:
        page += 1
        log(f"Fetching MR list page {page}...")

        resp = api_get(
            f"/projects/{project_id}/merge_requests",
            {
                "state": "merged",
                "order_by": "updated_at",
                "sort": "desc",
                "per_page": PAGE_SIZE,
                "page": page,
            },
        )
        if resp.status_code != 200:
            log(f"MR list failed: HTTP {resp.status_code}")
            break

        mrs = resp.json()
        if not mrs:
            break

        total = resp.headers.get("x-total", "?")

        for mr in mrs:
            is_squash = bool(mr.get("squash_commit_sha"))
            if SKIP_SQUASH_MERGED and is_squash:
                continue
            all_mrs.append(mr)

            if max_mrs and len(all_mrs) >= max_mrs:
                log(f"Reached max MRs limit ({max_mrs})")
                return all_mrs

        log(
            f"  Page {page}: {len(mrs)} MRs fetched,"
            f" {len(all_mrs)} qualifying so far"
            f" (total on server: {total})"
        )

        # Check if there are more pages
        if len(mrs) < PAGE_SIZE:
            break

    return all_mrs


def fetch_mr_discussions(project_id: int, mr_iid: int) -> list[dict]:
    """Fetch all discussion threads for a single MR.

    Handles pagination (multiple pages of discussions).
    """
    all_discussions: list[dict] = []
    page = 0

    while True:
        page += 1
        resp = api_get(
            f"/projects/{project_id}/merge_requests/{mr_iid}/discussions",
            {"per_page": 100, "page": page},
        )
        if resp.status_code != 200:
            log(f"    Discussions failed: HTTP {resp.status_code}")
            break

        discussions = resp.json()
        if not discussions:
            break

        all_discussions.extend(discussions)

        if len(discussions) < 100:
            break

    return all_discussions


def normalize_discussions(discussions: list[dict]) -> list[dict]:
    """Normalize GitLab discussions into a clean thread format.

    Mirrors the output format of collect_github.py so the downstream
    pipeline can process both uniformly.
    """
    threads = []

    for disc in discussions:
        notes = disc.get("notes", [])
        if not notes:
            continue

        # Skip system-generated discussions (merge status changes, etc.)
        first_note = notes[0]
        if first_note.get("system", False):
            continue

        # Determine thread type from the first note
        is_inline = (
            first_note.get("type") == "DiffNote"
            and first_note.get("position") is not None
        )

        # Extract resolution status from the first note
        is_resolved = first_note.get("resolved", False)

        comments = []
        for note in notes:
            if note.get("system", False):
                continue

            pos = note.get("position", {}) or {}
            comments.append({
                "id": str(note.get("id", "")),
                "author": note.get("author", {}).get("username", "unknown"),
                "body": note.get("body", ""),
                "file_path": pos.get("new_path") if is_inline else None,
                "line_number": pos.get("new_line") if is_inline else None,
                "commit_sha": pos.get("head_sha") if is_inline else None,
                "base_sha": pos.get("base_sha") if is_inline else None,
                "created_at": note.get("created_at", ""),
                "is_outdated": False,  # GitLab doesn't expose this directly
            })

        threads.append({
            "is_resolved": is_resolved,
            "is_outdated": False,  # Not directly available in GitLab REST
            "is_inline": is_inline,
            "comments": comments,
        })

    return threads


def collect_project(project_path: str) -> None:
    """Orchestrate full data collection for a single GitLab project."""
    data_dir = output_dir(project_path)

    max_mrs = MAX_MRS
    if TRIAL_MODE:
        max_mrs = TRIAL_MAX_MRS

    print(f"\nCollecting data for {project_path}")
    print(f"  Instance: {GITLAB_INSTANCE}")
    print(f"  Output: {data_dir}")
    print(f"  Max MRs: {max_mrs or 'unlimited'}")
    print(f"  Filters: skip_squash={SKIP_SQUASH_MERGED}")
    print()

    # Step 0: Look up project
    log("Step 0: Looking up project...")
    project = lookup_project(project_path)
    if not project:
        print("ERROR: Project not found.")
        sys.exit(1)

    project_id = project["id"]
    log(f"Project ID: {project_id}, Stars: {project.get('star_count', 0)}")

    # Step 1: Get filtered MR list
    log("Step 1: Fetching MR list...")
    mr_list = fetch_mr_list(project_id, max_mrs)
    log(f"Found {len(mr_list)} qualifying MRs")

    # Save MR list for reference (basic metadata only)
    mr_summaries = [
        {
            "iid": mr["iid"],
            "title": mr["title"],
            "merged_at": mr.get("merged_at"),
            "squash_commit_sha": mr.get("squash_commit_sha"),
        }
        for mr in mr_list
    ]
    save_json(mr_summaries, data_dir / "mr_list.json")

    # Step 2: Fetch discussions for each qualifying MR
    log("Step 2: Fetching MR discussions...")
    collected = 0
    skipped_existing = 0
    skipped_no_threads = 0

    for i, mr in enumerate(mr_list):
        iid = mr["iid"]
        mr_file = data_dir / "mrs" / f"mr_{iid}.json"

        # Skip if already collected (supports resuming)
        if mr_file.exists():
            skipped_existing += 1
            continue

        log(f"  [{i + 1}/{len(mr_list)}] MR !{iid}: {mr['title'][:60]}")

        try:
            discussions = fetch_mr_discussions(project_id, iid)
        except httpx.HTTPError as e:
            log(f"    ERROR: {e}")
            continue

        threads = normalize_discussions(discussions)

        if SKIP_NO_DISCUSSIONS and not threads:
            skipped_no_threads += 1
            continue

        mr_record = {
            "platform": "gitlab",
            "project": project_path,
            "number": iid,
            "title": mr["title"],
            "body": mr.get("description", ""),
            "author": mr.get("author", {}).get("username", "unknown"),
            "merged_at": mr.get("merged_at"),
            "merge_commit_sha": mr.get("merge_commit_sha"),
            "is_squash_merged": bool(mr.get("squash_commit_sha")),
            "review_threads": threads,
            "review_thread_count": len(threads),
        }

        save_json(mr_record, mr_file)
        collected += 1

    log(
        f"Done. Collected {collected} MRs,"
        f" skipped {skipped_existing} (already exist),"
        f" skipped {skipped_no_threads} (no discussion threads)"
    )

    # Save project metadata
    save_json({
        "platform": "gitlab",
        "instance": GITLAB_INSTANCE,
        "project_path": project_path,
        "project_id": project_id,
        "url": f"{GITLAB_INSTANCE}/{project_path}",
        "stars": project.get("star_count", 0),
        "merge_method": project.get("merge_method", "unknown"),
        "total_qualifying_mrs": len(mr_list),
        "collected": collected,
        "skipped_existing": skipped_existing,
        "skipped_no_threads": skipped_no_threads,
        "api_calls": api_call_count,
    }, data_dir / "project.json")


def main() -> None:
    global start_time
    start_time = time.time()

    # Allow overriding project via command line
    project_path = sys.argv[1] if len(sys.argv) > 1 else PROJECT_PATH

    print("GitLab Data Collection")
    if TRIAL_MODE:
        print(f"TRIAL MODE: collecting up to {TRIAL_MAX_MRS} MRs\n")
    else:
        print()

    if not GITLAB_TOKEN:
        print(
            "WARNING: GITLAB_TOKEN not set."
            " Requests will be unauthenticated (10 req/min limit)."
        )

    collect_project(project_path)

    elapsed = time.time() - start_time
    print(f"\nTotal API calls: {api_call_count}")
    print(f"Elapsed: {elapsed:.0f}s ({elapsed / 60:.1f} min)")


if __name__ == "__main__":
    main()
