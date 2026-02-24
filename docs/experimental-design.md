**WORK IN PROGRESS**

# Experimental Design

## Research Questions

- **RQ1:** To what extent does project-specific prompt tuning improve LLM code review recall?
- **RQ2:** What categories of review comments benefit most from project-based tuning?
- **RQ3:** How many optimization iterations before recall plateaus?

## Study Context

Subject projects are open-source repositories from GitLab and GitHub with non-squash merges and active inline code review. Selection criteria: 0% squash-merge rate, >1 inline review comment per MR on average, Python or JS/TS primary language. Data consists of inline review comments on merge requests, fetched via GitLab REST API or GitHub GraphQL API. Older MRs form the tuning set; newer MRs form a held-out evaluation set (temporal split).

Current candidates:

- **gitlab-ui** (gitlab-org/gitlab-ui): Vue.js, 0% squash, ~4,044 MRs, ~20.6 inline comments/MR (~83k estimated total)
- **fdroidserver** (fdroid/fdroidserver): Python, 0% squash, ~1,500 MRs, ~5.0 inline comments/MR (~7.5k estimated total)
- GitHub survey pending to reach 3+ project target

### Why Intermediate Commits Matter

The experiment measures whether the reviewer agent can catch the same issues that human reviewers caught. For this to be meaningful, the agent must see the code *before* the author fixed those issues. A human reviewer comments on revision N; the author pushes fixes in revision N+1. If the agent sees revision N+1 (or the final merged code), the problems are already fixed and there is nothing to find.

Comments made on the final MR revision (the one that was merged) are not useful for evaluation. Any feedback on the final revision was either non-blocking, ignored, or purely cosmetic (e.g., "LGTM", praise). The substantive comments that drove code changes are always on earlier revisions.

### Commit Reachability

The reviewer agent must be provided with the exact `head_sha` checked-out state that the human reviewer was commenting on. Human review comments include a `position.head_sha` field indicating which version of the MR branch they were made against. The agent must see the same code the human saw; otherwise line numbers drift and the comparison is invalid.

Only MRs where at least some intermediate (pre-final) `head_sha` values are reachable in the local git clone are usable. In practice, intermediate force-pushed commits on MR branches are often garbage collected and become dangling SHAs, even for non-squash merged MRs. The final MR branch commit is always reachable through the merge commit ancestry, but earlier revisions usually are not.

Within a single MR, human comments may span multiple revisions (each with a different `head_sha`). Each group of comments sharing a `head_sha` requires a separate reviewer agent run at that commit. Comments on unreachable commits must be excluded entirely. Comments on the final (merged) revision should also be excluded because they represent feedback that did not lead to code changes.

**Example:** MR 1176 from GitLab UI had comments across 5 different `head_sha` values. Only the final commit was locally reachable; the other 4 (which contained all the substantive feedback) were dangling. This MR is unusable for evaluation despite having rich review activity.

### Non-Squash Merge Requirement

Subject projects must use non-squash merges so that individual MR branch commits are preserved in the main branch history and reachable via `git log`/`git cat-file`. Projects with high squash-merge rates are excluded or require filtering to the subset of non-squash MRs.

**Threat to validity:** Projects that squash-merge may have different review cultures (smaller PRs, different comment patterns). Results may not generalize to squash-merge workflows.

### Comment Quality Filtering via Resolution Signals

Not all human review comments are equally good ground truth. Comments that were noise or not taken seriously dilute the evaluation signal. Filtering to substantive comments gives both the judge and optimizer cleaner data to work with. This is a preprocessing step during data collection, not part of the judge's scoring logic.

Signals:

- **Resolved status:** GitLab REST API exposes `resolved` on discussion threads. GitHub exposes `isResolved` on `PullRequestReviewThread` via GraphQL (not REST).
- **Thread outcome:** LLM-classified disposition of the comment thread based on who replied and what they said. Categories: accepted (author acknowledges or fixes), dismissed (author rejects), retracted (commenter withdraws), ambiguous. Accounts for the fact that the same words mean different things depending on the speaker (e.g., "won't fix" from the author vs. "nevermind" from the commenter).

Only "retracted" comments are filtered out; accepted, dismissed, and ambiguous comments remain in the ground truth. Dismissed comments still represent genuine reviewer thinking (the author disagreed, but the reviewer's concern was real). Retracted comments are excluded because the reviewer themselves decided the comment was invalid.

Resolution practices vary by project. Some teams use "Resolve conversation" consistently; others never do. Manual spot-checking of a sample per project is needed to validate that the signals are reliable before using them as hard filters.

### Diff Generation

The diff shown to the reviewer agent must be `base_sha..head_sha` for the specific revision being evaluated, not the full MR diff to the merge commit. This ensures the agent sees exactly what the human reviewer saw. Binary files (e.g., `*.png` snapshots) are excluded from the diff via git pathspec, since the reviewer agent cannot meaningfully review binary content.

## RQ1 Methodology

### Experimental Conditions

Generic prompt reviewer vs. project-tuned prompt reviewer. Both conditions use the same LLM, the same agent architecture, and the same tool set. Only the system prompt differs.

### Baseline

**Generic prompt:** Structured around BitsAI-CR review taxonomy categories (defect, security, maintainability, performance, style). No examples, no project-specific context.

### Metrics

- **Recall (primary):** Fraction of human review comments semantically matched by the agent, scored by the judge agent (not BLEU or surface-level text similarity).
- **Precision:** Fraction of agent comments that are true positives (not false alarms).

### Agent Design

**Reviewer agent:** LLM with tool-calling capability. Available tools: `Read`, `Glob`, `Grep` (read-only). No `Bash`, `Edit`, or `Write`, preventing modification of the subject repo. Subject repos are passed via `--add-dir` for repository isolation, ensuring no CLAUDE.md or config files in the subject project affect agent behavior.

**Judge agent:** No tools (`--tools ""`). Everything needed (human comments, agent comments, file/line context) is embedded in the prompt. Matches are semantic, not line-exact; a human comment on line 42 and an agent comment on line 44 about the same issue count as a match. File-level matching is respected: a comment about `foo.vue` should not match a comment about `bar.vue` unless the concern genuinely spans both files.

**Per-revision evaluation:** Each MR revision (identified by `head_sha`) is evaluated independently. The reviewer agent runs once per revision, seeing only the diff up to that revision.

### Comparison with Prior Work

Metrics mapped to prior work for empirical comparison:

- **Recall:** Our primary metric. Comparable to SWR-Bench's hit-based recall (Zeng et al., 2025) and CBI/Critique-Bug Inclusion (OpenAI). SWR-Bench uses LLM verification of ground-truth issue coverage, similar to our judge approach.
- **Precision:** Fraction of agent comments that are true positives. Comparable to BitsAI-CR's 75% precision, Cihan et al.'s 73.8% resolution rate, LAURA's 42% correct/helpful rate.

### Statistical Analysis

TBD

## RQ2 Methodology

TBD

## RQ3 Methodology

TBD
