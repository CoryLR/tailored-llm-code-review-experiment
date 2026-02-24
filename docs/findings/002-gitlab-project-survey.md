# 002: GitLab Project Survey

Feb 22, 2026. Script: `exploratory-tests/test2_survey_squash_rates_and_comments.py`

Surveyed 14 Python/JS/TS projects on gitlab.com for squash-merge rates and inline review comment volume. Per project: 20 sampled MRs for squash rate, discussions fetched for up to 5 non-squash MRs.

## Viable Candidates (0% Squash, Active Inline Review)

- **GitLab UI** (gitlab-org/gitlab-ui): Vue.js, 4,044 MRs, 20.6 inline comments/MR, ~83k estimated total
- **F-Droid Server** (fdroid/fdroidserver): Python, 1,500 MRs, 5.0 inline/MR, ~7.5k estimated total
- **Woob** (woob/woob): Python, 427 MRs, 2.4 inline/MR, ~1k estimated total (marginal)

## Eliminated Projects

Six projects had 0% squash but no meaningful inline review (review happens on mailing lists or outside GitLab). Six more had 50%+ squash rates. One returned HTTP 403.

## Implications

- Two strong GitLab candidates, one marginal
- GitHub survey needed to reach the 3-5 project target
- The 0% squash rate on the top two candidates means full commit history is preserved
