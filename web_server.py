"""
web_server.py
FastAPI 대시보드 — Cloudflare Access로 외부 인증 처리
"""

import json
import os
import re
import time

import anthropic
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

import database
from data_fetcher import get_current_price

# 상위 디렉토리의 .env 로드
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, ".env"))

_TG_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TG_USER_ID    = os.getenv("TELEGRAM_USER_ID", "")
_CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")

# 알고리즘 이름 정규화 (자유 입력 → 내부 키)
_KNOWN_ALGOS = ["골삼이", "골든샘플", "레드삼각", "골삼이(상승초입)"]

_ALLOWED_STATUSES = {"반영됨", "반려됨"}

DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_FILE = os.path.join(DIR, "templates", "index.html")

# 한국 주식 코드: 6자리 숫자
_TICKER_RE = re.compile(r'^\d{6}$')

# 허용된 요청 유형 (화이트리스트)
_ALLOWED_REQUEST_TYPES = {'정정 요청', '새 알고리즘 제안'}

# 현재가 캐시 (5분)
_price_cache: dict[str, tuple[int, float]] = {}
_CACHE_TTL = 300

app = FastAPI(title="Stock Dashboard")


def _normalize_algo(name: str) -> str | None:
    """자유 입력 알고리즘 이름을 내부 키로 정규화."""
    for k in _KNOWN_ALGOS:
        if k in name:
            return k
    return None


async def _interpret_and_apply(
    req_id: int, req_type: str, algo_name: str, description: str
) -> str:
    """Claude API로 요청 해석 → 파라미터 자동 업데이트. 결과 설명 문자열 반환."""
    if req_type == "새 알고리즘 제안":
        return "새 알고리즘 제안은 자동 반영이 불가합니다. 코드에 직접 추가가 필요합니다."

    algo = _normalize_algo(algo_name)
    if not algo:
        return f"알 수 없는 알고리즘입니다: '{algo_name}' (골삼이/골든샘플/레드삼각 중 하나여야 합니다)"

    if not _CLAUDE_API_KEY:
        return "CLAUDE_API_KEY가 설정되지 않아 자동 해석을 할 수 없습니다."

    current = database.get_algo_config(algo)
    docs    = database._PARAM_DOCS[algo]

    param_lines = "\n".join(
        f"  {k}: {v} | 현재값: {current.get(k)}"
        for k, v in docs.items()
    )

    prompt = (
        f"주식 패턴 스캐너 알고리즘 파라미터를 정정 요청에 맞게 업데이트해야 합니다.\n\n"
        f"알고리즘: {algo}\n\n"
        f"파라미터 목록:\n{param_lines}\n\n"
        f"정정 요청 내용:\n{description}\n\n"
        f"위 요청을 반영하여 변경된 파라미터를 포함한 전체 파라미터 JSON을 반환하세요. "
        f"숫자 타입(int/float)을 유지하고, JSON만 출력하세요 (코드블록 없이)."
    )

    try:
        client = anthropic.Anthropic(api_key=_CLAUDE_API_KEY)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        # JSON 블록 추출 (코드블록으로 감싸졌을 수도 있음)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return f"Claude 응답에서 JSON을 찾을 수 없습니다: {raw[:300]}"

        new_params = json.loads(m.group())

        # 안전 검증: 알려진 키만 허용, 타입 유지
        defaults = database._DEFAULT_CONFIGS[algo]
        validated: dict = {}
        changed: list[str] = []
        for k, default_val in defaults.items():
            new_val = new_params.get(k, current.get(k, default_val))
            try:
                if isinstance(default_val, int):
                    new_val = int(new_val)
                else:
                    new_val = float(new_val)
            except (TypeError, ValueError):
                new_val = current.get(k, default_val)
            if new_val != current.get(k, default_val):
                changed.append(f"{k}: {current.get(k)} → {new_val}")
            validated[k] = new_val

        database.update_algo_config(algo, validated, req_id)

        if changed:
            return f"✅ {algo} 파라미터 업데이트 완료\n변경 항목:\n" + "\n".join(changed)
        return f"✅ 반영됨 (파라미터 변경 없음 — Claude가 기존 값이 적절하다고 판단)"

    except json.JSONDecodeError as e:
        return f"JSON 파싱 실패: {e}"
    except Exception as e:
        return f"오류: {e}"


async def _notify_telegram(request_type: str, algorithm_name: str, description: str) -> None:
    """알고리즘 요청 제출 시 텔레그램으로 알림 전송."""
    if not _TG_TOKEN or not _TG_USER_ID:
        return
    preview = description[:200] + ("…" if len(description) > 200 else "")
    text = (
        f"📬 *알고리즘 요청 접수*\n"
        f"유형: {request_type}\n"
        f"이름: *{algorithm_name}*\n\n"
        f"{preview}"
    )
    url = f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={
                "chat_id": _TG_USER_ID,
                "text": text,
                "parse_mode": "Markdown",
            })
    except Exception:
        pass  # 알림 실패가 본 기능에 영향을 주지 않도록


