# Finding 005: Systematic Project Survey (GitHub + GitLab)

Mar 23, 2026. Script: `exploratory-tests/test3_survey_projects.py`.

Systematic survey of the most-starred Python, JavaScript, and TypeScript projects on GitHub and GitLab for experiment viability. Projects are examined in descending star-count order until 5 viable candidates are found per platform, or 200 repos are checked per platform (independent limits).

## Method

For each platform, repositories are discovered in descending order by star count across all three target languages simultaneously. Each candidate is surveyed by fetching 20 recent merged PRs/MRs and checking merge commit parent count (GitHub) or squash_commit_sha presence (GitLab) to determine the non-squash merge rate, plus inline review thread volume on non-squash PRs/MRs. Projects whose primary language is not Python, JavaScript, or TypeScript are skipped. Forks, archived repos, and repos with fewer than 400 merged PRs are excluded.

Viability criteria: at least 25% non-squash merge rate AND at least 0.5 inline review threads per non-squash PR/MR.

Rate limiting: 2-second delay between all API calls on both platforms, keeping usage at 36% of GitHub's limit (5,000 pts/hr) and 10% of GitLab's limit (300 req/min).

## GitHub Results

98 repos surveyed across Python, JavaScript, and TypeScript in 4.2 minutes. 5 viable candidates found:

1. **storybookjs/storybook**: TypeScript, 89,504 stars, 100% non-squash, 3.2 review threads/PR, ~40,313 estimated total threads
2. **mermaid-js/mermaid**: TypeScript, 86,902 stars, 95% non-squash, 0.6 threads/PR, ~1,574 estimated total
3. **browser-use/browser-use**: Python, 83,608 stars, 95% non-squash, 3.7 threads/PR, ~6,049 estimated total
4. **d2l-ai/d2l-zh**: Python, 76,436 stars, 40% non-squash, 6.0 threads/PR, ~2,445 estimated total. NOTE: this is a textbook repo (Dive into Deep Learning, Chinese edition), not application source code.
5. **apache/echarts**: TypeScript, 66,008 stars, 100% non-squash, 1.4 threads/PR, ~2,094 estimated total

93 of 98 surveyed repos (95%) had 0% non-squash merge rate. Squash merging is the dominant merge strategy among top-starred GitHub projects.

## GitLab Results

200 repos surveyed across all languages, filtered to Python/JavaScript/TypeScript, in 25.0 minutes. Most top-starred GitLab projects use Ruby (GitLab itself) or C++, so many repos were skipped by the language filter. 2 viable candidates found:

1. **ase/ase**: Python, 509 stars, 90% non-squash, 1.5 review threads/MR, 3,218 merged MRs, ~4,344 estimated total threads. Atomic Simulation Environment, a scientific computing library.
2. **gitlab-org/gitlab-services/design.gitlab.com**: JavaScript, 211 stars, 35% non-squash, 4.6 threads/MR, 3,853 merged MRs, ~6,164 estimated total threads

GitLab has far fewer highly-starred Python/JS/TS projects than GitHub. The top GitLab project by stars is gitlab-org/gitlab-foss at 7,164 stars (Ruby, excluded by language filter).

## Subject Project Selection

Selection criteria, applied in order:
1. At least 1 project from each platform (GitHub, GitLab) for platform diversity
2. At least 1 project from each language family (Python, JavaScript/TypeScript) for language diversity
3. Remaining slots filled by highest star count among viable candidates
4. Repos that are primarily data, metadata, or educational content rather than application source code are excluded

Selected projects (3):

1. **storybookjs/storybook** (GitHub, TypeScript): 89,504 stars, 100% non-squash, 3.2 threads/PR, ~40,313 estimated threads. UI component development environment. Top viable by stars overall. Satisfies GitHub and JS/TS constraints.
2. **mermaid-js/mermaid** (GitHub, TypeScript): 86,902 stars, 95% non-squash, 0.6 threads/PR, ~1,574 estimated threads. Diagramming and charting library. Second viable by stars overall.
3. **ase/ase** (GitLab, Python): 509 stars, 90% non-squash, 1.5 threads/MR, ~4,344 estimated threads. Atomic Simulation Environment for computational chemistry. Top viable GitLab project. Satisfies GitLab and Python constraints.

## Key Observations

1. Squash merging is overwhelmingly dominant among top-starred GitHub projects. 95% of surveyed GitHub repos use 100% squash merging. The non-squash requirement significantly limits the candidate pool.

2. The viable candidates that do exist tend to be developer tools (Storybook, Mermaid) or newer projects (browser-use) rather than the largest established frameworks (React, Django, Next.js, etc.).

3. GitLab has far fewer highly-starred Python/JS/TS projects. The star count gap between GitHub and GitLab viable candidates is roughly 170x (89k vs 509).

4. The prompt optimization pipeline does not require non-squash merges, since it works from human comments directly. Only evaluation requires per-commit checkout. This means the approach is applicable to all projects; the non-squash constraint affects only the evaluation methodology.
