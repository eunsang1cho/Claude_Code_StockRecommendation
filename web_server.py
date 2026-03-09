"""
web_server.py
FastAPI 대시보드 — Cloudflare Access로 외부 인증 처리
"""

import asyncio
import json
import os
import re
import time
from datetime import datetime

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


@app.get("/api/price-snapshots")
def api_get_price_snapshots() -> JSONResponse:
    return JSONResponse(database.get_price_snapshots())


@app.post("/api/price-snapshots")
async def api_save_price_snapshots(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "잘못된 형식"}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"ok": False, "error": "dict 형식 필요"}, status_code=400)
    prices: dict[str, int] = {}
    for ticker, price in data.items():
        if _TICKER_RE.match(str(ticker)) and isinstance(price, (int, float)) and price > 0:
            prices[str(ticker)] = int(price)
    if not prices:
        return JSONResponse({"ok": False, "error": "유효한 데이터 없음"}, status_code=400)
    database.save_price_snapshots(prices)
    return JSONResponse({"ok": True, "saved": len(prices)})


@app.get("/api/daily-indicators")
def api_get_daily_indicators(days: int = 60) -> JSONResponse:
    days = max(1, min(days, 365))
    return JSONResponse(database.get_daily_indicators(days))


@app.post("/api/daily-indicators")
async def api_save_daily_indicators(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "잘못된 형식"}, status_code=400)

    date        = str(body.get("date", "")).strip()
    data        = body.get("data", {})
    crash_score = float(body.get("crash_score", 0))
    notes       = str(body.get("notes", "")).strip()[:500]

    if not date or not isinstance(data, dict):
        return JSONResponse({"ok": False, "error": "date와 data 필요"}, status_code=400)

    req_id = database.save_daily_indicators(date, data, crash_score, notes)
    return JSONResponse({"ok": True, "id": req_id})


def _calc_crash_score(data: dict) -> float:
    STATUS_SCORE = {"위험": -2, "경고": -1, "관망": 0, "긍정": 1, "최상": 2}
    WEIGHTS = {
        "tariff": 12, "usd_krw": 15, "us10y": 10, "foreign_flow": 15,
        "commercial_law": 5, "fund_flow": 5, "semiconductor": 5, "ria": 5, "msci": 5,
        "wti": 12, "soxx": 8, "hy_spread": 20, "tga_mmf_status": 8,
        "mmf_total": 8, "rrp": 5, "tga": 8, "yield_curve": 10, "fear_greed": 10,
    }
    wsum, wmax = 0, 0
    for k, w in WEIGHTS.items():
        st = data.get(k, {}).get("status")
        if st and st in STATUS_SCORE:
            wsum += STATUS_SCORE[st] * w
        wmax += w * 2
    return round(50 - (wsum / wmax * 50) if wmax else 50, 1)


def _get_time_slot() -> str:
    h = datetime.now().hour
    if h >= 22 or h < 2:  return "night"
    if 2  <= h < 6:        return "dawn"
    if 14 <= h < 18:       return "afternoon"
    return "morning"


