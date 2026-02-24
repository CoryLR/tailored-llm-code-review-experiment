# 003: Proof of Concept Reviewer + Judge Pipeline

Feb 22, 2026. Scripts: `proof-of-concept/poc_1_prepare.py`, `poc_2_review.py`, `poc_3_judge.py`

End-to-end validation of the core experiment loop on a real merge request: run an LLM reviewer agent, then run a judge agent to compare its output against actual human review comments.

## Setup

Target: [MR !5074](https://gitlab.com/gitlab-org/gitlab-ui/-/merge_requests/5074) from gitlab-ui (Vue component refactor, 4 files, 447 lines of diff). Evaluated at an intermediate commit (5th of 7) where the human reviewer left 3 inline comments on nav.spec.js. Reviewer agent: Claude Sonnet 4.6 with tool-calling (Read, Glob, Grep) via `claude -p --add-dir` for read-only repo access.

## Results

- **Recall**: 0.5/3 (0 full matches, 1 partial match, 2 misses)
- **Novel findings**: 12 true positives, 2 false positives (non-actionable praise)

The partial match: human said "simplify three assertions into one `toEqual`"; agent said "`.classes().length` is brittle, prefer `toEqual`." Similar concern, different framing. BLEU-4 score: 0.11 (from `poc_4_bleu_score.py`), demonstrating that lexical metrics fail on semantically similar comments. [Link to original comment on GitLab](<https://gitlab.com/gitlab-org/gitlab-ui/-/merge_requests/5074#note_2442810269>).

The 2 missed comments were project-specific conventions (repetitive assertions unrelated to test descriptions, unnecessary `wrapper.destroy()`), the kind of patterns a tuned prompt could encode.

### Human Comment Details & Taxonomy

> "**suggestion (non-blocking):** I find the BSV specs to be quite repetitive and making assertions that are unrelated to the actual description of the spec. For example I don't know if we need to have `expect(wrapper.element.tagName).toBe('UL');` in every single spec, only the ones related to the `tag` prop."
- BitsAI-CR dimension: Maintainability and Readability
- BitsAI-CR category: Unused Definition/Redundant Code (3.9)
- Proof of Concept Result: missed

> "**nitpick:** we shouldn't need to put `wrapper.destroy()` in our specs"
- BitsAI-CR dimension: Maintainability and Readability
- BitsAI-CR category: Unused Definition/Redundant Code (3.9)
- Proof of Concept Result: missed

> "**suggestion (non-blocking):** we could simplify these three assertions into one:
>
> `expect(wrapper.classes()).toEqual(['nav', 'nav-pills'])`;
>
> Suggestion applies to all specs in this file"
- BitsAI-CR dimension: Maintainability and Readability
- BitsAI-CR category: Code Readability (3.3)
- Proof of Concept Result: partial match

## Key Takeaways

- Pipeline works end-to-end with structured, parseable output at each stage
- Even in 0% squash projects, intermediate commits can be dangling due to force-pushes; data collection must filter for reachability
- BLEU is inadequate for matching review comments; LLM judge is necessary
- Reviewer prompt needs refinement: discourage non-actionable output
