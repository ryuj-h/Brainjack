#!/bin/bash
# Launch Claude Code through the Codex OAuth proxy.
# Usage: bash connect.sh [claude args...]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_PORT=8741
SERVER_PID=""

# ── Pre-checks ──
AUTH_FILE="$HOME/.codex/auth.json"
MODELS_CACHE="$HOME/.codex/models_cache.json"

if [ ! -f "$AUTH_FILE" ]; then
    echo ""
    echo "  ERROR: Codex OAuth 토큰을 찾을 수 없습니다."
    echo "         $AUTH_FILE 파일이 없습니다."
    echo "         먼저 'codex login'을 실행해주세요."
    exit 1
fi

# ── Load models (API first, cache fallback) ──
MENU_DATA=$(python3 -c "
import json, sys, pathlib, httpx

cache = pathlib.Path('$MODELS_CACHE')
auth = json.loads(pathlib.Path('$AUTH_FILE').read_text())
tokens = auth.get('tokens', {})
data = None

# Try API first
try:
    resp = httpx.get(
        'https://chatgpt.com/backend-api/codex/models?client_version=1.0.0',
        headers={
            'Authorization': f'Bearer {tokens[\"access_token\"]}',
            'ChatGPT-Account-ID': tokens.get('account_id', ''),
            'Content-Type': 'application/json',
            'Origin': 'https://chatgpt.com',
        },
        timeout=5,
    )
    if resp.status_code == 200:
        data = resp.json()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data, indent=2))
except Exception:
    pass

# Fallback to cache
if not data or not data.get('models'):
    if cache.exists():
        data = json.loads(cache.read_text())

models = (data or {}).get('models', [])
if not models:
    sys.exit(1)

