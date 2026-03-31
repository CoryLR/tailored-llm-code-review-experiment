"""Systematic survey of GitHub and GitLab projects for experiment viability.

Discovers top projects by star count on both platforms, then surveys each
for merge strategy and inline review comment volume. Walks through projects
in descending star order per language until TARGET_VIABLE viable candidates
are found, or MAX_SURVEYED repos have been checked.

Viability criteria:
- Non-squash merge rate >= 25% of sampled PRs/MRs
- Inline review thread volume >= 0.5 threads per non-squash PR/MR
- At least 400 merged PRs/MRs

Selection constraint: at least 1 viable project must come from GitLab.

Rate limiting: uses a conservative 3-second delay between ALL API calls
on both platforms, staying well below both GitHub's 5,000 points/hour
and GitLab's 300 requests/minute limits (roughly 7% and 24% respectively).
"""

import json
import os
import sys
import time
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass

sys.stdout.reconfigure(line_buffering=True)

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent if not True else None  # type: ignore

# Fix the import
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(REPO_ROOT / ".env")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN", "")
DATA_DIR = SCRIPT_DIR / "data" / "test3_survey_projects"

# Rate limiting: conservative 3-second delay for ALL API calls on both
# platforms. This keeps us at ~20 req/min, which is 24% of GitHub's limit
# (5,000 pts/hr = 83/min) and 7% of GitLab's limit (300 req/min).
DELAY = 2.0

# Sampling
PRS_TO_SAMPLE = 20            # recent merged PRs/MRs to fetch per project
MRS_TO_CHECK_COMMENTS = 5     # GitLab: non-squash MRs to fetch discussions for

# Discovery and viability
LANGUAGES = ["Python", "JavaScript", "TypeScript"]
PAGE_SIZE = 20                # repos per search API page
TARGET_VIABLE_PER_PLATFORM = 5  # stop a platform after finding this many viable
MAX_SURVEYED_PER_PLATFORM = 200 # safety cap per platform
MIN_MERGED = 400              # minimum merged PRs/MRs to be worth surveying
MIN_MERGE_COMMIT_RATE = 0.25  # at least 25% non-squash
MIN_AVG_REVIEW_THREADS = 0.5  # at least 0.5 review threads per non-squash PR/MR

# Platform enable flags: set to False to skip a platform
ENABLE_GITHUB = True
ENABLE_GITLAB = True

# Trial mode: limits scope for quick testing
TRIAL_MODE = False

# ---------------------------------------------------------------------------
# End of configuration
# ---------------------------------------------------------------------------

api_call_count = 0
start_time = 0.0


@dataclass
class ProjectResult:
    platform: str              # "github" or "gitlab"
    owner: str                 # GitHub owner or GitLab namespace
    name: str                  # repo name
    slug: str                  # "owner/name" for display
    stars: int = 0
    primary_language: str = ""
    total_merged: int = 0
    sampled: int = 0
    non_squash: int = 0        # merge commits (GitHub) or non-squash MRs (GitLab)
    squash: int = 0
    review_threads: int = 0    # inline review threads on non-squash PRs/MRs
    rate_limit_remaining: int = 0
    error: str | None = None

    @property
    def merge_commit_rate(self) -> float:
        return self.non_squash / self.sampled if self.sampled else 0

    @property
    def avg_review_threads(self) -> float:
        return self.review_threads / self.non_squash if self.non_squash else 0

    @property
    def estimated_total_threads(self) -> int:
        if self.merge_commit_rate == 0:
            return 0
        return int(
            self.avg_review_threads * self.total_merged * self.merge_commit_rate
        )


def save_json(data: object, filename: str) -> None:
    filepath = DATA_DIR / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(data, indent=2, default=str))


def log(msg: str) -> None:
    elapsed = time.time() - start_time
    minutes, seconds = divmod(int(elapsed), 60)
    print(f"  [{minutes:02d}:{seconds:02d}] {msg}")


def is_viable(result: ProjectResult) -> bool:
    if result.error or result.sampled == 0:
        return False
    return (
        result.merge_commit_rate >= MIN_MERGE_COMMIT_RATE
        and result.avg_review_threads >= MIN_AVG_REVIEW_THREADS
    )


# ---------------------------------------------------------------------------
# Platform strategies
# ---------------------------------------------------------------------------