# ── 보안 헤더 미들웨어 ────────────────────────────────────────────────

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # 인라인 스크립트/스타일은 현재 HTML 구조상 필요 (CDN 없음, 외부 리소스 없음)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    return response


# ── 엔드포인트 ────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def read_root() -> HTMLResponse:
    with open(TEMPLATE_FILE, encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


@app.get("/api/latest")
def api_latest() -> JSONResponse:
    return JSONResponse(database.get_latest())


@app.get("/api/history")
def api_history(days: int = 30) -> JSONResponse:
    # FastAPI가 int 타입 강제 → 비정수 자동 거부
    days = max(1, min(days, 365))  # 1~365일로 클램프
    return JSONResponse(database.get_history(days))


@app.get("/api/stocks")
def api_stocks() -> JSONResponse:
    return JSONResponse(database.get_stock_tracking())


@app.get("/api/price/{ticker}")
def api_price(ticker: str) -> JSONResponse:
    # 형식 검증: 6자리 숫자만 허용
    if not _TICKER_RE.match(ticker):
        return JSONResponse({"error": "유효하지 않은 종목 코드입니다."}, status_code=400)

    now = time.time()
    if ticker in _price_cache:
        price, fetched_at = _price_cache[ticker]
        if now - fetched_at < _CACHE_TTL:
            return JSONResponse({"ticker": ticker, "price": price, "cached": True})

    price = get_current_price(ticker)
    _price_cache[ticker] = (price, now)
    return JSONResponse({"ticker": ticker, "price": price, "cached": False})


@app.post("/api/algorithm-request")
async def api_submit_request(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "잘못된 요청 형식입니다."}, status_code=400)

    request_type   = str(data.get("request_type", "")).strip()
    algorithm_name = str(data.get("algorithm_name", "")).strip()
    description    = str(data.get("description", "")).strip()

    # 화이트리스트 검증
    if request_type not in _ALLOWED_REQUEST_TYPES:
        return JSONResponse({"ok": False, "error": "유효하지 않은 요청 유형입니다."}, status_code=400)

    # 필수 항목 확인
    if not algorithm_name:
        return JSONResponse({"ok": False, "error": "알고리즘 이름을 입력해주세요."}, status_code=400)
    if not description:
        return JSONResponse({"ok": False, "error": "설명을 입력해주세요."}, status_code=400)

    # 서버측 길이 제한
    if len(algorithm_name) > 100:
        return JSONResponse({"ok": False, "error": "알고리즘 이름은 100자 이하로 입력해주세요."}, status_code=400)
    if len(description) > 2000:
        return JSONResponse({"ok": False, "error": "설명은 2000자 이하로 입력해주세요."}, status_code=400)

    try:
        req_id = database.save_algorithm_request(request_type, algorithm_name, description)
        await _notify_telegram(request_type, algorithm_name, description)
        return JSONResponse({"ok": True, "id": req_id})
    except Exception as e:
        return JSONResponse({"ok": False, "error": "저장 중 오류가 발생했습니다."}, status_code=500)


@app.get("/api/algorithm-requests")
def api_get_requests() -> JSONResponse:
    return JSONResponse(database.get_algorithm_requests())


@app.patch("/api/algorithm-request/{req_id}/status")
async def api_update_request_status(req_id: int, request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "잘못된 요청 형식"}, status_code=400)

    status = str(body.get("status", "")).strip()
    if status not in _ALLOWED_STATUSES:
        return JSONResponse({"ok": False, "error": "유효하지 않은 상태값"}, status_code=400)

    # 대상 요청 조회
    rows = database.get_algorithm_requests()
    req = next((r for r in rows if r["id"] == req_id), None)
    if not req:
        return JSONResponse({"ok": False, "error": "요청을 찾을 수 없습니다."}, status_code=404)
    if req["status"] != "검토중":
        return JSONResponse({"ok": False, "error": f"이미 처리된 요청입니다 (현재: {req['status']})"}, status_code=400)

    note = ""
    if status == "반영됨":
        note = await _interpret_and_apply(
            req_id,
            req["request_type"],
            req["algorithm_name"],
            req["description"],
        )

    database.update_request_status(req_id, status)
    return JSONResponse({"ok": True, "note": note})


@app.get("/api/algorithm-configs")
def api_algo_configs() -> JSONResponse:
    return JSONResponse(database.get_algo_configs_all())


@app.delete("/api/scan-result/{result_id}")
def api_delete_scan_result(result_id: int) -> JSONResponse:
    ok = database.delete_scan_result(result_id)
    if not ok:
        return JSONResponse({"ok": False, "error": "해당 결과를 찾을 수 없습니다."}, status_code=404)
    return JSONResponse({"ok": True})


@app.delete("/api/stock/{ticker}")
def api_delete_stock(ticker: str) -> JSONResponse:
    if not _TICKER_RE.match(ticker):
        return JSONResponse({"error": "유효하지 않은 종목 코드입니다."}, status_code=400)
    count = database.delete_stock_all(ticker)
    return JSONResponse({"ok": True, "deleted": count})
