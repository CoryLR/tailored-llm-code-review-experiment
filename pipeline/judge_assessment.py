"""Interactive tool for assessing judge agent accuracy.

Loads judge results alongside the original human and agent comments,
presents each verdict for manual review, and tracks agreement statistics.
This is the validation step targeting >90% human-judge agreement on
100+ labeled comment pairs before scaling up data collection.

Workflow:
1. Run reviewer agent on MRs (poc_2_review.py or production pipeline)
2. Run judge agent on (human, agent) pairs (poc_3_judge.py or production)
3. Use THIS tool to label judge verdicts as correct or incorrect
4. If agreement < 90%, tune judge prompt and repeat from step 2

Progress is saved automatically. Re-running the tool skips already-labeled
items, so you can review in multiple sessions.

Usage:
    python pipeline/judge_assessment.py

Or override the results directory:
    python pipeline/judge_assessment.py path/to/results/mr_1234
"""

import json
import sys

sys.stdout.reconfigure(line_buffering=True)
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Directory containing judge results. Each subdirectory should have:
#   1_human_comments.json    (from poc_1_prepare or collect pipeline)
#   2_reviewer_comments.json (from poc_2_review or reviewer pipeline)
#   3_judge_results.json     (from poc_3_judge or judge pipeline)
#
# To assess multiple MRs, set this to the parent directory (e.g.,
# "proof-of-concept/results") and the tool will scan all subdirectories.
RESULTS_DIR = REPO_ROOT / "proof-of-concept" / "results"

# Where to save assessment labels and statistics
LABELS_FILE = REPO_ROOT / "data" / "judge_assessment" / "labels.json"

# Trial mode: limit the number of items to review per session
TRIAL_MODE = True
TRIAL_LIMIT = 5  # max items to present before stopping

# ---------------------------------------------------------------------------
# End of configuration
# ---------------------------------------------------------------------------

# Expected filenames within each MR result directory
HUMAN_COMMENTS_FILE = "1_human_comments.json"
REVIEWER_COMMENTS_FILE = "2_reviewer_comments.json"
JUDGE_RESULTS_FILE = "3_judge_results.json"


def load_json(filepath: Path) -> dict | list:
    """Load and parse a JSON file."""
    return json.loads(filepath.read_text())


