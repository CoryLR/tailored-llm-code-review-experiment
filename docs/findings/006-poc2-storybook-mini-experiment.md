# Finding 006: Proof of Concept #2: Storybook Mini Experiment

Mar 24-30, 2026. Scripts: `proof-of-concept-2/` (0_collect through 6_summarize). Results: [summary.md](../../proof-of-concept-2/data/storybook/results/summary.md)

Mini-experiment on Storybook (storybookjs/storybook) testing generic vs. project-tuned LLM code review. 8 PRs were collected: 5 assigned to the tuning set, 3 to the test set. The reviewer agent uses Claude Sonnet 4.6 with tool-calling for codebase exploration, and the judge agent uses Claude Opus 4.6 for semantic comparison of reviewer output against human comments and TP/FP assessment of novel findings.

## Key Takeaways

- Tuning had no measurable effect at this scale: recall was identical (10% for both conditions) and none of the 9 optimizer rules fired on the 3 test PRs. The tuning and test sets (5 and 3 PRs) are too small to draw conclusions about tuning effectiveness.
- Both conditions found 18 novel true positives (issues humans did not flag, including null coercion, config mismatch, and race condition findings). These have not been verified against the live codebase.
- Replace mode (`--system-prompt`) outperformed append mode (`--append-system-prompt`): 18 vs 12 novel TPs with fewer false positives.
- The pipeline (optimizer, reviewer, judge) produced valid results: 40 judge verdicts were manually reviewed with no clear errors.
- Low recall (10%) is primarily explained by the reviewer prompt's "only provide actionable feedback" instruction, which suppressed the style-level comments that made up 8/10 human comments in the test set.

## Results

The optimizer processed 16 human review comments across the 5 tuning PRs and extracted 9 project-specific rules ($0.36 cost). Both conditions were then evaluated on the 3 held-out test PRs, which contained 10 human review comments total.

- **Generic**: 10% recall (1 full match out of 10 human comments), 10 novel TPs, 1 FP, $1.30 review cost
- **Tuned**: 10% recall (1 full match out of 10 human comments), 8 novel TPs, 1 FP, $0.97 review cost
- **Total pipeline cost**: $2.97 (optimizer + both review conditions + judge)

Both conditions identified plausible defects that human reviewers did not flag, including a `??` vs `||` null coercion issue that would cause stale URL parameters, and a documentation default value that contradicts the runtime default. These have not been verified against the live codebase. The tuned condition found 2 additional issues that the generic condition missed: a viewMode redirect path mismatch and a potential race condition with the filteredIndex.

## Causes of Low Recall

8 of the 10 human comments across the test PRs are style-level suggestions: emoji additions, wording tweaks, and ordering preferences ("should this be sorted?"). The reviewer prompt instructs the agent to "only provide actionable feedback" and avoid non-actionable observations, which filters out exactly the kind of feedback these human reviewers left. The single full match was the one substantive human comment, which flagged code duplication in `navigateWithQueryParams`.

PoC #2 did not include the human comment filtering pipeline from the CP3 experiment design. The full pipeline would filter bot comments and pure questions from the ground truth, but would not filter style nits, which are legitimate review feedback. The primary cause of low recall is the reviewer prompt's "only provide actionable feedback" instruction, which actively suppresses the kind of style-level comments that human reviewers frequently leave. Several unmatched comments (e.g., "should this be sorted?") were phrased as questions but had no replies yet were marked resolved and outdated. "Outdated" means the commented lines changed in a later commit, and "resolved" means someone closed the thread, but neither proves the feedback was actually addressed (resolution can also mean "acknowledged and moving on," and the line change could be incidental). Still, the combination suggests these comments were acted on, making them questions in form but likely actionable requests in practice. The comment filtering pipeline will need to consider the full thread context, not just the surface form.

## Optimizer Overfitting

The optimizer produced 9 rules from 5 tuning PRs, but most are highly specific to individual PRs (e.g., "verify StatusValue priority array index placement," "check that Nx cache inputs cover the command's file scope"). None of the 9 rules directly applied to any of the 3 test PRs, which is consistent with the identical recall between generic and tuned conditions. Despite this, the tuned reviewer still found issues that the generic reviewer missed, which suggests the rules may prime the reviewer's attention toward certain concern categories (ordering correctness, configuration mismatches) even when the specific rule text does not match. An evolutionary strategy redesign is planned to address overfitting by running parallel optimizers on different PR subsets, then synthesizing and cross-validating the results across multiple iterations.

## `--system-prompt` vs `--append-system-prompt`

