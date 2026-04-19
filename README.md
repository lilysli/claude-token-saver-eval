# Claude Token Saver — Benchmark Evaluation

Measures whether [tokensaver](https://github.com/lilysijiali/claude-token-saver) — a Claude Code hook that refines user prompts with a local LLM before Claude processes them — reduces token usage without degrading response quality.

## Dataset

30 tasks were selected from [WildBench v2](https://huggingface.co/datasets/allenai/WildBench/viewer/v2) under the **Coding & Debugging** primary tag. WildBench prompts are collected from real users interacting with ChatGPT, making them more representative of how people naturally write prompts. Tokensaver improves the prompts, refining them to decrease the thinking and output of Claude Code, thus lowering token usage.

Each task is stored in `prompts/tasks.json` with:
- `prompt` — the original user message
- `intent` — a one-sentence description of what the user actually wants
- `checklist` — a list of criteria the ideal response should satisfy

## Evaluation Methodology

Each task is run under two conditions:

| Condition | How it works |
|-----------|-------------|
| **baseline** | Prompt sent directly to Claude Code |
| **tokensaver** | Prompt first refined by the tokensaver hook (via Ollama/qwen2.5:32b), then sent to Claude Code |

**Token efficiency** is measured by summing all API usage across a session from Claude Code's session JSONL files (`~/.claude/projects/<hash>/*.jsonl`). Output tokens are the primary metric — they are the most expensive and the direct target of prompt refinement.

**Response quality** is measured along two dimensions:
- **Checklist score** — a Claude judge evaluates how many checklist criteria the response satisfies
- **Intent score** — a Claude judge rates how well the response addresses the user's actual intent (1–5)

## Scripts

### `run_benchmark.sh`
Runs all 30 tasks under both conditions. Each session gets an isolated working directory so JSONL files are never mixed. After each run, the session JSONL is copied to `results/` and the temp directory is deleted.

```bash
./run_benchmark.sh              # all tasks, both conditions
./run_benchmark.sh --task 5     # single task
./run_benchmark.sh --task 1-10  # range
./run_benchmark.sh --baseline-only
./run_benchmark.sh --tokensaver-only
```

### `scripts/analyze_tokens.py`
Parses the session JSONL files in `results/`, sums token usage per API call, and writes token data to `results/results.json`. Prints a per-task comparison table with aggregate totals.

```bash
python3 scripts/analyze_tokens.py
```

### `scripts/score_checklists.py`
Scores each session's output against its task checklist and intent using Claude as a judge. Merges `intent_score` and `checklist` fields into `results/results.json`.

```bash
python3 scripts/score_checklists.py
```

## Replicating the Experiment

> **Run this in an isolated environment.** The benchmark executes arbitrary Claude Code sessions from real user prompts. These sessions have filesystem access and may run shell commands. Use Docker (or a VM) to ensure nothing affects your host system.

### Prerequisites

1. Claude Code CLI installed and authenticated
2. [tokensaver](https://github.com/lilysijiali/claude-token-saver) hook configured in `~/.claude/settings.json`
3. Ollama running with `qwen2.5:32b` (`ollama serve`)
4. Tasks fetched: `python3 scripts/fetch_tasks.py`

### Steps

```bash
# 1. Run all benchmark sessions
./run_benchmark.sh

# 2. Compute token statistics
python3 scripts/analyze_tokens.py

# 3. Score response quality
python3 scripts/score_checklists.py
```

Results are written to `results/results.json`. Visualisations are in `results_visualization.ipynb`.

## Results

| Metric | Baseline | Tokensaver | Change |
|--------|----------|------------|--------|
| Output tokens (avg) | ~8,015 | ~5,923 | **−26.1%** |
| Total tokens (avg) | ~20,135 | ~18,017 | −10.5% |
| Checklist pass rate | 78% | 73% | −5pp |
| Intent score (avg) | 4.1/5 | 4.1/5 | 0 |

**Output tokens dropped 26.1%** — output tokens are billed at the highest rate and are the direct target of prompt refinement, so this is the most meaningful efficiency gain.

**Quality held up on average**, with intent scores unchanged (4.1/5 both conditions) and checklist pass rates nearly equivalent. However, there is per-task variance: tokensaver improved quality on 5 tasks and degraded it on 9. The failure mode is over-compression — when tokensaver strips what it considers verbosity, it occasionally removes load-bearing constraints from the prompt, causing Claude to produce a correct-looking but subtly wrong response. However, when using Claude Token Saver in real life, the user will always first review the prompts provided, allowing them to edit and minimizing misunderstandings.