@app.post("/api/daily-indicators/backfill")
async def api_backfill_indicators(request: Request) -> JSONResponse:
    """과거 N일치 지표 일괄 수집 후 DB 저장"""
    import fetch_indicators as fi
    try:
        body = await request.json()
        days = max(1, min(int(body.get("days", 90)), 180))
    except Exception:
        days = 90

    try:
        daily = await asyncio.to_thread(
            fi.fetch_backfill, os.getenv("FRED_API_KEY", ""), days
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    saved = 0
    for date, data in daily.items():
        try:
            score = _calc_crash_score(data)
            database.save_daily_indicators(date, data, score, "백필", "morning")
            saved += 1
        except Exception:
            pass

    return JSONResponse({"ok": True, "saved": saved, "total": len(daily)})


@app.get("/api/indicators/realtime")
async def api_realtime_indicators() -> JSONResponse:
    """Yahoo Finance + F&G + TGA 즉시 수집 — DB 저장 없음, 부하 최소"""
    import fetch_indicators as fi

    async def _yahoo(): return await asyncio.to_thread(fi.fetch_yahoo_all)
    async def _fg():
        r = await asyncio.to_thread(fi.fetch_fear_greed)
        r.pop('_historical', None)
        return r
    async def _tga(): return await asyncio.to_thread(fi._fetch_tga)

    try:
        yahoo_data, fg_data, tga_val = await asyncio.gather(_yahoo(), _fg(), _tga())
        data = yahoo_data
        if fg_data:
            data['fear_greed'] = fg_data
        if tga_val is not None:
            data['tga'] = {'value': tga_val, 'status': '관망', 'note': f'TGA {tga_val:.1f}B$'}
        return JSONResponse({"ok": True, "data": data, "ts": datetime.now().isoformat()})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/daily-indicators/all")
def api_get_all_indicators(days: int = 30) -> JSONResponse:
    days = max(1, min(days, 90))
    return JSONResponse(database.get_daily_indicators_all(days))


@app.post("/api/daily-indicators/refresh")
async def api_refresh_indicators() -> JSONResponse:
    """지표 자동 수집 후 저장"""
    import fetch_indicators as fi
    today     = datetime.now().strftime("%Y-%m-%d")
    time_slot = _get_time_slot()

    history  = database.get_daily_indicators(1)
    existing = history[0]["data"] if history and history[0]["date"] == today else {}

    try:
        data = await asyncio.to_thread(
            fi.fetch_all, os.getenv("FRED_API_KEY", ""), _CLAUDE_API_KEY, existing
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    crash_score = _calc_crash_score(data)
    req_id = database.save_daily_indicators(today, data, crash_score, "자동 수집", time_slot)
    return JSONResponse({"ok": True, "id": req_id, "crash_score": crash_score,
                         "date": today, "time_slot": time_slot})


@app.delete("/api/stock/{ticker}")
def api_delete_stock(ticker: str) -> JSONResponse:
    if not _TICKER_RE.match(ticker):
        return JSONResponse({"error": "유효하지 않은 종목 코드입니다."}, status_code=400)
    count = database.delete_stock_all(ticker)
    return JSONResponse({"ok": True, "deleted": count})


def _ensure_local_data(tickers: list[str]) -> dict[str, int]:
    """
    각 티커의 로컬 DB 행 수를 확인하고, 260행 미만이면 14개월치를 자동 크롤링.
    반환: {ticker: row_count} (크롤링 후 기준)
    """
    import sqlite3 as _sq
    import time as _time
    import crawl_daily
    from datetime import datetime, timedelta

    _mdb = os.path.join(DIR, "market_data.db")
    try:
        _conn = _sq.connect(_mdb)
        row_counts: dict[str, int] = dict(
            _conn.execute("SELECT ticker, COUNT(*) FROM stock_daily GROUP BY ticker").fetchall()
        )
        _conn.close()
    except Exception:
        row_counts = {}

    needs = [t for t in tickers if row_counts.get(t, 0) < 260]
    if not needs:
        return row_counts

    end   = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=420)).strftime("%Y%m%d")  # ~14개월

    print(f"[plus-analysis] 데이터 부족 {len(needs)}개 종목 자동 크롤링 시작")
    for ticker in needs:
        crawl_daily._collect_ticker(ticker, start, end)
        crawl_daily._compute_indicators(ticker)
        _time.sleep(0.15)
    print(f"[plus-analysis] 크롤링 완료")

    # 갱신된 행 수 재조회
    try:
        _conn = _sq.connect(_mdb)
        row_counts = dict(
            _conn.execute("SELECT ticker, COUNT(*) FROM stock_daily GROUP BY ticker").fetchall()
        )
        _conn.close()
    except Exception:
        pass
    return row_counts


