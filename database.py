"""
database.py
SQLite 기반 스캔 결과 저장 및 조회
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta

from data_fetcher import get_market_cap

DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(DIR, "stocks.db")

# ── 알고리즘 기본 파라미터 ────────────────────────────────────────────

_DEFAULT_CONFIGS: dict[str, dict] = {
    "골삼이": {
        "window": 25,          # 최근 N일 이내 대양봉 탐색 기간
        "big_pct": 0.15,       # 대양봉 최소 등락률
        "vol_mult": 10.0,      # 대양봉 거래량 배수 (20MA 대비)
        "price_tol": 0.05,     # 대양봉 시가 근접 허용 오차 (±N%)
        "ma_tol": 0.05,        # 20MA 근접 허용 오차 (±N%)
        "vol_dec": 0.5,        # 대양봉 이후 거래량 감소 비율 기준
        "conf_base": 70,       # 기본 신뢰도
        "conf_near2": 15,      # 시가 2% 이내 신뢰도 가산점
        "conf_near35": 8,      # 시가 3.5% 이내 신뢰도 가산점
        "conf_big29": 10,      # 대양봉 +29% 이상 가산점
        "conf_slope": 7,       # 20MA 기울기 2% 이상 가산점
    },
    "골든샘플": {
        "window": 15,          # 최근 N일 이내 대양봉 탐색 기간
        "big_pct": 0.15,       # 대양봉 최소 등락률
        "vol_mult": 10.0,      # 대양봉 거래량 배수
        "vol_dried": 0.2,      # 거래량 고갈 기준 (대양봉 대비 비율)
        "price_hold": 0.90,    # 대양봉 이후 종가 유지 기준
        "min_after": 5,        # 대양봉 이후 최소 경과 일수
        "conf_base": 80,       # 기본 신뢰도
        "conf_days10": 8,      # 경과 10일 이상 가산점
        "conf_big29": 7,       # 대양봉 +29% 이상 가산점
    },
    "레드삼각": {
        "box_start": 90,       # 박스권 탐색 시작 (N일 전)
        "box_end": 60,         # 박스권 탐색 끝 (N일 전)
        "box_spread": 0.15,    # 박스권 고저 편차 허용 기준
        "break_start": 60,     # 돌파 구간 시작 (N일 전)
        "break_end": 20,       # 돌파 구간 끝 (N일 전)
        "min_big": 2,          # 돌파 구간 최소 대양봉 수
        "ma_tol": 0.05,        # 60MA 근접 허용 오차 (±N%)
        "box_top_pct": 0.93,   # 박스권 상단 대비 현재가 최소 비율
        "conf_base": 75,       # 기본 신뢰도
        "conf_3candles": 10,   # 대양봉 3개 이상 가산점
        "conf_near_ma60": 8,   # 60MA 2% 이내 근접 가산점
    },
    "골삼이(상승초입)": {
        "window": 5,            # 대양봉 탐색 기간 (최근 N 거래일)
        "big_pct": 0.05,        # 대양봉 최소 등락률 (5%)
        "body_ratio": 0.60,     # 대양봉 몸통 비율 기준 (60% 이상)
        "vol_mult": 3.0,        # 거래량 급증 배수, 20MA 대비
        "ma20_cross_tol": 0.03, # 장대양봉 저가 vs 예상 20MA 근접 허용 오차 (±3%)
        "proj_days_min": 3,     # 골든크로스 예상 최소 일수
        "proj_days_max": 15,    # 골든크로스 예상 최대 일수
        "ma240_flat_tol": 0.005, # 240MA 하락 판정 임계값 (0.5%)
        "price_surge_limit": 0.50, # 20거래일 전 3일 평균가 대비 현재가 최대 상승 한도 (50%)
        "conf_base": 72,        # 기본 신뢰도
        "conf_body60": 8,       # 몸통 60% 이상 시 가산점
        "conf_vol5x": 8,        # 거래량 5배 이상 시 가산점
        "conf_near_ma20": 10,   # 현재가 20MA ±1.5% 이내 시 가산점
    },
    "MA압축지지": {
        "base_candle_lookback": 60,   # 장대양봉 탐색 기간 (최근 N 거래일)
        "big_pct": 0.07,              # 장대양봉 최소 등락률 (7%)
        "body_ratio": 0.60,           # 장대양봉 몸통 비율 기준 (60% 이상)
        "vol_mult": 2.0,              # 장대양봉 거래량 배수, 20MA 대비
        "ma20_approach_days_min": 3,  # MA20 접근 확인 최소 경과 일수
        "ma20_approach_days_max": 30, # MA20 접근 확인 최대 경과 일수
        "ma20_near_bottom_tol": 0.03, # MA20 vs 장대 저가 근접 허용 오차 (±3%)
        "ma20_slope_min": 0.001,      # MA20 최소 기울기 (0.1%/일, 우상향)
        "atr_ratio_max": 0.015,       # ATR / 현재가 최대 비율 (1.5%)
        "vol_shrink_ratio": 0.5,      # 현재 거래량 ≤ 장대 거래량 × N
        "box_days_min": 5,            # 박스권 최소 확인 일수
        "box_days_max": 30,           # 박스권 최대 확인 일수
        "box_range_pct": 0.08,        # 박스권 허용 등락 범위 (8% 이내)
        "ma20_ma60_conv_tol": 0.03,   # |MA20 - MA60| / 가격 최대 허용치 (3%)
        "conf_base": 72,              # 기본 신뢰도
        "conf_ma20_close": 10,        # MA20이 장대 저가 1% 이내 시 가산점
        "conf_big15": 8,              # 장대 +15% 이상 시 가산점
        "conf_near_ma20": 7,          # 현재가 MA20 1% 이내 시 가산점
    },
}

_PARAM_DOCS: dict[str, dict[str, str]] = {
    "골삼이": {
        "window":      "최근 N일 이내 대양봉 탐색 기간 (기본: 25)",
        "big_pct":     "대양봉 최소 등락률 (기본: 0.15 = 15%)",
        "vol_mult":    "대양봉 거래량 배수, 20MA 대비 (기본: 10.0)",
        "price_tol":   "대양봉 시가 근접 허용 오차 (기본: 0.05 = ±5%)",
        "ma_tol":      "20MA 근접 허용 오차 (기본: 0.05 = ±5%)",
        "vol_dec":     "대양봉 이후 거래량 감소 비율 기준 (기본: 0.5 = 50% 미만)",
        "conf_base":   "기본 신뢰도 점수 (기본: 70)",
        "conf_near2":  "시가 2% 이내 근접 시 신뢰도 가산점 (기본: 15)",
        "conf_near35": "시가 3.5% 이내 근접 시 신뢰도 가산점 (기본: 8)",
        "conf_big29":  "대양봉 +29% 이상일 때 신뢰도 가산점 (기본: 10)",
        "conf_slope":  "20MA 기울기 2% 이상일 때 신뢰도 가산점 (기본: 7)",
    },
    "골든샘플": {
        "window":      "최근 N일 이내 대양봉 탐색 기간 (기본: 15)",
        "big_pct":     "대양봉 최소 등락률 (기본: 0.15 = 15%)",
        "vol_mult":    "대양봉 거래량 배수, 20MA 대비 (기본: 10.0)",
        "vol_dried":   "거래량 고갈 기준, 대양봉 대비 비율 (기본: 0.2 = 20% 미만)",
        "price_hold":  "대양봉 이후 종가 유지 기준 (기본: 0.90 = 90% 이상)",
        "min_after":   "대양봉 이후 최소 경과 일수 (기본: 5)",
        "conf_base":   "기본 신뢰도 점수 (기본: 80)",
        "conf_days10": "경과 10일 이상 시 신뢰도 가산점 (기본: 8)",
        "conf_big29":  "대양봉 +29% 이상일 때 신뢰도 가산점 (기본: 7)",
    },
    "레드삼각": {
        "box_start":      "박스권 탐색 시작 (N일 전, 기본: 90)",
        "box_end":        "박스권 탐색 끝 (N일 전, 기본: 60)",
        "box_spread":     "박스권 고저 편차 허용 기준 (기본: 0.15 = 15% 미만)",
        "break_start":    "돌파 구간 시작 (N일 전, 기본: 60)",
        "break_end":      "돌파 구간 끝 (N일 전, 기본: 20)",
        "min_big":        "돌파 구간 최소 대양봉 수 (기본: 2)",
        "ma_tol":         "60MA 근접 허용 오차 (기본: 0.05 = ±5%)",
        "box_top_pct":    "박스권 상단 대비 현재가 최소 비율 (기본: 0.93 = 93%)",
        "conf_base":      "기본 신뢰도 점수 (기본: 75)",
        "conf_3candles":  "대양봉 3개 이상일 때 신뢰도 가산점 (기본: 10)",
        "conf_near_ma60": "60MA 2% 이내 근접 시 신뢰도 가산점 (기본: 8)",
    },
    "골삼이(상승초입)": {
        "window":         "대양봉 탐색 기간, 거래일 (기본: 5)",
        "big_pct":        "대양봉 최소 등락률 (기본: 0.05 = 5%)",
        "body_ratio":     "대양봉 몸통 비율 기준 (기본: 0.60 = 60% 이상)",
        "vol_mult":       "거래량 급증 배수, 20MA 대비 (기본: 3.0)",
        "ma20_cross_tol": "장대양봉 저가 vs 예상 20MA 근접 허용 오차 (기본: 0.03 = ±3%)",
        "proj_days_min":  "골든크로스 예상 최소 일수 (기본: 3)",
        "proj_days_max":  "골든크로스 예상 최대 일수 (기본: 15)",
        "ma240_flat_tol": "240MA 하락 판정 임계값 (기본: 0.005 = 0.5%)",
        "price_surge_limit": "20거래일 전 3일 평균가 대비 현재가 최대 상승 한도 (기본: 0.50 = 50%)",
        "conf_base":      "기본 신뢰도 점수 (기본: 72)",
        "conf_body60":    "몸통 60% 이상 시 신뢰도 가산점 (기본: 8)",
        "conf_vol5x":     "거래량 5배 이상 시 신뢰도 가산점 (기본: 8)",
        "conf_near_ma20": "현재가 20MA ±1.5% 이내 시 신뢰도 가산점 (기본: 10)",
    },
    "MA압축지지": {
        "base_candle_lookback":  "장대양봉 탐색 기간, 거래일 (기본: 60)",
        "big_pct":               "장대양봉 최소 등락률 (기본: 0.07 = 7%)",
        "body_ratio":            "장대양봉 몸통 비율 기준 (기본: 0.60 = 60% 이상)",
        "vol_mult":              "장대양봉 거래량 배수, 20MA 대비 (기본: 2.0)",
        "ma20_approach_days_min":"MA20 접근 확인 최소 경과 일수 (기본: 3)",
        "ma20_approach_days_max":"MA20 접근 확인 최대 경과 일수 (기본: 30)",
        "ma20_near_bottom_tol":  "MA20 vs 장대 저가 근접 허용 오차 (기본: 0.03 = ±3%)",
        "ma20_slope_min":        "MA20 최소 일간 기울기 (기본: 0.001 = 0.1%/일)",
        "atr_ratio_max":         "ATR14 / 현재가 최대 허용 비율 (기본: 0.015 = 1.5%)",
        "vol_shrink_ratio":      "현재 거래량 상한 = 장대 거래량 × N (기본: 0.5)",
        "box_days_min":          "박스권 최소 확인 일수 (기본: 5)",
        "box_days_max":          "박스권 최대 확인 일수 (기본: 30)",
        "box_range_pct":         "박스권 허용 고저 범위 (기본: 0.08 = 8% 이내)",
        "ma20_ma60_conv_tol":    "|MA20 - MA60| / 가격 최대 허용치 (기본: 0.03 = 3%)",
        "conf_base":             "기본 신뢰도 점수 (기본: 72)",
        "conf_ma20_close":       "MA20이 장대 저가 1% 이내 시 가산점 (기본: 10)",
        "conf_big15":            "장대 +15% 이상 시 가산점 (기본: 8)",
        "conf_near_ma20":        "현재가 MA20 1% 이내 시 가산점 (기본: 7)",
    },
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """테이블 생성 (없으면)"""
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS scan_sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                scanned_at  TEXT NOT NULL,
                total_candidates INTEGER NOT NULL DEFAULT 0,
                total_hits  INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS scan_results (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   INTEGER NOT NULL REFERENCES scan_sessions(id),
                scanned_at   TEXT NOT NULL,
                ticker       TEXT NOT NULL,
                name         TEXT NOT NULL,
                pattern      TEXT NOT NULL,
                conf         INTEGER NOT NULL,
                current_price INTEGER NOT NULL,
                ma240        INTEGER NOT NULL,
                entry_low    INTEGER NOT NULL DEFAULT 0,
                entry_high   INTEGER NOT NULL DEFAULT 0,
                stop_loss    INTEGER NOT NULL DEFAULT 0,
                target_price INTEGER NOT NULL DEFAULT 0,
                market_cap   INTEGER NOT NULL DEFAULT 0,
                week52_high  INTEGER NOT NULL DEFAULT 0,
                week52_low   INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS algorithm_requests (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                submitted_at   TEXT NOT NULL,
                request_type   TEXT NOT NULL,
                algorithm_name TEXT NOT NULL,
                description    TEXT NOT NULL,
                status         TEXT NOT NULL DEFAULT '검토중'
            );

            CREATE TABLE IF NOT EXISTS algorithm_configs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                algorithm    TEXT NOT NULL UNIQUE,
                params       TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                from_request INTEGER
            );

            CREATE TABLE IF NOT EXISTS price_snapshots (
                ticker      TEXT PRIMARY KEY,
                price       INTEGER NOT NULL,
                fetched_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_results_session ON scan_results(session_id);
            CREATE INDEX IF NOT EXISTS idx_results_ticker  ON scan_results(ticker);
            CREATE INDEX IF NOT EXISTS idx_results_scanned ON scan_results(scanned_at);
        """)
    print("✅ DB 초기화 완료:", DB_FILE)


