#!/bin/bash
# Token Efficiency Benchmark — tokensaver vs. baseline
#
# Runs 10 WildBench Coding & Debugging tasks under two conditions:
#
#   baseline    — original prompt sent directly to Claude Code
#   tokensaver  — prompt refined by the tokensaver hook before Claude sees it
#
# Each session runs from a unique temp directory so Claude Code writes its
# session JSONL to a predictable, isolated location. The JSONL is copied
# to results/ for analysis. The temp directory is deleted after each run.
#
# Token data is collected from Claude Code's session JSONL files
# (~/.claude/projects/<hash>/*.jsonl), which record usage per API call.
#
# Usage:
#   ./run_benchmark.sh                  # run all 10 tasks, both conditions
#   ./run_benchmark.sh --task 3         # run task 3 only
#   ./run_benchmark.sh --task 1-5       # run tasks 1 through 5
#   ./run_benchmark.sh --baseline-only
#   ./run_benchmark.sh --tokensaver-only
#
# Prerequisites:
#   1. claude CLI installed and logged in  (run: claude login)
#   2. tokensaver hook in ~/.claude/settings.json
#      (see: https://github.com/lilysijiali/claude-token-saver)
#   3. Ollama running with qwen2.5:32b    (run: ollama serve)
#   4. prompts/tasks.json generated       (run: python3 scripts/fetch_tasks.py)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="$SCRIPT_DIR/results"
TASKS_FILE="$SCRIPT_DIR/prompts/tasks.json"
WS_BASE="/tmp/tokensaver-bench"

RUN_BASELINE=true
RUN_TOKENSAVER=true
TASK_FROM=""
TASK_TO=""

parse_task_arg() {
    local val="$1"
    if [[ "$val" == *-* ]]; then
        TASK_FROM="${val%-*}"
        TASK_TO="${val#*-}"
    else
        TASK_FROM="$val"
        TASK_TO="$val"
    fi
}

for arg in "$@"; do
    case $arg in
        --baseline-only)   RUN_TOKENSAVER=false ;;
        --tokensaver-only) RUN_BASELINE=false ;;
        --task=*)          parse_task_arg "${arg#--task=}" ;;
        --task)            shift; parse_task_arg "$1" ;;
    esac
done

# ── Preflight checks ──────────────────────────────────────────────────────────
if [ ! -f "$TASKS_FILE" ]; then
    echo "ERROR: prompts/tasks.json not found."
    echo "       Run: python3 scripts/fetch_tasks.py"
    exit 1
fi
if ! command -v claude &>/dev/null; then
    echo "ERROR: claude CLI not found. Install Claude Code."
    exit 1
fi

mkdir -p "$RESULTS_DIR" "$WS_BASE"

TASKS=$(python3 -c "
import json
with open('$TASKS_FILE') as f:
    tasks = json.load(f)
for t in tasks:
    print(t['id'])
")
TOTAL=$(echo "$TASKS" | wc -l | tr -d ' ')

echo "=== Token Efficiency Benchmark ==="
echo "    Tasks: $TOTAL | Conditions: baseline + tokensaver | Sessions: $((TOTAL * 2))"
[ -n "$ONLY_TASK" ] && echo "    Running task $ONLY_TASK only."
echo

# ── Compute Claude Code project dir from a CWD ───────────────────────────────
# Claude Code derives its session directory by replacing '/' and '_' with '-'
# in the real (symlink-resolved) working directory path.
# e.g. /tmp/tokensaver-bench/abc123-baseline
#   → /private/tmp/tokensaver-bench/abc123-baseline   (macOS symlink resolution)
#   → ~/.claude/projects/-private-tmp-tokensaver-bench-abc123-baseline/
cwd_to_project_dir() {
    local REAL_CWD
    REAL_CWD=$(realpath "$1" 2>/dev/null || echo "$1")
    local HASH
    HASH=$(echo "$REAL_CWD" | sed 's|[/_]|-|g')
    echo "$HOME/.claude/projects/$HASH"
}

# ── Run one session ───────────────────────────────────────────────────────────
run_session() {
    local TASK_ID="$1"
    local CONDITION="$2"   # baseline | tokensaver
    local PROMPT="$3"

    local OUT_JSONL="$RESULTS_DIR/${TASK_ID}-${CONDITION}.jsonl"
    local OUT_TXT="$RESULTS_DIR/${TASK_ID}-${CONDITION}.output.txt"

    if [ -f "$OUT_JSONL" ]; then
        echo "  [SKIP] $CONDITION — results already exist"
        return
    fi

    # Each session gets its own working directory → its own Claude Code project
    # directory → no JSONL files are ever mixed between sessions.
    local SESSION_DIR="$WS_BASE/${TASK_ID}-${CONDITION}"
    mkdir -p "$SESSION_DIR"
    local PROJECT_DIR
    PROJECT_DIR=$(cwd_to_project_dir "$SESSION_DIR")

    echo "  [RUN] $CONDITION..."

    if [ "$CONDITION" = "baseline" ]; then
        # Send the prompt directly — tokensaver hook is off by default and
        # is never activated, so this session is a clean baseline.
        ( cd "$SESSION_DIR" && printf "%s\n" "$PROMPT" | \
            claude --dangerously-skip-permissions --allowedTools "Read,Write,Edit,Glob,Grep" 2>&1 | tee "$OUT_TXT" ) || true
    else
        # Activate the tokensaver hook for this prompt via three piped turns:
        #   turn 1: --tokensaver   → hook activates for the next prompt
        #   turn 2: <prompt>       → hook refines it with Ollama, shows gate
        #   turn 3: y              → accept refined prompt; Claude processes it
        ( cd "$SESSION_DIR" && printf -- "--tokensaver\n%s\ny\n" "$PROMPT" | \
            claude --dangerously-skip-permissions --allowedTools "Read,Write,Edit,Glob,Grep" 2>&1 | tee "$OUT_TXT" ) || true
    fi

    # Copy the session JSONL from its deterministic project directory
    local SESSION_JSONL
    SESSION_JSONL=$(find "$PROJECT_DIR" -name "*.jsonl" \
        -not -path "*/subagents/*" 2>/dev/null | sort | tail -1)

    if [ -z "$SESSION_JSONL" ]; then
        echo "  [ERROR] Session JSONL not found in: $PROJECT_DIR"
        echo "          Check that the session ran and claude is logged in."
    else
        cp "$SESSION_JSONL" "$OUT_JSONL"
        echo "  [DONE] $CONDITION — session saved to results/"
    fi

    rm -rf "$SESSION_DIR"
}

# ── Main loop ─────────────────────────────────────────────────────────────────
i=0
while IFS= read -r TASK_ID; do
    i=$((i + 1))
    [ -n "$TASK_FROM" ] && [ "$i" -lt "$TASK_FROM" ] && continue
    [ -n "$TASK_TO"   ] && [ "$i" -gt "$TASK_TO"   ] && continue

    echo "[$i/$TOTAL] $TASK_ID"

    PROMPT=$(python3 -c "
import json
with open('$TASKS_FILE') as f:
    tasks = json.load(f)
for t in tasks:
    if t['id'] == '$TASK_ID':
        print(t['prompt'], end='')
        break
")

    $RUN_BASELINE   && run_session "$TASK_ID" "baseline"   "$PROMPT"
    $RUN_TOKENSAVER && run_session "$TASK_ID" "tokensaver" "$PROMPT"
    echo
done <<< "$TASKS"

echo "=== Done ==="
echo "  Analyze tokens:   python3 scripts/analyze_tokens.py"
echo "  Score responses:  python3 scripts/score_checklists.py"
