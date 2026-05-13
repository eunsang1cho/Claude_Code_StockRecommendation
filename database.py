"""
database.py
SQLite 기반 스캔 결과 저장 및 조회
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta

from data_fetcher import get_market_cap, get_market_cap_us

DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(DIR, "stocks.db")

# ── 알고리즘 기본 파라미터 ────────────────────────────────────────────

_DEFAULT_CONFIGS: dict[str, dict] = {
    "골삼이": {
        "window": 20,          # 최근 N일 이내 대양봉 탐색 기간 (25→20)
        "big_pct": 0.15,       # 대양봉 최소 등락률
        "vol_mult": 10.0,      # 대양봉 거래량 배수 (20MA 대비)
        "price_tol": 0.03,     # 대양봉 시가 근접 허용 오차 ±3% (완화 5%→3%)
        "ma_tol": 0.03,        # 20MA 근접 허용 오차 ±3% (완화 5%→3%)
        "vol_dec": 0.4,        # 대양봉 이후 거래량 감소 비율 (50%→40%)
        "conf_base": 75,       # 기본 신뢰도 (70→75)
        "conf_near2": 15,      # 시가 2% 이내 신뢰도 가산점
        "conf_near35": 8,      # 시가 3% 이내 신뢰도 가산점
        "conf_big29": 10,      # 대양봉 +29% 이상 가산점
        "conf_slope": 7,       # 20MA 기울기 2% 이상 가산점
    },
    "골든샘플": {
        "window": 12,          # 최근 N일 이내 대양봉 탐색 기간 (15→12)
        "big_pct": 0.15,       # 대양봉 최소 등락률
        "vol_mult": 10.0,      # 대양봉 거래량 배수
        "vol_dried": 0.15,     # 거래량 고갈 기준 (20%→15%)
        "price_hold": 0.93,    # 대양봉 이후 종가 유지 기준 (90%→93%)
        "min_after": 7,        # 대양봉 이후 최소 경과 일수 (5→7)
        "conf_base": 80,       # 기본 신뢰도
        "conf_days10": 8,      # 경과 10일 이상 가산점
        "conf_big29": 7,       # 대양봉 +29% 이상 가산점
    },
    "레드삼각": {
        "box_start": 90,       # 박스권 탐색 시작 (N일 전)
        "box_end": 60,         # 박스권 탐색 끝 (N일 전)
        "box_spread": 0.10,    # 박스권 고저 편차 허용 기준 (15%→10%)
        "break_start": 60,     # 돌파 구간 시작 (N일 전)
        "break_end": 20,       # 돌파 구간 끝 (N일 전)
        "min_big": 3,          # 돌파 구간 최소 대양봉 수 (2→3)
        "ma_tol": 0.03,        # 60MA 근접 허용 오차 ±3% (5%→3%)
        "box_top_pct": 0.95,   # 박스권 상단 대비 현재가 최소 비율 (93%→95%)
        "conf_base": 78,       # 기본 신뢰도 (75→78)
        "conf_3candles": 10,   # 대양봉 3개 이상 가산점
        "conf_near_ma60": 8,   # 60MA 2% 이내 근접 가산점
    },
    "MA압축지지": {
        "base_candle_lookback": 50,   # 장대양봉 탐색 기간 (60→50일)
        "big_pct": 0.10,              # 장대양봉 최소 등락률 (7%→10%)
        "body_ratio": 0.60,           # 장대양봉 몸통 비율 기준 (60% 이상)
        "vol_mult": 4.0,              # 장대양봉 거래량 배수 (2.0→4.0배)
        "ma20_approach_days_min": 3,  # MA20 접근 확인 최소 경과 일수
        "ma20_approach_days_max": 25, # MA20 접근 확인 최대 경과 일수 (30→25)
        "ma20_near_bottom_tol": 0.02, # MA20 vs 장대 저가 근접 허용 오차 ±2% (3%→2%)
        "ma20_slope_min": 0.002,      # MA20 최소 기울기 (0.1%→0.2%/일)
        "atr_ratio_max": 0.012,       # ATR / 현재가 최대 비율 (1.5%→1.2%)
        "vol_shrink_ratio": 0.4,      # 현재 거래량 ≤ 장대 거래량 × N (50%→40%)
        "box_days_min": 5,            # 박스권 최소 확인 일수
        "box_days_max": 25,           # 박스권 최대 확인 일수 (30→25)
        "box_range_pct": 0.06,        # 박스권 허용 등락 범위 (8%→6%)
        "ma20_ma60_conv_tol": 0.025,  # |MA20 - MA60| / 가격 최대 허용치 (3%→2.5%)
        "conf_base": 76,              # 기본 신뢰도 (72→76)
        "conf_ma20_close": 10,        # MA20이 장대 저가 1% 이내 시 가산점
        "conf_big15": 8,              # 장대 +15% 이상 시 가산점
        "conf_near_ma20": 7,          # 현재가 MA20 1% 이내 시 가산점
    },
    "텐배거": {
        # ── STEP 1: 시장 리더 ──────────────────────────────────────
        "rs_6m_min":          0.25,   # 6개월 절대 수익률 최소 (20%→25%)
        "rs_3m_min":          0.12,   # 3개월 절대 수익률 최소 (10%→12%)
        # ── STEP 2: VCP 베이스 ─────────────────────────────────────
        "big_move_pct":       0.40,   # 120일 저점 대비 최소 상승폭 (35%→40%)
        "big_move_lookback":  120,    # 저점 탐색 기간 (거래일)
        "base_days_min":      10,     # 베이스 최소 기간 (거래일)
        "base_days_max":      30,     # 베이스 최대 기간 (35→30)
        "base_range_pct":     0.10,   # 베이스 허용 등락폭 (12%→10%)
        "atr_contract":       0.70,   # ATR20_현재 / ATR20_이전 최대 비율 (0.75→0.70)
        "vol_contract":       0.60,   # vol10 / vol50 최대 비율 (0.70→0.60)
        # ── STEP 3: 위치 ───────────────────────────────────────────
        "high52w_ratio":      0.92,   # 현재가 ≥ 52주 고점 × N (90%→92%)
        "high60d_ratio":      0.93,   # 현재가 ≥ 60일 고점 × N (92%→93%)
        "ma20_near_tol":      0.03,   # MA20 근접 허용 오차 ±3% (4%→3%)
        "ma50_near_tol":      0.05,   # MA50 근접 허용 오차 ±5% (6%→5%)
        # ── STEP 4: 돌파 트리거 ────────────────────────────────────
        "brkout_vol_mult":    2.0,    # 베이스 상단 돌파 거래량 배수 (1.8→2.0)
        "brkout52w_vol_mult": 1.8,    # 52주 신고가 돌파 거래량 배수 (1.5→1.8)
        # ── 신뢰도 ─────────────────────────────────────────────────
        "conf_base":          75,     # 기본 신뢰도 (70→75)
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

            CREATE TABLE IF NOT EXISTS calendar_analyses (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key    TEXT NOT NULL,
                event_date   TEXT NOT NULL,
                title        TEXT NOT NULL,
                forecast     TEXT DEFAULT '',
                previous     TEXT DEFAULT '',
                actual       TEXT DEFAULT '',
                analysis     TEXT NOT NULL,
                analyzed_at  TEXT NOT NULL,
                UNIQUE(event_key, event_date)
            );

            CREATE INDEX IF NOT EXISTS idx_cal_ana_date ON calendar_analyses(event_date);
            CREATE INDEX IF NOT EXISTS idx_lotto_rec_week ON lotto_recommendations(week_no);
            CREATE INDEX IF NOT EXISTS idx_cms_date ON cms_snapshots(date);
            CREATE INDEX IF NOT EXISTS idx_war_date ON war_indicators(date);

            CREATE TABLE IF NOT EXISTS iran_war_events (
                event_id    TEXT PRIMARY KEY,
                timestamp   TEXT NOT NULL,
                lat         REAL,
                lon         REAL,
                strike_type TEXT,
                target_desc TEXT,
                source_url  TEXT,
                verified_by TEXT,
                casualties  INTEGER DEFAULT 0,
                context     TEXT,
                saved_at    TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS iran_war_military (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                fetched_date TEXT NOT NULL UNIQUE,
                data_json   TEXT NOT NULL,
                saved_at    TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS iran_war_airspace (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                fetched_date TEXT NOT NULL UNIQUE,
                data_json   TEXT NOT NULL,
                saved_at    TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_iran_evt_ts ON iran_war_events(timestamp);
            CREATE INDEX IF NOT EXISTS idx_news_pub ON news_articles(published_at);
            CREATE INDEX IF NOT EXISTS idx_news_type ON news_articles(source_type);
            CREATE INDEX IF NOT EXISTS idx_news_analysis_date ON news_analysis(date);
            CREATE INDEX IF NOT EXISTS idx_portfolio_date ON portfolio_snapshots(date);
            CREATE INDEX IF NOT EXISTS idx_results_session ON scan_results(session_id);
            CREATE INDEX IF NOT EXISTS idx_results_ticker  ON scan_results(ticker);
            CREATE INDEX IF NOT EXISTS idx_results_scanned ON scan_results(scanned_at);
            CREATE INDEX IF NOT EXISTS idx_ind_date ON daily_indicators(date);
            CREATE INDEX IF NOT EXISTS idx_future_date ON future_indicators(date);

            CREATE TABLE IF NOT EXISTS liquidity_snapshots (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                date       TEXT NOT NULL UNIQUE,
                data_json  TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS short_radar (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                date       TEXT NOT NULL UNIQUE,
                data_json  TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS smart_money (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                cik        TEXT NOT NULL,
                quarter    TEXT NOT NULL,
                data_json  TEXT NOT NULL,
                saved_at   TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(cik, quarter)
            );

            CREATE TABLE IF NOT EXISTS block_deals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                fetch_date  TEXT NOT NULL UNIQUE,
                data_json   TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS etf_flows (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                date       TEXT NOT NULL UNIQUE,
                data_json  TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS semi_risk_snapshots (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                date       TEXT NOT NULL UNIQUE,
                data_json  TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS champion (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                ticker          TEXT NOT NULL,
                name            TEXT NOT NULL,
                market          TEXT NOT NULL DEFAULT 'KR',
                pattern         TEXT NOT NULL,
                conf            INTEGER NOT NULL,
                champion_score  REAL NOT NULL,
                current_price   INTEGER NOT NULL DEFAULT 0,
                entry_low       INTEGER NOT NULL DEFAULT 0,
                entry_high      INTEGER NOT NULL DEFAULT 0,
                stop_loss       INTEGER NOT NULL DEFAULT 0,
                target_price    INTEGER NOT NULL DEFAULT 0,
                week52_high     INTEGER NOT NULL DEFAULT 0,
                streak          INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_semi_risk_date ON semi_risk_snapshots(date);
            CREATE INDEX IF NOT EXISTS idx_liquidity_date ON liquidity_snapshots(date);
            CREATE INDEX IF NOT EXISTS idx_short_radar_date ON short_radar(date);
            CREATE INDEX IF NOT EXISTS idx_smart_money_cik ON smart_money(cik);
            CREATE INDEX IF NOT EXISTS idx_block_deals_date ON block_deals(fetch_date);
            CREATE INDEX IF NOT EXISTS idx_etf_flows_date ON etf_flows(date);
            CREATE INDEX IF NOT EXISTS idx_champion_date ON champion(date);
        """)
    # champion 마이그레이션: breakdown 컬럼 추가
    with _connect() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(champion)").fetchall()]
        if 'breakdown' not in cols:
            conn.execute("ALTER TABLE champion ADD COLUMN breakdown TEXT NOT NULL DEFAULT '{}'")

    # scan_results 마이그레이션: market 컬럼 추가
    with _connect() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(scan_results)").fetchall()]
        if 'market' not in cols:
            conn.execute("ALTER TABLE scan_results ADD COLUMN market TEXT NOT NULL DEFAULT ''")
            # 기존 한국 종목(숫자로 시작: 일반 6자리 + 우선주 03481K 등) → 'KR', 나머지 → 'US'
            conn.execute("""
                UPDATE scan_results
                SET market = CASE
                    WHEN ticker GLOB '[0-9]*' THEN 'KR'
                    ELSE 'US'
                END
                WHERE market = ''
            """)
            print("✅ scan_results.market 컬럼 마이그레이션 완료")

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

            # market 결정: result에 명시된 값 우선, 없으면 ticker로 추론
            mkt = r.get("market", "")
            if not mkt:
                t = r["ticker"]
                mkt = "KR" if t.isdigit() else "US"

            # 시가총액: 한국은 pykrx(원), 미국은 yfinance(USD)
            if mkt.startswith("KR") or mkt in ("KOSPI", "KOSDAQ"):
                mcap = market_cap
            elif mkt.startswith("US"):
                mcap = get_market_cap_us(r["ticker"])
            else:
                mcap = 0

            conn.execute(
                """INSERT INTO scan_results
                   (session_id, scanned_at, ticker, name, pattern, conf,
                    current_price, ma240, entry_low, entry_high,
                    stop_loss, target_price, market_cap, week52_high, week52_low, market)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    mcap,
                    r.get("week52_high", 0),
                    r.get("week52_low", 0),
                    mkt,
                ),
            )

    print(f"💾 스캔 저장 완료: session_id={session_id}, {len(results)}건")


def get_latest() -> list[dict]:
    """KR 최신 세션 + US 최신 세션을 항상 합쳐서 반환 (신뢰도 내림차순).
    각 시장의 마지막 스캔 결과를 날짜에 관계없이 독립적으로 유지.
    """
    with _connect() as conn:
        # KR 최신 세션
        kr_row = conn.execute(
            """SELECT id FROM scan_sessions s
               WHERE EXISTS (
                   SELECT 1 FROM scan_results r
                   WHERE r.session_id = s.id
                     AND (r.market IN ('KOSPI','KOSDAQ') OR r.market LIKE 'KR%')
               )
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()

        # US 최신 세션
        us_row = conn.execute(
            """SELECT id FROM scan_sessions s
               WHERE EXISTS (
                   SELECT 1 FROM scan_results r
                   WHERE r.session_id = s.id
                     AND r.market LIKE 'US%'
               )
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()

        session_ids = []
        if kr_row:
            session_ids.append(kr_row["id"])
        if us_row and us_row["id"] not in session_ids:
            session_ids.append(us_row["id"])

        if not session_ids:
            # 아무 시장도 없으면 마지막 세션 하나
            last = conn.execute(
                "SELECT id FROM scan_sessions ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not last:
                return []
            session_ids = [last["id"]]

        placeholders = ','.join('?' * len(session_ids))
        rows = conn.execute(
            f"""SELECT * FROM scan_results
                WHERE session_id IN ({placeholders})
                ORDER BY conf DESC""",
            session_ids,
        ).fetchall()

        # 동일 ticker 중복 제거 (최고 신뢰도 우선)
        seen: set[str] = set()
        result = []
        for r in rows:
            if r["ticker"] not in seen:
                seen.add(r["ticker"])
                result.append(dict(r))
        return result


def get_history(days: int = 30, market: str = '') -> list[dict]:
    """최근 N일 전체 스캔 결과 (최신순). market='' 이면 전체.
    market='US' 이면 US_NASDAQ/US_SP500/US_RUSSELL/US 모두 포함.
    market='KR' 이면 KOSPI/KOSDAQ/KR 모두 포함.
    """
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    with _connect() as conn:
        if market == 'US':
            rows = conn.execute(
                """SELECT * FROM scan_results
                   WHERE scanned_at >= ? AND market LIKE 'US%'
                   ORDER BY scanned_at DESC, conf DESC""",
                (since,),
            ).fetchall()
        elif market == 'KR':
            rows = conn.execute(
                """SELECT * FROM scan_results
                   WHERE scanned_at >= ? AND market IN ('KOSPI','KOSDAQ','KR')
                   ORDER BY scanned_at DESC, conf DESC""",
                (since,),
            ).fetchall()
        elif market:
            rows = conn.execute(
                """SELECT * FROM scan_results
                   WHERE scanned_at >= ? AND market = ?
                   ORDER BY scanned_at DESC, conf DESC""",
                (since, market),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM scan_results
                   WHERE scanned_at >= ?
                   ORDER BY scanned_at DESC, conf DESC""",
                (since,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_stock_tracking(market: str = '') -> list[dict]:
    """종목별 감지 횟수 + 최초 감지가 + 최근 신뢰도/패턴. market='' 이면 전체.
    market='US' → US_* 전체. market='KR' → KOSPI/KOSDAQ/KR 전체.
    """
    with _connect() as conn:
        if market == 'US':
            market_cond = "WHERE market LIKE 'US%'"
            params: tuple = ()
        elif market == 'KR':
            market_cond = "WHERE market IN ('KOSPI','KOSDAQ','KR')"
            params = ()
        elif market:
            market_cond = "WHERE market = ?"
            params = (market,)
        else:
            market_cond = ""
            params = ()
        rows = conn.execute(
            f"""SELECT
                   sr.ticker,
                   sr.name,
                   sr.market,
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
               {market_cond}
               GROUP BY sr.ticker
               ORDER BY total_hits DESC, last_detected DESC""",
            params,
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


# ── 캘린더 분석 결과 ─────────────────────────────────────────────────────

def save_calendar_actual(event_key: str, event_date: str, title: str,
                          forecast: str = '', previous: str = '',
                          actual: str = '') -> int:
    """FRED 실제값만 저장. 기존 analysis 텍스트는 보존."""
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO calendar_analyses
               (event_key, event_date, title, forecast, previous, actual, analysis, analyzed_at)
               VALUES (?, ?, ?, ?, ?, ?, '', ?)
               ON CONFLICT(event_key, event_date) DO UPDATE SET
                 actual=excluded.actual,
                 title=CASE WHEN excluded.title!='' THEN excluded.title ELSE title END,
                 analyzed_at=excluded.analyzed_at""",
            (event_key, event_date, title, forecast, previous, actual, now),
        )
        return cur.lastrowid


def save_calendar_analysis(event_key: str, event_date: str, title: str,
                           forecast: str, previous: str, actual: str,
                           analysis: str) -> int:
    """캘린더 이벤트 분석 결과 저장 (이미 있으면 업데이트)."""
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO calendar_analyses
               (event_key, event_date, title, forecast, previous, actual, analysis, analyzed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(event_key, event_date) DO UPDATE SET
                 actual=excluded.actual, analysis=excluded.analysis,
                 analyzed_at=excluded.analyzed_at""",
            (event_key, event_date, title, forecast, previous, actual, analysis, now),
        )
        return cur.lastrowid


def get_calendar_analysis(event_key: str, event_date: str) -> dict | None:
    """특정 이벤트의 분석 결과 조회."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM calendar_analyses WHERE event_key=? AND event_date=?",
            (event_key, event_date),
        ).fetchone()
        return dict(row) if row else None


def get_calendar_analyses(days: int = 30) -> list[dict]:
    """최근 N일 분석 결과 전체 조회 (최신순)."""
    since = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM calendar_analyses WHERE event_date >= ? ORDER BY analyzed_at DESC",
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_recent_calendar_alerts(hours: int = 24) -> list[dict]:
    """최근 N시간 내 분석된 이벤트 반환 (대시보드 알림용)."""
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM calendar_analyses WHERE analyzed_at >= ? ORDER BY analyzed_at DESC",
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]


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


# ── 이란 전쟁 OSINT ────────────────────────────────────────────────────

def upsert_iran_war_events(events: list[dict]) -> int:
    """이란 전쟁 이벤트 일괄 UPSERT. 반환: 신규 저장 수."""
    saved = 0
    with _connect() as conn:
        for e in events:
            try:
                conn.execute(
                    """INSERT INTO iran_war_events
                       (event_id, timestamp, lat, lon, strike_type, target_desc,
                        source_url, verified_by, casualties, context)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(event_id) DO NOTHING""",
                    (
                        e.get('event_id', ''),
                        e.get('timestamp', ''),
                        e.get('lat'),
                        e.get('lon'),
                        e.get('strike_type', ''),
                        e.get('target_desc', ''),
                        e.get('source_url', ''),
                        e.get('verified_by', ''),
                        int(e.get('casualties') or 0),
                        e.get('context', ''),
                    ),
                )
                saved += 1
            except Exception:
                pass
    return saved


def get_iran_war_events(since: str = None, limit: int = 200) -> list[dict]:
    """이란 전쟁 이벤트 조회. since: 'YYYY-MM-DD' 이후."""
    with _connect() as conn:
        if since:
            rows = conn.execute(
                """SELECT * FROM iran_war_events
                   WHERE timestamp >= ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM iran_war_events ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_iran_war_events_daily_summary() -> list[dict]:
    """날짜별 이벤트 수·사상자 집계."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT substr(timestamp,1,10) AS date,
                      COUNT(*) AS events,
                      SUM(casualties) AS casualties
               FROM iran_war_events
               GROUP BY date
               ORDER BY date ASC"""
        ).fetchall()
    return [dict(r) for r in rows]


def save_iran_war_military(date: str, data: list[dict]) -> None:
    """병력 현황 저장 (날짜당 1개)."""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO iran_war_military (fetched_date, data_json)
               VALUES (?, ?)
               ON CONFLICT(fetched_date) DO UPDATE SET
                 data_json = excluded.data_json,
                 saved_at  = datetime('now','localtime')""",
            (date, json.dumps(data, ensure_ascii=False)),
        )


def get_iran_war_military() -> list[dict]:
    """최신 병력 현황 반환."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT data_json FROM iran_war_military ORDER BY fetched_date DESC LIMIT 1"
        ).fetchone()
    if not row:
        return []
    try:
        return json.loads(row['data_json'])
    except Exception:
        return []


def save_iran_war_airspace(date: str, data: list[dict]) -> None:
    """영공 현황 저장."""
    with _connect() as conn:
        conn.execute(
            """INSERT INTO iran_war_airspace (fetched_date, data_json)
               VALUES (?, ?)
               ON CONFLICT(fetched_date) DO UPDATE SET
                 data_json = excluded.data_json,
                 saved_at  = datetime('now','localtime')""",
            (date, json.dumps(data, ensure_ascii=False)),
        )


def get_iran_war_airspace() -> list[dict]:
    """최신 영공 현황 반환."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT data_json FROM iran_war_airspace ORDER BY fetched_date DESC LIMIT 1"
        ).fetchone()
    if not row:
        return []
    try:
        return json.loads(row['data_json'])
    except Exception:
        return []


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


# ── 유동성 스냅샷 ─────────────────────────────────────────────────────

def save_liquidity_snapshot(date: str, data: dict) -> int:
    """유동성 스냅샷 저장 (날짜당 1개, UPSERT)."""
    data_json = json.dumps(data, ensure_ascii=False)
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO liquidity_snapshots (date, data_json)
               VALUES (?, ?)
               ON CONFLICT(date) DO UPDATE SET
                 data_json  = excluded.data_json,
                 created_at = datetime('now', 'localtime')""",
            (date, data_json),
        )
        return cur.lastrowid