def save_labels(labels: dict, filepath: Path) -> None:
    """Save assessment labels to disk."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(json.dumps(labels, indent=2))


def find_mr_directories(results_dir: Path) -> list[Path]:
    """Find all subdirectories containing judge results."""
    dirs = []
    # Check if results_dir itself contains judge results
    if (results_dir / JUDGE_RESULTS_FILE).exists():
        dirs.append(results_dir)
    # Check subdirectories
    for child in sorted(results_dir.iterdir()):
        if child.is_dir() and (child / JUDGE_RESULTS_FILE).exists():
            dirs.append(child)
    return dirs


def label_key(mr_dir_name: str, verdict_type: str, index: int) -> str:
    """Create a unique key for a label entry.

    Args:
        mr_dir_name: name of the MR directory (e.g., "mr_5074")
        verdict_type: "match" or "novel"
        index: the human_comment_index or agent_comment_index
    """
    return f"{mr_dir_name}:{verdict_type}:{index}"


def format_comment(comment: dict, prefix: str = "") -> str:
    """Format a single comment for display."""
    lines = []
    file_path = comment.get("file_path") or comment.get("path") or "(general)"
    line_num = comment.get("line_number") or comment.get("line") or ""
    location = f"{file_path}:{line_num}" if line_num else file_path
    lines.append(f"{prefix}Location: {location}")

    body = comment.get("body") or comment.get("comment", "")
    # Truncate very long comments for readability
    if len(body) > 500:
        body = body[:497] + "..."
    lines.append(f"{prefix}Body: {body}")
    return "\n".join(lines)


def get_agent_comment(reviewer_comments: dict, index: int) -> dict | None:
    """Look up an agent comment by its index in the combined list.

    The judge indexes agent comments sequentially: inline comments first,
    then general comments.
    """
    inline = reviewer_comments.get("inline_comments", [])
    general = reviewer_comments.get("general_comments", [])
    if index < len(inline):
        return inline[index]
    general_index = index - len(inline)
    if general_index < len(general):
        return general[general_index]
    return None


def prompt_user(prompt: str, valid_inputs: list[str]) -> str:
    """Prompt the user for input, repeating until a valid response."""
    while True:
        response = input(prompt).strip().lower()
        if response in valid_inputs:
            return response
        print(f"  Please enter one of: {', '.join(valid_inputs)}")


def review_match(
    match: dict,
    human_comments: list[dict],
    reviewer_comments: dict,
) -> dict | None:
    """Present a single match verdict for human review.

    Returns a label dict or None if skipped.
    """
    human_idx = match["human_comment_index"]
    verdict = match["verdict"]
    agent_idx = match.get("matched_agent_comment_index")

    print(f"\n{'=' * 70}")
    print("MATCH VERDICT REVIEW")
    print(f"{'=' * 70}")

    # Show the human comment
    human = human_comments[human_idx] if human_idx < len(human_comments) else {}
    print(f"\nHUMAN COMMENT (index {human_idx}):")
    print(format_comment(human, prefix="  "))

    # Show the matched agent comment (if any)
    if agent_idx is not None:
        agent = get_agent_comment(reviewer_comments, agent_idx)
        if agent:
            print(f"\nMATCHED AGENT COMMENT (index {agent_idx}):")
            print(format_comment(agent, prefix="  "))
        else:
            print(f"\nMATCHED AGENT COMMENT (index {agent_idx}): NOT FOUND")
    else:
        print("\nMATCHED AGENT COMMENT: (none)")

    # Show the judge's verdict and reasoning
    print(f"\nJUDGE VERDICT: {verdict}")
    print(f"  Reasoning: {match.get('explanation', '(none)')}")

    # Ask for assessment
    print("\nDo you agree with this verdict?")
    print("  [y] Yes, the verdict is correct")
    print("  [n] No, the verdict is wrong")
    print("  [s] Skip (don't label this one)")
    print("  [q] Quit (save progress and exit)")
    response = prompt_user("  > ", ["y", "n", "s", "q"])

    if response == "q":
        return {"action": "quit"}
    if response == "s":
        return None

    label = {
        "human_comment_index": human_idx,
        "judge_verdict": verdict,
        "human_agrees": response == "y",
    }

    if response == "n":
        print("\n  What should the correct verdict be?")
        print("    [f] full_match")
        print("    [p] partial_match")
        print("    [n] no_match")
        correction = prompt_user("    > ", ["f", "p", "n"])
        verdict_map = {"f": "full_match", "p": "partial_match", "n": "no_match"}
        label["correct_verdict"] = verdict_map[correction]

    return label


def review_novel(
    novel: dict,
    reviewer_comments: dict,
) -> dict | None:
    """Present a single novel agent comment assessment for human review.

    Returns a label dict or None if skipped.
    """
    agent_idx = novel["agent_comment_index"]
    assessment = novel["assessment"]

    print(f"\n{'=' * 70}")
    print("NOVEL COMMENT REVIEW")
    print(f"{'=' * 70}")

    # Show the agent comment
    agent = get_agent_comment(reviewer_comments, agent_idx)
    if agent:
        print(f"\nAGENT COMMENT (index {agent_idx}):")
        print(format_comment(agent, prefix="  "))
    else:
        print(f"\nAGENT COMMENT (index {agent_idx}): NOT FOUND")

    # Show the judge's assessment
    print(f"\nJUDGE ASSESSMENT: {assessment}")
    print(f"  Reasoning: {novel.get('explanation', '(none)')}")

    # Ask for assessment
    print("\nDo you agree with this assessment?")
    print("  [y] Yes, the assessment is correct")
    print("  [n] No, the assessment is wrong")
    print("  [s] Skip")
    print("  [q] Quit (save progress and exit)")
    response = prompt_user("  > ", ["y", "n", "s", "q"])

    if response == "q":
        return {"action": "quit"}
    if response == "s":
        return None

    label = {
        "agent_comment_index": agent_idx,
        "judge_assessment": assessment,
        "human_agrees": response == "y",
    }

    if response == "n":
        print("\n  What should the correct assessment be?")
        print("    [t] true_positive")
        print("    [f] false_positive")
        correction = prompt_user("    > ", ["t", "f"])
        correction_map = {"t": "true_positive", "f": "false_positive"}
        label["correct_assessment"] = correction_map[correction]

    return label


def compute_statistics(labels: dict) -> dict:
    """Compute agreement statistics from all labels."""
    all_labels = []
    for key, entry in labels.items():
        if isinstance(entry, dict) and "human_agrees" in entry:
            all_labels.append(entry)

    if not all_labels:
        return {"total": 0, "agreement_rate": 0.0}

    total = len(all_labels)
    agreed = sum(1 for l in all_labels if l["human_agrees"])
    disagreed = total - agreed

    # Break down by type
    match_labels = [l for l in all_labels if "judge_verdict" in l]
    novel_labels = [l for l in all_labels if "judge_assessment" in l]

    match_agreed = sum(1 for l in match_labels if l["human_agrees"])
    novel_agreed = sum(1 for l in novel_labels if l["human_agrees"])

    return {
        "total_labeled": total,
        "agreed": agreed,
        "disagreed": disagreed,
        "agreement_rate": agreed / total if total else 0.0,
        "match_verdicts": {
            "total": len(match_labels),
            "agreed": match_agreed,
            "rate": match_agreed / len(match_labels) if match_labels else 0.0,
        },
        "novel_assessments": {
            "total": len(novel_labels),
            "agreed": novel_agreed,
            "rate": novel_agreed / len(novel_labels) if novel_labels else 0.0,
        },
    }


def print_statistics(stats: dict) -> None:
    """Print assessment statistics."""
    print(f"\n{'=' * 70}")
    print("ASSESSMENT STATISTICS")
    print(f"{'=' * 70}")

    total = stats["total_labeled"]
    if total == 0:
        print("  No items labeled yet.")
        return

    rate = stats["agreement_rate"]
    target_met = "YES" if rate >= 0.90 else "NO"

    print(f"\n  Total labeled: {total}")
    print(f"  Agreed: {stats['agreed']}")
    print(f"  Disagreed: {stats['disagreed']}")
    print(f"  Agreement rate: {rate:.1%}")
    print(f"  Target (>= 90%): {target_met}")

    ms = stats["match_verdicts"]
    if ms["total"]:
        print(
            f"\n  Match verdicts: {ms['agreed']}/{ms['total']}"
            f" ({ms['rate']:.1%} agreement)"
        )

    ns = stats["novel_assessments"]
    if ns["total"]:
        print(
            f"  Novel assessments: {ns['agreed']}/{ns['total']}"
            f" ({ns['rate']:.1%} agreement)"
        )

    if total < 100:
        print(f"\n  Progress toward 100-pair target: {total}/100")


def main() -> None:
    results_dir = RESULTS_DIR
    if len(sys.argv) > 1:
        results_dir = Path(sys.argv[1])

    print("Judge Assessment Tool")
    print(f"Results directory: {results_dir}")
    print(f"Labels file: {LABELS_FILE}")
    if TRIAL_MODE:
        print(f"TRIAL MODE: reviewing up to {TRIAL_LIMIT} items\n")
    else:
        print()

    # Find MR directories with judge results
    mr_dirs = find_mr_directories(results_dir)
    if not mr_dirs:
        print(f"ERROR: No judge results found in {results_dir}")
        print(f"  Expected to find {JUDGE_RESULTS_FILE} in subdirectories.")
        sys.exit(1)

    print(f"Found {len(mr_dirs)} MR(s) with judge results:")
    for d in mr_dirs:
        print(f"  {d.name}")

    # Load existing labels (for resume support)
    labels: dict = {}
    if LABELS_FILE.exists():
        labels = load_json(LABELS_FILE)
        existing_count = sum(
            1 for v in labels.values()
            if isinstance(v, dict) and "human_agrees" in v
        )
        print(f"\nLoaded {existing_count} existing labels from {LABELS_FILE}")

    # Collect all items to review across all MRs
    items_reviewed = 0
    max_items = TRIAL_LIMIT if TRIAL_MODE else float("inf")
    quit_requested = False

    for mr_dir in mr_dirs:
        if quit_requested:
            break

        mr_name = mr_dir.name
        print(f"\n{'#' * 70}")
        print(f"MR: {mr_name}")
        print(f"{'#' * 70}")

        # Load data files
        human_path = mr_dir / HUMAN_COMMENTS_FILE
        reviewer_path = mr_dir / REVIEWER_COMMENTS_FILE
        judge_path = mr_dir / JUDGE_RESULTS_FILE

        if not human_path.exists():
            print(f"  Skipping: {HUMAN_COMMENTS_FILE} not found")
            continue
        if not reviewer_path.exists():
            print(f"  Skipping: {REVIEWER_COMMENTS_FILE} not found")
            continue

        human_comments = load_json(human_path)
        reviewer_comments = load_json(reviewer_path)
        judge_results = load_json(judge_path)

        # Review match verdicts
        matches = judge_results.get("matches", [])
        for match in matches:
            if items_reviewed >= max_items:
                break

            key = label_key(
                mr_name, "match", match["human_comment_index"],
            )
            if key in labels:
                print(f"\n  (skipping already-labeled: {key})")
                continue

            result = review_match(match, human_comments, reviewer_comments)
            if result and result.get("action") == "quit":
                quit_requested = True
                break
            if result:
                labels[key] = result
                items_reviewed += 1
                save_labels(labels, LABELS_FILE)

        # Review novel agent comment assessments
        novels = judge_results.get("novel_agent_comments", [])
        for novel in novels:
            if items_reviewed >= max_items or quit_requested:
                break

            key = label_key(
                mr_name, "novel", novel["agent_comment_index"],
            )
            if key in labels:
                print(f"\n  (skipping already-labeled: {key})")
                continue

            result = review_novel(novel, reviewer_comments)
            if result and result.get("action") == "quit":
                quit_requested = True
                break
            if result:
                labels[key] = result
                items_reviewed += 1
                save_labels(labels, LABELS_FILE)

    # Final statistics
    stats = compute_statistics(labels)
    print_statistics(stats)

    # Save final stats alongside labels
    stats_file = LABELS_FILE.parent / "statistics.json"
    save_labels(stats, stats_file)
    print(f"\n  Statistics saved to {stats_file}")

    if quit_requested:
        print("\n  Session ended early. Progress saved. Re-run to continue.")


if __name__ == "__main__":
    main()
