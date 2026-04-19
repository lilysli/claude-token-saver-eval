#!/usr/bin/env python3
"""Summarize token usage across benchmark sessions and save to results/results.json."""

import json
import os
import glob
import sys
from collections import defaultdict

RESULTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'results')

METRICS = [
    ("input_tokens",                "Input tokens"),
    ("cache_creation_input_tokens", "Cache creation tokens"),
    ("cache_read_input_tokens",     "Cache read tokens"),
    ("output_tokens",               "Output tokens"),
    ("total_tokens",                "Total tokens (excl. cache reads)"),
    ("n_turns",                     "API turns"),
]


def parse_session(jsonl_path: str) -> dict:
    totals = defaultdict(int)
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            usage = record.get("message", {}).get("usage") if isinstance(record.get("message"), dict) else None
            if not usage:
                continue
            for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"):
                totals[key] += usage.get(key, 0)
            totals["n_turns"] += 1

    totals["total_tokens"] = totals["input_tokens"] + totals["cache_creation_input_tokens"] + totals["output_tokens"]
    return dict(totals)


def format_num(n: int) -> str:
    return f"{n:>10,}"


def pct_change(baseline: int, treatment: int) -> str:
    if baseline == 0:
        return "     N/A"
    return f"{(treatment - baseline) / baseline * 100:>+7.1f}%"


def main():
    files = glob.glob(os.path.join(RESULTS_DIR, "*.jsonl"))
    if not files:
        print(f"No JSONL files found in {RESULTS_DIR}. Run run_benchmark.sh first.")
        sys.exit(1)

    sessions = defaultdict(dict)
    for path in sorted(files):
        stem = os.path.basename(path).replace(".jsonl", "")
        for suffix in ("-tokensaver", "-baseline"):
            if stem.endswith(suffix):
                sessions[stem[:-len(suffix)]][suffix[1:]] = parse_session(path)
                break

    if not sessions:
        print("No valid session files found.")
        sys.exit(1)

    col_w = 14
    header = f"{'Task':<45} {'Metric':<30} {'Baseline':>{col_w}} {'Tokensaver':>{col_w}} {'Change':>9}"
    print()
    print("=" * len(header))
    print("Token Efficiency Benchmark — Results")
    print("=" * len(header))
    print()

    agg = {"baseline": defaultdict(int), "tokensaver": defaultdict(int)}
    n_compared = 0

    for task_id in sorted(sessions):
        conds = sessions[task_id]
        has_both = "baseline" in conds and "tokensaver" in conds
        print(f"Task: {task_id}")
        for metric_key, metric_label in METRICS:
            b_val = conds.get("baseline", {}).get(metric_key, 0)
            t_val = conds.get("tokensaver", {}).get(metric_key, 0)
            b_str = format_num(b_val) if "baseline" in conds else "   missing"
            t_str = format_num(t_val) if "tokensaver" in conds else "   missing"
            print(f"  {metric_label:<32} {b_str}  {t_str}  {pct_change(b_val, t_val) if has_both else '     N/A'}")
            if has_both:
                agg["baseline"][metric_key] += b_val
                agg["tokensaver"][metric_key] += t_val
        if has_both:
            n_compared += 1
        print()

    if n_compared > 0:
        print("=" * len(header))
        print(f"AGGREGATE ({n_compared} tasks with both conditions)")
        print("=" * len(header))
        for metric_key, metric_label in METRICS:
            b_val, t_val = agg["baseline"][metric_key], agg["tokensaver"][metric_key]
            print(f"  {metric_label:<32} {format_num(b_val)}  {format_num(t_val)}  {pct_change(b_val, t_val)}")
        total_b, total_t = agg["baseline"]["total_tokens"], agg["tokensaver"]["total_tokens"]
        saved = total_b - total_t
        print(f"\n  Net token savings: {saved:,} tokens ({saved / total_b * 100 if total_b else 0:.1f}% reduction)\n")

    summary = {task_id: {c: dict(data) for c, data in conds.items()} for task_id, conds in sessions.items()}
    summary["_aggregate"] = {c: dict(data) for c, data in agg.items()}
    summary["_n_compared"] = n_compared

    out = os.path.join(RESULTS_DIR, "results.json")
    with open(out, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
