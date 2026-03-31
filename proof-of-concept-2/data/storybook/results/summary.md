# Aggregate Results

**Optimizer**: 9 rules extracted, $0.36

## Generic Condition
  PRs evaluated: 3
  Human comments: 10
  Agent comments: 10 inline + 2 general
  Matches: 1 full, 0 partial, 9 miss
  Recall (full): 10%
  Recall (full+partial): 10%
  Novel: 10 TP, 1 FP
  Review cost: $1.30
  Judge cost: $0.17

## Tuned Condition
  PRs evaluated: 3
  Human comments: 10
  Agent comments: 9 inline + 1 general
  Matches: 1 full, 0 partial, 9 miss
  Recall (full): 10%
  Recall (full+partial): 10%
  Novel: 8 TP, 1 FP
  Review cost: $0.97
  Judge cost: $0.17

**Total PoC #2 cost**: $2.97


# Generic vs Tuned Comparison

## PR #33841 (6 human comments)

**Generic**: 3 inline + 0 general comments
  Matches: 0 full, 0 partial, 6 miss
  Novel: 3 TP, 0 FP
  Cost: $0.50 (review) + $0.07 (judge)

**Tuned**: 3 inline + 0 general comments
  Matches: 0 full, 0 partial, 6 miss
  Novel: 3 TP, 0 FP
  Cost: $0.24 (review) + $0.08 (judge)

---

## PR #34283 (3 human comments)

**Generic**: 5 inline + 1 general comments
  Matches: 1 full, 0 partial, 2 miss
  Novel: 5 TP, 0 FP
  Cost: $0.56 (review) + $0.06 (judge)

**Tuned**: 5 inline + 1 general comments
  Matches: 1 full, 0 partial, 2 miss
  Novel: 4 TP, 1 FP
  Cost: $0.57 (review) + $0.07 (judge)

**Divergences (by file):**
  Generic only: code/core/src/manager-api/store.ts

---

## PR #34314 (1 human comments)

**Generic**: 2 inline + 1 general comments
  Matches: 0 full, 0 partial, 1 miss
  Novel: 2 TP, 1 FP
  Cost: $0.24 (review) + $0.04 (judge)

**Tuned**: 1 inline + 0 general comments
  Matches: 0 full, 0 partial, 1 miss
  Novel: 1 TP, 0 FP
  Cost: $0.15 (review) + $0.02 (judge)

**Divergences (by file):**
  Generic only: code/core/src/types/modules/core-common.ts

---


# Verdict List for Manual Validation

## PR #34314 [generic]

### Human Comment Matches
- [MISS] Human #0: Feature flags should be sorted alphabetically
  Reason: None of the AI comments address alphabetical ordering. The AI focused on default value mismatches, TODO tracking, and unused flag concerns, but completely missed the sorting/ordering issue.

### Novel Agent Comments
- [TP] Agent #0: Identifying a mismatch between documented default value (true) and actual code default (false) is a genuinely useful finding that could mislead users.

- [TP] Agent #1: Suggesting a tracking reference for a TODO comment is a minor but reasonable maintainability suggestion that a real reviewer might appreciate.

- [FP] Agent #2: Feature flags are commonly added ahead of their consuming code, which may land in a separate PR. Flagging this as a defect without knowing the broader context is speculative and would likely be noise in a real review.

---

## PR #34314 [tuned]

### Human Comment Matches
- [MISS] Human #0: Items should be sorted alphabetically
  Reason: The human comment is about alphabetical sorting of items, while the only AI comment is about an incorrect default value in documentation. Completely different concerns.

### Novel Agent Comments
- [TP] Agent #0: Flagging a documentation default value that contradicts the actual runtime default in code is a genuinely useful finding. If the code sets changeDetection: false but docs say Default: true, that is a real documentation bug that would confuse users.

---

## PR #34283 [generic]

### Human Comment Matches
- [FULL] Human #0: Why aren't we reusing existing code in navigateWithQueryParams?
  Matched agent #2
  Reason: Both comments identify that navigateWithQueryParams duplicates existing URL-building logic and suggest reusing the existing code rather than reimplementing it.

- [MISS] Human #1: _changed doesn't belong here
  Reason: No AI comment addresses anything related to a '_changed' property being in the wrong location.

- [MISS] Human #2: Should these be sorted?
  Reason: No AI comment raises a concern about sorting.

### Novel Agent Comments
- [TP] Agent #0: Identifies a real behavioral bug: serializeTagsParam returns '' (empty string) which is not nullish, so ?? null won't trigger, leaving a stale '?tags=' in the URL and overriding defaults on reload. This is a genuine code defect with a concrete fix suggestion.

- [TP] Agent #1: Identifies a legitimate logic error where storyPassesFilter only accepts type === 'story' but not 'docs', causing incorrect redirects for valid docs entries that match the filter.

- [TP] Agent #3: Identifies the same type-checking inconsistency in selectFirstStory, where only 'story' type entries are considered but docs entries are valid filter targets. Consistent with the finding in Agent Comment 1.

- [TP] Agent #4: Identifies unnecessary double storage reads in getInitialState. While minor, it's a valid performance concern and the observation about the test workaround confirms it's a real issue.

- [TP] Agent #5: Reasonable suggestion for integration-level test coverage that would catch the ?? vs || bug described in Agent Comment 0. This is actionable test feedback.

