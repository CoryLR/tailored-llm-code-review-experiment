# Finding 004: GitHub Project Survey

Mar 22, 2026. Script: `exploratory-tests/test3_survey_github_projects.py`.

Surveyed 15 Python/JS/TS projects on GitHub for merge-commit rates and inline review thread volume. This mirrors the GitLab survey (Finding 002) and uses the same viability criteria: non-squash merges, >1 inline review comment per PR, and 400+ merged PRs.

## Method

GitHub GraphQL API. For each project, fetched 20 recent merged PRs and checked merge commit parent count (2+ parents = merge commit, 1 parent = squash/rebase). Also counted review threads on merge-commit PRs only. 15 API calls total, 21 seconds elapsed.

## Results

Sorted by merge-commit rate (descending):

- **pallets/flask** (Python, 71k stars): 90% merge-commit rate, 1,628 total PRs, BUT 0.0 review threads per PR. No inline review activity at all. Excluded.
- **pytest-dev/pytest** (Python, 14k stars): 75% merge-commit rate, 5,821 total PRs, 0.6 review threads per merge-commit PR, ~2,619 estimated total threads. Low but non-zero review volume.
- **psf/requests** (Python, 54k stars): 30% merge-commit rate, 1,620 total PRs, 0.3 threads per PR, ~162 estimated total. Too low on both metrics. Excluded.
- **All 12 other projects**: 0% merge-commit rate (100% squash/rebase). Includes django, scikit-learn, pandas, httpx, fastapi, node, express, TypeScript, angular, svelte, react, next.js.

## Viable GitHub Candidates

Only one:

1. **pytest-dev/pytest**: 75% merge-commit rate, 0.6 threads/PR, ~2,619 estimated total threads. Viable but marginal. The review thread volume is significantly lower than gitlab-ui (20.6/MR) and comparable to fdroidserver (5.0/MR). Contributing guide confirms a mixed merge strategy: merge commits for PRs with independently valuable commits, squash for messy histories. The data collection pipeline would need to filter to only the merge-commit PRs (75% of total).

## Comparison to GitLab Survey (Finding 002)

The GitLab survey found 3 viable candidates from 14 surveyed. The GitHub survey found 1 from 15 surveyed. The difference is striking: GitLab projects are far more likely to use non-squash merges. This may reflect GitHub's UI defaulting to "Squash and merge" as a prominent option, while GitLab's default merge behavior preserves individual commits.

Combined viable projects across both platforms:

- **gitlab-ui** (GitLab, JS/Vue, 0% squash, 20.6 inline/MR, ~83k estimated threads): Best candidate by far
- **fdroidserver** (GitLab, Python, 0% squash, 5.0 inline/MR, ~7.5k estimated): Solid second
- **pytest** (GitHub, Python, 75% merge-commit rate, 0.6 threads/PR, ~2.6k estimated): Marginal third
- **woob** (GitLab, Python, 0% squash, 2.4 inline/MR, ~1k estimated): Marginal fourth

## Implications

1. The target of 3 to 5 subject projects is achievable with 2 strong candidates (gitlab-ui, fdroidserver) and 1 to 2 marginal ones (pytest, woob). However, the data volume gap between gitlab-ui (~83k threads) and the rest is large.

2. The non-squash merge requirement severely limits the GitHub candidate pool. This requirement is driven by evaluation, not optimization: the judge must compare the reviewer agent's output against human comments, and the agent must see the same code state the human saw. Changing optimization from per-revision to per-MR does not relax this constraint. There is no viable "naive mode" that avoids it.

3. No JS/TS projects on GitHub passed the non-squash filter. The only JS/TS project in the study is gitlab-ui (from GitLab). This limits cross-language analysis but does not prevent the experiment from proceeding.

4. The 0.6 threads/PR rate for pytest means many PRs will have zero review threads after filtering. The effective sample size for evaluation may be much smaller than the 5,821 total PRs suggests.
