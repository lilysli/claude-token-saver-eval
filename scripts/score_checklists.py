#!/usr/bin/env python3
"""
Score each session's output against the WildBench checklist.
Batches all checklist items into ONE claude call per task/condition (20 total).
Outputs results/_checklist_scores.json and prints a summary table.
"""

import json
import os
import subprocess

RESULTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'results')
TASKS_FILE  = os.path.join(os.path.dirname(__file__), '..', 'prompts', 'tasks.json')


def load_tasks():
    with open(TASKS_FILE) as f:
        return json.load(f)


def load_output(task_id, condition):
    path = os.path.join(RESULTS_DIR, f"{task_id}__{condition}.output.txt")
    if not os.path.exists(path):
        # try dash separator
        path = os.path.join(RESULTS_DIR, f"{task_id}-{condition}.output.txt")
    if not os.path.exists(path):
        return ""
    with open(path) as f:
        return f.read().strip()


def score_all_items(checklist: list, response: str, intent: str) -> list:
    """Single claude call to score all checklist items at once."""
    items_formatted = "\n".join(
        f"{i+1}. {item}" for i, item in enumerate(checklist)
    )
    prompt = (
        f"You are evaluating an AI assistant's response.\n\n"
        f"Task intent: {intent}\n\n"
        f"Response to evaluate:\n{response[:4000]}\n\n"
        f"Evaluate each criterion below. Reply with ONLY a JSON array of booleans "
        f"(true/false), one per criterion, in order. No explanation.\n\n"
        f"Criteria:\n{items_formatted}"
    )
    try:
        result = subprocess.run(
            ['claude', '--print', prompt],
            capture_output=True, text=True, timeout=120
        )
        output = result.stdout.strip()
        start = output.find('[')
        end   = output.rfind(']') + 1
        if start >= 0 and end > start:
            scores = json.loads(output[start:end])
            if isinstance(scores, list) and len(scores) == len(checklist):
                return [bool(s) for s in scores]
    except Exception as e:
        print(f"    WARNING: scoring call failed — {e}")
    return [False] * len(checklist)


def score_intent_alignment(response: str, intent: str) -> dict:
    """Single claude call: did the response address the user's actual intent?
    Returns a score 1-5 and a one-line reason."""
    prompt = (
        f"You are evaluating whether an AI response addresses the user's actual intent.\n\n"
        f"User's intent: {intent}\n\n"
        f"Response to evaluate:\n{response[:4000]}\n\n"
        f"Rate how well the response addresses the intent on a scale of 1-5:\n"
        f"  1 = completely missed the intent\n"
        f"  2 = partially addressed it\n"
        f"  3 = mostly addressed it\n"
        f"  4 = well addressed\n"
        f"  5 = fully and precisely addressed\n\n"
        f"Reply with ONLY valid JSON: {{\"score\": <1-5>, \"reason\": \"<one sentence>\"}}"
    )
    try:
        result = subprocess.run(
            ['claude', '--print', prompt],
            capture_output=True, text=True, timeout=120
        )
        output = result.stdout.strip()
        start = output.find('{')
        end   = output.rfind('}') + 1
        if start >= 0 and end > start:
            data = json.loads(output[start:end])
            if 'score' in data:
                return {'score': int(data['score']), 'reason': data.get('reason', '')}
    except Exception as e:
        print(f"    WARNING: intent scoring failed — {e}")
    return {'score': 0, 'reason': 'scoring failed'}


def main():
    tasks = load_tasks()
    all_scores = {}

    for task in tasks:
        tid       = task['id']
        checklist = task.get('checklist', [])
        intent    = task.get('intent', '')

        if not checklist:
            continue

        all_scores[tid] = {}
        print(f"\nTask: {tid}")
        print(f"  Intent: {intent[:90]}")

        for condition in ['baseline', 'tokensaver']:
            output = load_output(tid, condition)
            if not output:
                print(f"  [{condition}] no output file — skipping")
                all_scores[tid][condition] = None
                continue

            print(f"  [{condition}] checklist ({len(checklist)} items)...", end=' ', flush=True)
            item_scores = score_all_items(checklist, output, intent)
            n_pass = sum(item_scores)
            pct    = n_pass / len(checklist) * 100
            print(f"{n_pass}/{len(checklist)} ({pct:.0f}%)", end='  ')

            print(f"intent alignment...", end=' ', flush=True)
            intent_score = score_intent_alignment(output, intent)
            print(f"{intent_score['score']}/5 — {intent_score['reason'][:60]}")

            all_scores[tid][condition] = {
                'passed':        n_pass,
                'total':         len(checklist),
                'pct':           round(pct, 1),
                'items':         item_scores,
                'intent_score':  intent_score['score'],
                'intent_reason': intent_score['reason'],
            }

    # ── Summary table ─────────────────────────────────────────────────────────
    print()
    print("=" * 90)
    print("RESULTS SUMMARY")
    print("=" * 90)
    print(f"  {'Task ID':<20} {'B checklist':>12} {'TS checklist':>13} {'Δ':>5}  {'B intent':>9} {'TS intent':>9} {'Δ':>5}")
    print("  " + "-" * 76)

    total_b_pass = total_t_pass = total_items = 0
    total_b_intent = total_t_intent = 0
    compared = 0

    for task in tasks:
        tid  = task['id']
        cond = all_scores.get(tid, {})
        b    = cond.get('baseline')
        t    = cond.get('tokensaver')

        b_cl  = f"{b['passed']}/{b['total']} ({b['pct']:.0f}%)" if b else "missing"
        t_cl  = f"{t['passed']}/{t['total']} ({t['pct']:.0f}%)" if t else "missing"
        b_int = f"{b['intent_score']}/5" if b else "—"
        t_int = f"{t['intent_score']}/5" if t else "—"

        if b and t:
            cl_delta  = f"{t['pct'] - b['pct']:+.0f}pp"
            int_delta = f"{t['intent_score'] - b['intent_score']:+d}"
            total_b_pass   += b['passed'];   total_t_pass   += t['passed']
            total_items    += b['total']
            total_b_intent += b['intent_score']; total_t_intent += t['intent_score']
            compared += 1
        else:
            cl_delta = int_delta = "N/A"

        print(f"  {tid:<20} {b_cl:>12} {t_cl:>13} {cl_delta:>5}  {b_int:>9} {t_int:>9} {int_delta:>5}")

    if compared:
        print("  " + "-" * 76)
        b_pct  = total_b_pass / total_items * 100
        t_pct  = total_t_pass / total_items * 100
        b_iavg = total_b_intent / compared
        t_iavg = total_t_intent / compared
        print(f"  {'TOTAL/AVG':<20} {total_b_pass}/{total_items} ({b_pct:.0f}%)"
              f"  {total_t_pass}/{total_items} ({t_pct:.0f}%) {t_pct-b_pct:>+.0f}pp"
              f"  {b_iavg:>7.1f}/5 {t_iavg:>7.1f}/5 {t_iavg-b_iavg:>+.1f}")

    # Save
    out_path = os.path.join(RESULTS_DIR, '_checklist_scores.json')
    with open(out_path, 'w') as f:
        json.dump(all_scores, f, indent=2)
    print(f"\nSaved to {out_path}")


if __name__ == '__main__':
    main()