def get_liquidity_latest() -> dict | None:
    """가장 최근 유동성 스냅샷 반환."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT date, data_json, created_at FROM liquidity_snapshots ORDER BY date DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["data_json"])
    except Exception:
        data = {}
    return {"date": row["date"], "data": data, "created_at": row["created_at"]}


def get_liquidity_history(days: int = 90) -> list[dict]:
    """최근 N일 유동성 스냅샷 반환 (오래된→최신 순)."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT date, data_json, created_at
               FROM liquidity_snapshots
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


# ── 공매도 레이더 ─────────────────────────────────────────────────────

def save_short_radar(date: str, data: dict) -> int:
    """공매도 레이더 저장 (날짜당 1개, UPSERT)."""
    data_json = json.dumps(data, ensure_ascii=False)
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO short_radar (date, data_json)
               VALUES (?, ?)
               ON CONFLICT(date) DO UPDATE SET
                 data_json  = excluded.data_json,
                 created_at = datetime('now', 'localtime')""",
            (date, data_json),
        )
        return cur.lastrowid


def get_short_radar_latest() -> dict | None:
    """가장 최근 공매도 레이더 데이터 반환."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT date, data_json, created_at FROM short_radar ORDER BY date DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["data_json"])
    except Exception:
        data = {}
    return {"date": row["date"], "data": data, "created_at": row["created_at"]}


