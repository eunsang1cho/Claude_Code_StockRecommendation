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
    "텐배거": {
        # ── STEP 1: 시장 리더 ──────────────────────────────────────
        "rs_6m_min":          0.20,   # 6개월 절대 수익률 최소 (RS 80퍼센타일 프록시)
        "rs_3m_min":          0.10,   # 3개월 절대 수익률 최소 (RS 70퍼센타일 프록시)
        # ── STEP 2: VCP 베이스 ─────────────────────────────────────
        "big_move_pct":       0.35,   # 120일 저점 대비 최소 상승폭 (35%)
        "big_move_lookback":  120,    # 저점 탐색 기간 (거래일)
        "base_days_min":      10,     # 베이스 최소 기간 (거래일)
        "base_days_max":      35,     # 베이스 최대 기간 (거래일)
        "base_range_pct":     0.12,   # 베이스 허용 등락폭 (12%)
        "atr_contract":       0.75,   # ATR20_현재 / ATR20_이전 최대 비율
        "vol_contract":       0.70,   # vol10 / vol50 최대 비율 (거래량 수축 기준)
        # ── STEP 3: 위치 ───────────────────────────────────────────
        "high52w_ratio":      0.90,   # 현재가 ≥ 52주 고점 × N
        "high60d_ratio":      0.92,   # 현재가 ≥ 60일 고점 × N
        "ma20_near_tol":      0.04,   # MA20 근접 허용 오차 (±4%)
        "ma50_near_tol":      0.06,   # MA50 근접 허용 오차 (±6%)
        # ── STEP 4: 돌파 트리거 ────────────────────────────────────
        "brkout_vol_mult":    1.8,    # 베이스 상단 돌파 거래량 배수 (vol50 대비)
        "brkout52w_vol_mult": 1.5,    # 52주 신고가 돌파 거래량 배수
        # ── 신뢰도 ─────────────────────────────────────────────────
        "conf_base":          70,     # 기본 신뢰도
        "conf_breakout":      18,     # 거래량 동반 돌파 확인 시 가산점
        "conf_atr_strong":    8,      # ATR 40%+ 수축 시 가산점
        "conf_vol_strong":    7,      # vol10/vol50 < 0.50 극도 수축 시 가산점
        "conf_near_ma20":     6,      # 현재가 MA20 2% 이내 시 가산점
        "conf_big50":         5,      # 120일 저점 대비 50%+ 상승 시 가산점
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
    "텐배거": {
        "rs_6m_min":          "6개월 절대 수익률 최소값, RS 80퍼센타일 프록시 (기본: 0.20 = 20%)",
        "rs_3m_min":          "3개월 절대 수익률 최소값, RS 70퍼센타일 프록시 (기본: 0.10 = 10%)",
        "big_move_pct":       "120일 저점 대비 최소 상승폭, VCP 트리거 (기본: 0.35 = 35%)",
        "big_move_lookback":  "저점 탐색 기간, 거래일 (기본: 120)",
        "base_days_min":      "베이스 최소 기간, 거래일 (기본: 10)",
        "base_days_max":      "베이스 최대 기간, 거래일 (기본: 35)",
        "base_range_pct":     "베이스 허용 등락폭 (기본: 0.12 = 12%)",
        "atr_contract":       "ATR20_현재 / ATR20_이전 최대 비율, 변동성 수축 기준 (기본: 0.75)",
        "vol_contract":       "vol10 / vol50 최대 비율, 거래량 수축 기준 (기본: 0.70)",
        "high52w_ratio":      "현재가 ≥ 52주 고점 × N 최소 위치 (기본: 0.90)",
        "high60d_ratio":      "현재가 ≥ 60일 고점 × N 최소 위치 (기본: 0.92)",
        "ma20_near_tol":      "MA20 근접 허용 오차 (기본: 0.04 = ±4%)",
        "ma50_near_tol":      "MA50 근접 허용 오차 (기본: 0.06 = ±6%)",
        "brkout_vol_mult":    "베이스 상단 돌파 거래량 배수, vol50 대비 (기본: 1.8)",
        "brkout52w_vol_mult": "52주 신고가 돌파 거래량 배수 (기본: 1.5)",
        "conf_base":          "기본 신뢰도 점수 (기본: 70)",
        "conf_breakout":      "거래량 동반 돌파 확인 시 가산점 (기본: 18)",
        "conf_atr_strong":    "ATR 40%+ 수축 시 가산점 (기본: 8)",
        "conf_vol_strong":    "vol10/vol50 < 0.50 극도 수축 시 가산점 (기본: 7)",
        "conf_near_ma20":     "현재가 MA20 2% 이내 시 가산점 (기본: 6)",
        "conf_big50":         "120일 저점 대비 50%+ 상승 시 가산점 (기본: 5)",
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

            CREATE TABLE IF NOT EXISTS daily_indicators (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT NOT NULL,
                time_slot   TEXT NOT NULL DEFAULT 'morning',
                data_json   TEXT NOT NULL,
                crash_score REAL,
                notes       TEXT DEFAULT '',
                created_at  TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(date, time_slot)
            );

            CREATE TABLE IF NOT EXISTS future_indicators (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT NOT NULL UNIQUE,
                data_json   TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT NOT NULL UNIQUE,
                rows_json   TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS war_indicators (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT NOT NULL UNIQUE,
                data_json   TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS news_articles (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                source       TEXT NOT NULL,
                source_name  TEXT NOT NULL,
                source_type  TEXT NOT NULL,
                category     TEXT DEFAULT '',
                title        TEXT NOT NULL,
                url          TEXT NOT NULL UNIQUE,
                description  TEXT DEFAULT '',
                published_at TEXT NOT NULL,
                sentiment    TEXT DEFAULT '중립',
                tags         TEXT DEFAULT '[]',
                created_at   TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS news_analysis (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                date         TEXT NOT NULL UNIQUE,
                analysis_json TEXT NOT NULL,
                created_at   TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS news_backfill_log (
                week         TEXT NOT NULL UNIQUE,
                article_count INTEGER DEFAULT 0,
                analyzed     INTEGER DEFAULT 0,
                created_at   TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS cms_snapshots (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                date           TEXT NOT NULL,
                time_slot      TEXT NOT NULL DEFAULT 'morning',
                cms_score      REAL NOT NULL,
                regime         TEXT NOT NULL,
                components_json TEXT NOT NULL,
                created_at     TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(date, time_slot)
            );

            CREATE TABLE IF NOT EXISTS lotto_recommendations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                week_no     INTEGER NOT NULL,
                created_at  TEXT NOT NULL,
                games_json  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_lotto_rec_week ON lotto_recommendations(week_no);
            CREATE INDEX IF NOT EXISTS idx_cms_date ON cms_snapshots(date);
            CREATE INDEX IF NOT EXISTS idx_war_date ON war_indicators(date);
            CREATE INDEX IF NOT EXISTS idx_news_pub ON news_articles(published_at);
            CREATE INDEX IF NOT EXISTS idx_news_type ON news_articles(source_type);
            CREATE INDEX IF NOT EXISTS idx_news_analysis_date ON news_analysis(date);
            CREATE INDEX IF NOT EXISTS idx_portfolio_date ON portfolio_snapshots(date);
            CREATE INDEX IF NOT EXISTS idx_results_session ON scan_results(session_id);
            CREATE INDEX IF NOT EXISTS idx_results_ticker  ON scan_results(ticker);
            CREATE INDEX IF NOT EXISTS idx_results_scanned ON scan_results(scanned_at);
            CREATE INDEX IF NOT EXISTS idx_ind_date ON daily_indicators(date);
            CREATE INDEX IF NOT EXISTS idx_future_date ON future_indicators(date);
        """)
    # daily_indicators 마이그레이션: UNIQUE(date) → UNIQUE(date, time_slot)
    with _connect() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_indicators)").fetchall()]
        if 'time_slot' not in cols:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS daily_indicators_v2 (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    date        TEXT NOT NULL,
                    time_slot   TEXT NOT NULL DEFAULT 'morning',
                    data_json   TEXT NOT NULL,
                    crash_score REAL,
                    notes       TEXT DEFAULT '',
                    created_at  TEXT DEFAULT (datetime('now', 'localtime')),
                    UNIQUE(date, time_slot)
                );
                INSERT OR IGNORE INTO daily_indicators_v2
                    (date, time_slot, data_json, crash_score, notes, created_at)
                SELECT date, 'morning', data_json, crash_score, notes, created_at
                FROM daily_indicators;
                DROP TABLE daily_indicators;
                ALTER TABLE daily_indicators_v2 RENAME TO daily_indicators;
                CREATE INDEX IF NOT EXISTS idx_ind_date ON daily_indicators(date);
            """)

    print("✅ DB 초기화 완료:", DB_FILE)

    # market_data.db 초기화
    try:
        import data_store
        data_store.init_db()
    except Exception as e:
        print(f"⚠️  market_data.db 초기화 실패: {e}")


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


# ── 일일 지표 ──────────────────────────────────────────────────────────

SLOT_ORDER = {"morning": 0, "afternoon": 1, "night": 2, "dawn": 3}


def save_daily_indicators(date: str, data: dict, crash_score: float,
                          notes: str = "", time_slot: str = "morning") -> int:
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM daily_indicators WHERE date=? AND time_slot=?",
            (date, time_slot),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE daily_indicators
                   SET data_json=?, crash_score=?, notes=?, created_at=datetime('now','localtime')
                   WHERE date=? AND time_slot=?""",
                (json.dumps(data, ensure_ascii=False), crash_score, notes, date, time_slot),
            )
            return existing["id"]
        cur = conn.execute(
            """INSERT INTO daily_indicators (date, time_slot, data_json, crash_score, notes)
               VALUES (?,?,?,?,?)""",
            (date, time_slot, json.dumps(data, ensure_ascii=False), crash_score, notes),
        )
        return cur.lastrowid


def get_daily_indicators(days: int = 60) -> list[dict]:
    """날짜별 최신 슬롯 1개씩 반환 (차트용 일별 집계)"""
    with _connect() as conn:
        # 날짜별로 가장 최근 created_at 레코드 1개씩
        rows = conn.execute(
            """SELECT date, time_slot, data_json, crash_score, notes, created_at
               FROM daily_indicators
               WHERE date IN (
                   SELECT DISTINCT date FROM daily_indicators
                   ORDER BY date DESC LIMIT ?
               )
               ORDER BY date DESC, created_at DESC""",
            (days,),
        ).fetchall()

    seen, result = set(), []
    for row in rows:
        d = row["date"]
        if d in seen:
            continue
        seen.add(d)
        try:
            data = json.loads(row["data_json"])
        except Exception:
            data = {}
        result.append({
            "date":        d,
            "time_slot":   row["time_slot"],
            "data":        data,
            "crash_score": row["crash_score"],
            "notes":       row["notes"],
            "created_at":  row["created_at"],
        })
    return result


def get_daily_indicators_all(days: int = 60) -> list[dict]:
    """모든 슬롯 반환 (지표별 하루 4회 세부 차트용)"""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT date, time_slot, data_json, crash_score, notes, created_at
               FROM daily_indicators
               WHERE date >= date('now', ? || ' days')
               ORDER BY date ASC, created_at ASC""",
            (f"-{days}",),
        ).fetchall()
    result = []
    for row in rows:
        try:
            data = json.loads(row["data_json"])
        except Exception:
            data = {}
        result.append({
            "date":        row["date"],
            "time_slot":   row["time_slot"],
            "data":        data,
            "crash_score": row["crash_score"],
            "created_at":  row["created_at"],
        })
    return result


# ── 미래지표 ────────────────────────────────────────────────────────────

def save_future_indicators(date: str, data: dict) -> int:
    """미래방향성 스냅샷 저장 (날짜당 1개, UPSERT)"""
    data_json = json.dumps(data, ensure_ascii=False)
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO future_indicators (date, data_json)
               VALUES (?, ?)
               ON CONFLICT(date) DO UPDATE SET
                 data_json  = excluded.data_json,
                 created_at = datetime('now', 'localtime')""",
            (date, data_json),
        )
        return cur.lastrowid


def get_future_indicators(days: int = 90) -> list[dict]:
    """최근 N일 미래지표 스냅샷 반환 (오래된→최신 순)"""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT date, data_json, created_at
               FROM future_indicators
               WHERE date >= date('now', ? || ' days')
               ORDER BY date ASC""",
            (f"-{days}",),
        ).fetchall()
    result = []
    for row in rows:
        try:
            data = json.loads(row["data_json"])
        except Exception:
            data = {}
        result.append({
            "date":       row["date"],
            "data":       data,
            "created_at": row["created_at"],
        })
    return result


def get_future_indicators_latest() -> dict | None:
    """가장 최근 미래지표 스냅샷 1개 반환"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT date, data_json, created_at FROM future_indicators ORDER BY date DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["data_json"])
    except Exception:
        data = {}
    return {"date": row["date"], "data": data, "created_at": row["created_at"]}


def save_portfolio_snapshot(date: str, rows: list, summary: dict) -> int:
    """포트폴리오 수익률 스냅샷 저장 (날짜당 1개, UPSERT)"""
    rows_json    = json.dumps(rows, ensure_ascii=False)
    summary_json = json.dumps(summary, ensure_ascii=False)
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO portfolio_snapshots (date, rows_json, summary_json)
               VALUES (?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                 rows_json    = excluded.rows_json,
                 summary_json = excluded.summary_json,
                 created_at   = datetime('now', 'localtime')""",
            (date, rows_json, summary_json),
        )
        return cur.lastrowid


def get_portfolio_snapshot_latest() -> dict | None:
    """가장 최근 포트폴리오 스냅샷 반환"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT date, rows_json, summary_json, created_at FROM portfolio_snapshots ORDER BY date DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    try:
        rows    = json.loads(row["rows_json"])
        summary = json.loads(row["summary_json"])
    except Exception:
        rows, summary = [], {}
    return {"date": row["date"], "rows": rows, "summary": summary, "created_at": row["created_at"]}


# ── 뉴스 기사 ────────────────────────────────────────────────────────────

def save_news_articles(articles: list[dict]) -> int:
    """뉴스 기사 일괄 UPSERT (url UNIQUE). 저장 건수 반환."""
    if not articles:
        return 0
    saved = 0
    with _connect() as conn:
        for a in articles:
            try:
                conn.execute(
                    """INSERT INTO news_articles
                       (source, source_name, source_type, category, title, url,
                        description, published_at, sentiment, tags)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(url) DO NOTHING""",
                    (a.get('source',''), a.get('source_name',''), a.get('source_type',''),
                     a.get('category',''), a.get('title',''), a.get('url',''),
                     a.get('description',''), a.get('published_at',''),
                     a.get('sentiment','중립'), a.get('tags','[]')),
                )
                if conn.execute("SELECT changes()").fetchone()[0]:
                    saved += 1
            except Exception:
                pass
    return saved


def get_news_articles(days: int = 7, source_type: str = None,
                      category: str = None, limit: int = 100) -> list[dict]:
    """최근 N일 뉴스 기사 조회 (최신순)."""
    with _connect() as conn:
        conds = [f"published_at >= datetime('now', '-{days} days')"]
        params: list = []
        if source_type and source_type != 'all':
            conds.append("source_type = ?")
            params.append(source_type)
        if category and category != 'all':
            conds.append("category = ?")
            params.append(category)
        where = ' AND '.join(conds)
        limit = max(1, min(limit, 500))
        rows = conn.execute(
            f"SELECT * FROM news_articles WHERE {where} ORDER BY published_at DESC LIMIT {limit}",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def save_news_analysis(date: str, analysis: dict) -> int:
    """Claude 일일 분석 저장 (UPSERT)."""
    analysis_json = json.dumps(analysis, ensure_ascii=False)
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO news_analysis (date, analysis_json)
               VALUES (?, ?)
               ON CONFLICT(date) DO UPDATE SET
                 analysis_json = excluded.analysis_json,
                 created_at    = datetime('now', 'localtime')""",
            (date, analysis_json),
        )
        return cur.lastrowid


def get_news_analysis_latest() -> dict | None:
    """가장 최근 Claude 뉴스 분석 반환."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT date, analysis_json, created_at FROM news_analysis ORDER BY date DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    try:
        analysis = json.loads(row['analysis_json'])
    except Exception:
        analysis = {}
    return {'date': row['date'], 'analysis': analysis, 'created_at': row['created_at']}


def save_news_backfill_log(week: str, article_count: int, analyzed: int = 0) -> None:
    """백필 진행 로그 저장."""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO news_backfill_log (week, article_count, analyzed)
               VALUES (?, ?, ?)
               ON CONFLICT(week) DO UPDATE SET
                 article_count = excluded.article_count,
                 analyzed      = excluded.analyzed""",
            (week, article_count, analyzed),
        )


def get_news_backfill_status() -> dict:
    """백필 진행 현황 반환."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT week, article_count, analyzed FROM news_backfill_log ORDER BY week ASC"
        ).fetchall()
        total_articles = conn.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0]
    done_weeks = {r['week'] for r in rows}
    return {
        'done_weeks':    done_weeks,
        'week_count':    len(rows),
        'total_articles': total_articles,
        'weeks':         [dict(r) for r in rows],
    }


# ── 전쟁지표 ─────────────────────────────────────────────────────────────

def save_war_indicators(date: str, data: dict) -> int:
    """전쟁지표 스냅샷 저장 (날짜당 1개, UPSERT)"""
    data_json = json.dumps(data, ensure_ascii=False)
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO war_indicators (date, data_json)
               VALUES (?, ?)
               ON CONFLICT(date) DO UPDATE SET
                 data_json  = excluded.data_json,
                 created_at = datetime('now', 'localtime')""",
            (date, data_json),
        )
        return cur.lastrowid


