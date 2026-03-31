"""Survey GitHub projects for experiment viability.

Uses the GitHub GraphQL API to check merge strategies and inline review
comment volumes across candidate Python, JavaScript, and TypeScript
projects. Identifies projects suitable for the code review experiment.

Requirements for viable projects:
- Low squash/rebase-merge rate (merge commits with 2 parents preferred,
  so that PR branch commit SHAs are reachable in git history)
- Sufficient inline review thread volume (target: 500+ substantive threads)
- Active review culture with multiple distinct reviewers

Rate limiting: GitHub GraphQL allows 5,000 points/hour (authenticated).
Each query costs roughly 1 point per node requested. With a 1s delay
between calls and 1 call per project, a 14-project survey uses ~14
points total.
"""

import json
import os
import sys
import time

sys.stdout.reconfigure(line_buffering=True)
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(REPO_ROOT / ".env")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
DATA_DIR = SCRIPT_DIR / "data" / "test3_survey_github_projects"

# Rate limiting (GitHub GraphQL: 5,000 points/hour authenticated)
DELAY = 1.0  # seconds between API calls

# Sampling
PRS_TO_SAMPLE = 20  # recent merged PRs to fetch per project

# Candidate discovery: repos sorted by star count (descending) per language
# via GitHub search API. Surveys repos in descending star order until
# TARGET_VIABLE viable candidates are found, or MAX_SURVEYED repos have
# been checked.
DISCOVER_LANGUAGES = ["Python", "JavaScript", "TypeScript"]
DISCOVER_PAGE_SIZE = 20       # repos per search API page
TARGET_VIABLE = 3             # stop after finding this many viable repos total
MAX_SURVEYED = 200            # safety cap: never survey more than this many

# Trial mode: limits to 1 language and MAX_SURVEYED=5 for quick testing
TRIAL_MODE = False
# Minimum merged PRs to be worth surveying (filters out small/inactive repos)
MIN_MERGED_PRS_ESTIMATE = 400
# Viability thresholds: a repo is "viable" if it passes both
MIN_MERGE_COMMIT_RATE = 0.25  # at least 25% of sampled PRs are merge commits
MIN_AVG_REVIEW_THREADS = 0.5  # at least 0.5 review threads per merge-commit PR

# GraphQL query: discover repos by star count for a given language.
# Uses cursor-based pagination to walk through results in descending star order.
QUERY_DISCOVER = """
query($queryStr: String!, $count: Int!, $cursor: String) {
  search(query: $queryStr, type: REPOSITORY, first: $count, after: $cursor) {
    nodes {
      ... on Repository {
        owner { login }
        name
        stargazerCount
        primaryLanguage { name }
        pullRequests(states: MERGED) { totalCount }
        isFork
        isArchived
      }
    }
    pageInfo { hasNextPage endCursor }
  }
  rateLimit { remaining resetAt cost }
}
"""

# ---------------------------------------------------------------------------
# End of configuration
# ---------------------------------------------------------------------------

