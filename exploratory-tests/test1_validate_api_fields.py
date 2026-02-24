"""
GitLab API Spike: Validate data fields for the code review experiment.

Tests whether gitlab-org/gitlab provides the data we need:
- Inline review comments with resolved status, file paths, line numbers, commit SHAs
- Whether comment-time commit SHAs are still accessible after merge
- Proportion of squash-merged vs. non-squash-merged MRs
- Volume of substantive inline review comments

Target: gitlab-org/gitlab (project ID 278964)
"""

import json
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
import os

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

GITLAB_TOKEN = os.environ["GITLAB_TOKEN"]
PROJECT_ID = 278964  # gitlab-org/gitlab
BASE_URL = "https://gitlab.com/api/v4"
DELAY = 0.5  # seconds between API calls
DATA_DIR = REPO_ROOT / "data" / "test1_validate_api_fields"

api_call_count = 0


def api_get(path: str, params: dict | None = None) -> httpx.Response:
    """Make a GET request to the GitLab API with rate limiting."""
    global api_call_count
    time.sleep(DELAY)
    api_call_count += 1
    url = f"{BASE_URL}{path}"
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN}
    resp = httpx.get(url, headers=headers, params=params or {}, timeout=30)
    print(f"  [{api_call_count}] {resp.status_code} {url}")
    return resp


def save_json(data: object, filename: str) -> None:
    """Save data as JSON to the spike data directory."""
    path = DATA_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def phase1_fetch_mrs() -> list[dict]:
    """Phase 1: Fetch a small batch of recent merged MRs."""
    print("\n=== Phase 1: Fetch merged MRs ===\n")

    resp = api_get(
        f"/projects/{PROJECT_ID}/merge_requests",
        params={
            "state": "merged",
            "order_by": "updated_at",
            "sort": "desc",
            "per_page": 10,
        },
    )
    resp.raise_for_status()
    mrs = resp.json()
    save_json(mrs, "merge_requests.json")

    print(f"\nFetched {len(mrs)} merged MRs:\n")
    squash_count = 0
    for mr in mrs:
        has_squash_sha = bool(mr.get("squash_commit_sha"))
        has_merge_sha = bool(mr.get("merge_commit_sha"))
        if has_squash_sha:
            squash_count += 1
        print(
            f"  !{mr['iid']}: {mr['title'][:60]}"
            f"  squash={has_squash_sha}  merge_sha={has_merge_sha}"
        )

    print(f"\nSquash-merged: {squash_count}/{len(mrs)}")
    return mrs


def phase2_fetch_discussions(mrs: list[dict]) -> list[dict]:
    """Phase 2: Fetch discussions for MRs and identify inline comments."""
    print("\n=== Phase 2: Fetch discussions ===\n")

    all_inline_comments = []
    fields_confirmed = False
    mrs_with_inline = 0
    consecutive_confirmed = 0

    for mr in mrs:
        iid = mr["iid"]
        resp = api_get(
            f"/projects/{PROJECT_ID}/merge_requests/{iid}/discussions",
            params={"per_page": 100},
        )
        resp.raise_for_status()
        discussions = resp.json()
        save_json(discussions, f"discussions/{iid}.json")

        # Find inline (DiffNote) comments that aren't system-generated
        inline_comments = []
        for disc in discussions:
            for note in disc.get("notes", []):
                if note.get("type") == "DiffNote" and not note.get("system", False):
                    inline_comments.append(note)

        if inline_comments:
            mrs_with_inline += 1

        # Check field presence on first inline comment found
        sample = inline_comments[0] if inline_comments else None
        has_resolved = False
        has_old_path = False
        has_new_line = False
        has_head_sha = False

        if sample:
            pos = sample.get("position", {})
            has_resolved = "resolved" in sample or "resolved" in disc
            has_old_path = bool(pos.get("old_path"))
            has_new_line = pos.get("new_line") is not None
            has_head_sha = bool(pos.get("head_sha"))

        print(
            f"  !{iid}: {len(discussions)} discussions, "
            f"{len(inline_comments)} inline comments"
        )
        if sample:
            print(
                f"    fields: resolved={has_resolved}  "
                f"old_path={has_old_path}  new_line={has_new_line}  "
                f"head_sha={has_head_sha}"
            )

        all_inline_comments.extend(inline_comments)

        # Early stop: if 3 consecutive MRs with inline comments confirm all fields
        if inline_comments and has_resolved and has_old_path and has_new_line and has_head_sha:
            consecutive_confirmed += 1
            if consecutive_confirmed >= 3 and not fields_confirmed:
                fields_confirmed = True
                print(
                    f"\n  ** All fields confirmed after {consecutive_confirmed} MRs "
                    f"with inline comments. Continuing to gather volume data. **\n"
                )
        else:
            consecutive_confirmed = 0

    print(f"\nMRs with inline comments: {mrs_with_inline}/{len(mrs)}")
    print(f"Total inline comments found: {len(all_inline_comments)}")
    return all_inline_comments


