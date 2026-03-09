"""
crawl_daily.py
후보 종목(candidates_cache) 대상 OHLCV + 보조지표 수집기

pykrx 배치 API(전종목 by 날짜)가 KRX 세션 인증 문제로 막혀 있어,
개별 종목 API(get_market_ohlcv_by_date)만 사용하는 방식으로 구현.
티커 목록은 data_fetcher.get_candidates_cached() 에서 가져옴.

사용법:
    python crawl_daily.py                       # 후보 종목 최신 1년치 수집
    python crawl_daily.py --backfill            # 위와 동일 (alias)
    python crawl_daily.py --backfill --months 3 # 3개월치만 수집
    python crawl_daily.py --daily               # 최근 5거래일만 업데이트 (일상 실행)
"""

import argparse
import math
import time
from datetime import datetime, timedelta

from pykrx import stock

import data_store

try:
    import pandas_ta as ta
    HAS_TA = True
except ImportError:
    HAS_TA = False
    print("⚠️  pandas-ta 미설치 — 보조지표 계산 건너뜀")
    print("   pip install pandas-ta 로 설치하세요.")


# ── 티커 목록 ────────────────────────────────────────────────────────────

def _tickers_from_scan_history() -> list[str]:
    """stocks.db scan_results 전체 이력에서 unique 티커 반환"""
    import sqlite3, os
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stocks.db")
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM scan_results ORDER BY ticker"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def _get_tickers() -> list[str]:
    """
    후보 종목 코드 목록 반환 (우선순위):
    1) market_data.db 에 이미 저장된 종목 (일일 업데이트용)
    2) stocks.db scan_results 전체 이력 종목
    """
    stored = data_store.get_all_tickers()
    if stored:
        return stored
    return _tickers_from_scan_history()


def _get_fresh_tickers() -> list[str]:
    """
    backfill 시 사용할 티커 목록:
    stocks.db 전체 이력 종목 반환
    """
    tickers = _tickers_from_scan_history()
    print(f"📋 scan_results 이력 종목: {len(tickers)}개")
    return tickers


# ── 개별 종목 OHLCV 수집 ─────────────────────────────────────────────────

def _safe_float(val) -> float | None:
    """NaN/None → None, 그 외 float 변환"""
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _collect_ticker(ticker: str, start: str, end: str, market: str = "UNKNOWN") -> int:
    """
    단일 종목 OHLCV(start~end) 수집 → DB 저장.
    반환: 저장 행 수
    """
    try:
        df = stock.get_market_ohlcv_by_date(start, end, ticker)
        if df is None or df.empty:
            return 0

        df = df[df["거래량"] > 0]
        if df.empty:
            return 0

        rows = []
        for date_idx, row in df.iterrows():
            date_str = date_idx.strftime("%Y%m%d")
            rows.append({
                "ticker": ticker,
                "open":   int(row.get("시가",   0)),
                "high":   int(row.get("고가",   0)),
                "low":    int(row.get("저가",   0)),
                "close":  int(row.get("종가",   0)),
                "volume": int(row.get("거래량", 0)),
            })
            data_store.save_ohlcv_batch(date_str, market, [rows[-1]])

        return len(rows)

    except Exception as e:
        print(f"  ⚠️  {ticker} OHLCV 수집 오류: {e}")
        return 0


# ── 보조지표 계산 ─────────────────────────────────────────────────────────

def _compute_indicators(ticker: str) -> int:
    """단일 종목 보조지표 계산 → DB 저장. 업데이트 행 수 반환."""
    if not HAS_TA:
        return 0

    df = data_store.get_ticker_history(ticker, n=500)
    if df is None or len(df) < 14:
        return 0

    try:
        c = df["Close"]
        h = df["High"]
        l = df["Low"]
        v = df["Volume"]

        df["ma5"]         = c.rolling(5).mean()
        df["ma10"]        = c.rolling(10).mean()
        df["ma20"]        = c.rolling(20).mean()
        df["ma60"]        = c.rolling(60).mean()
        df["ma120"]       = c.rolling(120).mean()
        df["ma240"]       = c.rolling(240).mean()
        df["volume_ma20"] = v.rolling(20).mean()

        rsi = ta.rsi(c, length=14)
        df["rsi14"] = rsi

        macd = ta.macd(c, fast=12, slow=26, signal=9)
        if macd is not None and not macd.empty:
            df["macd_dif"]    = macd.iloc[:, 0]
            df["macd_signal"] = macd.iloc[:, 1]
            df["macd_hist"]   = macd.iloc[:, 2]

        stoch = ta.stoch(h, l, c, k=14, d=3, smooth_k=3)
        if stoch is not None and not stoch.empty:
            df["stoch_k"] = stoch.iloc[:, 0]
            df["stoch_d"] = stoch.iloc[:, 1]

        atr = ta.atr(h, l, c, length=14)
        df["atr14"] = atr

        bb = ta.bbands(c, length=20, std=2)
        if bb is not None and not bb.empty:
            df["bb_lower"]  = bb.iloc[:, 0]
            df["bb_middle"] = bb.iloc[:, 1]
            df["bb_upper"]  = bb.iloc[:, 2]
            df["bb_width"]  = bb.iloc[:, 3]

        obv = ta.obv(c, v)
        df["obv"] = obv

        records = []
        for idx, row in df.iterrows():
            records.append({
                "ticker":      ticker,
                "date":        idx.strftime("%Y%m%d"),
                "ma5":         _safe_float(row.get("ma5")),
                "ma10":        _safe_float(row.get("ma10")),
                "ma20":        _safe_float(row.get("ma20")),
                "ma60":        _safe_float(row.get("ma60")),
                "ma120":       _safe_float(row.get("ma120")),
                "ma240":       _safe_float(row.get("ma240")),
                "volume_ma20": _safe_float(row.get("volume_ma20")),
                "rsi14":       _safe_float(row.get("rsi14")),
                "macd_dif":    _safe_float(row.get("macd_dif")),
                "macd_signal": _safe_float(row.get("macd_signal")),
                "macd_hist":   _safe_float(row.get("macd_hist")),
                "stoch_k":     _safe_float(row.get("stoch_k")),
                "stoch_d":     _safe_float(row.get("stoch_d")),
                "atr14":       _safe_float(row.get("atr14")),
                "bb_upper":    _safe_float(row.get("bb_upper")),
                "bb_middle":   _safe_float(row.get("bb_middle")),
                "bb_lower":    _safe_float(row.get("bb_lower")),
                "bb_width":    _safe_float(row.get("bb_width")),
                "obv":         _safe_float(row.get("obv")),
            })

        data_store.update_indicators(ticker, records)
        return len(records)

    except Exception as e:
        print(f"  ⚠️  {ticker} 지표 계산 오류: {e}")
        return 0


