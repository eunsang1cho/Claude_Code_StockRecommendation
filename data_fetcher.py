"""
data_fetcher.py
pykrx 기반 주식 데이터 수집 + 후보 종목 필터
"""

import json
import os
import time
from datetime import datetime, timedelta

import pandas as pd
from pykrx import stock

DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(DIR, "candidates_cache.json")
CACHE_HOURS = 12


# ── 후보 종목 (최근 N일 내 장대양봉 발생) ─────────────────────────────

def get_candidates_cached(days_back: int = 70, force_refresh: bool = False) -> list[str]:
    """캐시를 활용한 후보 종목 조회 (12시간 유효)"""
    if not force_refresh and os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        cached_at = datetime.fromisoformat(cache["timestamp"])
        if (datetime.now() - cached_at).total_seconds() < CACHE_HOURS * 3600:
            return cache["tickers"]

    tickers = _fetch_candidates(days_back)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"timestamp": datetime.now().isoformat(), "tickers": tickers}, f)
    return tickers


def _fetch_candidates(days_back: int = 70, min_pct: float = 15.0) -> list[str]:
    """
    날짜별 전체 종목 데이터를 일괄 수집해 장대양봉 후보를 추출.
    하루에 2번 호출(KOSPI/KOSDAQ)이므로 days_back * 2 회 API 호출.
    """
    today = datetime.now()
    big_tickers: set[str] = set()

    # 시총 5조↓ 종목 집합 (오늘 기준)
    small_cap_set = _get_small_cap_set()

    for i in range(1, days_back + 1):
        date_str = (today - timedelta(days=i)).strftime("%Y%m%d")

        for market in ["KOSPI", "KOSDAQ"]:
            try:
                df = stock.get_market_ohlcv_by_ticker(date_str, market=market)
                if df.empty:
                    continue

                df = df[df["시가"] > 0].copy()
                df["_pct"] = (df["종가"] - df["시가"]) / df["시가"] * 100

                # 장대양봉 조건 + 시총 필터
                big = df[df["_pct"] >= min_pct].index.tolist()
                if small_cap_set:
                    big = [t for t in big if t in small_cap_set]
                big_tickers.update(big)
            except Exception:
                pass

            time.sleep(0.15)

    return list(big_tickers)


def _get_small_cap_set(threshold_trillion: float = 5.0) -> set[str]:
    """시총 5조 이하 종목 코드 집합 반환"""
    threshold = threshold_trillion * 1e12
    today = datetime.now().strftime("%Y%m%d")

    small_cap: set[str] = set()
    for market in ["KOSPI", "KOSDAQ"]:
        try:
            df = stock.get_market_cap_by_ticker(today, market=market)
            if df.empty:
                continue
            col = "시가총액" if "시가총액" in df.columns else df.columns[0]
            filtered = df[df[col] < threshold].index.tolist()
            small_cap.update(filtered)
        except Exception:
            pass
        time.sleep(0.2)

    return small_cap


# ── 개별 종목 데이터 ──────────────────────────────────────────────────

def get_ohlcv(ticker: str, days: int = 95) -> pd.DataFrame | None:
    """개별 종목 OHLCV (95일치)"""
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days + 10)).strftime("%Y%m%d")

    try:
        df = stock.get_market_ohlcv_by_date(start, end, ticker)
        if df.empty:
            return None

        df = df.rename(columns={
            "시가": "Open", "고가": "High", "저가": "Low",
            "종가": "Close", "거래량": "Volume",
        })
        df = df[df["Volume"] > 0]  # 거래 없는 날 제외
        return df
    except Exception:
        return None


def get_stock_name(ticker: str) -> str:
    """종목코드 → 종목명"""
    try:
        return stock.get_market_ticker_name(ticker)
    except Exception:
        return ticker


def get_current_price(ticker: str) -> int:
    """최신 종가 반환"""
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
    try:
        df = stock.get_market_ohlcv_by_date(start, end, ticker)
        if df.empty:
            return 0
        return int(df["종가"].iloc[-1])
    except Exception:
        return 0


def get_market_cap(ticker: str) -> int:
    """단일 종목 시가총액 (원 단위)"""
    today = datetime.now().strftime("%Y%m%d")
    for market in ["KOSPI", "KOSDAQ"]:
        try:
            df = stock.get_market_cap_by_ticker(today, market=market)
            if ticker in df.index:
                col = "시가총액" if "시가총액" in df.columns else df.columns[0]
                return int(df.loc[ticker, col])
        except Exception:
            pass
        time.sleep(0.1)
    return 0