@app.get("/api/plus-analysis")
def api_plus_analysis() -> JSONResponse:
    """
    과거 감지 종목에 Plus1/Plus2 필터를 적용한 비교 분석.
    데이터가 부족한 종목은 자동으로 14개월치 크롤링 후 분석.
    반환: [{ticker, name, base_pattern, base_conf,
            plus1_conf, plus1_change, plus2_conf, plus2_change,
            detect_date, detect_price, current_price, return_pct}]
    """
    import scanner
    import data_store
    from database import get_algo_config

    _BASE_PATS = ["골삼이(상승초입)", "MA압축지지", "골삼이", "골든샘플", "레드삼각"]

    def _normalize(pat: str) -> str:
        for b in _BASE_PATS:
            if pat.startswith(b):
                return b
        return pat

    stocks = database.get_stock_tracking()
    snaps  = database.get_price_snapshots()
    cfgs   = {k: get_algo_config(k) for k in _BASE_PATS}

    _DET1 = {
        "골삼이":           scanner.detect_golsami_plus1,
        "골든샘플":         scanner.detect_golden_sample_plus1,
        "레드삼각":         scanner.detect_red_triangle_plus1,
        "골삼이(상승초입)": scanner.detect_golsami_early_plus1,
        "MA압축지지":       scanner.detect_ma_compression_plus1,
    }
    _DET2 = {
        "골삼이":           scanner.detect_golsami_plus2,
        "골든샘플":         scanner.detect_golden_sample_plus2,
        "레드삼각":         scanner.detect_red_triangle_plus2,
        "골삼이(상승초입)": scanner.detect_golsami_early_plus2,
        "MA압축지지":       scanner.detect_ma_compression_plus2,
    }

    # 데이터 부족 종목 자동 크롤링 (260행 미만이면 14개월치 수집)
    all_tickers = [st["ticker"] for st in stocks]
    row_counts  = _ensure_local_data(all_tickers)

    # Plus1 캐시 초기화 (winner 프로필 재계산)
    scanner._PLUS1_CACHE.clear()

    def _run_det(fn, df, ticker, cfg, base_conf):
        # 크롤링 후에도 260행 미만이면 no_data
        if fn is None or row_counts.get(ticker, 0) < 260 or df is None:
            return None, "no_data"
        try:
            r = fn(df, ticker, cfg)
            if r:
                c = int(r.get("conf", base_conf))
                chg = "higher" if c > base_conf else ("lower" if c < base_conf else "same")
                return c, chg
            return None, "eliminated"
        except Exception:
            return None, "error"

    def _run_det1(fn, df, ticker, cfg, base_conf, base_pat):
        """Plus1 전용: winner 샘플 5개 미만이면 no_data 반환."""
        if fn is None or row_counts.get(ticker, 0) < 260 or df is None:
            return None, "no_data"
        profile = scanner._winner_profile(base_pat)
        if profile.get("winner_count", 0) < 5:
            return None, "no_data"
        return _run_det(fn, df, ticker, cfg, base_conf)

    rows = []
    for st in stocks:
        ticker    = st["ticker"]
        base_pat  = _normalize(st.get("last_pattern") or "")
        base_conf = int(st.get("last_conf") or 0)
        snap      = snaps.get(ticker, {})
        cur_price = snap.get("price") if snap else None
        det_price = st.get("first_price")
        ret_pct   = (
            round((cur_price - det_price) / det_price * 100, 2)
            if cur_price and det_price else None
        )

        try:
            df = data_store.get_ticker_history(ticker, n=300)
        except Exception:
            df = None

        cfg = cfgs.get(base_pat, {})
        p1c, p1chg = _run_det1(_DET1.get(base_pat), df, ticker, cfg, base_conf, base_pat)
        p2c, p2chg = _run_det(_DET2.get(base_pat), df, ticker, cfg, base_conf)

        rows.append({
            "ticker":        ticker,
            "name":          st.get("name", ticker),
            "base_pattern":  base_pat,
            "base_conf":     base_conf,
            "plus1_conf":    p1c,
            "plus1_change":  p1chg,
            "plus2_conf":    p2c,
            "plus2_change":  p2chg,
            "detect_date":   st.get("first_detected"),
            "detect_price":  det_price,
            "current_price": cur_price,
            "return_pct":    ret_pct,
        })

    return JSONResponse(rows)


@app.get("/api/future-indicators")
async def api_get_future_indicators(days: int = 90) -> JSONResponse:
    """미래방향성 지표 — 최신 스냅샷 + 히스토리"""
    days = max(1, min(days, 365))
    history = database.get_future_indicators(days)
    latest  = database.get_future_indicators_latest()
    return JSONResponse({"latest": latest, "history": history})


