#!/usr/bin/env bash
# AI-stockAlarm 서버 재시작 스크립트
# ※ ngrok은 main.py가 pyngrok으로 자동 시작 — 외부에서 별도 실행 불필요
#
# 사용법:
#   ./restart.sh          — 서버(+내장 ngrok) 재시작
#   ./restart.sh --status — 현재 상태 + URL 조회

set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 상태 조회만 ──────────────────────────────────────────────────
if [ "${1}" = "--status" ]; then
  echo "=== 프로세스 상태 ==="
  ps aux | grep "python main.py" | grep -v grep || echo "  서버: 꺼짐"
  ps aux | grep "[n]grok" | grep -v grep || echo "  ngrok: 꺼짐"
  echo ""
  echo "=== ngrok URL ==="
  curl -s http://localhost:4040/api/tunnels 2>/dev/null \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['tunnels'][0]['public_url'])" \
    2>/dev/null || echo "  ngrok 비활성"
  exit 0
fi

echo "=========================================="
echo "  AI-stockAlarm 재시작"
echo "=========================================="

# ── 1. 기존 프로세스 완전 종료 ───────────────────────────────────
echo "[1/3] 기존 프로세스 종료..."

# main.py (pyngrok 포함) 종료
pkill -9 -f "python main.py" 2>/dev/null || true

# ngrok 전체 종료 (pyngrok 에이전트 + 터널 프로세스 모두)
pkill -9 -f "ngrok" 2>/dev/null || true

# 포트 8080 강제 해제
fuser -k 8080/tcp 2>/dev/null || true

# ngrok이 완전히 죽을 때까지 대기 (ERR_NGROK_6030 방지 핵심)
echo "  ngrok 종료 대기..."
for i in $(seq 1 8); do
  sleep 1
  if ! pgrep -f "ngrok" > /dev/null 2>&1; then
    echo "  ✓ ngrok 종료 확인 (${i}초)"
    break
  fi
  if [ $i -eq 8 ]; then
    echo "  ⚠ ngrok 프로세스 잔존 — 강제 진행"
  fi
done

# ── 2. 서버 시작 (pyngrok이 ngrok도 함께 시작) ───────────────────
echo "[2/3] 서버 시작..."
cd "$DIR"
source venv/bin/activate
nohup python main.py > /tmp/stock-server.log 2>&1 &
SERVER_PID=$!
echo "  서버 PID: $SERVER_PID"

# ── 3. 서버 + ngrok URL 확인 ─────────────────────────────────────
echo "[3/3] 기동 확인 중..."
SERVER_OK=0
NGROK_URL=""

for i in $(seq 1 15); do
  sleep 1

  # 서버 응답 확인
  if [ $SERVER_OK -eq 0 ] && curl -sf http://localhost:8080/ > /dev/null 2>&1; then
    echo "  ✓ 서버 응답 확인 (${i}초)"
    SERVER_OK=1
  fi

  # ngrok URL 확인 (서버 뜬 후부터 시도)
  if [ $SERVER_OK -eq 1 ] && [ -z "$NGROK_URL" ]; then
    NGROK_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null \
      | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['tunnels'][0]['public_url'])" 2>/dev/null || true)
  fi

  # 둘 다 확인되면 종료
  if [ $SERVER_OK -eq 1 ] && [ -n "$NGROK_URL" ]; then
    break
  fi

  if [ $i -eq 15 ]; then
    echo ""
    echo "  ✗ 기동 실패. 서버 로그:"
    tail -20 /tmp/stock-server.log
    exit 1
  fi
done

echo ""
echo "=========================================="
echo "  ✓ 재시작 완료"
if [ -n "$NGROK_URL" ]; then
  echo "  URL: $NGROK_URL"
else
  echo "  URL: (ngrok 미응답 — 잠시 후 ./restart.sh --status 확인)"
fi
echo "=========================================="
