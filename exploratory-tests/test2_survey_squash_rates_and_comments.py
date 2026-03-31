"""Survey GitLab projects for experiment viability.

Checks squash-merge rates and inline review comment volumes across
candidate projects to find those suitable for the code review experiment.

Requirements for viable projects:
- Low squash-merge rate (so comment-time commit SHAs are reachable via git)
- Sufficient inline review comment volume (target: 500+ substantive threads)

Rate limiting strategy:
- gitlab.com (authenticated): 1.0s between calls (limit is 300 req/min)
- Other instances (unauthenticated): 7.0s between calls (limit is 10 req/min)
- 5s pause between projects
- Target: never exceed 50% of rate limit
"""

import json
import os
import sys
import time

# Ensure print output is not buffered (visible immediately when piped/redirected)
sys.stdout.reconfigure(line_buffering=True)
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(REPO_ROOT / ".env")

GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN", "")
DATA_DIR = SCRIPT_DIR / "data" / "test2_survey_squash_rates_and_comments"

# Rate limiting
AUTHENTICATED_DELAY = 1.0    # gitlab.com: 300 req/min limit
UNAUTHENTICATED_DELAY = 7.0  # other instances: 10 req/min limit
PROJECT_PAUSE = 5             # seconds between projects

# Sampling parameters
MRS_TO_SAMPLE = 20            # merged MRs to fetch per project
MRS_TO_CHECK_COMMENTS = 5     # non-squash MRs to fetch discussions for

api_call_count = 0
start_time = 0.0


# Candidates: (instance_url, project_path, display_name)
# Focused on Python, JavaScript, and TypeScript projects.
CANDIDATES = [
    # --- Python (gitlab.com, authenticated) ---
    ("https://gitlab.com", "volian/nala", "Nala"),                              # Python apt frontend, 921 stars
    ("https://gitlab.com", "mayan-edms/mayan-edms", "Mayan EDMS"),             # Django DMS, 666 stars
    ("https://gitlab.com", "fdroid/fdroidserver", "F-Droid Server"),            # Python build tools, 434 stars
    ("https://gitlab.com", "duplicity/duplicity", "Duplicity"),                 # Python backup, 284 stars
    ("https://gitlab.com", "crafty-controller/crafty-4", "Crafty Controller"),  # Python, 213 stars
    ("https://gitlab.com", "woob/woob", "Woob"),                               # Python scraping, 136 stars
    ("https://gitlab.com", "fdroid/repomaker", "F-Droid Repomaker"),            # Django, 124 stars
    ("https://gitlab.com", "mailman/mailman", "GNU Mailman"),                   # Python email
    ("https://gitlab.com", "inkscape/extensions", "Inkscape Extensions"),       # Python SVG, 67 stars
    # --- JavaScript / TypeScript (gitlab.com, authenticated) ---
    ("https://gitlab.com", "gitlab-org/gitlab-ui", "GitLab UI"),                # Vue.js component library
    ("https://gitlab.com", "gitlab-org/gitlab-vscode-extension", "GitLab VS Code Ext"),  # TypeScript
    ("https://gitlab.com", "baserow/baserow", "Baserow"),                       # Python + Vue.js (Airtable alt)
    ("https://gitlab.com", "dokos/dokos", "Dokos"),                             # Python + JS (ERPNext fork)
    # --- Mixed but large JS/Vue frontend (gitlab.com, authenticated) ---
    ("https://gitlab.com", "gitlab-org/gitlab", "GitLab CE/EE"),                # Ruby + Vue.js/JS
]


@dataclass
class ProjectResult:
    instance: str
    path: str
    name: str
    project_id: int | None = None
    stars: int = 0
    merge_method: str = ""
    total_merged_mrs: int = 0  # from X-Total header
    sampled_mrs: int = 0
    squash_merged: int = 0
    non_squash_merged: int = 0
    non_squash_mrs_checked: int = 0
    inline_comments: int = 0
    general_comments: int = 0
    error: str | None = None


def project_slug(path: str) -> str:
    """Convert project path to a filesystem-safe directory name."""
    return path.replace("/", "--")


def is_authenticated(instance_url: str) -> bool:
    """Check if we have a token for this instance (gitlab.com only)."""
    return (
        urllib.parse.urlparse(instance_url).hostname == "gitlab.com"
        and bool(GITLAB_TOKEN)
    )


def api_get(
    instance_url: str, path: str, params: dict | None = None
) -> httpx.Response:
    """GET request to a GitLab instance API with rate limiting and logging."""
    global api_call_count

    delay = AUTHENTICATED_DELAY if is_authenticated(instance_url) else UNAUTHENTICATED_DELAY
    time.sleep(delay)

    api_call_count += 1

    url = f"{instance_url}/api/v4{path}"
    headers = {}
    if is_authenticated(instance_url):
        headers["PRIVATE-TOKEN"] = GITLAB_TOKEN

    resp = httpx.get(url, headers=headers, params=params or {}, timeout=30)
    return resp


