"""Find MRs suitable for the PoC by checking multi-commit merge branches.

For each candidate, looks up the MR IID by branch name, fetches
discussions, and checks whether the human review comments reference
commits that are locally reachable (and not the final branch commit,
since final-commit comments represent feedback that didn't lead to
code changes).

Reusable: configure CANDIDATES and project settings below, then run.
Very conservative rate limiting: 3s between requests.
"""

import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(line_buffering=True)
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
POC_DIR = Path(__file__).resolve().parent
load_dotenv(REPO_ROOT / ".env")

# ---------------------------------------------------------------------------
# Configuration: edit these to search different projects or candidates
# ---------------------------------------------------------------------------

GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN", "")
GITLAB_INSTANCE = "https://gitlab.com"
PROJECT_ID = 7071551  # gitlab-org/gitlab-ui
SUBJECT_REPO = REPO_ROOT / "subject-repos" / "gitlab-ui"

# Where to cache fetched discussion data
DATA_DIR = POC_DIR / "results" / "0_candidate_search"

# Seconds between API calls (conservative; gitlab.com allows 300/min authenticated)
DELAY = 3.0

# Candidates to check: (merge_commit_sha, branch_name, description)
# To find these, run in the subject repo:
#   git log --merges --oneline --first-parent -500 | while read sha rest; do
#     count=$(git log --oneline "${sha}^2" --not "${sha}^1" 2>/dev/null | wc -l)
#     [ "$count" -gt 2 ] && echo "$count commits: $sha $rest"
#   done
# Then pick branches with multiple commits suggesting review iteration.
CANDIDATES = [
    ("0258f15e9", "2764-add-dashboard-panel-component", "GlDashboardPanel, 7 commits with iterative refactoring"),
    ("ffd3c804e", "drawer-update-design-tokens", "Drawer design tokens, 5 commits including 'Apply suggestions'"),
    ("5865688af", "3066-rm-bnav-from-glnav", "GlNav refactor, 7 commits"),
    ("c208051c6", "3007-form-group-description-is-not-accounced-when-used-with-input-group", "FormGroup a11y fix, 4 commits"),
]

# ---------------------------------------------------------------------------
# End of configuration
# ---------------------------------------------------------------------------

api_call_count = 0


def api_get(path: str, params: dict | None = None) -> httpx.Response:
    """GET request to GitLab API with conservative rate limiting."""
    global api_call_count

    time.sleep(DELAY)
    api_call_count += 1

    url = f"{GITLAB_INSTANCE}/api/v4{path}"
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN} if GITLAB_TOKEN else {}

    print(f"    API call #{api_call_count}: GET {path}")
    resp = httpx.get(url, headers=headers, params=params or {}, timeout=30)
    return resp


def save_json(data: object, filename: str) -> None:
    """Save data as JSON to the data directory."""
    filepath = DATA_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(data, indent=2, default=str))


def is_commit_reachable(sha: str) -> bool:
    """Check if a commit SHA is reachable in the local subject repo."""
    result = subprocess.run(
        ["git", "cat-file", "-t", sha],
        cwd=SUBJECT_REPO,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def get_final_branch_commit(merge_sha: str) -> str:
    """Get the branch tip commit from a merge commit (second parent)."""
    result = subprocess.run(
        ["git", "rev-parse", f"{merge_sha}^2"],
        cwd=SUBJECT_REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def extract_review_comments(
    discussions: list[dict], mr_author: str
) -> dict[str, list[dict]]:
    """Extract inline review comments grouped by head_sha.

    Takes only thread-starting notes. Filters out system notes, MR author
    comments, and bot comments. Only includes DiffNotes with position data.
    """
    head_sha_groups: dict[str, list[dict]] = {}

    for disc in discussions:
        notes = disc.get("notes", [])
        if not notes:
            continue
        note = notes[0]  # thread starter only

        if note.get("system", False):
            continue
        author = note.get("author", {}).get("username", "")
        if author == mr_author:
            continue
        if "bot" in author.lower():
            continue
        if note.get("type") != "DiffNote" or not note.get("position"):
            continue

        head_sha = note["position"]["head_sha"]
        if head_sha not in head_sha_groups:
            head_sha_groups[head_sha] = []
        head_sha_groups[head_sha].append({
            "file": note["position"].get("new_path", "?"),
            "line": note["position"].get("new_line"),
            "body": note["body"][:80],
            "author": author,
        })

    return head_sha_groups


def check_candidate(merge_sha: str, branch_name: str, description: str) -> None:
    """Check one candidate MR for suitability."""
    print(f"\n{'=' * 60}")
    print(f"Branch: {branch_name}")
    print(f"Merge commit: {merge_sha}")
    print(f"Description: {description}")

    # Step 1: Look up MR IID by source branch
    resp = api_get(
        f"/projects/{PROJECT_ID}/merge_requests",
        {"source_branch": branch_name, "state": "merged", "per_page": 1},
    )
    if resp.status_code != 200 or not resp.json():
        print(f"  Could not find MR for branch {branch_name}: HTTP {resp.status_code}")
        return

    mr = resp.json()[0]
    iid = mr["iid"]
    title = mr["title"]
    mr_author = mr["author"]["username"]
    print(f"  MR !{iid}: {title}")
    print(f"  Author: {mr_author}")

    # Step 2: Fetch discussions
    resp = api_get(
        f"/projects/{PROJECT_ID}/merge_requests/{iid}/discussions",
        {"per_page": 100},
    )
    if resp.status_code != 200:
        print(f"  Could not fetch discussions: HTTP {resp.status_code}")
        return

    discussions = resp.json()
    save_json(discussions, f"discussions_{iid}.json")

    # Step 3: Analyze comments
    head_sha_groups = extract_review_comments(discussions, mr_author)

    if not head_sha_groups:
        print("  No qualifying inline review comments found.")
        return

    # Step 4: Check reachability of each head_sha
    final_commit = get_final_branch_commit(merge_sha)

    print(f"  Final branch commit: {final_commit[:12]}")
    print(f"  Comment groups by head_sha:")

    usable_comments = 0
    for sha, comments in sorted(head_sha_groups.items(), key=lambda x: -len(x[1])):
        reachable = is_commit_reachable(sha)
        is_final = sha == final_commit
        status = "REACHABLE" if reachable else "dangling"
        final_tag = " (FINAL, skip)" if is_final else ""
        usable = reachable and not is_final

        print(f"    {sha[:12]}: {len(comments)} comments, {status}{final_tag}")
        if usable:
            usable_comments += len(comments)
            for c in comments:
                print(f"      [{c['author']}] {c['file']}:{c['line']} {c['body']}")

    if usable_comments > 0:
        print(f"\n  *** USABLE: {usable_comments} comments on reachable non-final commits ***")
    else:
        print("  Not usable: no comments on reachable intermediate commits.")


def main() -> None:
    print("Searching for suitable PoC MRs with reachable intermediate commits")
    print(f"Instance: {GITLAB_INSTANCE}")
    print(f"Project ID: {PROJECT_ID}")
    print(f"Subject repo: {SUBJECT_REPO}")
    print(f"Delay between API calls: {DELAY}s")
    print(f"Candidates to check: {len(CANDIDATES)}")

    if not GITLAB_TOKEN:
        print("WARNING: GITLAB_TOKEN not set, requests will be unauthenticated.")

    for merge_sha, branch, desc in CANDIDATES:
        check_candidate(merge_sha, branch, desc)

    print(f"\n\nTotal API calls: {api_call_count}")


if __name__ == "__main__":
    main()