---

## PR #34283 [tuned]

### Human Comment Matches
- [FULL] Human #0: Why aren't we reusing existing logic in navigateWithQueryParams?
  Matched agent #4
  Reason: Both comments raise the same concern: navigateWithQueryParams duplicates URL construction logic already present in navigateTo, and the code should be consolidated.

- [MISS] Human #1: _changed doesn't belong here
  Reason: No AI comment mentions _changed or addresses a misplaced field/property.

- [MISS] Human #2: Should these be sorted?
  Reason: No AI comment directly raises sorting as its primary concern. Agent Comment 4 mentions sort order tangentially but its core point is code deduplication, not sorting.

### Novel Agent Comments
- [TP] Agent #0: Identifies a plausible bug where `??` (nullish coalescing) doesn't coerce empty strings to null, causing stale query params. The reasoning chain is concrete and the fix is actionable.

- [TP] Agent #1: Highlights that docs entries in filteredIndex are not recognized by storyPassesFilter, which could cause unnecessary redirects. A reasonable logic concern.

- [TP] Agent #2: Points out a mismatch between viewMode from the event payload and the actual type of the first filtered entry, which could produce incorrect URLs. A valid edge case.

- [TP] Agent #3: Notes inconsistency between selectFirstStory and the STORY_SPECIFIED handler in their treatment of docs entries. Consistent with Agent Comment 1 and reasonable.

- [FP] Agent #5: The race condition concern is speculative and unlikely given typical initialization order. Without evidence that this race actually occurs, it adds noise rather than value.

---

## PR #33841 [generic]

### Human Comment Matches
- [MISS] Human #0: Minor wording suggestion to clarify framework replacement text
  Reason: No AI comment addresses this editorial wording suggestion about framework examples.

- [MISS] Human #1: Add 👇 emoji prefix to comment about workspace package source files
  Reason: Agent Comment 2 is on the same comment area but raises a completely different concern (explaining the first include entry preserves defaults). The human just wants the 👇 emoji added.

- [MISS] Human #2: Add 👇 emoji prefix to comment (second instance)
  Reason: Same editorial suggestion as Comment 1; no AI comment matches this.

- [MISS] Human #3: Adjust section heading text to 'Inherited args are missing for components from workspace packages'
  Reason: Agent Comment 0 discusses the heading/section scope (Vite specificity) but the human's concern is purely about the heading wording, not builder specificity.

- [MISS] Human #4: Reword explanation paragraph about react-docgen-typescript in monorepo with workspaces
  Reason: No AI comment addresses the specific rewording of this explanation paragraph.

- [MISS] Human #5: Reword path explanation and request clarification about tsconfigPath context
  Reason: No AI comment addresses the tsconfigPath clarification or the suggested rewording.

### Novel Agent Comments
- [TP] Agent #0: Pointing out that the section applies to all React users but the issue may be Vite-builder specific is a valid observation that could prevent user confusion. A reasonable documentation concern.

- [TP] Agent #1: Noting that the glob pattern only covers .tsx and not .ts files is a genuinely useful observation. Workspace packages with type-only .ts files would be missed, making the fix incomplete for some users.

- [TP] Agent #2: Explaining that providing an include array replaces the default and that omitting the first entry would break local file processing is a valuable insight that could prevent user errors when adapting the snippet.

---

## PR #33841 [tuned]

### Human Comment Matches
- [MISS] Human #0: Wording suggestion for framework comment
  Reason: No AI comment addresses this minor wording change about framework examples.

- [MISS] Human #1: Add emoji prefix to inline code comment
  Reason: No AI comment addresses the emoji/wording style of this inline comment. Agent Comment 1 is about the same snippet but raises a completely different concern (documenting the default glob).

- [MISS] Human #2: Add emoji prefix to inline code comment (second location)
  Reason: Same as Comment 1 — no AI comment addresses this formatting suggestion.

- [MISS] Human #3: Adjust section heading text
  Reason: No AI comment suggests changing the heading text to 'Inherited args are missing for components from workspace packages'.

- [MISS] Human #4: Reword opening explanation paragraph
  Reason: Agent Comment 2 touches on scoping to Vite, which is tangentially related, but the human comment is about general prose clarity (mentioning workspace packages, inherited args like MUI's ButtonProps), not about Vite-scoping. Different core concerns.

- [MISS] Human #5: Clarify prose about tsconfigPath behavior and ask for more context
  Reason: Human asks to clarify why tsconfigPath doesn't affect file inclusion. Agent Comment 0 warns about include replacing the default glob. These are related to the same feature area but address distinct concerns: root-cause explanation vs. side-effect warning.

### Novel Agent Comments
- [TP] Agent #0: Flagging that `include` replaces the default glob is a genuine and important technical concern. Users could silently break prop inference for their own components if they only add the workspace path. This is a valuable finding that the human reviewers didn't explicitly call out.

- [TP] Agent #1: Suggesting that the `**/**.tsx` entry in the code snippet should be documented as the default that must be preserved is a useful readability improvement, complementing Agent Comment 0's concern.

- [TP] Agent #2: Pointing out that the section isn't scoped to Vite when the underlying fix is Vite-specific is a valid accuracy concern. Webpack users could be misled. This is a reasonable documentation quality finding.

---