@app.get("/api/future-portfolio")
async def api_future_portfolio(refresh: bool = False) -> JSONResponse:
    """방향성별 추천 포트폴리오 수익률.
    refresh=false → DB 캐시 우선 (없으면 실시간 수집 후 저장)
    refresh=true  → 실시간 수집 후 DB 저장
    """
    import future_indicators as fi

    if not refresh:
        cached = database.get_portfolio_snapshot_latest()
        if cached:
            return JSONResponse({
                "ok": True,
                "rows": cached["rows"],
                "summary": cached["summary"],
                "cached_at": cached["created_at"],
                "from_cache": True,
            })

    try:
        rows = await asyncio.to_thread(fi.fetch_portfolio_performance)
        total_invest  = sum(r["invest_krw"] for r in rows)
        total_current = sum(r["current_value_krw"] for r in rows)
        total_profit  = total_current - total_invest
        total_return  = round((total_current / total_invest - 1) * 100, 2) if total_invest else 0
        summary = {
            "total_invest":  total_invest,
            "total_current": total_current,
            "total_profit":  total_profit,
            "total_return":  total_return,
            "count":         len(rows),
        }
        today = datetime.now().strftime("%Y-%m-%d")
        database.save_portfolio_snapshot(today, rows, summary)
        return JSONResponse({"ok": True, "rows": rows, "summary": summary, "from_cache": False})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/milestones")
async def api_get_milestones() -> JSONResponse:
    """마일스톤 탭 — 방향성별 최신 스코어 반환 (프런트에서 동적 상태 계산)"""
    latest = database.get_future_indicators_latest()
    if not latest or not latest.get("data"):
        return JSONResponse({"scores": {}, "updated_at": None})
    scores = {k: v.get("score", 50) for k, v in latest["data"].items()}
    return JSONResponse({"scores": scores, "updated_at": latest.get("created_at") or latest.get("date")})


@app.post("/api/future-indicators/refresh")
async def api_refresh_future_indicators() -> JSONResponse:
    """미래지표 즉시 수집 후 저장"""
    import future_indicators as fi
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        data = await asyncio.to_thread(fi.fetch_future_indicators)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    req_id = database.save_future_indicators(today, data)
    return JSONResponse({"ok": True, "id": req_id, "date": today, "count": len(data)})


@app.get("/api/war-indicators")
async def api_get_war_indicators() -> JSONResponse:
    """전쟁지표 최신 스냅샷 반환"""
    try:
        latest = database.get_war_indicators_latest()
    except Exception as e:
        return JSONResponse({"ok": False, "data": None, "error": f"DB 오류: {e}"})
    if not latest:
        return JSONResponse({"ok": False, "data": None, "error": "데이터 없음 — 새로고침으로 수집하세요"})
    return JSONResponse({"ok": True, "data": latest["data"], "date": latest["date"],
                         "updated_at": latest["created_at"]})