def save_json(data: object, filename: str) -> None:
    """Save data as JSON to the survey data directory."""
    filepath = DATA_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(data, indent=2, default=str))


def log(msg: str) -> None:
    """Print a timestamped log message."""
    elapsed = time.time() - start_time
    minutes, seconds = divmod(int(elapsed), 60)
    print(f"  [{minutes:02d}:{seconds:02d}] {msg}")


def survey_project(
    instance_url: str, project_path: str, name: str
) -> ProjectResult:
    """Survey a single project for squash rate and comment volume."""
    result = ProjectResult(instance=instance_url, path=project_path, name=name)
    slug = project_slug(project_path)

    # Step 1: Look up project by URL-encoded path
    log("Looking up project...")
    encoded_path = urllib.parse.quote(project_path, safe="")
    resp = api_get(instance_url, f"/projects/{encoded_path}")
    if resp.status_code != 200:
        result.error = f"Project lookup failed: HTTP {resp.status_code}"
        return result

    project = resp.json()
    result.project_id = project["id"]
    result.stars = project.get("star_count", 0)
    result.merge_method = project.get("merge_method", "unknown")
    save_json(project, f"{slug}/project.json")

    # Step 2: Fetch recent merged MRs
    log(f"Fetching {MRS_TO_SAMPLE} recent merged MRs...")
    resp = api_get(
        instance_url,
        f"/projects/{result.project_id}/merge_requests",
        {
            "state": "merged",
            "order_by": "updated_at",
            "sort": "desc",
            "per_page": MRS_TO_SAMPLE,
        },
    )
    if resp.status_code != 200:
        result.error = f"MR list failed: HTTP {resp.status_code}"
        return result

    mrs = resp.json()
    result.sampled_mrs = len(mrs)
    result.total_merged_mrs = int(resp.headers.get("x-total", 0))
    save_json(mrs, f"{slug}/merge_requests.json")

    # Count squash vs non-squash
    for mr in mrs:
        if mr.get("squash_commit_sha"):
            result.squash_merged += 1
        else:
            result.non_squash_merged += 1

    squash_pct = (
        result.squash_merged / result.sampled_mrs * 100
        if result.sampled_mrs
        else 0
    )
    log(
        f"Squash rate: {result.squash_merged}/{result.sampled_mrs}"
        f" ({squash_pct:.0f}%)"
    )

    # Step 3: For up to N non-squash MRs, fetch discussions and count comments
    non_squash = [mr for mr in mrs if not mr.get("squash_commit_sha")]
    to_check = non_squash[:MRS_TO_CHECK_COMMENTS]

    if not to_check:
        log("No non-squash MRs to check for comments, skipping discussions.")
    else:
        log(f"Fetching discussions for {len(to_check)} non-squash MRs...")

    for j, mr in enumerate(to_check):
        iid = mr["iid"]
        log(f"  Discussion {j + 1}/{len(to_check)}: MR !{iid}")
        resp = api_get(
            instance_url,
            f"/projects/{result.project_id}/merge_requests/{iid}/discussions",
            {"per_page": 100},
        )
        if resp.status_code != 200:
            continue

        discussions = resp.json()
        result.non_squash_mrs_checked += 1
        save_json(discussions, f"{slug}/discussions/{iid}.json")

        for disc in discussions:
            for note in disc.get("notes", []):
                if note.get("system", False):
                    continue
                if note.get("type") == "DiffNote":
                    result.inline_comments += 1
                else:
                    result.general_comments += 1

    log("Done.")
    return result


def print_project_summary(result: ProjectResult) -> None:
    """Print summary for a single project after surveying it."""
    if result.error:
        print(f"\n  ERROR: {result.error}")
        return

    squash_pct = (
        result.squash_merged / result.sampled_mrs * 100
        if result.sampled_mrs
        else 0
    )
    avg_inline = (
        result.inline_comments / result.non_squash_mrs_checked
        if result.non_squash_mrs_checked
        else 0
    )

    print(f"\n  Project ID: {result.project_id}  Stars: {result.stars}")
    print(f"  Merge method: {result.merge_method}")
    print(f"  Total merged MRs (from header): {result.total_merged_mrs}")
    print(
        f"  Squash rate: {result.squash_merged}/{result.sampled_mrs}"
        f" ({squash_pct:.0f}%)"
    )
    print(
        f"  Non-squash MRs in sample: {result.non_squash_merged}/{result.sampled_mrs}"
    )
    print(f"  Non-squash MRs checked for comments: {result.non_squash_mrs_checked}")
    if result.non_squash_mrs_checked:
        print(
            f"  Inline comments: {result.inline_comments}"
            f" (avg {avg_inline:.1f}/MR)"
        )
        print(f"  General comments: {result.general_comments}")
    elif result.non_squash_merged == 0:
        print("  (no non-squash MRs to check for comments)")


