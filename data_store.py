"""
data_store.py
market_data.db 전용 모듈 — 종목별 일별 OHLCV + 보조지표 저장/조회
"""

import os
import sqlite3

import pandas as pd

DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(DIR, "market_data.db")

_DDL = """
CREATE TABLE IF NOT EXISTS stock_daily (
    ticker      TEXT NOT NULL,
    date        TEXT NOT NULL,   -- YYYYMMDD
    market      TEXT NOT NULL,   -- KOSPI / KOSDAQ
    open        INTEGER NOT NULL,
    high        INTEGER NOT NULL,
    low         INTEGER NOT NULL,
    close       INTEGER NOT NULL,
    volume      INTEGER NOT NULL,
    -- Moving Averages
    ma5         REAL, ma10  REAL, ma20  REAL,
    ma60        REAL, ma120 REAL, ma240 REAL,
    volume_ma20 REAL,
    -- Momentum
    rsi14       REAL,
    macd_dif    REAL, macd_signal REAL, macd_hist REAL,
    stoch_k     REAL, stoch_d    REAL,
    -- Volatility / Bands
    atr14       REAL,
    bb_upper    REAL, bb_middle REAL, bb_lower REAL, bb_width REAL,
    -- Volume
    obv         REAL,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_sd_date   ON stock_daily(date);
CREATE INDEX IF NOT EXISTS idx_sd_ticker ON stock_daily(ticker);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """테이블/인덱스 생성"""
    with _connect() as conn:
        conn.executescript(_DDL)
    print(f"✅ market_data.db 초기화 완료: {DB_FILE}")


def save_ohlcv_batch(date: str, market: str, rows: list[dict]) -> int:
    """
    하루치 OHLCV UPSERT.
    rows: [{"ticker": "005930", "open": 70000, "high": ..., ...}, ...]
    반환: 저장 행 수
    """
    if not rows:
        return 0
    with _connect() as conn:
        conn.executemany(
            """INSERT INTO stock_daily (ticker, date, market, open, high, low, close, volume)
               VALUES (:ticker, :date, :market, :open, :high, :low, :close, :volume)
               ON CONFLICT(ticker, date) DO UPDATE SET
                 market = excluded.market,
                 open   = excluded.open,
                 high   = excluded.high,
                 low    = excluded.low,
                 close  = excluded.close,
                 volume = excluded.volume""",
            [
                {
                    "ticker": r["ticker"],
                    "date":   date,
                    "market": market,
                    "open":   r["open"],
                    "high":   r["high"],
                    "low":    r["low"],
                    "close":  r["close"],
                    "volume": r["volume"],
                }
                for r in rows
            ],
        )
    return len(rows)


def get_ticker_history(ticker: str, n: int = 300) -> pd.DataFrame | None:
    """
    최근 n일 OHLCV + 보조지표 DataFrame 반환.
    인덱스: datetime, 컬럼: Open High Low Close Volume + 지표 컬럼들
    데이터 부족(0행) 시 None 반환.
    """
    with _connect() as conn:
        rows = conn.execute(
            """SELECT date, open, high, low, close, volume,
                      ma5, ma10, ma20, ma60, ma120, ma240, volume_ma20,
                      rsi14, macd_dif, macd_signal, macd_hist,
                      stoch_k, stoch_d, atr14,
                      bb_upper, bb_middle, bb_lower, bb_width, obv
               FROM stock_daily
               WHERE ticker = ?
               ORDER BY date DESC
               LIMIT ?""",
            (ticker, n),
        ).fetchall()

    if not rows:
        return None

    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.sort_values("date").set_index("date")

    df = df.rename(columns={
        "open":        "Open",
        "high":        "High",
        "low":         "Low",
        "close":       "Close",
        "volume":      "Volume",
        "ma5":         "MA5",
        "ma10":        "MA10",
        "ma20":        "MA20",
        "ma60":        "MA60",
        "ma120":       "MA120",
        "ma240":       "MA240",
        "volume_ma20": "VMA20",
        "rsi14":       "RSI14",
        "macd_dif":    "MACD_DIF",
        "macd_signal": "MACD_SIG",
        "macd_hist":   "MACD_HIST",
        "stoch_k":     "STOCH_K",
        "stoch_d":     "STOCH_D",
        "atr14":       "ATR14",
        "bb_upper":    "BB_UPPER",
        "bb_middle":   "BB_MIDDLE",
        "bb_lower":    "BB_LOWER",
        "bb_width":    "BB_WIDTH",
        "obv":         "OBV",
    })
    return df


def get_latest_date() -> str | None:
    """저장된 가장 최신 날짜 반환 (YYYYMMDD), 없으면 None"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM stock_daily"
        ).fetchone()
    return row["d"] if row and row["d"] else None


def update_indicators(ticker: str, records: list[dict]) -> None:
    """
    지표 컬럼 UPSERT.
    records: [{"date": "20250101", "ma5": ..., "rsi14": ..., ...}, ...]
    OHLCV 행이 존재할 때만 UPDATE (없는 날짜는 무시).
    """
    if not records:
        return
    with _connect() as conn:
        conn.executemany(
            """UPDATE stock_daily SET
                   ma5         = :ma5,
                   ma10        = :ma10,
                   ma20        = :ma20,
                   ma60        = :ma60,
                   ma120       = :ma120,
                   ma240       = :ma240,
                   volume_ma20 = :volume_ma20,
                   rsi14       = :rsi14,
                   macd_dif    = :macd_dif,
                   macd_signal = :macd_signal,
                   macd_hist   = :macd_hist,
                   stoch_k     = :stoch_k,
                   stoch_d     = :stoch_d,
                   atr14       = :atr14,
                   bb_upper    = :bb_upper,
                   bb_middle   = :bb_middle,
                   bb_lower    = :bb_lower,
                   bb_width    = :bb_width,
                   obv         = :obv
               WHERE ticker = :ticker AND date = :date""",
            records,
        )


def get_all_tickers() -> list[str]:
    """저장된 전체 티커 목록 (알파벳 순)"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM stock_daily ORDER BY ticker"
        ).fetchall()
    return [r["ticker"] for r in rows]