def save_scan(results: list[dict], total_candidates: int) -> None:
    """스캔 세션 + 결과 일괄 저장"""
    now = datetime.now().isoformat(timespec="seconds")

    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO scan_sessions (scanned_at, total_candidates, total_hits) VALUES (?, ?, ?)",
            (now, total_candidates, len(results)),
        )
        session_id = cur.lastrowid

        for r in results:
            entry = r.get("entry")
            if entry:
                entry_low, entry_high = entry[0], entry[1]
            else:
                ma20 = r.get("ma20", 0)
                entry_low = entry_high = ma20

            market_cap = get_market_cap(r["ticker"])

            conn.execute(
                """INSERT INTO scan_results
                   (session_id, scanned_at, ticker, name, pattern, conf,
                    current_price, ma240, entry_low, entry_high,
                    stop_loss, target_price, market_cap, week52_high, week52_low)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    now,
                    r["ticker"],
                    r["name"],
                    r["pattern"],
                    r["conf"],
                    r.get("current", 0),
                    r.get("ma240", 0),
                    entry_low,
                    entry_high,
                    r.get("stop", 0),
                    r.get("target", 0),
                    market_cap,
                    r.get("week52_high", 0),
                    r.get("week52_low", 0),
                ),
            )

    print(f"💾 스캔 저장 완료: session_id={session_id}, {len(results)}건")


def get_latest() -> list[dict]:
    """가장 최근 세션 결과 (신뢰도 내림차순)"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM scan_sessions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return []

        rows = conn.execute(
            """SELECT * FROM scan_results
               WHERE session_id = ?
               ORDER BY conf DESC""",
            (row["id"],),
        ).fetchall()
        return [dict(r) for r in rows]


def get_history(days: int = 30) -> list[dict]:
    """최근 N일 전체 스캔 결과 (최신순)"""
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM scan_results
               WHERE scanned_at >= ?
               ORDER BY scanned_at DESC, conf DESC""",
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_stock_tracking() -> list[dict]:
    """종목별 감지 횟수 + 최초 감지가 + 최근 신뢰도/패턴 (감지 횟수 내림차순)"""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT
                   sr.ticker,
                   sr.name,
                   COUNT(*)                AS total_hits,
                   MIN(sr.scanned_at)      AS first_detected,
                   MAX(sr.scanned_at)      AS last_detected,
                   (SELECT conf FROM scan_results
                    WHERE ticker = sr.ticker
                    ORDER BY scanned_at DESC LIMIT 1) AS last_conf,
                   (SELECT pattern FROM scan_results
                    WHERE ticker = sr.ticker
                    ORDER BY scanned_at DESC LIMIT 1) AS last_pattern,
                   (SELECT current_price FROM scan_results
                    WHERE ticker = sr.ticker
                    ORDER BY scanned_at ASC LIMIT 1)  AS first_price
               FROM scan_results sr
               GROUP BY sr.ticker
               ORDER BY total_hits DESC, last_detected DESC""",
        ).fetchall()
        return [dict(r) for r in rows]


