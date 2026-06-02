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
CACHE_FILE       = os.path.join(DIR, "candidates_cache.json")
US_CACHE_FILE    = os.path.join(DIR, "us_candidates_cache.json")
MOVER_POOL_FILE  = os.path.join(DIR, "kr_mover_pool.json")
SNAPSHOT_FILE    = os.path.join(DIR, "kr_market_snapshot.json")
CACHE_HOURS      = 12
SMALL_CAP_WON    = 5e12   # 시총 5조 (원)

# Naver 모바일 시세 API (KRX 배치 API가 'LOGOUT' 차단되어 대체)
_NAVER_HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}


def _num(s) -> float:
    """'1,234' / '15500000000' → float (실패 시 0)"""
    try:
        return float(str(s).replace(",", ""))
    except Exception:
        return 0.0


# ── Naver 전체 시장 스냅샷 (KRX 배치 API 대체) ────────────────────────

def get_kr_market_snapshot(force_refresh: bool = False) -> dict[str, dict]:
    """
    Naver 모바일 API로 KOSPI+KOSDAQ 전 종목 시세 스냅샷 수집.
    반환: {ticker: {'name','close','flux','marketcap','volume','market'}}
    1시간 캐시. KRX 'LOGOUT' 차단을 우회하는 핵심 데이터 소스.
    """
    # 1시간 캐시
    if not force_refresh and os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            cached_at = datetime.fromisoformat(cache["timestamp"])
            if (datetime.now() - cached_at).total_seconds() < 3600:
                return cache["data"]
        except Exception:
            pass

    import urllib.request

    snapshot: dict[str, dict] = {}
    for mk in ("KOSPI", "KOSDAQ"):
        page = 1
        total = None
        while True:
            url = (f"https://m.stock.naver.com/api/stocks/marketValue/{mk}"
                   f"?page={page}&pageSize=100")
            try:
                req = urllib.request.Request(url, headers=_NAVER_HDR)
                with urllib.request.urlopen(req, timeout=15) as r:
                    j = json.loads(r.read().decode("utf-8", errors="ignore"))
            except Exception as e:
                print(f"[Naver스냅샷] {mk} page{page} 오류: {e}")
                break
            stocks = j.get("stocks", [])
            if not stocks:
                break
            if total is None:
                total = j.get("totalCount", 0)
            for s in stocks:
                code = s.get("itemCode", "").strip()
                if not code or not code.isdigit():
                    continue
                snapshot[code] = {
                    "name":      s.get("stockName", code),
                    "close":     _num(s.get("closePriceRaw") or s.get("closePrice")),
                    "flux":      _num(s.get("fluctuationsRatio")),
                    "marketcap": _num(s.get("marketValueRaw")),
                    "volume":    _num(s.get("accumulatedTradingVolumeRaw")
                                      or s.get("accumulatedTradingVolume")),
                    "market":    mk,
                }
            if total and len(snapshot) and page * 100 >= total:
                break
            page += 1
            time.sleep(0.05)

    if snapshot:
        try:
            with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
                json.dump({"timestamp": datetime.now().isoformat(),
                           "data": snapshot}, f, ensure_ascii=False)
        except Exception:
            pass
    return snapshot


