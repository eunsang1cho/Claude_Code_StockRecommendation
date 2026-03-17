"""
data_fetcher.py
pykrx 기반 주식 데이터 수집 + 후보 종목 필터 (한국 + 미국)
"""

import json
import os
import time
from datetime import datetime, timedelta

import pandas as pd
from pykrx import stock

DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE     = os.path.join(DIR, "candidates_cache.json")
US_CACHE_FILE  = os.path.join(DIR, "us_candidates_cache.json")
CACHE_HOURS    = 12


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
    KRX 배치 API 실패 시 로컬 DB 폴백.
    """
    today = datetime.now()
    big_tickers: set[str] = set()
    batch_success = False

    # 시총 5조↓ 종목 집합 (오늘 기준)
    small_cap_set = _get_small_cap_set()

    for i in range(1, days_back + 1):
        date_str = (today - timedelta(days=i)).strftime("%Y%m%d")

        for market in ["KOSPI", "KOSDAQ"]:
            try:
                df = stock.get_market_ohlcv_by_ticker(date_str, market=market)
                if df.empty:
                    continue
                if "시가" not in df.columns:
                    continue

                df = df[df["시가"] > 0].copy()
                df["_pct"] = (df["종가"] - df["시가"]) / df["시가"] * 100

                # 장대양봉 조건 + 시총 필터
                big = df[df["_pct"] >= min_pct].index.tolist()
                if small_cap_set:
                    big = [t for t in big if t in small_cap_set]
                big_tickers.update(big)
                batch_success = True
            except Exception:
                pass

            time.sleep(0.15)

    if not batch_success or not big_tickers:
        # KRX 배치 API 불가 → 로컬 DB 기반 폴백
        print("⚠️  KRX 배치 API 응답 없음 → 로컬 DB 폴백 모드")
        big_tickers = _fetch_candidates_local(days_back, min_pct)

    return list(big_tickers)


def _fetch_candidates_local(days_back: int = 70, min_pct: float = 15.0) -> set[str]:
    """
    KRX 배치 API 실패 시 폴백:
    1) market_data.db에서 최근 N일 내 장대양봉 종목 추출
    2) scan_results 이력 종목 추가 (과거 감지 종목은 계속 모니터링)
    """
    import sqlite3

    big_tickers: set[str] = set()
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")

    # 1. market_data.db 로컬 OHLCV → 장대양봉 조건
    mdb = os.path.join(DIR, "market_data.db")
    if os.path.exists(mdb):
        try:
            conn = sqlite3.connect(mdb)
            rows = conn.execute(
                """SELECT DISTINCT ticker FROM stock_daily
                   WHERE date >= ? AND open > 0
                     AND CAST(close - open AS REAL) / open * 100 >= ?""",
                (cutoff, min_pct),
            ).fetchall()
            conn.close()
            big_tickers.update(r[0] for r in rows)
        except Exception:
            pass

    # 2. scan_results 이력 종목 (과거 패턴 감지 종목)
    sdb = os.path.join(DIR, "stocks.db")
    if os.path.exists(sdb):
        try:
            conn = sqlite3.connect(sdb)
            rows = conn.execute(
                "SELECT DISTINCT ticker FROM scan_results"
            ).fetchall()
            conn.close()
            big_tickers.update(r[0] for r in rows)
        except Exception:
            pass

    print(f"   로컬 폴백 후보: {len(big_tickers)}개")
    return big_tickers


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
        name = stock.get_market_ticker_name(ticker)
        # pykrx 버전에 따라 DataFrame/Series로 반환될 수 있으므로 str 변환
        if hasattr(name, "iloc"):
            name = name.iloc[0] if len(name) > 0 else ticker
        return str(name).strip() or ticker
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


# ── 미국 주식 (yfinance) ───────────────────────────────────────────────

def get_ohlcv_us(ticker: str, days: int = 390) -> pd.DataFrame | None:
    """미국 주식 OHLCV (yfinance). days ≈ 1년6개월 → MA240 계산 충분."""
    try:
        import yfinance as yf
        period = '2y' if days >= 365 else '1y'
        df = yf.download(ticker, period=period, interval='1d',
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        # MultiIndex 컬럼 평탄화 (yfinance 0.2.x 이상)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
        df = df[df['Volume'] > 0]
        return df
    except Exception as e:
        print(f'[US OHLCV] {ticker} 오류: {e}')
        return None


def get_us_candidates(days_back: int = 70,
                      force_refresh: bool = False) -> dict[str, str]:
    """
    최근 days_back일 내 10%+ 장대양봉이 있는 US 종목 반환.
    반환: {ticker: market}  예) {'NVDA': 'US_NASDAQ', 'JPM': 'US_SP500'}

    Phase 1: yfinance batch 3개월 데이터로 빠른 필터
    Phase 2: 캐시 (12시간 유효)
    """
    if not force_refresh and os.path.exists(US_CACHE_FILE):
        try:
            with open(US_CACHE_FILE, 'r') as f:
                cache = json.load(f)
            cached_at = datetime.fromisoformat(cache['timestamp'])
            if (datetime.now() - cached_at).total_seconds() < CACHE_HOURS * 3600:
                return cache['candidates']
        except Exception:
            pass

    from us_tickers import get_us_ticker_market, ALL_US_TICKERS
    ticker_market = get_us_ticker_market()
    candidates: dict[str, str] = {}

    try:
        import yfinance as yf
        print(f'[US후보] {len(ALL_US_TICKERS)}개 종목 batch 다운로드 중...')

        # 200개씩 배치 처리 (rate limit 방지)
        BATCH = 200
        all_data: dict[str, pd.DataFrame] = {}
        for i in range(0, len(ALL_US_TICKERS), BATCH):
            batch = ALL_US_TICKERS[i:i + BATCH]
            try:
                raw = yf.download(
                    batch,
                    period='3mo',
                    interval='1d',
                    progress=False,
                    auto_adjust=True,
                    group_by='ticker',
                )
                for t in batch:
                    try:
                        if isinstance(raw.columns, pd.MultiIndex):
                            df_t = raw[t].dropna(subset=['Close', 'Open'])
                        else:
                            df_t = raw.dropna(subset=['Close', 'Open'])
                        if not df_t.empty:
                            all_data[t] = df_t
                    except Exception:
                        pass
            except Exception as e:
                print(f'[US후보] batch {i//BATCH+1} 오류: {e}')
            time.sleep(1.0)  # rate limit 방지

        print(f'[US후보] 수집 완료: {len(all_data)}개, 장대양봉 필터 중...')

        cutoff_dt = datetime.now() - timedelta(days=days_back)
        for t, df_t in all_data.items():
            # 최근 days_back일 내 10%+ 장대양봉 있으면 후보 등록
            df_recent = df_t[df_t.index >= pd.Timestamp(cutoff_dt)]
            if df_recent.empty:
                continue
            o = df_recent['Open'].values
            c = df_recent['Close'].values
            mask = (o > 0) & ((c - o) / o * 100 >= 10.0)
            if mask.any():
                candidates[t] = ticker_market.get(t, 'US_SP500')

    except ImportError:
        print('[US후보] yfinance 미설치 → pip install yfinance')
    except Exception as e:
        print(f'[US후보] 오류: {e}')

    # 후보가 너무 적으면 전체 목록 반환 (장기 횡보장 등)
    if len(candidates) < 20:
        print(f'[US후보] 후보 부족({len(candidates)}개) → 전체 목록 사용')
        candidates = dict(get_us_ticker_market())

    with open(US_CACHE_FILE, 'w') as f:
        json.dump({'timestamp': datetime.now().isoformat(), 'candidates': candidates}, f)

    print(f'[US후보] 최종 후보: {len(candidates)}개')
    return candidates
