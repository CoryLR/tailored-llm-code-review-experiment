"""Compute BLEU-4 scores for the PoC partial match to demonstrate why
BLEU is a poor evaluation metric for code review comments.

Reads the human and agent comments from the PoC data directory and
computes sentence-level BLEU-4 between the partial-match pair identified
by the judge (human comment 2 vs. agent inline comment 4).

Also computes BLEU-4 for all three human comments against their best
matching agent comment for a complete picture.
"""

import json
import sys

sys.stdout.reconfigure(line_buffering=True)
from pathlib import Path

from nltk.translate.bleu_score import SmoothingFunction, sentence_bleu

REPO_ROOT = Path(__file__).resolve().parent.parent
POC_DIR = Path(__file__).resolve().parent
DATA_DIR = POC_DIR / "results" / "mr_5074"


def tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer for BLEU.

    Splits on whitespace, then separates trailing punctuation so that
    e.g. "toEqual(['nav'])" becomes ["toEqual", "(", "[", "'nav'", "]", ")"].
    This mirrors the lightweight tokenization used in CodeReviewer evaluation.
    """
    import re

    tokens = []
    for word in text.split():
        parts = re.findall(r"[a-zA-Z0-9_.'+]+|[^\s]", word)
        tokens.extend(parts)
    return [t.lower() for t in tokens]


def compute_bleu4(reference: str, hypothesis: str) -> dict:
    """Compute sentence-level BLEU-4 between reference and hypothesis.

    Returns a dict with the BLEU-4 score, individual n-gram precisions,
    and the tokenized texts for inspection.
    """
    ref_tokens = tokenize(reference)
    hyp_tokens = tokenize(hypothesis)

    # Standard BLEU-4 (no smoothing; zeros out if any n-gram has 0 matches)
    bleu4_raw = sentence_bleu(
        [ref_tokens],
        hyp_tokens,
        weights=(0.25, 0.25, 0.25, 0.25),
    )

    # Smoothed BLEU-4 (method 1: add epsilon to zero counts)
    # This is what some CodeReviewer evaluation scripts use
    smoothing = SmoothingFunction()
    bleu4_smoothed = sentence_bleu(
        [ref_tokens],
        hyp_tokens,
        weights=(0.25, 0.25, 0.25, 0.25),
        smoothing_function=smoothing.method1,
    )

    # Individual n-gram precisions for analysis
    precisions = {}
    for n in range(1, 5):
        weights = tuple(1.0 if i == n - 1 else 0.0 for i in range(4))
        p = sentence_bleu(
            [ref_tokens],
            hyp_tokens,
            weights=weights,
            smoothing_function=smoothing.method1,
        )
        precisions[f"p{n}"] = p

    return {
        "bleu4_raw": bleu4_raw,
        "bleu4_smoothed": bleu4_smoothed,
        "precisions": precisions,
        "ref_token_count": len(ref_tokens),
        "hyp_token_count": len(hyp_tokens),
        "ref_tokens": ref_tokens,
        "hyp_tokens": hyp_tokens,
    }


def main() -> None:
    print("PoC Step 4: BLEU-4 score analysis")
    print("=" * 60)
    print()

    # Load data
    human_comments = json.loads((DATA_DIR / "1_human_comments.json").read_text())
    reviewer_comments = json.loads((DATA_DIR / "2_reviewer_comments.json").read_text())
    judge_results = json.loads((DATA_DIR / "3_judge_results.json").read_text())

    inline_comments = reviewer_comments["inline_comments"]
    all_results = []

    # Compute BLEU-4 for the partial match pair (the key example)
    print("PARTIAL MATCH PAIR (Human #2 vs. Agent #4)")
    print("-" * 60)

    human_text = human_comments[2]["body"]
    agent_text = inline_comments[4]["comment"]

    print(f"Human: {human_text[:120]}...")
    print()
    print(f"Agent: {agent_text[:120]}...")
    print()

    result = compute_bleu4(human_text, agent_text)
    print(f"BLEU-4 (raw):      {result['bleu4_raw']:.4f}")
    print(f"BLEU-4 (smoothed): {result['bleu4_smoothed']:.4f}")
    print(f"Unigram precision: {result['precisions']['p1']:.4f}")
    print(f"Bigram precision:  {result['precisions']['p2']:.4f}")
    print(f"Trigram precision: {result['precisions']['p3']:.4f}")
    print(f"4-gram precision:  {result['precisions']['p4']:.4f}")
    print(f"Reference tokens:  {result['ref_token_count']}")
    print(f"Hypothesis tokens: {result['hyp_token_count']}")
    print()

    partial_match_result = {
        "pair": "Human #2 vs. Agent #4",
        "judge_verdict": "partial_match",
        "human_text": human_text,
        "agent_text": agent_text,
        **{k: v for k, v in result.items() if k not in ("ref_tokens", "hyp_tokens")},
    }
    all_results.append(partial_match_result)

    # Compute BLEU-4 for each human comment against ALL agent comments,
    # showing the best-scoring agent comment for each
    print()
    print("ALL HUMAN COMMENTS: BEST BLEU-4 MATCH")
    print("=" * 60)

    for i, human in enumerate(human_comments):
        human_text = human["body"]
        judge_match = judge_results["matches"][i]

        best_bleu = 0.0
        best_agent_idx = None
        best_result = None

        # Check all inline + general agent comments
        agent_texts = []
        for j, c in enumerate(inline_comments):
            agent_texts.append((j, "inline", c["comment"]))
        for j, c in enumerate(reviewer_comments["general_comments"]):
            agent_texts.append((len(inline_comments) + j, "general", c["comment"]))

        for agent_idx, agent_type, agent_text in agent_texts:
            r = compute_bleu4(human_text, agent_text)
            if r["bleu4_smoothed"] > best_bleu:
                best_bleu = r["bleu4_smoothed"]
                best_agent_idx = agent_idx
                best_result = r

        print(f"\nHuman #{i} [{human['file_path']}:{human['line_number']}]")
        print(f"  Text: {human_text[:100]}...")
        print(f"  Judge verdict: {judge_match['verdict']}"
              f" (matched agent #{judge_match['matched_agent_comment_index']})"
              if judge_match["verdict"] != "no_match"
              else f"  Judge verdict: {judge_match['verdict']}")
        print(f"  Best BLEU-4 agent: #{best_agent_idx}"
              f" (smoothed={best_result['bleu4_smoothed']:.4f},"
              f" raw={best_result['bleu4_raw']:.4f})")

        all_results.append({
            "pair": f"Human #{i} vs. best BLEU agent #{best_agent_idx}",
            "judge_verdict": judge_match["verdict"],
            "judge_matched_agent": judge_match["matched_agent_comment_index"],
            "best_bleu_agent": best_agent_idx,
            "bleu4_raw": best_result["bleu4_raw"],
            "bleu4_smoothed": best_result["bleu4_smoothed"],
        })

    # Save results
    output_path = DATA_DIR / "4_bleu_analysis.json"
    output_path.write_text(json.dumps(all_results, indent=2))
    print(f"\nSaved results to {output_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