# ── 알고리즘 요청 ─────────────────────────────────────────────────────

def save_algorithm_request(request_type: str, algorithm_name: str, description: str) -> int:
    """알고리즘 정정/신규 요청 저장"""
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO algorithm_requests (submitted_at, request_type, algorithm_name, description)
               VALUES (?, ?, ?, ?)""",
            (now, request_type, algorithm_name, description),
        )
        return cur.lastrowid


def get_algorithm_requests() -> list[dict]:
    """알고리즘 요청 목록 (최신순)"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM algorithm_requests ORDER BY submitted_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def update_request_status(req_id: int, status: str) -> None:
    """요청 상태 업데이트"""
    with _connect() as conn:
        conn.execute(
            "UPDATE algorithm_requests SET status = ? WHERE id = ?",
            (status, req_id),
        )


# ── 알고리즘 파라미터 ─────────────────────────────────────────────────

def get_algo_config(algorithm: str) -> dict:
    """알고리즘 파라미터 조회 (DB 없으면 기본값 반환)"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT params FROM algorithm_configs WHERE algorithm = ?",
            (algorithm,),
        ).fetchone()
    if row:
        stored = json.loads(row["params"])
        # 기본값에 없는 키 보충 (새 파라미터 추가 시 하위 호환)
        defaults = _DEFAULT_CONFIGS.get(algorithm, {})
        return {**defaults, **stored}
    return dict(_DEFAULT_CONFIGS.get(algorithm, {}))


def get_algo_configs_all() -> dict:
    """세 알고리즘 파라미터 전체 반환"""
    return {algo: get_algo_config(algo) for algo in _DEFAULT_CONFIGS}


def delete_scan_result(result_id: int) -> bool:
    """스캔 결과 단건 삭제. 삭제된 경우 True 반환."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM scan_results WHERE id = ?", (result_id,))
        return cur.rowcount > 0