def phase3_check_commit_reachability(inline_comments: list[dict]) -> None:
    """Phase 3: Check if comment-time commit SHAs are still accessible."""
    print("\n=== Phase 3: Commit SHA reachability ===\n")

    # Collect unique head_sha values
    unique_shas = set()
    for comment in inline_comments:
        sha = comment.get("position", {}).get("head_sha")
        if sha:
            unique_shas.add(sha)

    if not unique_shas:
        print("  No head_sha values found in inline comments.")
        return

    print(f"  Found {len(unique_shas)} unique head_sha values to check.\n")

    accessible = 0
    not_found = 0
    checked = 0

    for sha in list(unique_shas)[:10]:  # Check up to 10
        resp = api_get(f"/projects/{PROJECT_ID}/repository/commits/{sha}")
        checked += 1

        if resp.status_code == 200:
            accessible += 1
            print(f"  {sha[:12]}: accessible (200)")
        elif resp.status_code == 404:
            not_found += 1
            print(f"  {sha[:12]}: NOT FOUND (404)")
        else:
            print(f"  {sha[:12]}: unexpected status {resp.status_code}")

        # Early stop: clear signal after 5 checks
        if checked >= 5:
            if accessible == checked:
                print(f"\n  ** All {checked} SHAs accessible. Stopping early. **")
                break
            if not_found >= 2:
                print(f"\n  ** {not_found} SHAs missing. Stopping early. **")
                break

    print(f"\n  Accessible: {accessible}/{checked}")
    print(f"  Not found: {not_found}/{checked}")