# ── 백필 모드 ─────────────────────────────────────────────────────────────

def run_backfill(months: int = 12) -> None:
    """
    후보 종목 전체 OHLCV(최대 N개월) 수집 + 지표 계산.
    종목당 1 API 호출 (get_market_ohlcv_by_date, 전체 기간).
    """
    data_store.init_db()

    end_dt   = datetime.now()
    start_dt = end_dt - timedelta(days=months * 31)
    start    = start_dt.strftime("%Y%m%d")
    end      = end_dt.strftime("%Y%m%d")

    tickers = _get_fresh_tickers()
    if not tickers:
        print("❌ 후보 종목 없음. data_fetcher 확인 필요.")
        return

    print(f"\n📅 수집 기간: {start} ~ {end} ({months}개월)")
    print(f"📋 대상 종목: {len(tickers)}개\n")

    # Step 1: OHLCV 수집 (종목당 1 API 호출)
    total_rows = 0
    for i, ticker in enumerate(tickers, 1):
        rows = _collect_ticker(ticker, start, end)
        total_rows += rows
        if i % 50 == 0 or i == len(tickers):
            print(f"  [{i:4}/{len(tickers)}] {ticker}: 누적 {total_rows:,}행")
        time.sleep(0.15)

    print(f"\n✅ OHLCV 수집 완료: {len(tickers)}개 종목 / 총 {total_rows:,}행")

    # Step 2: 보조지표 계산
    if not HAS_TA:
        print("⚠️  pandas-ta 없음 — 지표 계산 건너뜀")
        return

    stored = data_store.get_all_tickers()
    print(f"\n📊 보조지표 계산: {len(stored)}개 종목")
    for i, ticker in enumerate(stored, 1):
        _compute_indicators(ticker)
        if i % 50 == 0 or i == len(stored):
            print(f"  {i}/{len(stored)} 완료...")

    print(f"✅ 백필 완료!")


# ── 일일 모드 ─────────────────────────────────────────────────────────────

def run_daily(date: str | None = None) -> None:
    """
    최근 5거래일 OHLCV 업데이트 + 지표 재계산.
    date: 기준 날짜 YYYYMMDD (None이면 오늘)
    """
    data_store.init_db()

    end_dt   = datetime.strptime(date, "%Y%m%d") if date else datetime.now()
    start_dt = end_dt - timedelta(days=10)  # 주말/공휴일 여유
    start    = start_dt.strftime("%Y%m%d")
    end      = end_dt.strftime("%Y%m%d")

    tickers = _get_tickers()
    if not tickers:
        print("❌ 종목 목록 없음. 먼저 --backfill 실행 필요.")
        return

    print(f"📅 일일 업데이트: {end} 기준 / {len(tickers)}개 종목")

    total_rows = 0
    for ticker in tickers:
        rows = _collect_ticker(ticker, start, end)
        total_rows += rows
        time.sleep(0.1)

    print(f"✅ OHLCV: {total_rows:,}행 저장")

    if not HAS_TA:
        return

    stored = data_store.get_all_tickers()
    for ticker in stored:
        _compute_indicators(ticker)

    print(f"✅ 일일 업데이트 완료")


# ── 진입점 ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="후보 종목 OHLCV + 지표 수집")
    parser.add_argument("--backfill", action="store_true",
                        help="전체 기간 수집 (초기 실행)")
    parser.add_argument("--daily",    action="store_true",
                        help="최근 5거래일 업데이트 (일상 실행, 기본값)")
    parser.add_argument("--months",   type=int, default=12,
                        help="--backfill 수집 개월 수 (기본: 12)")
    parser.add_argument("--date",     type=str, default=None,
                        help="--daily 기준 날짜 YYYYMMDD (기본: 오늘)")
    args = parser.parse_args()

    if args.backfill:
        run_backfill(months=args.months)
    else:
        run_daily(date=args.date)


if __name__ == "__main__":
    main()