def delete_stock_all(ticker: str) -> int:
    """특정 종목의 모든 스캔 결과 삭제. 삭제 건수 반환."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM scan_results WHERE ticker = ?", (ticker,))
        return cur.rowcount


def update_algo_config(
    algorithm: str, params: dict, from_request_id: int | None = None
) -> None:
    """알고리즘 파라미터 업데이트 (없으면 삽입, 있으면 덮어쓰기)"""
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            """INSERT INTO algorithm_configs (algorithm, params, updated_at, from_request)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(algorithm) DO UPDATE SET
                 params       = excluded.params,
                 updated_at   = excluded.updated_at,
                 from_request = excluded.from_request""",
            (algorithm, json.dumps(params, ensure_ascii=False), now, from_request_id),
        )


# ── 현재가 스냅샷 ──────────────────────────────────────────────────────

def save_price_snapshots(prices: dict[str, int]) -> None:
    """종목별 현재가 + 조회 시각을 DB에 UPSERT"""
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.executemany(
            """INSERT INTO price_snapshots (ticker, price, fetched_at)
               VALUES (?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                 price      = excluded.price,
                 fetched_at = excluded.fetched_at""",
            [(ticker, price, now) for ticker, price in prices.items()],
        )


def get_price_snapshots() -> dict:
    """저장된 현재가 스냅샷 전체 반환: {ticker: {price, fetched_at}}"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT ticker, price, fetched_at FROM price_snapshots"
        ).fetchall()
    return {row["ticker"]: {"price": row["price"], "fetched_at": row["fetched_at"]} for row in rows}