The full pipeline was run twice to compare two Claude Code invocation modes. `--system-prompt` replaces Claude Code's default system prompt entirely with our reviewer prompt. `--append-system-prompt` preserves the default instructions (which include tool usage guidance and safety guidelines) and appends our reviewer prompt after them.

| Mode    | Condition | Comments | Recall | Novel TP | Novel FP | Review cost |
| ------- | --------- | -------- | ------ | -------- | -------- | ----------- |
| Replace | Generic   | 10i + 2g | 10%    | 10       | 1        | $1.30       |
| Replace | Tuned     | 9i + 1g  | 10%    | 8        | 1        | $0.97       |
| Append  | Generic   | 10i + 1g | 20%    | 6        | 3        | $1.28       |
| Append  | Tuned     | 5i + 1g  | 0%     | 6        | 0        | $0.72       |

*i = inline, g = general. TP = true positive (useful finding humans missed). FP = false positive (incorrect or unhelpful).*

Replace mode produced more novel true positives across both conditions (18 vs 12) and fewer false positives (2 vs 3). The strongest single-condition result was replace+tuned, which found unique issues no other condition caught. Append+tuned was the weakest condition overall, with 0% recall and the fewest comments. On timing, append mode was faster on small diffs but slower on the largest diff (786 lines), possibly because the default system prompt encourages more thorough tool exploration. Replace mode is the chosen approach for the full experiment due to reproducibility and the stronger results observed here.

## Prompt Design Notes

The "only provide actionable feedback" instruction in the reviewer prompt actively suppresses the kinds of style-level comments that human reviewers frequently leave. Softening this instruction (e.g., "prioritize substantive feedback but include style observations when clear") could improve recall without adding much noise.

The tuned condition may hyperfocus on project-specific rules at the expense of general review coverage. On the smallest test PR, the tuned reviewer produced only 1 comment compared to 3 from the generic reviewer. Adding a guardrail such as "these rules supplement the general guidelines; do not limit your review to only these rules" could help without hurting the cases where tuned already works well.

Sentinel markers (`===FINAL_REVIEW_OUTPUT_BEGIN===`) worked reliably for extracting structured JSON from agent output. Only 1 retry was needed across 12 reviewer runs (the first attempt on PR #34283 in generic+replace mode produced a text summary instead of the expected JSON).

## Judge Quality

40 individual verdicts were produced across the replace-mode track (20 human comment match verdicts + 20 novel agent comment assessments, covering 3 PRs x 2 conditions). All 40 were reviewed for correctness. The judge correctly distinguished between comments that address the same code area but raise different concerns (e.g., a human requesting an emoji prefix vs. the agent warning about a default glob being silently replaced). No clear errors were found in the match verdicts or the novel TP/FP classifications. The one debatable call was a race condition flagged as FP on the tuned condition for PR #34283, which is reasonable given the "speculative without evidence" standard. Total judge cost was $0.34 for 6 evaluations ($0.02 to $0.08 each).

However, the judge assessed TP/FP for novel comments based on plausibility from the diff alone, without access to the subject repo. It could not verify claims like "this will cause a race condition" against the actual codebase. This limits confidence in novel TP/FP classifications.

## Decisions for Full Experiment

Based on PoC #2 findings, the following decisions were made for the full experiment:

- **Chose replace mode** (`--system-prompt`): replace mode produced more novel TPs (18 vs 12) with fewer false positives (2 vs 3) across both conditions. Replace mode is used for all future runs.
- **Soften reviewer prompt to allow style-level feedback**: the "only provide actionable feedback" instruction may have suppressed style nits, which made up 8/10 human comments in the test set. The full experiment prompt should permit style and formatting suggestions to improve recall against human comments.
- **Redesign optimizer as evolutionary strategy**: v1 sequential optimizer overfitted (9 rules from 5 PRs, none fired on test PRs). The redesign runs parallel optimizers on PR subsets, then a synthesizer merges, cross-validates, and generalizes the rules. This cycle repeats to produce fewer, broader rules. The process will also be faster since it will also allow for parallelization.
- **Give judge repo access**: the judge will receive `--add-dir` access to the subject repo (same as the reviewer) and run in tandem with the reviewer so it can verify novel findings against the actual codebase rather than assessing plausibility from the diff alone.
- **Add comment filtering pipeline**: PoC #2 did not filter human comments before evaluation. The full experiment will filter bot comments and pure questions from the ground truth. Style nits are retained as legitimate review feedback.
- **Report cost as a metric**: per-condition and total pipeline cost will be reported as a formal metric.
