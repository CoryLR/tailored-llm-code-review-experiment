"""Summarize PoC #2 judge results into a human-readable report.

Aggregates judge outputs across PRs and conditions, produces side-by-side
comparisons, a flat verdict list for manual validation, and cost summaries.

Usage:
    python proof-of-concept-2/6_summarize.py
    python proof-of-concept-2/6_summarize.py --data-dir proof-of-concept-2/data/storybook-backup-append-system-prompt
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
POC2_DIR = Path(__file__).resolve().parent

DEFAULT_DATA_DIR = POC2_DIR / "data" / "storybook"

CONDITIONS = ["generic", "tuned"]


def load_json(filepath: Path) -> dict | list | None:
    if not filepath.exists():
        return None
    return json.loads(filepath.read_text())


def load_pr_data(data_dir: Path, pr_number: int, condition: str) -> dict:
    """Load all relevant data for one PR + condition."""
    test_dir = data_dir / "test"

    # Find the evaluation revision (most human comments)
    pr_dir = test_dir / f"pr_{pr_number}"
    best_comments = []
    best_rev = None
    if pr_dir.exists():
        for rev_dir in sorted(pr_dir.glob("rev_*")):
            comments_file = rev_dir / "human_comments.json"
            if comments_file.exists():
                comments = json.loads(comments_file.read_text())
                if len(comments) > len(best_comments):
                    best_comments = comments
                    best_rev = rev_dir.name.replace("rev_", "")

    review = load_json(data_dir / "reviews" / condition / f"pr_{pr_number}.json")
    review_raw = load_json(data_dir / "reviews" / condition / f"pr_{pr_number}_raw.json")
    judgment = load_json(data_dir / "judgments" / condition / f"pr_{pr_number}.json")
    judgment_raw = load_json(data_dir / "judgments" / condition / f"pr_{pr_number}_raw.json")

    return {
        "pr_number": pr_number,
        "condition": condition,
        "revision": best_rev,
        "human_comments": best_comments,
        "review": review,
        "review_cost": (review_raw or {}).get("total_cost_usd", 0) or 0,
        "review_duration": (review_raw or {}).get("duration_ms", 0) or 0,
        "judgment": judgment,
        "judgment_cost": (judgment_raw or {}).get("total_cost_usd", 0) or 0,
    }


def format_verdict_list(all_data: list[dict]) -> str:
    """Flat list of all verdicts for manual validation."""
    lines = ["# Verdict List for Manual Validation", ""]

    for d in all_data:
        j = d["judgment"]
        if not j or j.get("parse_error"):
            continue

        pr = d["pr_number"]
        cond = d["condition"]
        lines.append(f"## PR #{pr} [{cond}]")
        lines.append("")

        lines.append("### Human Comment Matches")
        for m in j.get("matches", []):
            verdict = m["verdict"]
            marker = {"full_match": "[FULL]", "partial_match": "[PARTIAL]", "no_match": "[MISS]"}[verdict]
            lines.append(f"- {marker} Human #{m['human_comment_index']}: {m['human_summary']}")
            if m.get("matched_agent_comment_index") is not None:
                lines.append(f"  Matched agent #{m['matched_agent_comment_index']}")
            lines.append(f"  Reason: {m['explanation']}")
            lines.append("")

        lines.append("### Novel Agent Comments")
        for n in j.get("novel_agent_comments", []):
            marker = "[TP]" if n["assessment"] == "true_positive" else "[FP]"
            lines.append(f"- {marker} Agent #{n['agent_comment_index']}: {n['explanation']}")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def format_side_by_side(all_data: list[dict]) -> str:
    """Compare generic vs tuned for each PR."""
    lines = ["# Generic vs Tuned Comparison", ""]

    # Group by PR
    by_pr: dict[int, dict[str, dict]] = {}
    for d in all_data:
        by_pr.setdefault(d["pr_number"], {})[d["condition"]] = d

    for pr_number, conditions in sorted(by_pr.items()):
        generic = conditions.get("generic", {})
        tuned = conditions.get("tuned", {})
        human_count = len(generic.get("human_comments", []))

        lines.append(f"## PR #{pr_number} ({human_count} human comments)")
        lines.append("")

        for label, d in [("Generic", generic), ("Tuned", tuned)]:
            review = d.get("review", {}) or {}
            judgment = d.get("judgment", {}) or {}
            summary = judgment.get("summary", {})

            inline = len(review.get("inline_comments", []))
            general = len(review.get("general_comments", []))
            full = summary.get("full_matches", 0)
            partial = summary.get("partial_matches", 0)
            no = summary.get("no_matches", 0)
            novel_list = judgment.get("novel_agent_comments", [])
            tp = sum(1 for n in novel_list if n.get("assessment") == "true_positive")
            fp = sum(1 for n in novel_list if n.get("assessment") == "false_positive")

            lines.append(f"**{label}**: {inline} inline + {general} general comments")
            lines.append(f"  Matches: {full} full, {partial} partial, {no} miss")
            lines.append(f"  Novel: {tp} TP, {fp} FP")
            lines.append(f"  Cost: ${d.get('review_cost', 0):.2f} (review) + ${d.get('judgment_cost', 0):.2f} (judge)")
            lines.append("")

        # Highlight divergences
        g_review = generic.get("review", {}) or {}
        t_review = tuned.get("review", {}) or {}
        g_judgment = generic.get("judgment", {}) or {}
        t_judgment = tuned.get("judgment", {}) or {}

        # Find comments unique to each condition (by file_path + approximate content)
        g_comments = [(c.get("file_path", ""), c.get("comment", "")[:80])
                      for c in g_review.get("inline_comments", [])]
        t_comments = [(c.get("file_path", ""), c.get("comment", "")[:80])
                      for c in t_review.get("inline_comments", [])]

        # Comments in generic but not tuned (by file path)
        g_files = {c[0] for c in g_comments}
        t_files = {c[0] for c in t_comments}
        only_generic_files = g_files - t_files
        only_tuned_files = t_files - g_files

        if only_generic_files or only_tuned_files:
            lines.append("**Divergences (by file):**")
            for f in sorted(only_generic_files):
                lines.append(f"  Generic only: {f}")
            for f in sorted(only_tuned_files):
                lines.append(f"  Tuned only: {f}")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def format_aggregate(all_data: list[dict], data_dir: Path) -> str:
    """Aggregate metrics and cost summary."""
    lines = ["# Aggregate Results", ""]

    # Load optimizer cost
    opt_log = load_json(data_dir / "optimization" / "log.json")
    opt_cost = sum(step.get("cost_usd", 0) for step in (opt_log or []))
    opt_rules = 0
    if opt_log:
        last_step = opt_log[-1]
        opt_rules = last_step.get("rules_after", 0)

    lines.append(f"**Optimizer**: {opt_rules} rules extracted, ${opt_cost:.2f}")
    lines.append("")

    for condition in CONDITIONS:
        cond_data = [d for d in all_data if d["condition"] == condition]
        if not cond_data:
            continue

        total_human = sum(len(d["human_comments"]) for d in cond_data)
        total_full = 0
        total_partial = 0
        total_no = 0
        total_novel_tp = 0
        total_novel_fp = 0
        total_review_cost = 0.0
        total_judge_cost = 0.0
        total_inline = 0
        total_general = 0

        for d in cond_data:
            j = d.get("judgment", {}) or {}
            s = j.get("summary", {})
            total_full += s.get("full_matches", 0)
            total_partial += s.get("partial_matches", 0)
            total_no += s.get("no_matches", 0)

            novel_list = j.get("novel_agent_comments", [])
            total_novel_tp += sum(1 for n in novel_list if n.get("assessment") == "true_positive")
            total_novel_fp += sum(1 for n in novel_list if n.get("assessment") == "false_positive")

            total_review_cost += d.get("review_cost", 0)
            total_judge_cost += d.get("judgment_cost", 0)

            r = d.get("review", {}) or {}
            total_inline += len(r.get("inline_comments", []))
            total_general += len(r.get("general_comments", []))

        recall_full = total_full / total_human if total_human > 0 else 0
        recall_partial = (total_full + total_partial) / total_human if total_human > 0 else 0

        lines.append(f"## {condition.title()} Condition")
        lines.append(f"  PRs evaluated: {len(cond_data)}")
        lines.append(f"  Human comments: {total_human}")
        lines.append(f"  Agent comments: {total_inline} inline + {total_general} general")
        lines.append(f"  Matches: {total_full} full, {total_partial} partial, {total_no} miss")
        lines.append(f"  Recall (full): {recall_full:.0%}")
        lines.append(f"  Recall (full+partial): {recall_partial:.0%}")
        lines.append(f"  Novel: {total_novel_tp} TP, {total_novel_fp} FP")
        lines.append(f"  Review cost: ${total_review_cost:.2f}")
        lines.append(f"  Judge cost: ${total_judge_cost:.2f}")
        lines.append("")

    total_cost = opt_cost + sum(d.get("review_cost", 0) + d.get("judgment_cost", 0) for d in all_data)
    lines.append(f"**Total PoC #2 cost**: ${total_cost:.2f}")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    data_dir = DEFAULT_DATA_DIR
    if "--data-dir" in sys.argv:
        idx = sys.argv.index("--data-dir")
        if idx + 1 < len(sys.argv):
            data_dir = Path(sys.argv[idx + 1])
            if not data_dir.is_absolute():
                data_dir = REPO_ROOT / data_dir

    print(f"Summarizing: {data_dir.relative_to(REPO_ROOT)}")

    split = load_json(data_dir / "split.json")
    if not split:
        print("ERROR: split.json not found")
        sys.exit(1)

    test_prs = split["test"]

    # Load all data
    all_data = []
    for pr_number in test_prs:
        for condition in CONDITIONS:
            d = load_pr_data(data_dir, pr_number, condition)
            all_data.append(d)

    # Generate report sections
    aggregate = format_aggregate(all_data, data_dir)
    comparison = format_side_by_side(all_data)
    verdicts = format_verdict_list(all_data)

    # Combine into single report
    report = "\n\n".join([aggregate, comparison, verdicts])

    # Save
    results_dir = data_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    output_file = results_dir / "summary.md"
    output_file.write_text(report)
    print(f"Saved: {output_file.relative_to(REPO_ROOT)}")

    # Also print aggregate to stdout
    print()
    print(aggregate)


if __name__ == "__main__":
    main()