# GraphQL query: fetch repository info and recent merged PRs in one call.
# For each PR, gets merge commit parent count (to detect squash/rebase vs.
# true merge commits) and review thread count (inline comment volume).
QUERY_REPO_AND_PRS = """
query($owner: String!, $name: String!, $prCount: Int!) {
  repository(owner: $owner, name: $name) {
    stargazerCount
    primaryLanguage { name }
    pullRequests(states: MERGED) { totalCount }
    recentPRs: pullRequests(
      states: MERGED
      first: $prCount
      orderBy: {field: UPDATED_AT, direction: DESC}
    ) {
      nodes {
        number
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

api_call_count = 0
start_time = 0.0


@dataclass
class ProjectResult:
    owner: str
    name: str
    language_hint: str
    description: str
    stars: int = 0
    primary_language: str = ""
    total_merged_prs: int = 0
    sampled_prs: int = 0
    merge_commit_prs: int = 0      # 2-parent merge commits (good for us)
    squash_rebase_prs: int = 0     # 1-parent commits (squash or rebase)
    no_merge_commit_prs: int = 0   # missing merge commit data
    total_review_threads: int = 0  # across merge-commit PRs only
    rate_limit_remaining: int = 0
    rate_limit_cost: int = 0
    error: str | None = None


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
    owner: str, name: str, language_hint: str, description: str,
) -> ProjectResult:
    """Survey a single GitHub project for merge strategy and review volume."""
    result = ProjectResult(
        owner=owner,
        name=name,
        language_hint=language_hint,
        description=description,
    )
    slug = f"{owner}--{name}"

    log("Fetching repo info and recent PRs...")
    try:
        data = graphql(QUERY_REPO_AND_PRS, {
            "owner": owner,
            "name": name,
            "prCount": PRS_TO_SAMPLE,
        })
    except (httpx.HTTPError, RuntimeError) as e:
        result.error = str(e)
        return result

    repo = data["repository"]
    result.stars = repo["stargazerCount"]
    result.primary_language = (
        (repo.get("primaryLanguage") or {}).get("name", "?")
    )
    result.total_merged_prs = repo["pullRequests"]["totalCount"]

    rate = data.get("rateLimit", {})
    result.rate_limit_remaining = rate.get("remaining", 0)
    result.rate_limit_cost = rate.get("cost", 0)

    prs = repo["recentPRs"]["nodes"]
    result.sampled_prs = len(prs)

    for pr in prs:
        mc = pr.get("mergeCommit")
        if not mc:
            result.no_merge_commit_prs += 1
            continue

        parent_count = mc["parents"]["totalCount"]
        thread_count = pr["reviewThreads"]["totalCount"]

        if parent_count >= 2:
            # True merge commit: PR branch commits are preserved in history
            result.merge_commit_prs += 1
            result.total_review_threads += thread_count
        else:
            # Squash or rebase: original PR branch commits may be unreachable
            result.squash_rebase_prs += 1

    save_json(
        {"result": asdict(result), "raw_prs": prs},
        f"{slug}/result.json",
    )

    merge_pct = (
        result.merge_commit_prs / result.sampled_prs * 100
        if result.sampled_prs
        else 0
    )
    log(
        f"Merge-commit rate: {result.merge_commit_prs}/{result.sampled_prs}"
        f" ({merge_pct:.0f}%)"
    )
    log(
        f"Rate limit: {result.rate_limit_remaining} remaining,"
        f" cost {result.rate_limit_cost}"
    )

    return result


def print_project_summary(result: ProjectResult) -> None:
    """Print a summary for a single project after surveying it."""
    if result.error:
        print(f"\n  ERROR: {result.error}")
        return

    merge_pct = (
        result.merge_commit_prs / result.sampled_prs * 100
        if result.sampled_prs
        else 0
    )
    squash_pct = (
        result.squash_rebase_prs / result.sampled_prs * 100
        if result.sampled_prs
        else 0
    )
    avg_threads = (
        result.total_review_threads / result.merge_commit_prs
        if result.merge_commit_prs
        else 0
    )
    # Rough estimate: avg threads per merge-commit PR * total PRs * merge rate
    estimated_total = (
        int(avg_threads * result.total_merged_prs * (merge_pct / 100))
        if merge_pct > 0
        else 0
    )

    print(f"\n  Stars: {result.stars}  Language: {result.primary_language}")
    print(f"  Total merged PRs: {result.total_merged_prs}")
    print(
        f"  Merge commits (2+ parents): {result.merge_commit_prs}"
        f"/{result.sampled_prs} ({merge_pct:.0f}%)"
    )
    print(
        f"  Squash/rebase (1 parent): {result.squash_rebase_prs}"
        f"/{result.sampled_prs} ({squash_pct:.0f}%)"
    )
    if result.merge_commit_prs:
        print(
            f"  Review threads (merge-commit PRs only):"
            f" {result.total_review_threads}"
            f" (avg {avg_threads:.1f}/PR)"
        )
        print(f"  Estimated total review threads: ~{estimated_total}")


def build_final_comparison(results: list[ProjectResult]) -> str:
    """Build a final comparison summary as a string."""
    lines: list[str] = []
    lines.append("=" * 90)
    lines.append("FINAL COMPARISON (sorted by merge-commit rate, descending)")
    lines.append("=" * 90)
    lines.append("")

    valid = [r for r in results if not r.error and r.sampled_prs > 0]
    valid.sort(
        key=lambda r: r.merge_commit_prs / r.sampled_prs,
        reverse=True,
    )

    lines.append(
        f"  {'Project':<30s}  {'Lang':>4s}  {'Merge%':>6s}  "
        f"{'Thrd/PR':>7s}  {'Stars':>6s}  {'PRs':>7s}  {'Est.Threads':>11s}"
    )
    lines.append(
        f"  {'-' * 30}  {'-' * 4}  {'-' * 6}  "
        f"{'-' * 7}  {'-' * 6}  {'-' * 7}  {'-' * 11}"
    )

    for r in valid:
        merge_pct = r.merge_commit_prs / r.sampled_prs * 100
        avg_threads = (
            r.total_review_threads / r.merge_commit_prs
            if r.merge_commit_prs
            else 0
        )
        estimated = (
            int(avg_threads * r.total_merged_prs * (merge_pct / 100))
            if merge_pct > 0
            else 0
        )
        lang_short = r.primary_language[:4] if r.primary_language else "?"
        slug = f"{r.owner}/{r.name}"
        lines.append(
            f"  {slug:<30s}  {lang_short:>4s}  "
            f"{merge_pct:5.0f}%  {avg_threads:7.1f}  "
            f"{r.stars:6d}  {r.total_merged_prs:7d}  {estimated:>11d}"
        )

    failed = [r for r in results if r.error]
    if failed:
        lines.append(f"\n  Failed ({len(failed)}):")
        for r in failed:
            lines.append(f"    {r.owner}/{r.name}: {r.error}")

    elapsed = time.time() - start_time
    lines.append(f"\n  Total API calls: {api_call_count}")
    lines.append(f"  Elapsed: {elapsed:.0f}s ({elapsed / 60:.1f} min)")

    return "\n".join(lines)


def is_viable(result: ProjectResult) -> bool:
    """Check if a surveyed project meets viability thresholds."""
    if result.error or result.sampled_prs == 0:
        return False
    merge_rate = result.merge_commit_prs / result.sampled_prs
    avg_threads = (
        result.total_review_threads / result.merge_commit_prs
        if result.merge_commit_prs > 0
        else 0
    )
    return (
        merge_rate >= MIN_MERGE_COMMIT_RATE
        and avg_threads >= MIN_AVG_REVIEW_THREADS
    )


def main() -> None:
    global start_time, MAX_SURVEYED
    start_time = time.time()

    languages = DISCOVER_LANGUAGES
    max_surveyed = MAX_SURVEYED

    if TRIAL_MODE:
        languages = DISCOVER_LANGUAGES[:1]  # first language only
        max_surveyed = 5
        MAX_SURVEYED = max_surveyed
        print("TRIAL MODE: 1 language, 5 repos max\n")

    print("GitHub Project Survey: Systematic experiment viability check")
    print(f"Languages: {', '.join(languages)}")
    print(f"Target: {TARGET_VIABLE} viable repos (descending by star count)")
    print(
        f"Viable = {MIN_MERGE_COMMIT_RATE:.0%}+ merge-commit rate"
        f" AND {MIN_AVG_REVIEW_THREADS}+ review threads per PR"
    )
    print(f"Sampling {PRS_TO_SAMPLE} recent merged PRs per project")
    print(f"Safety cap: {max_surveyed} repos max")
    print()

    if not GITHUB_TOKEN:
        print(
            "ERROR: GITHUB_TOKEN not set."
            " GitHub GraphQL requires authentication."
        )
        sys.exit(1)

    all_results: list[ProjectResult] = []
    viable_results: list[ProjectResult] = []
    seen: set[str] = set()
    surveyed_count = 0

    for language in languages:
        if len(viable_results) >= TARGET_VIABLE:
            break
        if surveyed_count >= MAX_SURVEYED:
            break

        cursor: str | None = None
        page = 0

        while True:
            if len(viable_results) >= TARGET_VIABLE:
                break
            if surveyed_count >= MAX_SURVEYED:
                break

            page += 1
            query_str = (
                f"language:{language} stars:>100"
                " sort:stars-desc is:public"
            )

            print(f"\n--- {language} page {page} ---")
            try:
                variables: dict = {
                    "queryStr": query_str,
                    "count": DISCOVER_PAGE_SIZE,
                }
                if cursor:
                    variables["cursor"] = cursor
                data = graphql(QUERY_DISCOVER, variables)
            except (httpx.HTTPError, RuntimeError) as e:
                print(f"  ERROR fetching {language} page {page}: {e}")
                break

            search = data["search"]
            rate = data.get("rateLimit", {})
            nodes = search["nodes"]
            has_next = search["pageInfo"]["hasNextPage"]
            cursor = search["pageInfo"]["endCursor"]

            print(
                f"  Got {len(nodes)} repos."
                f" Rate limit: {rate.get('remaining', '?')} remaining."
            )

            if not nodes:
                break

            for repo in nodes:
                if len(viable_results) >= TARGET_VIABLE:
                    break
                if surveyed_count >= MAX_SURVEYED:
                    break

                owner = repo["owner"]["login"]
                name = repo["name"]
                slug = f"{owner}/{name}"

                if slug in seen:
                    continue
                seen.add(slug)

                if repo.get("isFork") or repo.get("isArchived"):
                    continue

                merged_prs = repo["pullRequests"]["totalCount"]
                if merged_prs < MIN_MERGED_PRS_ESTIMATE:
                    continue

                stars = repo["stargazerCount"]
                primary_lang = (repo.get("primaryLanguage") or {}).get(
                    "name", "?"
                )
                desc = (
                    f"{primary_lang}, {stars:,} stars,"
                    f" {merged_prs:,} merged PRs"
                )

                surveyed_count += 1
                print(
                    f"\n[{surveyed_count}] {slug}"
                    f" ({desc})"
                )

                result = survey_project(owner, name, language, desc)
                all_results.append(result)
                print_project_summary(result)

                if is_viable(result):
                    viable_results.append(result)
                    print(
                        f"  >>> VIABLE ({len(viable_results)}"
                        f"/{TARGET_VIABLE} found)"
                    )

            if not has_next:
                print(f"  No more {language} results.")
                break

    # Save all results
    save_json([asdict(r) for r in all_results], "all_results.json")

    # Print and save final comparison
    summary = build_final_comparison(all_results)
    print(f"\n\n{summary}")

    # Print viable summary
    if viable_results:
        print(f"\n\nVIABLE CANDIDATES ({len(viable_results)} found):")
        for r in viable_results:
            merge_pct = r.merge_commit_prs / r.sampled_prs * 100
            avg_threads = (
                r.total_review_threads / r.merge_commit_prs
                if r.merge_commit_prs > 0
                else 0
            )
            print(
                f"  {r.owner}/{r.name}: {merge_pct:.0f}% merge-commit,"
                f" {avg_threads:.1f} threads/PR,"
                f" {r.stars:,} stars"
            )
    else:
        print(
            f"\n\nNo viable candidates found after surveying"
            f" {surveyed_count} repos."
        )

    summary_path = DATA_DIR / "summary.txt"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(summary + "\n")


if __name__ == "__main__":
    main()
