#!/usr/bin/env python3
"""
Parse session JSONL files from results/ and produce a token comparison report.

For each task, sums all usage blocks across the full session:
  - input_tokens
  - cache_creation_input_tokens
  - cache_read_input_tokens
  - output_tokens
  - total_tokens (input + cache_creation + output, excluding cache reads which are cheaper)

Prints a table comparing baseline vs. tokensaver per task, plus aggregates.
"""

import json
import os
import glob
import sys
from collections import defaultdict

RESULTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'results')


def parse_session(jsonl_path: str) -> dict:
    """Sum all usage blocks in a session JSONL."""
    totals = defaultdict(int)
    n_turns = 0

    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg = record.get("message", {})
            if not isinstance(msg, dict):
                continue

            usage = msg.get("usage")
            if not usage:
                continue

            totals["input_tokens"]                 += usage.get("input_tokens", 0)
            totals["cache_creation_input_tokens"]  += usage.get("cache_creation_input_tokens", 0)
            totals["cache_read_input_tokens"]      += usage.get("cache_read_input_tokens", 0)
            totals["output_tokens"]                += usage.get("output_tokens", 0)
            n_turns += 1

    totals["n_turns"] = n_turns
    # "effective" total: all tokens that consumed generation capacity
    # cache reads are excluded as they are a fraction of the cost
    totals["total_tokens"] = (
        totals["input_tokens"] +
        totals["cache_creation_input_tokens"] +
        totals["output_tokens"]
    )
    return dict(totals)


def format_num(n: int) -> str:
    return f"{n:>10,}"


def pct_change(baseline: int, treatment: int) -> str:
    if baseline == 0:
        return "     N/A"
    change = (treatment - baseline) / baseline * 100
    sign = "+" if change > 0 else ""
    return f"{sign}{change:>+6.1f}%"


def main():
    files = glob.glob(os.path.join(RESULTS_DIR, "*.jsonl"))
    if not files:
        print(f"No JSONL files found in {RESULTS_DIR}")
        print("Run run_benchmark.sh first.")
        sys.exit(1)

    # Group by task_id
    sessions = defaultdict(dict)
    for path in sorted(files):
        fname = os.path.basename(path)
        # Expected: <task_id>-<condition>.jsonl
        stem = fname.replace(".jsonl", "")
        if not (stem.endswith("-baseline") or stem.endswith("-tokensaver")):
            continue
        for suffix in ("-tokensaver", "-baseline"):
            if stem.endswith(suffix):
                task_id = stem[: -len(suffix)]
                condition = suffix[1:]
                break
        sessions[task_id][condition] = parse_session(path)

    if not sessions:
        print("No valid session files found.")
        sys.exit(1)

    # ── Print report ──────────────────────────────────────────────────────────
    metrics = [
        ("input_tokens",                "Input tokens"),
        ("cache_creation_input_tokens", "Cache creation tokens"),
        ("cache_read_input_tokens",     "Cache read tokens"),
        ("output_tokens",               "Output tokens"),
        ("total_tokens",                "Total tokens (excl. cache reads)"),
        ("n_turns",                     "API turns"),
    ]

    col_w = 14
    header = f"{'Task':<45} {'Metric':<30} {'Baseline':>{col_w}} {'Tokensaver':>{col_w}} {'Change':>9}"
    print()
    print("=" * len(header))
    print("SWE-bench Token Efficiency Benchmark — Results")
    print("=" * len(header))
    print()

    agg = {"baseline": defaultdict(int), "tokensaver": defaultdict(int)}
    n_compared = 0

    for task_id in sorted(sessions):
        conds = sessions[task_id]
        has_both = "baseline" in conds and "tokensaver" in conds

        print(f"Task: {task_id}")
        for metric_key, metric_label in metrics:
            b_val = conds.get("baseline", {}).get(metric_key, 0)
            t_val = conds.get("tokensaver", {}).get(metric_key, 0)

            b_str = format_num(b_val) if "baseline" in conds else "   missing"
            t_str = format_num(t_val) if "tokensaver" in conds else "   missing"
            chg   = pct_change(b_val, t_val) if has_both else "     N/A"

            print(f"  {metric_label:<32} {b_str}  {t_str}  {chg}")

            if has_both:
                agg["baseline"][metric_key]   += b_val
                agg["tokensaver"][metric_key] += t_val

        if has_both:
            n_compared += 1
        print()

    # ── Aggregate ─────────────────────────────────────────────────────────────
    if n_compared > 0:
        print("=" * len(header))
        print(f"AGGREGATE ({n_compared} tasks with both conditions)")
        print("=" * len(header))
        for metric_key, metric_label in metrics:
            b_val = agg["baseline"][metric_key]
            t_val = agg["tokensaver"][metric_key]
            print(f"  {metric_label:<32} {format_num(b_val)}  {format_num(t_val)}  {pct_change(b_val, t_val)}")
        print()

        total_b = agg["baseline"]["total_tokens"]
        total_t = agg["tokensaver"]["total_tokens"]
        saved   = total_b - total_t
        print(f"  Net token savings: {saved:,} tokens "
              f"({(saved/total_b*100) if total_b else 0:.1f}% reduction)")
        print()

    # Save JSON summary
    summary = {}
    for task_id, conds in sessions.items():
        summary[task_id] = {c: dict(data) for c, data in conds.items()}
    summary["_aggregate"] = {c: dict(data) for c, data in agg.items()}
    summary["_n_compared"] = n_compared

    out = os.path.join(RESULTS_DIR, "results.json")
    with open(out, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"Full summary saved to {out}")


if __name__ == "__main__":
    main()