def build_final_comparison(results: list[ProjectResult]) -> str:
    """Build the final comparison table as a string."""
    lines: list[str] = []
    lines.append(f"{'=' * 78}")
    lines.append("FINAL COMPARISON (sorted by squash rate, ascending)")
    lines.append(f"{'=' * 78}\n")

    valid = [r for r in results if not r.error and r.sampled_mrs > 0]
    valid.sort(key=lambda r: r.squash_merged / r.sampled_mrs)

    lines.append(
        f"  {'Project':<22s}  {'Squash':>7s}  {'Non-sq':>7s}  "
        f"{'Inl/MR':>6s}  {'Stars':>6s}  {'MRs':>7s}  Instance"
    )
    lines.append(
        f"  {'-' * 22}  {'-' * 7}  {'-' * 7}  "
        f"{'-' * 6}  {'-' * 6}  {'-' * 7}  {'-' * 28}"
    )

    for r in valid:
        squash_pct = r.squash_merged / r.sampled_mrs * 100
        avg_inline = (
            r.inline_comments / r.non_squash_mrs_checked
            if r.non_squash_mrs_checked
            else 0
        )
        host = urllib.parse.urlparse(r.instance).hostname
        lines.append(
            f"  {r.name:<22s}  {squash_pct:6.0f}%  "
            f"{r.non_squash_merged:3d}/{r.sampled_mrs:<2d}  "
            f"{avg_inline:6.1f}  {r.stars:6d}  {r.total_merged_mrs:7d}  {host}"
        )

    failed = [r for r in results if r.error]
    if failed:
        lines.append(f"\n  Failed ({len(failed)}):")
        for r in failed:
            lines.append(f"    {r.name}: {r.error}")

    elapsed = time.time() - start_time
    lines.append(f"\n  Total API calls: {api_call_count}")
    lines.append(f"  Elapsed: {elapsed:.0f}s ({elapsed / 60:.1f} min)")
    if elapsed > 0:
        lines.append(
            f"  Average rate: {api_call_count / (elapsed / 60):.1f} calls/min"
        )

    return "\n".join(lines)


def main() -> None:
    global start_time
    start_time = time.time()

    print("GitLab Project Survey: Experiment viability check")
    print(
        f"Candidates: {len(CANDIDATES)} projects across "
        f"{len(set(c[0] for c in CANDIDATES))} instances"
    )
    print(
        f"Sampling {MRS_TO_SAMPLE} MRs per project, "
        f"checking discussions on up to {MRS_TO_CHECK_COMMENTS} non-squash MRs\n"
    )

    if not GITLAB_TOKEN:
        print(
            "WARNING: GITLAB_TOKEN not set. "
            "gitlab.com requests will be unauthenticated (10 req/min limit).\n"
        )

    results: list[ProjectResult] = []
    total = len(CANDIDATES)
    project_times: list[float] = []

    for i, (instance, path, name) in enumerate(CANDIDATES):
        pct = i / total * 100
        eta_str = ""
        if project_times:
            avg = sum(project_times) / len(project_times)
            remaining = avg * (total - i)
            eta_min, eta_sec = divmod(int(remaining), 60)
            eta_str = f"  ETA: ~{eta_min}m{eta_sec:02d}s"

        print(f"\n{'=' * 60}")
        print(f"[{i + 1}/{total}] {name}  ({pct:.0f}% complete){eta_str}")
        print(f"  {instance}/{path}")
        print(f"{'=' * 60}")

        project_start = time.time()
        try:
            result = survey_project(instance, path, name)
        except httpx.TimeoutException:
            result = ProjectResult(
                instance=instance, path=path, name=name,
                error="Request timed out",
            )
        except httpx.HTTPError as e:
            result = ProjectResult(
                instance=instance, path=path, name=name,
                error=f"HTTP error: {e}",
            )

        project_times.append(time.time() - project_start)
        results.append(result)
        print_project_summary(result)

        # Save per-project result
        slug = project_slug(path)
        save_json(asdict(result), f"{slug}/result.json")

        if i < total - 1:
            print(f"\n  Pausing {PROJECT_PAUSE}s...")
            time.sleep(PROJECT_PAUSE)

    # Save combined results
    save_json([asdict(r) for r in results], "all_results.json")

    # Print and save final comparison
    summary = build_final_comparison(results)
    print(f"\n\n{summary}")
    summary_path = DATA_DIR / "summary.txt"
    summary_path.write_text(summary + "\n")


if __name__ == "__main__":
    main()