# ── 스마트머니 ────────────────────────────────────────────────────────

def save_smart_money(cik: str, quarter: str, data: dict) -> int:
    """스마트머니 13F 저장 (CIK+분기당 1개, UPSERT)."""
    data_json = json.dumps(data, ensure_ascii=False)
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO smart_money (cik, quarter, data_json, saved_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(cik, quarter) DO UPDATE SET
                 data_json = excluded.data_json,
                 saved_at  = excluded.saved_at""",
            (cik, quarter, data_json, now),
        )
        return cur.lastrowid


def get_smart_money_latest() -> list[dict]:
    """각 CIK별 최신 13F 데이터 반환."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT cik, quarter, data_json, saved_at
               FROM smart_money
               WHERE (cik, quarter) IN (
                   SELECT cik, MAX(quarter) FROM smart_money GROUP BY cik
               )
               ORDER BY cik"""
        ).fetchall()
    result = []
    for row in rows:
        try:
            data = json.loads(row["data_json"])
        except Exception:
            data = {}
        result.append({
            "cik":      row["cik"],
            "quarter":  row["quarter"],
            "data":     data,
            "saved_at": row["saved_at"],
        })
    return result


# ── 블록딜 추적 ───────────────────────────────────────────────────────

def save_block_deals(fetch_date: str, data: dict) -> int:
    """블록딜 스냅샷 저장 (날짜당 1개, UPSERT)."""
    data_json = json.dumps(data, ensure_ascii=False)
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        # block_deals 테이블이 없으면 생성 (마이그레이션 대응)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS block_deals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                fetch_date  TEXT NOT NULL UNIQUE,
                data_json   TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        cur = conn.execute(
            """INSERT INTO block_deals (fetch_date, data_json, created_at)
               VALUES (?, ?, ?)
               ON CONFLICT(fetch_date) DO UPDATE SET
                 data_json  = excluded.data_json,
                 created_at = excluded.created_at""",
            (fetch_date, data_json, now),
        )
        return cur.lastrowid


def get_block_deals_latest() -> dict | None:
    """가장 최근 블록딜 스냅샷 반환."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT fetch_date, data_json, created_at FROM block_deals ORDER BY fetch_date DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["data_json"])
    except Exception:
        data = {}
    return {"fetch_date": row["fetch_date"], "data": data, "created_at": row["created_at"]}


def get_block_deals_history(days: int = 30) -> list[dict]:
    """최근 N일간 블록딜 스냅샷 목록 반환."""
    cutoff = (datetime.now() - __import__('datetime').timedelta(days=days)).strftime('%Y-%m-%d')
    with _connect() as conn:
        rows = conn.execute(
            "SELECT fetch_date, data_json, created_at FROM block_deals WHERE fetch_date >= ? ORDER BY fetch_date DESC",
            (cutoff,),
        ).fetchall()
    result = []
    for row in rows:
        try:
            data = json.loads(row["data_json"])
        except Exception:
            data = {}
        result.append({"fetch_date": row["fetch_date"], "data": data, "created_at": row["created_at"]})
    return result


def save_etf_flows(date: str, data: dict) -> int:
    """ETF 플로우 스냅샷 저장 (날짜당 1개, UPSERT)."""
    data_json = json.dumps(data, ensure_ascii=False)
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO etf_flows (date, data_json)
               VALUES (?, ?)
               ON CONFLICT(date) DO UPDATE SET
                 data_json = excluded.data_json,
                 created_at = datetime('now','localtime')""",
            (date, data_json),
        )
        return cur.lastrowid


def get_etf_flows_latest() -> dict | None:
    """가장 최신 ETF 플로우 스냅샷 반환."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT date, data_json, created_at FROM etf_flows ORDER BY date DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["data_json"])
    except Exception:
        data = {}
    return {"date": row["date"], "data": data, "created_at": row["created_at"]}


def get_etf_flows_history(days: int = 30) -> list[dict]:
    """최근 N일간 ETF 플로우 스냅샷 목록 반환."""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    with _connect() as conn:
        rows = conn.execute(
            "SELECT date, data_json, created_at FROM etf_flows WHERE date >= ? ORDER BY date DESC",
            (cutoff,),
        ).fetchall()
    result = []
    for row in rows:
        try:
            data = json.loads(row["data_json"])
        except Exception:
            data = {}
        result.append({"date": row["date"], "data": data, "created_at": row["created_at"]})
    return result


# ── 반도체 취약 리스크 스냅샷 ──────────────────────────────────────

def save_semi_risk(date: str, data: dict) -> int:
    """반도체 리스크 스냅샷 저장 (날짜별 1개, UPSERT)."""
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO semi_risk_snapshots (date, data_json)
               VALUES (?, ?)
               ON CONFLICT(date) DO UPDATE SET data_json=excluded.data_json,
               created_at=datetime('now','localtime')""",
            (date, json.dumps(data, ensure_ascii=False)),
        )
        return cur.lastrowid


def get_semi_risk_latest() -> dict | None:
    """가장 최신 반도체 리스크 스냅샷 반환."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT date, data_json, created_at FROM semi_risk_snapshots ORDER BY date DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["data_json"])
    except Exception:
        data = {}
    return {"date": row["date"], "data": data, "created_at": row["created_at"]}


# ── 챔피언 ────────────────────────────────────────────────────────────

def _parse_champion(row) -> dict:
    import json as _json
    d = dict(row)
    try:
        d["breakdown"] = _json.loads(d.get("breakdown") or "{}")
    except Exception:
        d["breakdown"] = {}
    return d


def get_current_champion(market: str | None = None) -> dict | None:
    """현재 챔피언 조회. market='KR'/'US' 로 시장별 분리."""
    with _connect() as conn:
        if market:
            row = conn.execute(
                "SELECT * FROM champion WHERE market=? ORDER BY id DESC LIMIT 1", (market,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM champion ORDER BY id DESC LIMIT 1"
            ).fetchone()
    if not row:
        return None
    return _parse_champion(row)


def get_champion_history(limit: int = 30, market: str | None = None) -> list[dict]:
    """챔피언 이력 조회."""
    with _connect() as conn:
        if market:
            rows = conn.execute(
                "SELECT * FROM champion WHERE market=? ORDER BY id DESC LIMIT ?", (market, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM champion ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [_parse_champion(r) for r in rows]


def get_champion_history_full(limit: int = 60, market: str | None = None) -> list[dict]:
    """역대 챔피언 + 최신가 + 수익률 포함."""
    with _connect() as conn:
        if market:
            rows = conn.execute(
                "SELECT * FROM champion WHERE market=? ORDER BY id DESC LIMIT ?", (market, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM champion ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for row in rows:
            d = _parse_champion(row)
            detect_price = d.get("current_price", 0)
            latest_price = 0
            return_pct = None
            if detect_price > 0:
                price_row = conn.execute(
                    "SELECT current_price FROM scan_results WHERE ticker=? ORDER BY scanned_at DESC LIMIT 1",
                    (d["ticker"],)
                ).fetchone()
                if price_row and price_row[0]:
                    latest_price = int(price_row[0])
                    return_pct = round((latest_price - detect_price) / detect_price * 100, 1)
            d["latest_price"] = latest_price
            d["return_pct"] = return_pct
            result.append(d)
    return result


def update_champion(result: dict) -> dict:
    """
    새 스캔 결과(result)와 같은 시장 현재 챔피언을 비교해 필요 시 교체.
    - 더 높은 점수의 다른 종목 발견 → 교체 (streak=1)
    - 같은 종목이 오늘 다시 최고 → streak + 1
    - 현재 챔피언이 여전히 우위 → 유지 (변경 없음)
    반환: 업데이트된 champion dict
    """
    from datetime import datetime as _dt
    today  = _dt.now().strftime("%Y-%m-%d")
    market = result.get("market", "KR")
    new_score = float(result.get("champion_score", 0.0))
    cur = get_current_champion(market)

    if not cur:
        _save_champion(result, streak=1, today=today)
        return get_current_champion(market)

    cur_score  = float(cur.get("champion_score", 0.0))
    cur_date   = cur.get("date", "")
    cur_ticker = cur.get("ticker", "")

    if result["ticker"] == cur_ticker:
        streak = cur.get("streak", 1) + (1 if cur_date != today else 0)
        if new_score >= cur_score or cur_date != today:
            _save_champion(result, streak=streak, today=today)
    elif new_score > cur_score:
        _save_champion(result, streak=1, today=today)

    return get_current_champion(market)


def _save_champion(result: dict, streak: int, today: str) -> None:
    import json as _json
    entry = result.get("entry")
    entry_low  = entry[0] if isinstance(entry, (list, tuple)) and len(entry) >= 2 else 0
    entry_high = entry[1] if isinstance(entry, (list, tuple)) and len(entry) >= 2 else 0
    breakdown_json = _json.dumps(result.get("champion_breakdown") or {}, ensure_ascii=False)
    with _connect() as conn:
        conn.execute(
            """INSERT INTO champion
               (date, ticker, name, market, pattern, conf, champion_score,
                current_price, entry_low, entry_high, stop_loss, target_price,
                week52_high, streak, breakdown)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                today,
                result.get("ticker", ""),
                result.get("name", ""),
                result.get("market", "KR"),
                result.get("pattern", ""),
                int(result.get("conf", 0)),
                round(float(result.get("champion_score", 0.0)), 1),
                int(result.get("current", 0)),
                int(entry_low),
                int(entry_high),
                int(result.get("stop", 0)),
                int(result.get("target", 0)),
                int(result.get("week52_high", 0)),
                int(streak),
                breakdown_json,
            ),
        )
