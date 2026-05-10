"""
cms_backtest.py
CMS v3.0 5년 월별 백테스트

- 전월 말 CMS 레짐 → 당월 포트폴리오 적용 (look-ahead 없음)
- 포트 프록시: 주식→SPY, 채권/국채→BND, 금→GLD, 현금/방어/헤지→0.3%/month
- 벤치마크: KOSPI(^KS11), 다우(^DJI), 나스닥(^IXIC), S&P500(^GSPC)
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from cms_fetcher import NORM_WEIGHTS, REGIME_PORTFOLIOS, _get_regime, _clip

# ── 캐시 ──────────────────────────────────────────────────────────────
_cache: tuple[dict, float] | None = None
_CACHE_TTL = 6 * 3600   # 6시간

HEADERS = {"User-Agent": "Mozilla/5.0 StockBot/1.0"}


# ── 데이터 수집 ───────────────────────────────────────────────────────

def _fetch_fred_series(series_id: str, api_key: str, start: str) -> pd.Series:
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": series_id, "api_key": api_key,
                    "file_type": "json", "observation_start": start},
            timeout=20,
        )
        r.raise_for_status()
        data = {}
        for o in r.json().get("observations", []):
            try:
                data[o["date"]] = float(o["value"])
            except (ValueError, KeyError):
                pass
        s = pd.Series(data, dtype=float)
        s.index = pd.to_datetime(s.index)
        return s.sort_index().dropna()
    except Exception as e:
        print(f"[CMS백테스트/FRED] {series_id}: {e}")
        return pd.Series(dtype=float)


def _fetch_yf_series(symbol: str, start: str) -> pd.Series:
    try:
        df = yf.download(symbol, start=start, auto_adjust=True,
                         progress=False, multi_level_index=False)
        if df.empty:
            return pd.Series(dtype=float)
        s = df["Close"]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        s.index = pd.to_datetime(s.index)
        return s.sort_index().dropna()
    except Exception as e:
        print(f"[CMS백테스트/yf] {symbol}: {e}")
        return pd.Series(dtype=float)


def _fetch_all(fred_api_key: str, years: int = 5) -> pd.DataFrame:
    start = (datetime.now() - timedelta(days=years * 365 + 90)).strftime("%Y-%m-%d")
    print(f"[CMS백테스트] 데이터 수집 중 (start={start})…")

    fred_map = {
        "HY":     "BAMLH0A0HYM2",
        "UST2Y":  "DGS2",
        "UST10Y": "DGS10",
        "DXY":    "DTWEXBGS",
        "BRENT":  "DCOILBRENTEU",
    }
    yf_map = {
        "VIX":    "^VIX",
        "KRE":    "KRE",
        "XLF":    "XLF",
        # 포트폴리오 프록시
        "SPY":    "SPY",
        "BND":    "BND",
        "GLD":    "GLD",
        # 벤치마크
        "KOSPI":  "^KS11",
        "DOW":    "^DJI",
        "NASDAQ": "^IXIC",
        "SPX":    "^GSPC",
    }

    series: dict[str, pd.Series] = {}
    for name, sid in fred_map.items():
        series[name] = _fetch_fred_series(sid, fred_api_key, start)
        time.sleep(0.3)

    for name, sym in yf_map.items():
        series[name] = _fetch_yf_series(sym, start)
        time.sleep(0.2)

    present = [k for k, v in series.items() if not v.empty]
    print(f"[CMS백테스트] 수집 완료: {present}")

    df = pd.DataFrame({k: v for k, v in series.items() if not v.empty})
    df = df.sort_index().ffill()
    return df


# ── 벡터화 CMS 계산 ───────────────────────────────────────────────────

def _compute_cms_vectorized(df: pd.DataFrame) -> pd.Series:
    """전체 기간 CMS 스코어 벡터 계산 (cms_fetcher.py 동일 공식)"""

    def col(name: str) -> pd.Series:
        return df[name] if name in df else pd.Series(np.nan, index=df.index)

    HY       = col("HY")
    BRENT    = col("BRENT")
    VIX      = col("VIX")
    UST10Y   = col("UST10Y")
    UST2Y    = col("UST2Y")
    DXY      = col("DXY")
    KRE      = col("KRE")
    XLF      = col("XLF")

    HY_20avg  = HY.rolling(20, min_periods=5).mean()
    VIX_10avg = VIX.rolling(10, min_periods=5).mean()
    BRENT_5d  = BRENT.pct_change(5)
    KRE_20d   = KRE.pct_change(20)
    XLF_20d   = XLF.pct_change(20)

    # Credit
    hy_level = (HY / 4.5).clip(0, 3.0)
    hy_mom   = ((HY - HY_20avg) / 1.0).clip(-1.0, 1.0).clip(lower=0)
    credit_c = 0.75 * hy_level + 0.25 * hy_mom

    # Oil
    oil_level = ((BRENT - 75.0).abs() / 25.0).clip(0, 2.0)
    oil_shock = (BRENT_5d.abs() / 0.15).clip(0, 2.0)
    oil_c     = 0.60 * oil_level + 0.40 * oil_shock

    # Volatility
    vix_level = (VIX / 20.0).clip(0, 3.0)
    vix_shock = ((VIX - VIX_10avg) / 10.0).clip(0, 2.0)
    vola_c    = 0.70 * vix_level + 0.30 * vix_shock

    # Liquidity
    dxy_level    = (DXY / 105.0).clip(0, 2.0)
    yield_stress = ((UST10Y - UST2Y).abs() / 1.5).clip(0, 2.0)
    liq_c        = 0.60 * dxy_level + 0.40 * yield_stress

    # Bank
    kre_drop = (KRE_20d.abs() / 0.20).clip(0, 2.0)
    xlf_drop = (XLF_20d.abs() / 0.15).clip(0, 2.0)
    bank_c   = 0.70 * kre_drop + 0.30 * xlf_drop

    cms_base = (
        100.0 * NORM_WEIGHTS["Credit"]     * credit_c +
        100.0 * NORM_WEIGHTS["Oil"]        * oil_c    +
        100.0 * NORM_WEIGHTS["Volatility"] * vola_c   +
        100.0 * NORM_WEIGHTS["Liquidity"]  * liq_c    +
        100.0 * NORM_WEIGHTS["Bank"]       * bank_c
    )

    # 트리거 보너스 (벡터)
    bonus = pd.Series(0.0, index=df.index)
    bonus += (HY > 5.0).astype(float) * 10.0
    bonus += ((HY - HY_20avg) > 1.0).astype(float) * 10.0
    bonus += (BRENT > 100.0).astype(float) * 8.0
    bonus += (BRENT_5d.abs() > 0.15).astype(float) * 8.0
    bonus += (VIX > 35.0).astype(float) * 10.0
    bonus += (KRE_20d < -0.20).astype(float) * 10.0
    bonus += ((HY > 4.5) & (VIX > 30.0)).astype(float) * 12.0
    bonus += ((HY > 4.5) & (BRENT > 95.0)).astype(float) * 10.0

    return (cms_base + bonus).round(1)


# ── 월별 포트폴리오 시뮬레이션 ────────────────────────────────────────

def _simulate(df: pd.DataFrame, cms_daily: pd.Series) -> dict:
    # 월말 리샘플
    cms_monthly = cms_daily.resample("ME").last()

    # 자산별 월별 수익률
    asset_cols  = ["SPY", "BND", "GLD"]
    bench_cols  = ["KOSPI", "DOW", "NASDAQ", "SPX"]
    all_cols    = asset_cols + bench_cols

    monthly_px  = {}
    for c in all_cols:
        if c in df.columns:
            monthly_px[c] = df[c].resample("ME").last()

    months  = sorted(cms_monthly.index)
    port_v  = 100.0
    bench_v = {b: 100.0 for b in bench_cols}

    port_hist  = {}
    bench_hist = {b: {} for b in bench_cols}
    cms_hist   = {}
    regime_hist = {}

    def month_ret(col: str, month) -> float:
        if col not in monthly_px:
            return 0.0
        s = monthly_px[col]
        if month not in s.index:
            return 0.0
        idx = s.index.get_loc(month)
        if idx == 0:
            return 0.0
        prev_v = s.iloc[idx - 1]
        cur_v  = s.iloc[idx]
        if prev_v == 0 or np.isnan(prev_v) or np.isnan(cur_v):
            return 0.0
        return float((cur_v - prev_v) / prev_v)

    for i, month in enumerate(months):
        # CMS 기록
        cms_val = float(cms_monthly.get(month, np.nan))
        if not np.isnan(cms_val):
            ym = month.strftime("%Y-%m")
            cms_hist[ym] = cms_val
            regime_hist[ym] = _get_regime(cms_val)[0]

        if i == 0:
            ym = month.strftime("%Y-%m")
            port_hist[ym] = round(port_v, 2)
            for b in bench_cols:
                bench_hist[b][ym] = round(bench_v[b], 2)
            continue

        # 전월 CMS → 레짐 → 포지션
        prev_month = months[i - 1]
        prev_cms   = float(cms_monthly.get(prev_month, 50.0))
        if np.isnan(prev_cms):
            prev_cms = 50.0
        regime = _get_regime(prev_cms)[0]

        alloc_data = REGIME_PORTFOLIOS.get(regime, REGIME_PORTFOLIOS["Caution"])
        alloc      = alloc_data.get("allocation", {})
        total_w    = sum(alloc.values()) or 100.0

        # 포트폴리오 월 수익률
        port_ret = 0.0
        for asset_ko, w_pct in alloc.items():
            w = w_pct / total_w
            if asset_ko == "주식":
                port_ret += w * month_ret("SPY", month)
            elif asset_ko in ("채권", "국채"):
                port_ret += w * month_ret("BND", month)
            elif asset_ko == "금":
                port_ret += w * month_ret("GLD", month)
            else:   # 현금, 방어주, 헤지 등
                port_ret += w * 0.003   # ~0.3%/month

        port_v *= (1 + port_ret)
        ym = month.strftime("%Y-%m")
        port_hist[ym] = round(port_v, 2)

        # 벤치마크
        for b in bench_cols:
            r = month_ret(b, month)
            bench_v[b] *= (1 + r)
            bench_hist[b][ym] = round(bench_v[b], 2)

    # ── 요약 통계 ─────────────────────────────────────────────────────
    port_series = pd.Series(port_hist)
    total_return = round((port_v / 100 - 1) * 100, 1)

    bench_returns = {}
    for b in bench_cols:
        if bench_hist[b]:
            final = list(bench_hist[b].values())[-1]
            bench_returns[b] = round((final / 100 - 1) * 100, 1)

    # 최대 낙폭
    roll_max = port_series.cummax()
    dd       = (port_series - roll_max) / roll_max * 100
    max_dd   = round(float(dd.min()), 1)

    # 샤프 (연율화)
    monthly_r = port_series.pct_change().dropna()
    sharpe    = round(
        float(monthly_r.mean() / monthly_r.std() * (12 ** 0.5))
        if monthly_r.std() > 0 else 0.0, 2
    )

    # 레짐 분포
    from collections import Counter
    regime_counts = dict(Counter(regime_hist.values()))

    return {
        "portfolio":  port_hist,
        "benchmarks": bench_hist,
        "cms":        cms_hist,
        "regimes":    regime_hist,
        "summary": {
            "total_return":   total_return,
            "max_drawdown":   max_dd,
            "sharpe":         sharpe,
            "bench_returns":  bench_returns,
            "regime_months":  regime_counts,
            "months":         len(port_hist),
        },
    }


# ── 진입점 ─────────────────────────────────────────────────────────────

def run_backtest(fred_api_key: str, years: int = 5, force: bool = False) -> dict:
    """CMS 5년 백테스트 실행. 결과는 6시간 캐시."""
    global _cache
    if not force and _cache is not None:
        result, ts = _cache
        if time.time() - ts < _CACHE_TTL:
            print("[CMS백테스트] 캐시 반환")
            return result

    df         = _fetch_all(fred_api_key, years)
    cms_daily  = _compute_cms_vectorized(df)
    result     = _simulate(df, cms_daily)
    _cache     = (result, time.time())
    print(f"[CMS백테스트] 완료: {result['summary']}")
    return result
