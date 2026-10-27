#!/usr/bin/env bash
# 从零复现 Recap 的一次真实运行：独立端口、独立数据目录，不碰本机已有的 Server。
#
#   scripts/demo.sh                       # 默认 8101 端口，数据落在 data/demo/
#   PORT=8201 SINCE=2026-10-20 UNTIL=2026-10-26 scripts/demo.sh
#   REPO=~/code/my-project AUTHOR=me scripts/demo.sh   # 用你自己的提交记录当输入
#
# 每一步都是评审可以照抄的命令；脚本本身只是把这些命令串起来。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8101}"
SINCE="${SINCE:-}"
UNTIL="${UNTIL:-}"
REPO="${REPO:-$ROOT}"
AUTHOR="${AUTHOR:-$(git -C "$ROOT" config user.name 2>/dev/null || echo "")}"
DEMO_DIR="$ROOT/data/demo"
PC_ENV="$ROOT/deploy/demo.powercontext.env"
RECAP_CFG="$DEMO_DIR/recap.config.json"

command -v uv >/dev/null || { echo "需要 uv：https://docs.astral.sh/uv/"; exit 1; }
cd "$ROOT"
mkdir -p "$DEMO_DIR"

echo "==> 1/6 安装依赖（powercontext[server] + pyseekdb + pylibseekdb）"
uv sync --quiet

TOKEN="$(openssl rand -hex 16 2>/dev/null || python3 -c 'import secrets;print(secrets.token_hex(16))')"
# The demo Server gets its own PowerContext home, so its embedded seekdb never opens
# the same directory as an already-running Server on this machine.
sed -e "s|__PROJECT_ROOT__/data/powercontext|$DEMO_DIR/powercontext|g" \
  -e "s|__PROJECT_ROOT__|$ROOT|g" -e "s|__PORT__|$PORT|g" -e "s|__TOKEN__|$TOKEN|g" \
  deploy/powercontext.env.example > "$PC_ENV"

cat > "$RECAP_CFG" <<JSON
{
  "powercontext_env": "$PC_ENV",
  "data_dir": "$DEMO_DIR",
  "seekdb_path": "$DEMO_DIR/seekdb",
  "state_path": "$DEMO_DIR/state.json",
  "scope": {
    "title": "Recap 复现演示 · 独立 Scope",
    "summary": "scripts/demo.sh 生成，用来证明整条链路可从零跑通。"
  },
  "repos": [
    { "path": "$REPO", "author": "$AUTHOR" }
  ],
  "notes_dirs": ["$ROOT/notes"],
  "embedding_provider": "pyseekdb-default"
}
JSON

echo "==> 2/6 在 127.0.0.1:$PORT 起一个一次性的 PowerContext Server（日志 data/demo/server.log）"
uv run powercontext server run --env-file "$PC_ENV" --role all > "$DEMO_DIR/server.log" 2>&1 &
SERVER_PID=$!
cleanup() { kill "$SERVER_PID" 2>/dev/null || true; }
trap cleanup EXIT

ready=""
for _ in $(seq 1 60); do
  # Any HTTP answer means the port is served; authentication is checked by `recap doctor`.
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/v1/health"; then
    ready=yes; break
  fi
  sleep 1
done
[ -n "$ready" ] || { echo "Server 未就绪，见 $DEMO_DIR/server.log"; exit 1; }

window=()
[ -n "$SINCE" ] && window+=(--since "$SINCE")
[ -n "$UNTIL" ] && window+=(--until "$UNTIL")

echo "==> 3/6 recap doctor：握手并建 Scope"
uv run recap --config "$RECAP_CFG" doctor

echo "==> 4/6 recap collect：把真实工作痕迹写进 Source / Memory / Handoff"
uv run recap --config "$RECAP_CFG" collect ${window[@]+"${window[@]}"}

echo "==> 5/6 recap index + report：镜像进 seekdb 并合成周报"
uv run recap --config "$RECAP_CFG" index ${window[@]+"${window[@]}"}
uv run recap --config "$RECAP_CFG" report ${window[@]+"${window[@]}"}

echo "==> 6/6 混合检索：同一条查询走向量 + 全文 + 时间窗"
uv run recap --config "$RECAP_CFG" search "seekdb hybrid_search" --limit 3 --raw-sql

echo
echo "周报已存到 $DEMO_DIR/report-*.md；Server 与演示数据都是临时的，脚本退出即关闭 Server。"
echo "看网站：python3 -m http.server -d web 8099  然后打开 http://127.0.0.1:8099/"