def get_war_indicators_latest() -> dict | None:
    """가장 최근 전쟁지표 스냅샷 반환"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT date, data_json, created_at FROM war_indicators ORDER BY date DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["data_json"])
    except Exception:
        data = {}
    return {"date": row["date"], "data": data, "created_at": row["created_at"]}


def get_war_indicators_prev() -> dict | None:
    """직전 전쟁지표 스냅샷 반환 (최신 제외 2번째)"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT date, data_json FROM war_indicators ORDER BY date DESC LIMIT 2"
        ).fetchall()
    if len(row) < 2:
        return None
    try:
        data = json.loads(row[1]["data_json"])
    except Exception:
        data = {}
    return {"date": row[1]["date"], "data": data}


def get_war_indicators(days: int = 30) -> list[dict]:
    """최근 N일 전쟁지표 스냅샷 반환"""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT date, data_json, created_at
               FROM war_indicators
               WHERE date >= date('now', ? || ' days')
               ORDER BY date ASC""",
            (f"-{days}",),
        ).fetchall()
    result = []
    for row in rows:
        try:
            data = json.loads(row["data_json"])
        except Exception:
            data = {}
        result.append({"date": row["date"], "data": data, "created_at": row["created_at"]})
    return result


# ── CMS 스냅샷 ─────────────────────────────────────────────────────────

def save_cms_snapshot(date: str, time_slot: str, cms_score: float,
                      regime: str, components: dict) -> int:
    """CMS 스냅샷 저장 (날짜+슬롯당 1개, UPSERT)"""
    comp_json = json.dumps(components, ensure_ascii=False)
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM cms_snapshots WHERE date=? AND time_slot=?",
            (date, time_slot),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE cms_snapshots
                   SET cms_score=?, regime=?, components_json=?,
                       created_at=datetime('now','localtime')
                   WHERE date=? AND time_slot=?""",
                (cms_score, regime, comp_json, date, time_slot),
            )
            return existing["id"]
        cur = conn.execute(
            """INSERT INTO cms_snapshots (date, time_slot, cms_score, regime, components_json)
               VALUES (?,?,?,?,?)""",
            (date, time_slot, cms_score, regime, comp_json),
        )
        return cur.lastrowid


