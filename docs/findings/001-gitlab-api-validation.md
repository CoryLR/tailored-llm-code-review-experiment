# 001: GitLab API Field Validation

Feb 22, 2026. Script: `exploratory-tests/test1_validate_api_fields.py`

Validated whether the GitLab REST API exposes all fields needed for the experiment pipeline. Sampled 10 merged MRs from gitlab-org/gitlab (project ID 278964), 24 API calls total.

## Findings

All required fields are present on inline review comments: resolved status, file paths (old_path, new_path), line numbers (new_line, old_line), and commit SHAs (head_sha, base_sha, start_sha). 8/10 sampled MRs had inline comments (75 total).

All 5 unique head_sha values were accessible via API (HTTP 200), but 4/5 were dangling locally (not reachable from any git ref). They exist on GitLab's server but are not included in a standard `git clone`. This confirms that for our custom review agent design, which includes full repo access at the time of review, we cannot assess on Pull Requests that do not contain the full commit history, such as if (a) commits were squashed, or (b) otherwise reorganized before merging such that the exact commit hashes are unavailable in a local clone.

gitlab-org/gitlab has a 90% squash-merge rate in our sample, making it a poor candidate unless filtering to non-squash MRs only.

## Implications

- GitLab's API is sufficient
- Need projects with low squash-merge rates for local commit checkout
- Next step: survey candidate projects for squash rates and review volume
