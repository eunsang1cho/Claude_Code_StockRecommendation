"""
cms_fetcher.py
CMS (Crash Market Score) v3.0 실시간 계산

백테스트 결과 (Codex/cms_backtest):
  - 급락 20일 선행: SPY 33.3%, QQQ 34.95%, KOSPI 34.76% 적중, 평균 선행 34~35일
  - 급등 20일 선행: SPY 16.27%, QQQ 15.81%, KOSPI 16.36% 적중, 평균 선행 35~37일
  - 하락 추세전환 50x200: SPY 33.3%, QQQ 33.3%, KOSPI 50.0% 적중
  - 상승 추세전환 50x200: SPY 33.3%, QQQ 25.0%, KOSPI 37.5% 적중

신호 기준:
  - CMS >= 65 상향 돌파 → 급락/하락추세전환 선행 경보
  - CMS <= 45 하향 돌파 → 급등/상승추세전환 선행 경보
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ── 컴포넌트 가중치 (PrivateCredit/China 제외 후 정규화) ─────────────
_BASE_WEIGHTS = {
    "Credit":     0.28,
    "Oil":        0.14,
    "Volatility": 0.12,
    "Liquidity":  0.14,
    "Bank":       0.12,
}
_TOTAL = sum(_BASE_WEIGHTS.values())   # 0.80
NORM_WEIGHTS = {k: v / _TOTAL for k, v in _BASE_WEIGHTS.items()}

# ── 레짐 임계값 ─────────────────────────────────────────────────────────
REGIME_BANDS = [
    (25,  "Risk-On",  "#4ade80", "🟢"),
    (45,  "Neutral",  "#a3e635", "🟡"),
    (65,  "Caution",  "#fbbf24", "🟠"),
    (85,  "Risk-Off", "#fb923c", "🔴"),
    (105, "Crisis",   "#f87171", "🚨"),
]
REGIME_DEFAULT = ("Collapse", "#ef4444", "💥")

# 레짐별 추천 포트폴리오 (backtest 기준)
REGIME_PORTFOLIOS = {
    "Risk-On": {
        "allocation": {"주식":80,"채권":10,"현금":10},
        "etfs": ["SPY","QQQ","SOXX","XLY"],
        "kr_picks": ["KODEX 코스닥150","삼성전자","SK하이닉스","TIGER 2차전지"],
    },
    "Neutral": {
        "allocation": {"주식":60,"채권":20,"현금":20},
        "etfs": ["VTI","SCHD","QUAL","RSP"],
        "kr_picks": ["KODEX 200","TIGER 우량주","현대차","POSCO홀딩스"],
    },
    "Caution": {
        "allocation": {"주식":45,"채권":25,"현금":20,"금":10},
        "etfs": ["SCHD","XLP","XLU","GLD"],
        "kr_picks": ["KODEX 고배당","KT&G","SK텔레콤","TIGER 리츠부동산인프라"],
    },
    "Risk-Off": {
        "allocation": {"주식":30,"채권":30,"현금":25,"금":15},
        "etfs": ["XLP","XLU","GLD","TLT"],
        "kr_picks": ["KODEX 국채10년","TIGER 단기통안채","KT","한국전력"],
    },
    "Crisis": {
        "allocation": {"현금":40,"채권":30,"금":20,"방어주":10},
        "etfs": ["GLD","TLT","IEF","SH"],
        "kr_picks": ["KODEX 골드선물(H)","KODEX 단기채권PLUS","TIGER 단기통안채","KODEX인버스"],
    },
    "Collapse": {
        "allocation": {"현금":60,"금":25,"국채":10,"헤지":5},
        "etfs": ["GLD","IEF","SH","SQQQ","SGOV"],
        "kr_picks": ["KODEX 골드선물(H)","KODEX 200선물인버스2X","KODEX 단기채권PLUS","CMA·MMF"],
    },
}


def _get_regime(score: float) -> tuple[str, str, str]:
    for threshold, name, color, icon in REGIME_BANDS:
        if score < threshold:
            return name, color, icon
    return REGIME_DEFAULT


def _fred(series_id: str, api_key: str, days: int = 50) -> pd.Series:
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    r = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={"series_id": series_id, "api_key": api_key,
                "file_type": "json", "observation_start": start},
        timeout=15,
    )
    r.raise_for_status()
    obs = r.json().get("observations", [])
    data = {}
    for o in obs:
        try:
            data[o["date"]] = float(o["value"])
        except (ValueError, KeyError):
            pass
    s = pd.Series(data, dtype=float)
    s.index = pd.to_datetime(s.index)
    return s.sort_index().dropna()


def _yf(symbol: str, days: int = 50) -> pd.Series:
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    df = yf.download(symbol, start=start, auto_adjust=True,
                     progress=False, multi_level_index=False)
    if df.empty:
        return pd.Series(dtype=float)
    s = df["Close"]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    s.index = pd.to_datetime(s.index)
    return s.sort_index().dropna()


def _clip(v: float, lo: float, hi: float) -> float:
    if pd.isna(v):
        return 0.0
    return max(lo, min(hi, float(v)))


def fetch_cms(fred_api_key: str, alpha_vantage_api_key: str | None = None) -> dict:
    """
    CMS 스코어 실시간 계산.
    반환: {cms_score, cms_base, trigger_bonus, regime, regime_color, regime_icon,
           components, raw, signals, portfolio, errors, updated_at}
    """
    days = 50
    errors: dict[str, str] = {}
    series: dict[str, pd.Series] = {}

    # ── FRED 시리즈 ─────────────────────────────────────────────────────
    fred_map = {
        "HY":     "BAMLH0A0HYM2",   # 하이일드 스프레드 (Credit)
        "UST2Y":  "DGS2",            # 미국 2년 국채 (Liquidity)
        "UST10Y": "DGS10",           # 미국 10년 국채 (Liquidity)
        "DXY":    "DTWEXBGS",        # 달러 광의 지수 (Liquidity)
        "BRENT":  "DCOILBRENTEU",    # 브렌트 유가 (Oil)
    }
    for name, sid in fred_map.items():
        try:
            series[name] = _fred(sid, fred_api_key, days)
        except Exception as e:
            errors[name] = str(e)

    # ── yfinance 시리즈 ──────────────────────────────────────────────────
    yf_map = {
        "VIX": "^VIX",   # CBOE 변동성 (Volatility)
        "KRE": "KRE",    # 지역은행 ETF (Bank)
        "XLF": "XLF",    # 금융섹터 ETF (Bank)
    }
    for name, sym in yf_map.items():
        try:
            series[name] = _yf(sym, days)
        except Exception as e:
            errors[name] = str(e)

    # ── DataFrame 합치기 + 전진 채우기 ──────────────────────────────────
    if not series:
        raise RuntimeError(f"CMS 데이터 수집 실패: {errors}")
    df = pd.DataFrame(series).ffill()

    # ── 최신 값 + 이동평균 ───────────────────────────────────────────────
    def last(col: str) -> float:
        if col not in df or df[col].dropna().empty:
            return float("nan")
        return float(df[col].dropna().iloc[-1])

    def rolling_mean(col: str, window: int) -> float:
        if col not in df:
            return float("nan")
        return float(df[col].rolling(window, min_periods=5).mean().dropna().iloc[-1]) if not df[col].dropna().empty else float("nan")

    def pct_chg(col: str, n: int) -> float:
        if col not in df:
            return float("nan")
        s = df[col].dropna()
        return float(s.pct_change(n).iloc[-1]) if len(s) > n else float("nan")

    HY        = last("HY")
    HY_20avg  = rolling_mean("HY", 20)
    BRENT     = last("BRENT")
    BRENT_5d  = pct_chg("BRENT", 5)
    VIX       = last("VIX")
    VIX_10avg = rolling_mean("VIX", 10)
    UST10Y    = last("UST10Y")
    UST2Y     = last("UST2Y")
    DXY       = last("DXY")
    KRE_20d   = pct_chg("KRE", 20)
    XLF_20d   = pct_chg("XLF", 20)

    # ── 컴포넌트 계산 ────────────────────────────────────────────────────

    # Credit (HY 스프레드): 기준값 4.5%, 최대 3x 위험
    hy_level = _clip(HY / 4.5, 0, 3.0)
    hy_mom   = _clip((HY - HY_20avg) / 1.0, -1.0, 1.0) if not np.isnan(HY_20avg) else 0.0
    credit_c = 0.75 * hy_level + 0.25 * max(0.0, hy_mom)

    # Oil (BRENT): 기준 $75, 이격도 + 5일 충격
    oil_level = _clip(abs(BRENT - 75.0) / 25.0, 0, 2.0) if not np.isnan(BRENT) else 0.0
    oil_shock = _clip(abs(BRENT_5d) / 0.15, 0, 2.0)      if not np.isnan(BRENT_5d) else 0.0
    oil_c     = 0.60 * oil_level + 0.40 * oil_shock

    # Volatility (VIX): 기준 20, 10일 MA 대비 충격
    vix_level = _clip(VIX / 20.0, 0, 3.0)                       if not np.isnan(VIX) else 0.0
    vix_shock = _clip((VIX - VIX_10avg) / 10.0, 0, 2.0)        if not np.isnan(VIX_10avg) else 0.0
    vola_c    = 0.70 * vix_level + 0.30 * vix_shock

    # Liquidity (DXY + 금리차 스트레스)
    dxy_level    = _clip(DXY / 105.0, 0, 2.0) if not np.isnan(DXY) else 0.0
    yield_stress = _clip(abs(UST10Y - UST2Y) / 1.5, 0, 2.0) if not (np.isnan(UST10Y) or np.isnan(UST2Y)) else 0.0
    liq_c        = 0.60 * dxy_level + 0.40 * yield_stress

    # Bank (지역은행/금융 20일 수익률)
    kre_drop = _clip(abs(KRE_20d) / 0.20, 0, 2.0) if not np.isnan(KRE_20d) else 0.0
    xlf_drop = _clip(abs(XLF_20d) / 0.15, 0, 2.0) if not np.isnan(XLF_20d) else 0.0
    bank_c   = 0.70 * kre_drop + 0.30 * xlf_drop

    comp_raw = {"Credit": credit_c, "Oil": oil_c, "Volatility": vola_c,
                "Liquidity": liq_c, "Bank": bank_c}

    cms_base = sum(100.0 * NORM_WEIGHTS[k] * v for k, v in comp_raw.items())

    # ── 트리거 보너스 ────────────────────────────────────────────────────
    bonus = 0.0
    bonuses: list[str] = []
    if not np.isnan(HY) and HY > 5.0:
        bonus += 10.0; bonuses.append(f"HY>5% (+10)")
    if not np.isnan(HY_20avg) and not np.isnan(HY) and (HY - HY_20avg) > 1.0:
        bonus += 10.0; bonuses.append(f"HY급등 (+10)")
    if not np.isnan(BRENT) and BRENT > 100.0:
        bonus += 8.0; bonuses.append(f"BRENT>$100 (+8)")
    if not np.isnan(BRENT_5d) and abs(BRENT_5d) > 0.15:
        bonus += 8.0; bonuses.append(f"BRENT충격 (+8)")
    if not np.isnan(VIX) and VIX > 35.0:
        bonus += 10.0; bonuses.append(f"VIX>35 (+10)")
    if not np.isnan(KRE_20d) and KRE_20d < -0.20:
        bonus += 10.0; bonuses.append(f"KRE급락 (+10)")
    if not np.isnan(HY) and not np.isnan(VIX) and HY > 4.5 and VIX > 30.0:
        bonus += 12.0; bonuses.append(f"HY+VIX동시 (+12)")
    if not np.isnan(HY) and not np.isnan(BRENT) and HY > 4.5 and BRENT > 95.0:
        bonus += 10.0; bonuses.append(f"HY+유가동시 (+10)")

    cms_score = round(cms_base + bonus, 1)
    regime, regime_color, regime_icon = _get_regime(cms_score)

    # ── CMS 시그널 판단 ──────────────────────────────────────────────────
    signals: list[str] = []
    if cms_score >= 65:
        signals.append("⚠️ 급락/하락추세전환 선행 경보 (CMS≥65)")
    if cms_score <= 45:
        signals.append("📈 급등/상승추세전환 가능성 (CMS≤45)")

    return {
        "cms_score":     cms_score,
        "cms_base":      round(cms_base, 1),
        "trigger_bonus": round(bonus, 1),
        "bonuses":       bonuses,
        "regime":        regime,
        "regime_color":  regime_color,
        "regime_icon":   regime_icon,
        "signals":       signals,
        "portfolio":     REGIME_PORTFOLIOS.get(regime, {}),
        "components": {
            "Credit": {
                "name": "신용 스트레스 (HY 스프레드)",
                "value": round(credit_c, 3),
                "weight_pct": round(NORM_WEIGHTS["Credit"] * 100),
                "contribution": round(100 * NORM_WEIGHTS["Credit"] * credit_c, 1),
                "raw": {"HY": round(HY, 2) if not np.isnan(HY) else None,
                        "HY_20avg": round(HY_20avg, 2) if not np.isnan(HY_20avg) else None},
            },
            "Oil": {
                "name": "유가 충격 (BRENT)",
                "value": round(oil_c, 3),
                "weight_pct": round(NORM_WEIGHTS["Oil"] * 100),
                "contribution": round(100 * NORM_WEIGHTS["Oil"] * oil_c, 1),
                "raw": {"BRENT": round(BRENT, 1) if not np.isnan(BRENT) else None,
                        "BRENT_5d_ret": round(BRENT_5d * 100, 1) if not np.isnan(BRENT_5d) else None},
            },
            "Volatility": {
                "name": "변동성 (VIX)",
                "value": round(vola_c, 3),
                "weight_pct": round(NORM_WEIGHTS["Volatility"] * 100),
                "contribution": round(100 * NORM_WEIGHTS["Volatility"] * vola_c, 1),
                "raw": {"VIX": round(VIX, 1) if not np.isnan(VIX) else None,
                        "VIX_10avg": round(VIX_10avg, 1) if not np.isnan(VIX_10avg) else None},
            },
            "Liquidity": {
                "name": "유동성 스트레스 (DXY + 금리차)",
                "value": round(liq_c, 3),
                "weight_pct": round(NORM_WEIGHTS["Liquidity"] * 100),
                "contribution": round(100 * NORM_WEIGHTS["Liquidity"] * liq_c, 1),
                "raw": {"DXY": round(DXY, 1) if not np.isnan(DXY) else None,
                        "UST10Y": round(UST10Y, 2) if not np.isnan(UST10Y) else None,
                        "UST2Y": round(UST2Y, 2) if not np.isnan(UST2Y) else None,
                        "yield_spread": round(UST10Y - UST2Y, 2) if not (np.isnan(UST10Y) or np.isnan(UST2Y)) else None},
            },
            "Bank": {
                "name": "은행 스트레스 (KRE/XLF 20일 수익률)",
                "value": round(bank_c, 3),
                "weight_pct": round(NORM_WEIGHTS["Bank"] * 100),
                "contribution": round(100 * NORM_WEIGHTS["Bank"] * bank_c, 1),
                "raw": {"KRE_20d_pct": round(KRE_20d * 100, 1) if not np.isnan(KRE_20d) else None,
                        "XLF_20d_pct": round(XLF_20d * 100, 1) if not np.isnan(XLF_20d) else None},
            },
        },
        "errors":     errors,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