def get_cms_latest() -> dict | None:
    """가장 최근 CMS 스냅샷 반환"""
    with _connect() as conn:
        row = conn.execute(
            """SELECT date, time_slot, cms_score, regime, components_json, created_at
               FROM cms_snapshots ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()
    if not row:
        return None
    try:
        components = json.loads(row["components_json"])
    except Exception:
        components = {}
    return {
        "date":       row["date"],
        "time_slot":  row["time_slot"],
        "cms_score":  row["cms_score"],
        "regime":     row["regime"],
        "components": components,
        "created_at": row["created_at"],
    }


def get_cms_history(days: int = 30) -> list[dict]:
    """최근 N일 CMS 스냅샷 (모든 슬롯 포함, 오래된→최신 순)"""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT date, time_slot, cms_score, regime, components_json, created_at
               FROM cms_snapshots
               WHERE date >= date('now', ? || ' days')
               ORDER BY date ASC, created_at ASC""",
            (f"-{days}",),
        ).fetchall()
    result = []
    for row in rows:
        try:
            components = json.loads(row["components_json"])
        except Exception:
            components = {}
        result.append({
            "date":       row["date"],
            "time_slot":  row["time_slot"],
            "cms_score":  row["cms_score"],
            "regime":     row["regime"],
            "components": components,
            "created_at": row["created_at"],
        })
    return result



# ── 로또 추천번호 저장/조회 ───────────────────────────────────────────

def save_lotto_recommendations(week_no: int, games: list) -> None:
    """금요일 추천 번호 저장 (week_no = 다음 회차 번호)"""
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO lotto_recommendations (week_no, created_at, games_json) VALUES (?, ?, ?)",
            (week_no, now, json.dumps(games, ensure_ascii=False)),
        )


def get_lotto_recommendation(week_no: int) -> list | None:
    """특정 회차 추천번호 조회. 없으면 None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT games_json FROM lotto_recommendations WHERE week_no = ? ORDER BY id DESC LIMIT 1",
            (week_no,),
        ).fetchone()
    return json.loads(row["games_json"]) if row else None