def _load_mover_pool() -> dict[str, str]:
    """장대양봉 발생 이력 풀 {ticker: 'YYYYMMDD'} 로드"""
    if os.path.exists(MOVER_POOL_FILE):
        try:
            with open(MOVER_POOL_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_mover_pool(pool: dict[str, str]) -> None:
    try:
        with open(MOVER_POOL_FILE, "w", encoding="utf-8") as f:
            json.dump(pool, f)
    except Exception:
        pass


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
    장대양봉 후보(시총 5조↓) 추출. Naver 시세 스냅샷 기반.

    KRX 배치 API('전종목시세')는 2026년부터 'LOGOUT' 차단되어 사용 불가.
    대체 전략:
      1) Naver 전 종목 스냅샷에서 '오늘 +min_pct% 이상' 급등 소형주 → 후보
      2) 롤링 풀(kr_mover_pool.json): 매 스캔마다 당일 급등주를 누적,
         days_back 일 이내 이력만 유지 → "최근 N일 내 장대양봉" 집합 구성
      3) DB scan_results 국장 이력 종목 추가 (과거 감지 종목 지속 모니터링)
    """
    snapshot = get_kr_market_snapshot()
    big_tickers: set[str] = set()

    if snapshot:
        # 1. 시총 5조↓ 소형주 전체에 대해 최근 days_back일 장대양봉 prefilter
        small = [
            t for t, info in snapshot.items()
            if 0 < info["marketcap"] < SMALL_CAP_WON
        ]
        prefiltered = _prefilter_kr_movers(small, days_back, min_pct)
        big_tickers |= prefiltered

        # 2. 롤링 풀에 누적 (prefilter 실패/누락 대비 연속성 확보)
        today_str = datetime.now().strftime("%Y%m%d")
        pool = _load_mover_pool()
        for t in prefiltered:
            pool[t] = today_str
        cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")
        pool = {t: d for t, d in pool.items() if d >= cutoff}
        _save_mover_pool(pool)
        big_tickers |= set(pool)

        print(f"   Naver 후보: prefilter {len(prefiltered)}개 "
              f"(소형주 {len(small)}개 중, ≥{min_pct}%/{days_back}일), "
              f"롤링풀 {len(pool)}개")
    else:
        print("⚠️  Naver 스냅샷 실패 → 로컬 DB 폴백 모드")

    # 3. DB 이력 종목 추가 (Naver 실패 시 폴백 + 정상 시 보강)
    big_tickers |= _fetch_candidates_local(days_back, min_pct)

    return list(big_tickers)


def _prefilter_kr_movers(tickers: list[str], days_back: int,
                         min_pct: float) -> set[str]:
    """
    개별 종목 OHLCV(90일)를 빠르게 조회해 최근 days_back일 내
    min_pct%↑ 장대양봉이 있었던 종목을 추출. (Naver fchart, ~0.03s/종목)
    """
    movers: set[str] = set()
    cutoff = pd.Timestamp(datetime.now() - timedelta(days=days_back))
    for t in tickers:
        try:
            df = get_ohlcv(t, days=90)
            if df is None or df.empty:
                continue
            recent = df[df.index >= cutoff]
            if recent.empty:
                continue
            o = recent["Open"].values
            c = recent["Close"].values
            mask = (o > 0) & ((c - o) / o * 100 >= min_pct)
            if mask.any():
                movers.add(t)
        except Exception:
            continue
    return movers


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

    # 2. scan_results 이력 종목 (과거 패턴 감지 국장 종목만)
    sdb = os.path.join(DIR, "stocks.db")
    if os.path.exists(sdb):
        try:
            conn = sqlite3.connect(sdb)
            rows = conn.execute(
                "SELECT DISTINCT ticker FROM scan_results WHERE market NOT LIKE 'US%'"
            ).fetchall()
            conn.close()
            big_tickers.update(r[0] for r in rows)
        except Exception:
            pass

    print(f"   로컬 폴백 후보: {len(big_tickers)}개")
    return big_tickers


def _get_small_cap_set(threshold_trillion: float = 5.0) -> set[str]:
    """시총 N조 이하 종목 코드 집합 (Naver 스냅샷 기반)"""
    threshold = threshold_trillion * 1e12
    snapshot = get_kr_market_snapshot()
    return {
        t for t, info in snapshot.items()
        if 0 < info["marketcap"] < threshold
    }


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


def get_current_price(ticker: str) -> float:
    """최신 종가 반환 (KR: pykrx 정수, US: yfinance 소수)"""
    # US 티커 판별 (숫자만으로 구성되지 않으면 US)
    if not ticker.isdigit():
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).fast_info
            price = info.get('lastPrice') or info.get('regularMarketPreviousClose') or 0
            return round(float(price), 2) if price else 0
        except Exception:
            return 0

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
    """단일 종목 시가총액 (원 단위, Naver 스냅샷 기반)"""
    snapshot = get_kr_market_snapshot()
    info = snapshot.get(ticker)
    if info and info["marketcap"] > 0:
        return int(info["marketcap"])
    return 0


def get_market_cap_us(ticker: str) -> int:
    """미국 주식 시가총액 (USD 단위). yfinance 사용."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        return int(info.get("marketCap") or 0)
    except Exception:
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