class PlatformStrategy(ABC):
    """Abstract base for platform-specific discovery and survey logic."""

    @abstractmethod
    def discover_page(
        self, language: str, page_cursor: str | None,
    ) -> tuple[list[dict], str | None, bool]:
        """Fetch one page of repos sorted by stars descending.

        Returns: (repos, next_cursor, has_more)
        Each repo dict has: owner, name, stars, language, merged_prs,
                           is_fork, is_archived
        """
        ...

    @abstractmethod
    def survey_project(
        self, owner: str, name: str, language: str, description: str,
    ) -> ProjectResult:
        """Survey a single project for merge strategy and review volume."""
        ...


class GitHubStrategy(PlatformStrategy):

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

    QUERY_SURVEY = """
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

    def _graphql(self, query: str, variables: dict | None = None) -> dict:
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
            msgs = "; ".join(e.get("message", "?") for e in data["errors"])
            raise RuntimeError(f"GraphQL errors: {msgs}")
        return data["data"]

    def discover_page(
        self, language: str, page_cursor: str | None,
    ) -> tuple[list[dict], str | None, bool]:
        # Search all target languages at once, sorted by stars descending.
        # GitHub search supports multiple language qualifiers with OR logic
        # when space-separated in the query. Client-side filtering handles
        # any non-target languages that slip through.
        lang_filter = " ".join(
            f"language:{lang}" for lang in LANGUAGES
        )
        query_str = f"{lang_filter} stars:>100 sort:stars-desc is:public"
        variables: dict = {"queryStr": query_str, "count": PAGE_SIZE}
        if page_cursor:
            variables["cursor"] = page_cursor

        data = self._graphql(self.QUERY_DISCOVER, variables)
        search = data["search"]
        rate = data.get("rateLimit", {})

        repos = []
        for node in search["nodes"]:
            repos.append({
                "owner": node["owner"]["login"],
                "name": node["name"],
                "stars": node["stargazerCount"],
                "language": (node.get("primaryLanguage") or {}).get(
                    "name", "?"
                ),
                "merged_prs": node["pullRequests"]["totalCount"],
                "is_fork": node.get("isFork", False),
                "is_archived": node.get("isArchived", False),
            })

        next_cursor = search["pageInfo"]["endCursor"]
        has_more = search["pageInfo"]["hasNextPage"]
        log(
            f"GitHub discover: got {len(repos)} repos."
            f" Rate limit: {rate.get('remaining', '?')} remaining."
        )
        return repos, next_cursor, has_more

    def survey_project(
        self, owner: str, name: str, language: str, description: str,
    ) -> ProjectResult:
        result = ProjectResult(
            platform="github", owner=owner, name=name,
            slug=f"{owner}/{name}",
        )

        try:
            data = self._graphql(self.QUERY_SURVEY, {
                "owner": owner, "name": name, "prCount": PRS_TO_SAMPLE,
            })
        except (httpx.HTTPError, RuntimeError) as e:
            result.error = str(e)
            return result

        repo = data["repository"]
        result.stars = repo["stargazerCount"]
        result.primary_language = (
            (repo.get("primaryLanguage") or {}).get("name", "?")
        )

        # Filter: only Python, JavaScript, or TypeScript projects
        allowed = {"Python", "JavaScript", "TypeScript"}
        if result.primary_language not in allowed:
            result.error = (
                f"Language '{result.primary_language}' not in"
                f" {sorted(allowed)}"
            )
            return result

        result.total_merged = repo["pullRequests"]["totalCount"]

        rate = data.get("rateLimit", {})
        result.rate_limit_remaining = rate.get("remaining", 0)

        prs = repo["recentPRs"]["nodes"]
        result.sampled = len(prs)

        for pr in prs:
            mc = pr.get("mergeCommit")
            if not mc:
                continue
            parent_count = mc["parents"]["totalCount"]
            thread_count = pr["reviewThreads"]["totalCount"]
            if parent_count >= 2:
                result.non_squash += 1
                result.review_threads += thread_count
            else:
                result.squash += 1

        save_json(
            {"result": asdict(result), "raw_prs": prs},
            f"github--{owner}--{name}/result.json",
        )
        return result


class GitLabStrategy(PlatformStrategy):

    INSTANCE = "https://gitlab.com"

    def _api_get(
        self, path: str, params: dict | None = None,
    ) -> httpx.Response:
        global api_call_count
        time.sleep(DELAY)
        api_call_count += 1

        headers = {}
        if GITLAB_TOKEN:
            headers["PRIVATE-TOKEN"] = GITLAB_TOKEN

        resp = httpx.get(
            f"{self.INSTANCE}/api/v4{path}",
            headers=headers,
            params=params or {},
            timeout=30,
        )
        return resp

    def discover_page(
        self, language: str, page_cursor: str | None,
    ) -> tuple[list[dict], str | None, bool]:
        # GitLab uses page numbers, not cursors. page_cursor is the page number
        # as a string, or None for page 1.
        page_num = int(page_cursor) if page_cursor else 1

        # GitLab's with_programming_language filter is unreliable (returns
        # 500 errors on gitlab.com). Instead, we fetch top projects by star
        # count without a language filter. Language filtering happens
        # downstream: we skip projects whose detected language doesn't match,
        # but since we're iterating through ALL top-starred projects (not
        # per-language), we call discover_page once with language="" and
        # let the caller handle language matching.
        #
        # To avoid redundant pages across languages, this strategy ignores
        # the language parameter for discovery and returns all projects.
        # The main loop should call this with a single language pass.
        resp = self._api_get("/projects", {
            "order_by": "star_count",
            "sort": "desc",
            "visibility": "public",
            "per_page": PAGE_SIZE,
            "page": page_num,
        })

        if resp.status_code != 200:
            log(f"GitLab discover error: HTTP {resp.status_code}")
            return [], None, False

        projects = resp.json()

        repos = []
        for proj in projects:
            path_parts = proj.get("path_with_namespace", "").split("/")
            owner = "/".join(path_parts[:-1]) if len(path_parts) > 1 else ""
            name_part = path_parts[-1] if path_parts else ""

            repos.append({
                "owner": owner,
                "name": name_part,
                "stars": proj.get("star_count", 0),
                "language": language,
                "merged_prs": 0,
                "is_fork": bool(proj.get("forked_from_project")),
                "is_archived": proj.get("archived", False),
                "project_id": proj["id"],
                "path_with_namespace": proj.get("path_with_namespace", ""),
            })

        # GitLab may not return x-total-pages for large result sets.
        # Assume more pages exist if we got a full page of results.
        has_more = len(projects) >= PAGE_SIZE
        next_cursor = str(page_num + 1) if has_more else None
        log(
            f"GitLab discover: got {len(repos)} repos (page {page_num})."
        )
        return repos, next_cursor, has_more

    def survey_project(
        self, owner: str, name: str, language: str, description: str,
    ) -> ProjectResult:
        full_path = f"{owner}/{name}" if owner else name
        result = ProjectResult(
            platform="gitlab", owner=owner, name=name,
            slug=full_path,
        )
        slug_safe = full_path.replace("/", "--")

        # Step 1: Look up project
        encoded_path = urllib.parse.quote(full_path, safe="")
        resp = self._api_get(f"/projects/{encoded_path}")
        if resp.status_code != 200:
            result.error = f"Project lookup failed: HTTP {resp.status_code}"
            return result

        project = resp.json()
        result.stars = project.get("star_count", 0)
        save_json(project, f"gitlab--{slug_safe}/project.json")
        project_id = project["id"]

        # Step 1b: Check primary language via languages endpoint
        resp = self._api_get(f"/projects/{project_id}/languages")
        if resp.status_code == 200:
            lang_data = resp.json()  # e.g. {"Python": 85.2, "Shell": 14.8}
            if lang_data:
                primary = max(lang_data, key=lang_data.get)
                result.primary_language = primary
            else:
                result.primary_language = "unknown"
        else:
            result.primary_language = "unknown"

        # Filter: only Python, JavaScript, or TypeScript projects
        allowed = {"python", "javascript", "typescript"}
        if result.primary_language.lower() not in allowed:
            result.error = (
                f"Language '{result.primary_language}' not in"
                f" {sorted(allowed)}"
            )
            return result

        # Step 2: Fetch recent merged MRs
        resp = self._api_get(
            f"/projects/{project_id}/merge_requests",
            {
                "state": "merged",
                "order_by": "updated_at",
                "sort": "desc",
                "per_page": PRS_TO_SAMPLE,
            },
        )
        if resp.status_code != 200:
            result.error = f"MR list failed: HTTP {resp.status_code}"
            return result

        mrs = resp.json()
        result.sampled = len(mrs)
        # GitLab's x-total header returns 0 for large projects (known bug).
        # If we got a full page of results, the project clearly has many MRs;
        # use a conservative lower bound instead.
        x_total = int(resp.headers.get("x-total", 0))
        if x_total > 0:
            result.total_merged = x_total
        elif result.sampled >= PRS_TO_SAMPLE:
            result.total_merged = PRS_TO_SAMPLE  # conservative lower bound
        else:
            result.total_merged = result.sampled
        save_json(mrs, f"gitlab--{slug_safe}/merge_requests.json")

        # Count squash vs non-squash
        non_squash_mrs = []
        for mr in mrs:
            if mr.get("squash_commit_sha"):
                result.squash += 1
            else:
                result.non_squash += 1
                non_squash_mrs.append(mr)

        # Step 3: For non-squash MRs, fetch discussions and count inline comments
        to_check = non_squash_mrs[:MRS_TO_CHECK_COMMENTS]
        for j, mr in enumerate(to_check):
            iid = mr["iid"]
            log(f"  Discussion {j + 1}/{len(to_check)}: MR !{iid}")
            resp = self._api_get(
                f"/projects/{project_id}/merge_requests/{iid}/discussions",
                {"per_page": 100},
            )
            if resp.status_code != 200:
                continue

            discussions = resp.json()
            save_json(discussions, f"gitlab--{slug_safe}/discussions/{iid}.json")

            for disc in discussions:
                for note in disc.get("notes", []):
                    if note.get("system", False):
                        continue
                    if note.get("type") == "DiffNote":
                        result.review_threads += 1

        save_json(asdict(result), f"gitlab--{slug_safe}/result.json")
        return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def print_result(result: ProjectResult) -> None:
    if result.error:
        print(f"\n  ERROR: {result.error}")
        return

    merge_pct = result.merge_commit_rate * 100
    squash_pct = result.squash / result.sampled * 100 if result.sampled else 0
    avg_threads = result.avg_review_threads
    estimated = result.estimated_total_threads

    print(f"\n  Platform: {result.platform}  Stars: {result.stars:,}"
          f"  Language: {result.primary_language}")
    print(f"  Total merged: {result.total_merged:,}")
    print(f"  Non-squash: {result.non_squash}/{result.sampled}"
          f" ({merge_pct:.0f}%)")
    print(f"  Squash: {result.squash}/{result.sampled}"
          f" ({squash_pct:.0f}%)")
    if result.non_squash:
        print(f"  Review threads (non-squash only): {result.review_threads}"
              f" (avg {avg_threads:.1f}/PR)")
        print(f"  Estimated total threads: ~{estimated:,}")


def build_summary(results: list[ProjectResult]) -> str:
    lines: list[str] = []
    lines.append("=" * 95)
    lines.append("FINAL COMPARISON (sorted by non-squash rate, descending)")
    lines.append("=" * 95)
    lines.append("")

    valid = [r for r in results if not r.error and r.sampled > 0]
    valid.sort(key=lambda r: r.merge_commit_rate, reverse=True)

    lines.append(
        f"  {'Project':<35s} {'Plat':>4s}  {'NSquash%':>8s}  "
        f"{'Thrd/PR':>7s}  {'Stars':>8s}  {'Merged':>7s}  {'Est.Thrd':>8s}"
    )
    lines.append(
        f"  {'-'*35} {'-'*4}  {'-'*8}  {'-'*7}  {'-'*8}  {'-'*7}  {'-'*8}"
    )

    for r in valid:
        plat = "GH" if r.platform == "github" else "GL"
        lines.append(
            f"  {r.slug:<35s} {plat:>4s}  "
            f"{r.merge_commit_rate*100:7.0f}%  "
            f"{r.avg_review_threads:7.1f}  "
            f"{r.stars:>8,}  {r.total_merged:>7,}  "
            f"{r.estimated_total_threads:>8,}"
        )

    elapsed = time.time() - start_time
    lines.append(f"\n  Total API calls: {api_call_count}")
    lines.append(f"  Elapsed: {elapsed:.0f}s ({elapsed/60:.1f} min)")

    return "\n".join(lines)


def main() -> None:
    global start_time
    start_time = time.time()

    max_per_platform = MAX_SURVEYED_PER_PLATFORM
    target_per_platform = TARGET_VIABLE_PER_PLATFORM
    if TRIAL_MODE:
        max_per_platform = 5
        target_per_platform = 1
        print(
            f"TRIAL MODE: {max_per_platform} repos max per platform,"
            f" {target_per_platform} viable target per platform\n"
        )

    platforms_enabled = []
    if ENABLE_GITHUB:
        platforms_enabled.append("GitHub")
    if ENABLE_GITLAB:
        platforms_enabled.append("GitLab")

    print("Project Survey: Systematic experiment viability check")
    print(f"Platforms: {' + '.join(platforms_enabled)}")
    print(f"Languages: {', '.join(LANGUAGES)}")
    print(
        f"Target: {target_per_platform} viable repos per platform"
        f" (independent limits)"
    )
    print(
        f"Viable = {MIN_MERGE_COMMIT_RATE:.0%}+ non-squash rate"
        f" AND {MIN_AVG_REVIEW_THREADS}+ review threads per PR"
    )
    print(f"Delay: {DELAY}s between API calls")
    print(f"Safety cap: {max_per_platform} repos per platform")

    strategies: list[tuple[str, PlatformStrategy]] = []
    if ENABLE_GITHUB and GITHUB_TOKEN:
        strategies.append(("GitHub", GitHubStrategy()))
    elif ENABLE_GITHUB:
        print("\nWARNING: GitHub enabled but GITHUB_TOKEN not set. Skipping.")
    if ENABLE_GITLAB and GITLAB_TOKEN:
        strategies.append(("GitLab", GitLabStrategy()))
    elif ENABLE_GITLAB:
        print("\nWARNING: GitLab enabled but GITLAB_TOKEN not set. Skipping.")

    all_results: list[ProjectResult] = []
    all_viable: list[ProjectResult] = []
    seen: set[str] = set()

    for platform_name, strategy in strategies:
        platform_surveyed = 0
        platform_viable: list[ProjectResult] = []

        print(f"\n{'#' * 60}")
        print(f"# {platform_name} survey")
        print(f"{'#' * 60}")

        def platform_done() -> bool:
            return (
                len(platform_viable) >= target_per_platform
                or platform_surveyed >= max_per_platform
            )

        # Both platforms discover repos in descending star order across all
        # target languages at once. Language filtering happens client-side
        # (GitHub via primaryLanguage field, GitLab via languages API).
        cursor: str | None = None
        page = 0

        while not platform_done():
            page += 1
            print(f"\n--- {platform_name} page {page} ---")

            try:
                repos, cursor, has_more = strategy.discover_page(
                    "all", cursor,
                )
            except (httpx.HTTPError, RuntimeError) as e:
                print(f"  ERROR: {e}")
                break

            if not repos:
                break

            for repo in repos:
                if platform_done():
                    break

                owner = repo["owner"]
                name = repo["name"]
                slug = f"{platform_name.lower()}:{owner}/{name}"

                if slug in seen:
                    continue
                seen.add(slug)

                if repo.get("is_fork") or repo.get("is_archived"):
                    continue

                # For GitHub, we know merged PR count from discovery.
                # For GitLab, we don't; skip the pre-filter.
                if repo.get("merged_prs", 0) > 0:
                    if repo["merged_prs"] < MIN_MERGED:
                        continue

                stars = repo["stars"]
                lang = repo.get("language", "?")
                desc = f"{lang}, {stars:,} stars"

                platform_surveyed += 1
                print(
                    f"\n[{platform_name} #{platform_surveyed}]"
                    f" {owner}/{name} ({desc})"
                )

                result = strategy.survey_project(
                    owner, name, lang, desc,
                )
                all_results.append(result)
                print_result(result)

                # Skip if errored (e.g., wrong language)
                if result.error:
                    continue

                # Check total_merged post-survey
                if result.total_merged < MIN_MERGED:
                    log(
                        f"Skipping: only {result.total_merged}"
                        f" merged (need {MIN_MERGED}+)"
                    )
                    continue

                if is_viable(result):
                    platform_viable.append(result)
                    all_viable.append(result)
                    print(
                        f"  >>> VIABLE ({len(platform_viable)}"
                        f"/{target_per_platform}"
                        f" for {platform_name})"
                    )

            if not has_more:
                print(f"  No more {platform_name} results.")
                break

        print(
            f"\n{platform_name} complete:"
            f" {platform_surveyed} surveyed,"
            f" {len(platform_viable)} viable"
        )

    viable_results = all_viable

    # Save all results
    save_json([asdict(r) for r in all_results], "all_results.json")

    # Summary
    summary = build_summary(all_results)
    print(f"\n\n{summary}")

    summary_path = DATA_DIR / "summary.txt"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(summary + "\n")

    # Viable summary
    if viable_results:
        print(f"\n\nVIABLE CANDIDATES ({len(viable_results)} found):")
        for r in viable_results:
            print(
                f"  [{r.platform}] {r.slug}:"
                f" {r.merge_commit_rate*100:.0f}% non-squash,"
                f" {r.avg_review_threads:.1f} threads/PR,"
                f" {r.stars:,} stars"
            )
        # Check GitLab constraint
        gl_viable = [r for r in viable_results if r.platform == "gitlab"]
        if not gl_viable:
            print("\n  WARNING: No viable GitLab projects found."
                  " GitLab constraint not met.")
    else:
        total_surveyed = sum(
            1 for r in all_results if not r.error
        )
        print(
            f"\n\nNo viable candidates found after surveying"
            f" {total_surveyed} repos."
        )


if __name__ == "__main__":
    main()