@app.post("/api/war-indicators/refresh")
async def api_refresh_war_indicators() -> JSONResponse:
    """전쟁지표 즉시 수집 후 저장"""
    import war_indicators as wi
    today    = datetime.now().strftime("%Y-%m-%d")
    existing = database.get_war_indicators_latest()
    ex_data  = existing["data"] if existing else {}
    try:
        data = await asyncio.to_thread(
            wi.fetch_all_war, _CLAUDE_API_KEY, ex_data
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    req_id = database.save_war_indicators(today, data)
    return JSONResponse({"ok": True, "id": req_id, "date": today})


@app.get("/api/news")
async def api_get_news(days: int = 3, type: str = "all",
                       category: str = "all", limit: int = 100) -> JSONResponse:
    """뉴스 기사 목록 반환"""
    days  = max(1, min(days, 365))
    limit = max(1, min(limit, 500))
    stype = None if type == "all" else type
    cat   = None if category == "all" else category
    try:
        articles = database.get_news_articles(days=days, source_type=stype,
                                              category=cat, limit=limit)
        return JSONResponse({"ok": True, "articles": articles, "count": len(articles)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/news/analysis")
async def api_get_news_analysis() -> JSONResponse:
    """최신 Claude 뉴스 분석 반환"""
    try:
        latest = database.get_news_analysis_latest()
        return JSONResponse({"ok": True, "data": latest})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/news/refresh")
async def api_refresh_news() -> JSONResponse:
    """뉴스 즉시 수집 + Claude 분석 후 DB 저장"""
    import news_collector as nc
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        result = await asyncio.to_thread(nc.fetch_and_analyze, _CLAUDE_API_KEY, 1)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    articles = result.get("articles", [])
    analysis = result.get("analysis", {})
    saved    = database.save_news_articles(articles)
    if analysis:
        database.save_news_analysis(today, analysis)

    return JSONResponse({
        "ok":       True,
        "collected": len(articles),
        "saved":    saved,
        "analyzed": bool(analysis),
        "date":     today,
    })


@app.post("/api/news/backfill")
async def api_news_backfill(request: Request) -> JSONResponse:
    """GDELT 역사 뉴스 백필 (비동기 실행)"""
    import news_backfill as nb
    try:
        body       = await request.json()
        start      = str(body.get("start", "2020-01"))
        max_weeks  = max(1, min(int(body.get("max_weeks", 10)), 52))
    except Exception:
        start, max_weeks = "2020-01", 10

    async def _run():
        return await asyncio.to_thread(
            nb.run_backfill, start, max_weeks, _CLAUDE_API_KEY
        )

    task   = asyncio.ensure_future(_run())
    result = await task
    return JSONResponse({"ok": True, **result})


@app.get("/api/news/backfill-status")
async def api_news_backfill_status() -> JSONResponse:
    """백필 진행 현황"""
    try:
        status = database.get_news_backfill_status()
        status["done_weeks"] = list(status["done_weeks"])  # set → list for JSON
        return JSONResponse({"ok": True, **status})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/usage")
async def api_get_usage() -> JSONResponse:
    """Claude API 토큰 사용량 (ANTHROPIC_ADMIN_API_KEY 필요)"""
    admin_key = os.getenv("ANTHROPIC_ADMIN_API_KEY", "")
    if not admin_key or not admin_key.startswith("sk-ant-admin"):
        return JSONResponse({"ok": False, "error": "ANTHROPIC_ADMIN_API_KEY 미설정"})
    try:
        from datetime import datetime as _dt, timezone, timedelta
        now = _dt.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
        url = "https://api.anthropic.com/v1/organizations/usage_report/messages"
        params = f"starting_at={fmt(month_start)}&ending_at={fmt(now)}&bucket_width=1d"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{url}?{params}",
                headers={"x-api-key": admin_key, "anthropic-version": "2023-06-01"},
            )
            r.raise_for_status()
            data = r.json()
        # 토큰 합산
        totals = {"input": 0, "output": 0, "cache_read": 0}
        for bucket in data.get("data", []):
            totals["input"]      += bucket.get("input_tokens", 0)
            totals["output"]     += bucket.get("output_tokens", 0)
            totals["cache_read"] += bucket.get("cache_read_input_tokens", 0)
        totals["total"] = totals["input"] + totals["output"]
        # 다음 리셋: 다음 달 1일
        if now.month == 12:
            next_reset = _dt(now.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_reset = _dt(now.year, now.month + 1, 1, tzinfo=timezone.utc)
        days_left = (next_reset - now).days
        return JSONResponse({
            "ok": True,
            "tokens": totals,
            "month": now.strftime("%Y-%m"),
            "next_reset": next_reset.strftime("%Y-%m-%d"),
            "days_left": days_left,
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/index-history")
async def api_index_history() -> JSONResponse:
    """KOSPI, KOSDAQ, NASDAQ, DOW 30일 종가 히스토리"""
    symbols = {
        "kospi":  "^KS11",
        "kosdaq": "^KQ11",
        "nasdaq": "^IXIC",
        "dow":    "^DJI",
    }
    headers = {"User-Agent": "Mozilla/5.0 (compatible; StockBot/1.0)"}

    async def fetch_one(key: str, sym: str):
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        params = {"interval": "1d", "range": "3mo"}
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                r = await client.get(url, params=params, headers=headers)
                r.raise_for_status()
                res = r.json()["chart"]["result"][0]
                timestamps = res["timestamp"]
                closes = res["indicators"]["quote"][0]["close"]
                data = [
                    {"d": datetime.fromtimestamp(ts).strftime("%Y-%m-%d"), "v": round(cv, 2)}
                    for ts, cv in zip(timestamps, closes) if cv is not None
                ]
                return key, data
        except Exception as e:
            print(f"[index-history] {key} 오류: {e}")
            return key, []

    tasks = [fetch_one(k, s) for k, s in symbols.items()]
    results = await asyncio.gather(*tasks)
    return JSONResponse({k: v for k, v in results})