for m in models:
    efforts = [r['effort'] for r in m.get('supported_reasoning_levels', [])]
    print(f\"{m['slug']}|{','.join(efforts)}\")
" 2>/dev/null)

if [ -z "$MENU_DATA" ]; then
    echo "  ERROR: 모델 목록을 불러올 수 없습니다."
    exit 1
fi

# Parse into arrays
MODEL_SLUGS=()
MODEL_EFFORTS=()
while IFS='|' read -r slug efforts; do
    MODEL_SLUGS+=("$slug")
    MODEL_EFFORTS+=("$efforts")
done <<< "$MENU_DATA"

MODEL_COUNT=${#MODEL_SLUGS[@]}
DEFAULT_MODEL_IDX=""
for i in "${!MODEL_SLUGS[@]}"; do
    if [ "${MODEL_SLUGS[$i]}" = "gpt-5.1-codex-mini" ]; then
        DEFAULT_MODEL_IDX=$((i + 1))
        break
    fi
done
: "${DEFAULT_MODEL_IDX:=1}"

# ── Model selection ──
echo ""
echo "╔══════════════════════════════════╗"
echo "║      Codex Proxy Launcher        ║"
echo "╚══════════════════════════════════╝"
echo ""
echo "  Select model:"
for i in "${!MODEL_SLUGS[@]}"; do
    n=$((i + 1))
    slug="${MODEL_SLUGS[$i]}"
    if [ "$n" -eq "$DEFAULT_MODEL_IDX" ]; then
        echo "    $n) $slug (기본)"
    else
        echo "    $n) $slug"
    fi
done
echo ""
read -p "  model [1-$MODEL_COUNT] (default: $DEFAULT_MODEL_IDX): " model_choice
model_choice="${model_choice:-$DEFAULT_MODEL_IDX}"
if ! [[ "$model_choice" =~ ^[0-9]+$ ]] || [ "$model_choice" -lt 1 ] || [ "$model_choice" -gt "$MODEL_COUNT" ]; then
    model_choice=$DEFAULT_MODEL_IDX
fi
CODEX_MODEL="${MODEL_SLUGS[$((model_choice - 1))]}"

# ── Reasoning selection (from selected model) ──
IFS=',' read -ra EFFORTS <<< "${MODEL_EFFORTS[$((model_choice - 1))]}"
EFFORT_COUNT=${#EFFORTS[@]}
DEFAULT_EFFORT_IDX=$EFFORT_COUNT  # last = highest

echo ""
echo "  Select reasoning effort:"
for i in "${!EFFORTS[@]}"; do
    n=$((i + 1))
    effort="${EFFORTS[$i]}"
    if [ "$n" -eq "$DEFAULT_EFFORT_IDX" ]; then
        echo "    $n) $effort (기본)"
    else
        echo "    $n) $effort"
    fi
done
echo ""
read -p "  reasoning [1-$EFFORT_COUNT] (default: $DEFAULT_EFFORT_IDX): " reason_choice
reason_choice="${reason_choice:-$DEFAULT_EFFORT_IDX}"
if ! [[ "$reason_choice" =~ ^[0-9]+$ ]] || [ "$reason_choice" -lt 1 ] || [ "$reason_choice" -gt "$EFFORT_COUNT" ]; then
    reason_choice=$DEFAULT_EFFORT_IDX
fi
CODEX_REASONING="${EFFORTS[$((reason_choice - 1))]}"

# ── Sub-agent model selection ──
DEFAULT_SUB_IDX=""
for i in "${!MODEL_SLUGS[@]}"; do
    if [ "${MODEL_SLUGS[$i]}" = "gpt-5.1-codex-mini" ]; then
        DEFAULT_SUB_IDX=$((i + 1))
        break
    fi
done
: "${DEFAULT_SUB_IDX:=$MODEL_COUNT}"

echo ""
echo "  Select sub-agent model (haiku):"
for i in "${!MODEL_SLUGS[@]}"; do
    n=$((i + 1))
    slug="${MODEL_SLUGS[$i]}"
    if [ "$n" -eq "$DEFAULT_SUB_IDX" ]; then
        echo "    $n) $slug (기본)"
    else
        echo "    $n) $slug"
    fi
done
echo ""
read -p "  sub-agent [1-$MODEL_COUNT] (default: $DEFAULT_SUB_IDX): " sub_choice
sub_choice="${sub_choice:-$DEFAULT_SUB_IDX}"
if ! [[ "$sub_choice" =~ ^[0-9]+$ ]] || [ "$sub_choice" -lt 1 ] || [ "$sub_choice" -gt "$MODEL_COUNT" ]; then
    sub_choice=$DEFAULT_SUB_IDX
fi
CODEX_SUBAGENT_MODEL="${MODEL_SLUGS[$((sub_choice - 1))]}"

export CODEX_MODEL
export CODEX_REASONING
export CODEX_SUBAGENT_MODEL

cleanup() {
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "[connect.sh] Stopping proxy server (PID $SERVER_PID)..."
        kill "$SERVER_PID" 2>/dev/null
        wait "$SERVER_PID" 2>/dev/null
    fi
}
trap cleanup EXIT INT TERM

# Kill any existing server on the port
EXISTING_PID=$(lsof -ti tcp:$SERVER_PORT 2>/dev/null)
if [ -n "$EXISTING_PID" ]; then
    echo "[connect.sh] Killing existing server (PID $EXISTING_PID) on port $SERVER_PORT..."
    kill $EXISTING_PID 2>/dev/null
    sleep 1
    # Force kill if still alive
    kill -9 $EXISTING_PID 2>/dev/null 2>&1
fi

# Start the proxy server in background
echo ""
echo "[connect.sh] Model: $CODEX_MODEL | Sub-agent: $CODEX_SUBAGENT_MODEL | Reasoning: $CODEX_REASONING"
echo "[connect.sh] Starting Codex proxy server on port $SERVER_PORT..."
python3 "$SCRIPT_DIR/app/main.py" >/dev/null 2>&1 &
SERVER_PID=$!

# Wait for server to be ready (max 15 seconds)
MAX_WAIT=15
for i in $(seq 1 $MAX_WAIT); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "[connect.sh] ERROR: Server process died. Check auth/dependencies."
        exit 1
    fi
    if curl -s "http://localhost:$SERVER_PORT/health" >/dev/null 2>&1; then
        echo "[connect.sh] Proxy server ready."
        break
    fi
    if [ "$i" -eq "$MAX_WAIT" ]; then
        echo "[connect.sh] ERROR: Server failed to start within ${MAX_WAIT}s."
        cleanup
        exit 1
    fi
    sleep 1
done

export ANTHROPIC_BASE_URL="http://localhost:$SERVER_PORT"
export ANTHROPIC_AUTH_TOKEN="sk-ant-fake-codex-proxy-token"

# Disable features that won't work through the proxy
export CLAUDE_CODE_DISABLE_FAST_MODE=1
export CLAUDE_CODE_DISABLE_THINKING=1
export DISABLE_PROMPT_CACHING=1
export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1

claude --dangerously-skip-permissions "${CLAUDE_ARGS[@]}"