def phase4_check_local_checkoutability(
    mrs: list[dict], inline_comments: list[dict]
) -> None:
    """Phase 4: Can we git-fetch and git-checkout the comment-time commits?

    For the experiment, the reviewer agent needs full local repo access at the
    exact commit when the review comment was left. This requires that the
    commit SHA is reachable via git (not just the API). Two checks:

    1. Do MR refs (refs/merge-requests/:iid/head) resolve via the API?
       If so, git fetch origin refs/merge-requests/:iid/head should work.
    2. What refs contain each head_sha? If none, the commit is dangling and
       won't be in a clone even with --mirror.
    """
    print("\n=== Phase 4: Local checkout feasibility ===\n")

    # Check 1: Do MR refs resolve?
    # Use the commits endpoint with ref_name to see if the MR ref is valid
    print("  --- Check 1: MR ref resolution ---\n")

    # Pick squash-merged MRs that had inline comments
    squash_mrs_with_comments = [
        mr for mr in mrs
        if mr.get("squash_commit_sha")
        and any(
            c.get("noteable_iid") == mr["iid"]
            or True  # we don't have noteable_iid; just check all squash MRs
            for c in inline_comments[:1]
        )
    ][:3]

    for mr in squash_mrs_with_comments:
        iid = mr["iid"]
        # Try to get the tip commit of the MR ref
        resp = api_get(
            f"/projects/{PROJECT_ID}/repository/commits",
            params={"ref_name": f"refs/merge-requests/{iid}/head", "per_page": 1},
        )
        if resp.status_code == 200 and resp.json():
            tip = resp.json()[0]
            print(
                f"  !{iid}: MR ref resolves, tip={tip['id'][:12]}"
            )
        elif resp.status_code == 200 and not resp.json():
            print(f"  !{iid}: MR ref returned empty (ref may not exist)")
        else:
            print(f"  !{iid}: MR ref check returned {resp.status_code}")

    # Check 2: What refs contain each head_sha?
    # GET /projects/:id/repository/commits/:sha/refs tells us which
    # branches/tags contain the commit. If empty, it's dangling.
    print("\n  --- Check 2: Refs containing comment head_sha values ---\n")

    unique_shas = set()
    for comment in inline_comments:
        sha = comment.get("position", {}).get("head_sha")
        if sha:
            unique_shas.add(sha)

    reachable = 0
    dangling = 0
    checked = 0

    for sha in list(unique_shas)[:5]:
        resp = api_get(f"/projects/{PROJECT_ID}/repository/commits/{sha}/refs")
        checked += 1

        if resp.status_code == 200:
            refs = resp.json()
            ref_names = [r["name"] for r in refs[:5]]
            if refs:
                reachable += 1
                print(f"  {sha[:12]}: reachable from {len(refs)} ref(s): {ref_names}")
            else:
                dangling += 1
                print(f"  {sha[:12]}: DANGLING (no refs contain this commit)")
        else:
            print(f"  {sha[:12]}: refs check returned {resp.status_code}")

    print(f"\n  Reachable from refs: {reachable}/{checked}")
    print(f"  Dangling (no refs): {dangling}/{checked}")

    if dangling > 0:
        print(
            "\n  ** WARNING: Dangling commits cannot be git-fetched normally. **"
            "\n  ** Squash-merged MRs may require MR-specific ref fetching. **"
        )
    if reachable == checked:
        print(
            "\n  ** All checked SHAs are reachable from at least one ref. **"
            "\n  ** Local git checkout should work. **"
        )


def print_summary(mrs: list[dict], inline_comments: list[dict]) -> None:
    """Print a summary block suitable for slide content."""
    squash_count = sum(1 for mr in mrs if mr.get("squash_commit_sha"))

    print("\n" + "=" * 60)
    print("SUMMARY: GitLab API Spike Results")
    print("=" * 60)
    print(f"Project: gitlab-org/gitlab (ID {PROJECT_ID})")
    print(f"MRs sampled: {len(mrs)}")
    print(f"Squash-merged: {squash_count}/{len(mrs)} ({100*squash_count//len(mrs)}%)")
    print(f"Total inline review comments: {len(inline_comments)}")

    # Field presence summary
    if inline_comments:
        sample = inline_comments[0]
        pos = sample.get("position", {})
        print(f"\nKey fields present in inline comments:")
        print(f"  position.old_path: {bool(pos.get('old_path'))}")
        print(f"  position.new_path: {bool(pos.get('new_path'))}")
        print(f"  position.new_line: {pos.get('new_line') is not None}")
        print(f"  position.old_line: {pos.get('old_line') is not None}")
        print(f"  position.head_sha: {bool(pos.get('head_sha'))}")
        print(f"  position.base_sha: {bool(pos.get('base_sha'))}")
        print(f"  position.start_sha: {bool(pos.get('start_sha'))}")
        print(f"  resolved (on note): {'resolved' in sample}")
        print(f"  body length (sample): {len(sample.get('body', ''))}")

    print(f"\nTotal API calls: {api_call_count}")
    print("=" * 60)


def main() -> None:
    print("GitLab API Spike: Validating data fields for code review experiment")
    print(f"Target: gitlab-org/gitlab (project ID {PROJECT_ID})")

    mrs = phase1_fetch_mrs()
    inline_comments = phase2_fetch_discussions(mrs)

    if inline_comments:
        phase3_check_commit_reachability(inline_comments)
        phase4_check_local_checkoutability(mrs, inline_comments)
    else:
        print("\nNo inline comments found. Skipping Phases 3 and 4.")

    print_summary(mrs, inline_comments)


if __name__ == "__main__":
    main()
