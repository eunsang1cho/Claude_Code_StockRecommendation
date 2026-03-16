"""
scanner.py
골삼이 / 골든샘플 / 레드삼각 패턴 탐지 + 보조지표 강화 Plus 버전

추가 조건:
- 현재가 1,000원 이상
- 현재가가 240일 이동평균선 위에 위치
- 240일선이 우상향 중
"""

import time

import pandas as pd

from data_fetcher import get_ohlcv, get_stock_name

# MA240을 위해 최소 250 거래일 (≈ 390 달력일) 필요
_OHLCV_DAYS = 390
_MIN_ROWS = 250  # 최소 데이터 수


# ── 공통 지표 계산 ────────────────────────────────────────────────────

def _indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()
    df["MA240"] = df["Close"].rolling(240).mean()
    df["VMA20"] = df["Volume"].rolling(20).mean()
    df["pct"] = df["Close"].pct_change()  # 전일 대비 등락률
    return df


def _esc(text: str) -> str:
    """MarkdownV2 특수문자 이스케이프"""
    special = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


# ── 공통 기본 조건 (MA240 + 가격 필터) ───────────────────────────────

def _base_ok(df: pd.DataFrame, price: float, ma240: float) -> bool:
    """
    공통 사전 필터:
    1. 현재가 1,000원 이상
    2. 현재가 > 240MA (240일선 위에 있어야)
    3. 240MA 우상향 (현재 > 20일 전)
    """
    if price < 1000:
        return False
    if pd.isna(ma240) or ma240 == 0:
        return False
    if price <= ma240:
        return False
    # 240MA 우상향: 현재 vs 20거래일 전
    if len(df) < 260:
        return False
    ma240_20d = df["MA240"].iloc[-20]
    if pd.isna(ma240_20d) or ma240 <= ma240_20d:
        return False
    return True


# ── 골삼이 ────────────────────────────────────────────────────────────

def detect_golsami(df: pd.DataFrame, ticker: str, cfg: dict) -> dict | None:
    """
    골삼이: 대양봉(big_pct%, 거래량 20MA의 vol_mult배+) 후
    현재가가 대양봉 시가 ±price_tol% + 20MA ±ma_tol% 구간에서 지지
    """
    if df is None or len(df) < _MIN_ROWS:
        return None

    df = _indicators(df)
    cur = df.iloc[-1]
    price, ma240 = cur["Close"], cur["MA240"]

    if not _base_ok(df, price, ma240):
        return None

    ma20 = cur["MA20"]
    if pd.isna(ma20) or ma20 == 0:
        return None

    window   = int(cfg["window"])
    big_pct  = float(cfg["big_pct"])
    vol_mult = float(cfg["vol_mult"])
    price_tol = float(cfg["price_tol"])
    ma_tol   = float(cfg["ma_tol"])
    vol_dec  = float(cfg["vol_dec"])

    # 최근 window일 내 대양봉
    win = df.tail(window)
    big = win[
        (win["pct"] >= big_pct) &
        (win["Volume"] > win["VMA20"] * vol_mult)
    ]
    if big.empty:
        return None

    bc = big.iloc[-1]
    bc_idx = big.index[-1]
    after = df[df.index > bc_idx]

    if len(after) < 3 or bc["Open"] == 0:
        return None

    near_open     = abs(price - bc["Open"]) / bc["Open"] < price_tol
    near_ma20     = abs(price - ma20) / ma20 < ma_tol
    ma20_rising   = df["MA20"].iloc[-1] > df["MA20"].iloc[-6]
    vol_decreasing = after["Volume"].mean() < bc["Volume"] * vol_dec

    if not (near_open and near_ma20 and ma20_rising and vol_decreasing):
        return None

    conf = int(cfg["conf_base"])
    open_dist = abs(price - bc["Open"]) / bc["Open"]
    if open_dist < 0.02:
        conf += int(cfg["conf_near2"])
    elif open_dist < 0.035:
        conf += int(cfg["conf_near35"])
    if bc["pct"] >= 0.29:
        conf += int(cfg["conf_big29"])
    ma20_slope = (df["MA20"].iloc[-1] - df["MA20"].iloc[-5]) / df["MA20"].iloc[-5]
    if ma20_slope > 0.02:
        conf += int(cfg["conf_slope"])

    name = get_stock_name(ticker)
    return {
        "ticker": ticker,
        "name": name,
        "pattern": "골삼이",
        "bc_date": bc_idx.strftime("%m/%d"),
        "bc_pct": f"+{bc['pct'] * 100:.1f}%",
        "bc_open": int(bc["Open"]),
        "current": int(price),
        "ma20": int(ma20),
        "ma240": int(ma240),
        "entry": (int(bc["Open"] * (1 - price_tol)), int(bc["Open"] * (1 + price_tol))),
        "stop": int(ma20 * 0.95),
        "target": int(bc["High"]),
        "week52_high": int(df["High"].tail(252).max()),
        "week52_low": int(df["Low"].tail(252).min()),
        "conf": min(conf, 97),
    }


# ── 골든샘플 ──────────────────────────────────────────────────────────

def detect_golden_sample(df: pd.DataFrame, ticker: str, cfg: dict) -> dict | None:
    """
    골든샘플: 대양봉 이후 거래량↓ + 주가 유지/소폭 상승
    매도세 고갈 신호
    """
    if df is None or len(df) < _MIN_ROWS:
        return None

    df = _indicators(df)
    cur = df.iloc[-1]
    price, ma240 = cur["Close"], cur["MA240"]

    if not _base_ok(df, price, ma240):
        return None

    ma20 = cur["MA20"]
    if pd.isna(ma20) or ma20 == 0:
        return None

    window      = int(cfg["window"])
    big_pct     = float(cfg["big_pct"])
    vol_mult    = float(cfg["vol_mult"])
    vol_dried   = float(cfg["vol_dried"])
    price_hold  = float(cfg["price_hold"])
    min_after   = int(cfg["min_after"])

    # 최근 window일 내 대양봉
    win = df.tail(window)
    big = win[
        (win["pct"] >= big_pct) &
        (win["Volume"] > win["VMA20"] * vol_mult)
    ]
    if big.empty:
        return None

    bc = big.iloc[-1]
    bc_idx = big.index[-1]
    after = df[df.index > bc_idx]

    if len(after) < min_after:
        return None

    avg_vol       = after["Volume"].mean()
    vol_dried_ok  = avg_vol < bc["Volume"] * vol_dried
    price_holding = (after["Close"] >= bc["Close"] * price_hold).all()
    above_ma20    = price > ma20
    ma20_rising   = df["MA20"].iloc[-1] > df["MA20"].iloc[-6]

    if not (vol_dried_ok and price_holding and above_ma20 and ma20_rising):
        return None

    conf = int(cfg["conf_base"])
    if len(after) >= 10:
        conf += int(cfg["conf_days10"])
    if bc["pct"] >= 0.29:
        conf += int(cfg["conf_big29"])

    name = get_stock_name(ticker)
    return {
        "ticker": ticker,
        "name": name,
        "pattern": "골든샘플",
        "bc_date": bc_idx.strftime("%m/%d"),
        "bc_pct": f"+{bc['pct'] * 100:.1f}%",
        "current": int(price),
        "ma20": int(ma20),
        "ma240": int(ma240),
        "days_after": len(after),
        "vol_ratio": f"{avg_vol / bc['Volume'] * 100:.1f}%",
        "stop": int(ma20 * 0.95),
        "target": int(bc["High"]),
        "week52_high": int(df["High"].tail(252).max()),
        "week52_low": int(df["Low"].tail(252).min()),
        "conf": min(conf, 97),
    }


# ── 레드삼각 ──────────────────────────────────────────────────────────

def detect_red_triangle(df: pd.DataFrame, ticker: str, cfg: dict) -> dict | None:
    """
    레드삼각: 박스권 횡보 → 대양봉 돌파 → 60MA까지 조정 → 반등
    """
    if df is None or len(df) < _MIN_ROWS:
        return None

    df = _indicators(df)
    cur = df.iloc[-1]
    price, ma240 = cur["Close"], cur["MA240"]

    if not _base_ok(df, price, ma240):
        return None

    ma60 = cur["MA60"]
    if pd.isna(ma60) or ma60 == 0:
        return None

    box_start   = int(cfg["box_start"])
    box_end     = int(cfg["box_end"])
    box_spread  = float(cfg["box_spread"])
    break_start = int(cfg["break_start"])
    break_end   = int(cfg["break_end"])
    min_big     = int(cfg["min_big"])
    ma_tol      = float(cfg["ma_tol"])
    box_top_pct = float(cfg["box_top_pct"])

    box = df.iloc[-box_start:-box_end]
    if len(box) < 20:
        return None

    box_mid = box["Close"].mean()
    if box_mid == 0:
        return None
    if (box["High"].max() - box["Low"].min()) / box_mid > box_spread:
        return None

    box_top = box["High"].max()

    # 돌파 구간 내 대양봉 min_big개 이상
    breakout = df.iloc[-break_start:-break_end]
    if (breakout["pct"] >= 0.15).sum() < min_big:
        return None

    near_ma60   = abs(price - ma60) / ma60 < ma_tol
    ma60_rising = df["MA60"].iloc[-1] > df["MA60"].iloc[-10]
    above_box   = price > box_top * box_top_pct

    if not (near_ma60 and ma60_rising and above_box):
        return None

    conf = int(cfg["conf_base"])
    if (breakout["pct"] >= 0.15).sum() >= 3:
        conf += int(cfg["conf_3candles"])
    if abs(price - ma60) / ma60 < 0.02:
        conf += int(cfg["conf_near_ma60"])

    name = get_stock_name(ticker)
    return {
        "ticker": ticker,
        "name": name,
        "pattern": "레드삼각",
        "box_top": int(box_top),
        "current": int(price),
        "ma60": int(ma60),
        "ma240": int(ma240),
        "entry": (int(ma60 * 0.98), int(box_top)),
        "stop": int(ma60 * 0.95),
        "target": int(df.iloc[-break_start:-break_end]["High"].max()),
        "week52_high": int(df["High"].tail(252).max()),
        "week52_low": int(df["Low"].tail(252).min()),
        "conf": min(conf, 97),
    }


# ── 골삼이(상승초입) ──────────────────────────────────────────────────

def detect_golsami_early(df: pd.DataFrame, ticker: str, cfg: dict) -> dict | None:
    """
    골삼이(상승초입): 장대양봉 발생 직후 (1~5거래일 이내) 상승 초입 포착

    조건:
    1. 240MA 횡보 또는 우상향 (최근 20거래일 변화율 > -0.5%)
    2. 최근 window일 내 장대양봉 (등락률 5%+ OR 몸통 60%+)
    3. 20MA 골든크로스 임박 (장대양봉 저가 ≈ 향후 3~15일 예상 20MA ±3%)
       + 현재가 20MA 위 또는 3% 이내
    4. 거래량 급증 (대양봉일 거래량 ≥ 20MA의 3배)
    """
    if df is None or len(df) < _MIN_ROWS:
        return None

    df = _indicators(df)
    cur = df.iloc[-1]
    price, ma240 = cur["Close"], cur["MA240"]
    ma20 = cur["MA20"]

    if price < 1000:
        return None
    if pd.isna(ma240) or ma240 == 0 or pd.isna(ma20) or ma20 == 0:
        return None

    window            = int(cfg["window"])
    big_pct           = float(cfg["big_pct"])
    body_ratio        = float(cfg["body_ratio"])
    vol_mult          = float(cfg["vol_mult"])
    ma20_cross_tol    = float(cfg["ma20_cross_tol"])
    proj_days_min     = int(cfg["proj_days_min"])
    proj_days_max     = int(cfg["proj_days_max"])
    ma240_flat_tol    = float(cfg["ma240_flat_tol"])
    price_surge_limit = float(cfg.get("price_surge_limit", 0.50))

    # 조건 1: 240MA 방향 — 하락(< -0.5%) 이면 제외
    if len(df) < 260:
        return None
    ma240_20d = df["MA240"].iloc[-20]
    if pd.isna(ma240_20d) or ma240_20d == 0:
        return None
    if (ma240 - ma240_20d) / ma240_20d < -ma240_flat_tol:
        return None

    # 조건 2: 최근 window일 내 장대양봉 탐색
    win = df.tail(window)
    big_candles = []
    for idx, row in win.iterrows():
        candle_range = row["High"] - row["Low"]
        body = row["Close"] - row["Open"]
        pct_ok  = row["pct"] >= big_pct
        body_ok = candle_range > 0 and body > 0 and (body / candle_range) >= body_ratio
        if pct_ok or body_ok:
            big_candles.append((idx, row))

    if not big_candles:
        return None

    # 가장 최근 장대양봉 사용
    bc_idx, bc = big_candles[-1]
    after = df[df.index > bc_idx]
    days_after = len(after)

    # 조건 4: 거래량 급증 (20MA 기준)
    if bc["VMA20"] == 0 or bc["Volume"] < bc["VMA20"] * vol_mult:
        return None

    # 조건 3-a: 현재가가 20MA 위 또는 3% 이내
    if price < ma20 * (1 - ma20_cross_tol):
        return None

    # 조건 3-b: 향후 N일 내 예상 20MA가 장대양봉 저가 ±3% 이내
    ma20_5d = df["MA20"].iloc[-5]
    if pd.isna(ma20_5d) or ma20_5d == 0:
        return None
    slope_per_day = (ma20 - ma20_5d) / 5

    bc_low = float(bc["Low"])
    ma20_cross_ok = False
    for days in range(proj_days_min, proj_days_max + 1):
        projected = ma20 + slope_per_day * days
        if projected > 0 and abs(projected - bc_low) / bc_low <= ma20_cross_tol:
            ma20_cross_ok = True
            break

    if not ma20_cross_ok:
        return None

    # 조건 5: 현재가가 20거래일 전 3일 평균가 대비 price_surge_limit 이내
    # (-22, -21, -20 인덱스 = 20거래일 전 기준 3일 평균)
    if len(df) < 23:
        return None
    ref_avg = df["Close"].iloc[-22:-19].mean()
    if pd.isna(ref_avg) or ref_avg == 0:
        return None
    if price > ref_avg * (1 + price_surge_limit):
        return None

    # 신뢰도 계산
    conf = int(cfg["conf_base"])

    candle_range = bc["High"] - bc["Low"]
    body = bc["Close"] - bc["Open"]
    if candle_range > 0 and body > 0 and (body / candle_range) >= body_ratio:
        conf += int(cfg["conf_body60"])

    if bc["VMA20"] > 0 and bc["Volume"] >= bc["VMA20"] * 5:
        conf += int(cfg["conf_vol5x"])

    if abs(price - ma20) / ma20 <= 0.015:
        conf += int(cfg["conf_near_ma20"])

    name = get_stock_name(ticker)
    return {
        "ticker": ticker,
        "name": name,
        "pattern": "골삼이(상승초입)",
        "bc_date": bc_idx.strftime("%m/%d"),
        "bc_pct": f"+{bc['pct'] * 100:.1f}%",
        "bc_low": int(bc_low),
        "current": int(price),
        "ma20": int(ma20),
        "ma240": int(ma240),
        "days_after": days_after,
        "entry": (int(ma20 * 0.99), int(price)),   # 20MA 부근 ~ 현재가
        "stop": int(bc_low * 0.97),                 # 장대양봉 저가 -3%
        "target": int(bc["High"] * 1.10),           # 장대양봉 고가 +10%
        "week52_high": int(df["High"].tail(252).max()),
        "week52_low": int(df["Low"].tail(252).min()),
        "conf": min(conf, 97),
    }


# ── MA압축지지 ────────────────────────────────────────────────────────

def detect_ma_compression(df: pd.DataFrame, ticker: str, cfg: dict) -> dict | None:
    """
    MA압축지지: 장대양봉 발생 후 3~30일 사이에서
    1. MA20이 장대 저가 ±3% 이내 접근 + 우상향 (slope ≥ 0.001)
    2. ATR/가격 ≤ 1.5% (변동성 압축)
    3. 현재 거래량 ≤ 장대 거래량 × 0.5 (거래량 축소)
    4. 5~30일 박스권 내 등락 ≤ 8%
    5. |MA20 - MA60| / 가격 ≤ 3% (MA 수렴)
    """
    if df is None or len(df) < _MIN_ROWS:
        return None

    df = _indicators(df)
    cur   = df.iloc[-1]
    price = float(cur["Close"])
    ma20  = float(cur["MA20"])
    ma60  = float(cur["MA60"])
    ma240 = float(cur["MA240"])

    if not _base_ok(df, price, ma240):
        return None
    if pd.isna(ma20) or ma20 == 0 or pd.isna(ma60) or ma60 == 0:
        return None

    # 파라미터
    lookback       = int(cfg.get("base_candle_lookback", 60))
    big_pct        = float(cfg.get("big_pct", 0.07))
    body_ratio     = float(cfg.get("body_ratio", 0.6))
    vol_mult       = float(cfg.get("vol_mult", 2.0))
    days_min       = int(cfg.get("ma20_approach_days_min", 3))
    days_max       = int(cfg.get("ma20_approach_days_max", 30))
    ma20_near_tol  = float(cfg.get("ma20_near_bottom_tol", 0.03))
    ma20_slope_min = float(cfg.get("ma20_slope_min", 0.001))
    atr_ratio_max  = float(cfg.get("atr_ratio_max", 0.015))
    vol_shrink     = float(cfg.get("vol_shrink_ratio", 0.5))
    box_days_min   = int(cfg.get("box_days_min", 5))
    box_days_max   = int(cfg.get("box_days_max", 30))
    box_range_pct  = float(cfg.get("box_range_pct", 0.08))
    ma_conv_tol    = float(cfg.get("ma20_ma60_conv_tol", 0.03))

    # Step 1: 최근 lookback일 내 장대양봉 탐색 (현재봉 제외, 최신부터 역탐)
    search = df.iloc[-(lookback + 1):-1]
    bc_row, bc_idx = None, None
    for i in range(len(search) - 1, -1, -1):
        row = search.iloc[i]
        candle_range = float(row["High"] - row["Low"])
        if candle_range == 0 or row["Open"] >= row["Close"]:
            continue
        body  = float(row["Close"] - row["Open"])
        vma20 = float(row["VMA20"])
        if vma20 == 0:
            continue
        if (float(row["pct"]) >= big_pct
                and body / candle_range >= body_ratio
                and float(row["Volume"]) >= vma20 * vol_mult):
            bc_row = row
            bc_idx = search.index[i]
            break

    if bc_row is None:
        return None

    # Step 2: 경과 일수 확인
    days_after = len(df[df.index > bc_idx])
    if not (days_min <= days_after <= days_max):
        return None

    bc_low = float(bc_row["Low"])
    bc_vol = float(bc_row["Volume"])

    # Step 2 (cont): MA20이 장대 저가 ma20_near_tol% 이내 접근
    if abs(ma20 - bc_low) / bc_low > ma20_near_tol:
        return None

    # Step 2 (cont): MA20 기울기 ≥ ma20_slope_min (우상향)
    prev_ma20 = float(df["MA20"].iloc[-2])
    if pd.isna(prev_ma20) or prev_ma20 == 0:
        return None
    if (ma20 - prev_ma20) / prev_ma20 < ma20_slope_min:
        return None

    # Step 3a: ATR(14) / 현재가 ≤ atr_ratio_max
    atr_window = df.iloc[-15:]
    tr_vals = [
        max(
            float(atr_window["High"].iloc[j]) - float(atr_window["Low"].iloc[j]),
            abs(float(atr_window["High"].iloc[j]) - float(atr_window["Close"].iloc[j - 1])),
            abs(float(atr_window["Low"].iloc[j])  - float(atr_window["Close"].iloc[j - 1])),
        )
        for j in range(1, len(atr_window))
    ]
    atr = sum(tr_vals) / len(tr_vals) if tr_vals else 0
    if price == 0 or atr / price > atr_ratio_max:
        return None

    # Step 3b: 현재 거래량 ≤ 장대 거래량 × vol_shrink
    if float(cur["Volume"]) > bc_vol * vol_shrink:
        return None

    # Step 3c: 박스권 (장대 이후 구간, 최대 box_days_max) ≤ box_range_pct
    box_size = min(days_after, box_days_max)
    if box_size < box_days_min:
        return None
    box_df   = df.iloc[-box_size:]
    box_high = float(box_df["High"].max())
    box_low  = float(box_df["Low"].min())
    if box_low == 0 or (box_high - box_low) / box_low > box_range_pct:
        return None

    # Step 3d: |MA20 - MA60| / 가격 ≤ ma_conv_tol
    if abs(ma20 - ma60) / price > ma_conv_tol:
        return None

    # 신뢰도 계산
    conf = int(cfg.get("conf_base", 72))
    ma20_to_low = abs(ma20 - bc_low) / bc_low
    if ma20_to_low < 0.01:
        conf += int(cfg.get("conf_ma20_close", 10))   # MA20이 장대 저가 1% 이내
    if float(bc_row["pct"]) >= 0.15:
        conf += int(cfg.get("conf_big15", 8))          # 장대 +15% 이상
    if abs(price - ma20) / ma20 <= 0.01:
        conf += int(cfg.get("conf_near_ma20", 7))      # 현재가 MA20 1% 이내

    name = get_stock_name(ticker)
    return {
        "ticker":     ticker,
        "name":       name,
        "pattern":    "MA압축지지",
        "bc_date":    bc_idx.strftime("%m/%d"),
        "bc_pct":     f"+{bc_row['pct'] * 100:.1f}%",
        "bc_low":     int(bc_low),
        "days_after": days_after,
        "current":    int(price),
        "ma20":       int(ma20),
        "ma60":       int(ma60),
        "ma240":      int(ma240),
        "entry":      (int(bc_low * 0.99), int(ma20 * 1.01)),
        "stop":       int(bc_low * 0.97),
        "target":     int(float(bc_row["High"]) * 1.10),
        "week52_high": int(df["High"].tail(252).max()),
        "week52_low":  int(df["Low"].tail(252).min()),
        "conf":       min(conf, 97),
    }


# ── 텐배거 v2 ─────────────────────────────────────────────────────────

def _calc_atr(window_df: pd.DataFrame, period: int) -> float | None:
    """ATR(period) 계산 헬퍼."""
    n = min(period + 1, len(window_df))
    if n < 2:
        return None
    vals = [
        max(
            float(window_df["High"].iloc[j]) - float(window_df["Low"].iloc[j]),
            abs(float(window_df["High"].iloc[j]) - float(window_df["Close"].iloc[j - 1])),
            abs(float(window_df["Low"].iloc[j])  - float(window_df["Close"].iloc[j - 1])),
        )
        for j in range(1, n)
    ]
    return sum(vals) / len(vals) if vals else None


def detect_ten_bagger(df: pd.DataFrame, ticker: str, cfg: dict) -> dict | None:
    """
    텐배거 v2: VCP (Volatility Contraction Pattern) 기반 주도주 초기 구간 탐지

    STEP 1 시장 리더 — MA정배열(20>50>200), RS 6개월/3개월 절대 수익률, 52주 고점 근처
    STEP 2 VCP 베이스 — 120일 저점 대비 35%+ 상승 후 베이스(10~35일·12% 이내),
                        ATR20 수축(<0.75배), 거래량 수축(vol10/vol50 < 0.70)
    STEP 3 위치 — 52주 고점 90%·60일 고점 92% 이상, MA20±4% 또는 MA50±6%
    STEP 4 돌파 트리거 — ①베이스 상단 돌파+거래량 1.8배, ②52주 신고가+1.5배
    """
    if df is None or len(df) < 210:
        return None

    df = _indicators(df)
    df["MA50"]  = df["Close"].rolling(50).mean()
    df["MA200"] = df["Close"].rolling(200).mean()
    df["VMA10"] = df["Volume"].rolling(10).mean()
    df["VMA50"] = df["Volume"].rolling(50).mean()

    cur   = df.iloc[-1]
    price = float(cur["Close"])
    vol   = float(cur["Volume"])
    ma20  = float(cur["MA20"])
    ma50  = float(cur["MA50"])
    ma200 = float(cur["MA200"])
    vma10 = float(cur["VMA10"])
    vma50 = float(cur["VMA50"])

    for v in (ma20, ma50, ma200, vma50):
        if pd.isna(v) or v == 0:
            return None
    if price < 1000:
        return None

    # ── 파라미터 ──────────────────────────────────────────────────────
    rs_6m_min          = float(cfg.get("rs_6m_min",          0.20))   # 6개월 수익률 최소
    rs_3m_min          = float(cfg.get("rs_3m_min",          0.10))   # 3개월 수익률 최소
    big_move_pct       = float(cfg.get("big_move_pct",       0.35))   # 120일 저점 대비 최소 상승폭
    big_move_lookback  = int(cfg.get("big_move_lookback",    120))    # 저점 탐색 기간
    base_days_min      = int(cfg.get("base_days_min",        10))     # 베이스 최소 기간
    base_days_max      = int(cfg.get("base_days_max",        35))     # 베이스 최대 기간
    base_range_pct     = float(cfg.get("base_range_pct",     0.12))   # 베이스 허용 등락폭
    atr_contract       = float(cfg.get("atr_contract",       0.75))   # ATR20 수축 비율
    vol_contract       = float(cfg.get("vol_contract",       0.70))   # vol10/vol50 수축 비율
    high52w_ratio      = float(cfg.get("high52w_ratio",      0.90))   # 52주 고점 대비 최소 위치
    high60d_ratio      = float(cfg.get("high60d_ratio",      0.92))   # 60일 고점 대비 최소 위치
    ma20_near_tol      = float(cfg.get("ma20_near_tol",      0.04))   # MA20 근접 허용 오차 ±4%
    ma50_near_tol      = float(cfg.get("ma50_near_tol",      0.06))   # MA50 근접 허용 오차 ±6%
    brkout_vol_mult    = float(cfg.get("brkout_vol_mult",    1.8))    # 베이스 돌파 거래량 배수
    brkout52w_vol_mult = float(cfg.get("brkout52w_vol_mult", 1.5))    # 52주 신고가 돌파 거래량 배수

    # ── STEP 1: 시장 리더 필터 ────────────────────────────────────────
    # 1a. MA 정배열: MA20 > MA50 > MA200
    if not (ma20 > ma50 > ma200):
        return None

    # 1b. price > MA50 > MA200 (정배열에 포함되나 명시)
    if price < ma50:
        return None

    # 1c. RS 프록시: 6개월(126거래일) / 3개월(63거래일) 절대 수익률
    if len(df) >= 127:
        p6m = float(df["Close"].iloc[-127])
        if p6m > 0 and (price / p6m - 1) < rs_6m_min:
            return None
    if len(df) >= 64:
        p3m = float(df["Close"].iloc[-64])
        if p3m > 0 and (price / p3m - 1) < rs_3m_min:
            return None

    # 1d. 52주 고점 / 현재가 < 1.1  ↔  현재가 ≥ 52주 고점 × 0.909
    high52w = float(df["High"].tail(252).max())
    if high52w > 0 and high52w / price > (1.0 / high52w_ratio):
        return None

    # ── STEP 2: VCP 베이스 패턴 ──────────────────────────────────────
    # 2a. 현재가 / 120일 저점 - 1 > big_move_pct (큰 상승 확인)
    lb = min(big_move_lookback, len(df) - 1)
    low_lb = float(df["Low"].iloc[-lb:].min())
    if low_lb == 0 or (price / low_lb - 1) < big_move_pct:
        return None

    # 2b. 베이스 찾기: 최대 35일부터 10일까지 줄이며 가장 넓은 타이트 박스 탐색
    #     오늘 캔들 제외(기준 저항선 확보) → base_high가 돌파 기준점
    base_high_val = 0.0
    base_low_val  = 0.0
    base_days_used = 0
    for days in range(base_days_max, base_days_min - 1, -1):
        if days + 1 >= len(df):
            continue
        b_df   = df.iloc[-(days + 1):-1]   # 오늘 제외한 이전 N일
        b_high = float(b_df["High"].max())
        b_low  = float(b_df["Low"].min())
        if b_high == 0:
            continue
        if (b_high - b_low) / b_high < base_range_pct:
            base_high_val  = b_high
            base_low_val   = b_low
            base_days_used = days
            break

    if base_days_used == 0:
        return None

    # 2c. ATR20 수축: ATR_최근20일 / ATR_이전20일 < atr_contract
    atr_now  = _calc_atr(df.iloc[-21:],   20)
    atr_prev = _calc_atr(df.iloc[-41:-20], 20)
    if atr_now and atr_prev and atr_prev > 0:
        if atr_now / atr_prev >= atr_contract:
            return None

    # 2d. 거래량 수축: vol10 / vol50 < vol_contract
    if vma50 > 0 and vma10 / vma50 >= vol_contract:
        return None

    # ── STEP 3: 위치 필터 ─────────────────────────────────────────────
    # 3a. 현재가 ≥ 52주 고점 × high52w_ratio
    if high52w > 0 and price < high52w * high52w_ratio:
        return None

    # 3b. 현재가 ≥ 60일 고점 × high60d_ratio
    high60d = float(df["High"].tail(60).max())
    if high60d > 0 and price < high60d * high60d_ratio:
        return None

    # 3c. MA20 ±4% 이내 OR MA50 ±6% 이내 (돌파 중이면 MA 위에 있으므로 패스 가능)
    near_ma20 = abs(price - ma20) / ma20 <= ma20_near_tol
    near_ma50 = abs(price - ma50) / ma50 <= ma50_near_tol
    is_breakout_pos = price > base_high_val   # 베이스 상단 이미 돌파 시 MA 조건 면제
    if not (near_ma20 or near_ma50 or is_breakout_pos):
        return None

    # ── STEP 4: 돌파 트리거 ───────────────────────────────────────────
    breakout_a = (price > base_high_val) and (vma50 > 0 and vol > vma50 * brkout_vol_mult)
    breakout_b = (price > high52w * 0.999) and (vma50 > 0 and vol > vma50 * brkout52w_vol_mult)
    is_breakout = breakout_a or breakout_b

    # ── 신뢰도 계산 ───────────────────────────────────────────────────
    conf = int(cfg.get("conf_base", 70))

    if is_breakout:
        conf += int(cfg.get("conf_breakout", 18))           # 거래량 동반 돌파
    if atr_now and atr_prev and atr_prev > 0 and atr_now / atr_prev < 0.60:
        conf += int(cfg.get("conf_atr_strong", 8))          # ATR 40%+ 수축
    if vma50 > 0 and vma10 / vma50 < 0.50:
        conf += int(cfg.get("conf_vol_strong", 7))          # 거래량 극도 위축(50% 이하)
    if near_ma20 and abs(price - ma20) / ma20 <= 0.02:
        conf += int(cfg.get("conf_near_ma20", 6))           # MA20 2% 이내 밀착
    if price / low_lb - 1 > 0.50:
        conf += int(cfg.get("conf_big50", 5))               # 저점 대비 50%+ 대상승

    name = get_stock_name(ticker)
    base_start_date = df.index[-(base_days_used + 1)].strftime("%m/%d") if base_days_used < len(df) else "—"
    return {
        "ticker":      ticker,
        "name":        name,
        "pattern":     "텐배거",
        "bc_date":     base_start_date,
        "bc_pct":      f"+{(price / low_lb - 1) * 100:.1f}%",
        "bc_high":     int(base_high_val),
        "days_after":  base_days_used,
        "current":     int(price),
        "ma20":        int(ma20),
        "ma60":        int(ma50),    # ma60 키 유지, 실질값은 MA50
        "ma240":       int(ma200),   # ma240 키 유지, 실질값은 MA200
        "entry":       (int(base_high_val * 1.001), int(base_high_val * 1.03)),
        "stop":        int(base_low_val * 0.97),
        "target":      int(base_high_val * 2.0),
        "week52_high": int(high52w),
        "week52_low":  int(df["Low"].tail(252).min()),
        "conf":        min(conf, 95),
        "breakout":    is_breakout,
    }


# ── 전체 스캔 ─────────────────────────────────────────────────────────

def scan_all(tickers: list[str]) -> list[dict]:
    """
    후보 종목 전체 스캔 (동기 — asyncio.to_thread로 호출).
    스캔 시작 시 DB에서 파라미터를 한 번만 로드해 사용.
    """
    from database import get_algo_config
    cfg_early        = get_algo_config("골삼이(상승초입)")
    cfg_golsami      = get_algo_config("골삼이")
    cfg_golden       = get_algo_config("골든샘플")
    cfg_red          = get_algo_config("레드삼각")
    cfg_ma_compress  = get_algo_config("MA압축지지")
    cfg_ten_bagger   = get_algo_config("텐배거")

    results: list[dict] = []

    for ticker in tickers:
        try:
            df = get_ohlcv(ticker, days=_OHLCV_DAYS)
            if df is None or len(df) < 30:
                continue

            result = (
                detect_golsami_early(df, ticker, cfg_early) or
                detect_golsami(df, ticker, cfg_golsami) or
                detect_golden_sample(df, ticker, cfg_golden) or
                detect_red_triangle(df, ticker, cfg_red) or
                detect_ma_compression(df, ticker, cfg_ma_compress) or
                detect_ten_bagger(df, ticker, cfg_ten_bagger)
            )
            if result:
                results.append(result)

            time.sleep(0.15)
        except Exception:
            continue

    return results


# ── 텔레그램 메시지 포맷 ─────────────────────────────────────────────

def format_result(r: dict) -> str:
    """
    한 줄 포맷:
    [아이콘패턴(신뢰도%)/종목명] : 현(₩X) 240(₩Y), 매수존(₩A~B), 손절가(₩C)
    """
    p = r["pattern"]
    name = r["name"]
    conf = r["conf"]
    current = r["current"]
    ma240 = r["ma240"]
    stop = r["stop"]

    emoji = {
        "골삼이":         "📊", "골든샘플":       "🔑",
        "레드삼각":       "📐", "골삼이(상승초입)": "🚀",
        "MA압축지지":     "📦", "텐배거":          "💎",
        # Plus 버전
        "골삼이+":              "📊✨",  "골든샘플+":            "🔑✨",
        "레드삼각+":            "📐✨",  "골삼이(상승초입)+":    "🚀✨",
        "MA압축지지+":          "📦✨",
        # Plus1 (데이터 기반)
        "골삼이+1":             "📊📈", "골든샘플+1":           "🔑📈",
        "레드삼각+1":           "📐📈", "골삼이(상승초입)+1":   "🚀📈",
        "MA압축지지+1":         "📦📈",
        # Plus2 (TA 지식 기반)
        "골삼이+2":             "📊🔬", "골든샘플+2":           "🔑🔬",
        "레드삼각+2":           "📐🔬", "골삼이(상승초입)+2":   "🚀🔬",
        "MA압축지지+2":         "📦🔬",
    }.get(p, "⚪")

    # 패턴별 매수존 (Plus 패턴도 기존과 동일한 entry 구조 사용)
    base_p = p.rstrip("+")
    if base_p in ("골삼이", "레드삼각", "골삼이(상승초입)", "MA압축지지", "텐배거"):
        entry_str = f"₩{r['entry'][0]:,}~{r['entry'][1]:,}"
    else:  # 골든샘플 계열
        entry_str = f"₩{r['ma20']:,} 부근"

    return (
        f"[{emoji}{p}({conf}%)/{name}] : "
        f"현(₩{current:,}) 240(₩{ma240:,}), "
        f"매수존({entry_str}), 손절가(₩{stop:,})"
    )


# ── 로컬 DB 조회 (Plus 전용) ──────────────────────────────────────────

def _read_local(ticker: str, n: int = 300) -> pd.DataFrame | None:
    """
    market_data.db에서 최근 n일 데이터 반환.
    data_store 미존재 or 데이터 부족 시 None 반환.
    """
    try:
        import data_store
        return data_store.get_ticker_history(ticker, n)
    except Exception:
        return None


# ── Plus 공통 지표 헬퍼 ───────────────────────────────────────────────

def _get_ind(df: pd.DataFrame, col: str, idx: int = -1):
    """지표 컬럼 값 안전 조회. NaN이면 None 반환."""
    if col not in df.columns:
        return None
    val = df[col].iloc[idx]
    if pd.isna(val):
        return None
    return float(val)


def _obv_rising(df: pd.DataFrame, window: int = 5) -> bool:
    """OBV가 최근 window일 동안 증가 추세인지 확인"""
    if "OBV" not in df.columns:
        return False
    obv = df["OBV"].dropna()
    if len(obv) < window:
        return False
    return float(obv.iloc[-1]) > float(obv.iloc[-window])


def _macd_golden_cross(df: pd.DataFrame, lookback: int = 3) -> bool:
    """최근 lookback일 이내에 MACD 골든크로스(DIF가 SIG를 하향→상향 돌파)가 있는지 확인"""
    if "MACD_DIF" not in df.columns or "MACD_SIG" not in df.columns:
        return False
    recent = df[["MACD_DIF", "MACD_SIG"]].iloc[-(lookback + 1):]
    for i in range(1, len(recent)):
        prev_dif = recent["MACD_DIF"].iloc[i - 1]
        prev_sig = recent["MACD_SIG"].iloc[i - 1]
        cur_dif  = recent["MACD_DIF"].iloc[i]
        cur_sig  = recent["MACD_SIG"].iloc[i]
        if any(pd.isna(v) for v in [prev_dif, prev_sig, cur_dif, cur_sig]):
            continue
        if float(prev_dif) < float(prev_sig) and float(cur_dif) >= float(cur_sig):
            return True
    return False


def _bb_width_shrinking(df: pd.DataFrame, days: int = 3) -> bool:
    """BB_WIDTH가 최근 days일 연속 감소하는지 확인"""
    if "BB_WIDTH" not in df.columns:
        return False
    bw = df["BB_WIDTH"].dropna()
    if len(bw) < days:
        return False
    vals = [float(bw.iloc[-i]) for i in range(1, days + 1)]  # [최신, 1일전, 2일전 ...]
    return all(vals[i] < vals[i + 1] for i in range(len(vals) - 1))


# ── Plus 탐지 함수 ────────────────────────────────────────────────────

def detect_golsami_plus(df: pd.DataFrame, ticker: str, cfg: dict) -> dict | None:
    """
    골삼이+: 기존 조건 + RSI14 < 65 (과열 아님) + MACD hist > 0 (상승 전환)
    """
    result = detect_golsami(df, ticker, cfg)
    if result is None:
        return None

    rsi  = _get_ind(df, "RSI14")
    hist = _get_ind(df, "MACD_HIST")

    if rsi is None or rsi >= 65:
        return None
    if hist is None or hist <= 0:
        return None

    result["pattern"] = "골삼이+"
    return result


def detect_golden_sample_plus(df: pd.DataFrame, ticker: str, cfg: dict) -> dict | None:
    """
    골든샘플+: 기존 조건 + RSI14 in [35, 70] + OBV 증가 추세
    """
    result = detect_golden_sample(df, ticker, cfg)
    if result is None:
        return None

    rsi = _get_ind(df, "RSI14")

    if rsi is None or not (35 <= rsi <= 70):
        return None
    if not _obv_rising(df, window=5):
        return None

    result["pattern"] = "골든샘플+"
    return result


def detect_red_triangle_plus(df: pd.DataFrame, ticker: str, cfg: dict) -> dict | None:
    """
    레드삼각+: 기존 조건 + 최근 3일 이내 MACD 골든크로스
    """
    result = detect_red_triangle(df, ticker, cfg)
    if result is None:
        return None

    if not _macd_golden_cross(df, lookback=3):
        return None

    result["pattern"] = "레드삼각+"
    return result


def detect_golsami_early_plus(df: pd.DataFrame, ticker: str, cfg: dict) -> dict | None:
    """
    골삼이(상승초입)+: 기존 조건 + RSI14 in [30, 60] + BB_width < 0.08 (압축)
    """
    result = detect_golsami_early(df, ticker, cfg)
    if result is None:
        return None

    rsi      = _get_ind(df, "RSI14")
    bb_width = _get_ind(df, "BB_WIDTH")

    if rsi is None or not (30 <= rsi <= 60):
        return None
    if bb_width is None or bb_width >= 0.08:
        return None

    result["pattern"] = "골삼이(상승초입)+"
    return result


def detect_ma_compression_plus(df: pd.DataFrame, ticker: str, cfg: dict) -> dict | None:
    """
    MA압축지지+: 기존 조건 + BB_width 3일 연속 감소 + Stoch_K < 50
    """
    result = detect_ma_compression(df, ticker, cfg)
    if result is None:
        return None

    stoch_k = _get_ind(df, "STOCH_K")

    if not _bb_width_shrinking(df, days=3):
        return None
    if stoch_k is None or stoch_k >= 50:
        return None

    result["pattern"] = "MA압축지지+"
    return result


# ── Plus 전체 스캔 ────────────────────────────────────────────────────

def scan_all_plus(tickers: list[str]) -> list[dict]:
    """
    로컬 DB 전종목 대상 Plus 알고리즘 실행 (API 호출 없음).
    tickers: data_store.get_all_tickers() 로 가져온 전체 종목 코드 목록
    """
    from database import get_algo_config
    cfg_early       = get_algo_config("골삼이(상승초입)")
    cfg_golsami     = get_algo_config("골삼이")
    cfg_golden      = get_algo_config("골든샘플")
    cfg_red         = get_algo_config("레드삼각")
    cfg_ma_compress = get_algo_config("MA압축지지")

    results: list[dict] = []

    for ticker in tickers:
        try:
            df = _read_local(ticker, n=300)
            if df is None or len(df) < 30:
                continue

            result = (
                detect_golsami_early_plus(df, ticker, cfg_early) or
                detect_golsami_plus(df, ticker, cfg_golsami) or
                detect_golden_sample_plus(df, ticker, cfg_golden) or
                detect_red_triangle_plus(df, ticker, cfg_red) or
                detect_ma_compression_plus(df, ticker, cfg_ma_compress)
            )
            if result:
                results.append(result)

        except Exception:
            continue

    return results


# ═══════════════════════════════════════════════════════════════════════════
# Plus1 — 데이터 기반: 수익 종목들의 지표 프로필에 매칭되는 경우만 통과
# ═══════════════════════════════════════════════════════════════════════════

_PLUS1_CACHE: dict = {}


def _build_winner_profile(pattern: str) -> dict:
    """
    stocks.db 수익 종목(+2% 이상)의 감지 시점 지표값 분포를 분석해 프로필 반환.
    데이터 부족 시 넓은 허용 범위(사실상 무필터) 반환.
    """
    default = {
        "rsi14_lo": 20, "rsi14_hi": 80,
        "macd_hist_pos_pct": 0.3,
        "stoch_k_lo": 5, "stoch_k_hi": 95,
        "count": 0, "winner_count": 0,
    }
    try:
        import sqlite3, os
        sdb = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stocks.db")
        mdb = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_data.db")
        if not os.path.exists(sdb) or not os.path.exists(mdb):
            return default

        conn = sqlite3.connect(sdb)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT sr.ticker,
                   sr.scanned_at    AS det_at,
                   sr.current_price AS entry,
                   ps.price         AS cur
            FROM scan_results sr
            INNER JOIN (
                SELECT ticker, MIN(scanned_at) AS min_at
                FROM   scan_results WHERE pattern = ?
                GROUP  BY ticker
            ) first ON sr.ticker = first.ticker
                    AND sr.scanned_at = first.min_at
            LEFT JOIN price_snapshots ps ON sr.ticker = ps.ticker
            WHERE sr.pattern = ? AND ps.price IS NOT NULL
        """, (pattern, pattern)).fetchall()
        conn.close()

        if not rows:
            return default

        winners = [r for r in rows
                   if r["cur"] and r["entry"] and r["cur"] > r["entry"] * 1.02]
        target  = winners if len(winners) >= 3 else list(rows)

        mconn = sqlite3.connect(mdb)
        mconn.row_factory = sqlite3.Row
        rsi_v, macd_v, stoch_v = [], [], []
        for r in target:
            det_date = r["det_at"][:10].replace("-", "")
            ind = mconn.execute(
                """SELECT rsi14, macd_hist, stoch_k
                   FROM stock_daily WHERE ticker=? AND date<=?
                   ORDER BY date DESC LIMIT 1""",
                (r["ticker"], det_date)
            ).fetchone()
            if not ind:
                continue
            if ind["rsi14"]     is not None: rsi_v.append(ind["rsi14"])
            if ind["macd_hist"] is not None: macd_v.append(ind["macd_hist"])
            if ind["stoch_k"]   is not None: stoch_v.append(ind["stoch_k"])
        mconn.close()

        if not rsi_v:
            return default

        rsi_v.sort()
        n = len(rsi_v)
        rsi_lo = max(15, rsi_v[max(0, n // 10)] - 5)
        rsi_hi = min(85, rsi_v[min(n - 1, n * 9 // 10)] + 5)
        macd_pos_pct = sum(1 for v in macd_v if v > 0) / max(len(macd_v), 1)

        profile: dict = {
            "rsi14_lo":          rsi_lo,
            "rsi14_hi":          rsi_hi,
            "macd_hist_pos_pct": macd_pos_pct,
            "count":             len(target),
            "winner_count":      len(winners),
        }
        if stoch_v:
            stoch_v.sort()
            m = len(stoch_v)
            profile["stoch_k_lo"] = max(5,  stoch_v[max(0, m // 10)] - 5)
            profile["stoch_k_hi"] = min(95, stoch_v[min(m - 1, m * 9 // 10)] + 5)
        else:
            profile["stoch_k_lo"], profile["stoch_k_hi"] = 5, 95

        return profile
    except Exception:
        return default


def _winner_profile(pattern: str) -> dict:
    """캐시된 winner profile (scan_all_plus1 세션 내 1회 계산)"""
    if pattern not in _PLUS1_CACHE:
        _PLUS1_CACHE[pattern] = _build_winner_profile(pattern)
    return _PLUS1_CACHE[pattern]


def _plus1_filter(df: pd.DataFrame, base_pattern: str) -> bool:
    """현재 지표가 winner profile 범위 안인지 확인. 지표 없으면 통과."""
    p = _winner_profile(base_pattern)
    rsi     = _get_ind(df, "RSI14")
    hist    = _get_ind(df, "MACD_HIST")
    stoch_k = _get_ind(df, "STOCH_K")
    if rsi is not None and not (p["rsi14_lo"] <= rsi <= p["rsi14_hi"]):
        return False
    if hist is not None and p["macd_hist_pos_pct"] >= 0.70 and hist <= 0:
        return False
    if stoch_k is not None and not (p["stoch_k_lo"] <= stoch_k <= p["stoch_k_hi"]):
        return False
    return True


def _plus1_label(base_pattern: str) -> str:
    p = _winner_profile(base_pattern)
    return (f"RSI[{p['rsi14_lo']:.0f}~{p['rsi14_hi']:.0f}] "
            f"{p['winner_count']}승/{p['count']}건")


def detect_golsami_plus1(df, ticker, cfg):
    r = detect_golsami(df, ticker, cfg)
    if r is None or not _plus1_filter(df, "골삼이"): return None
    r["pattern"] = "골삼이+1"; r["plus1_info"] = _plus1_label("골삼이"); return r

def detect_golden_sample_plus1(df, ticker, cfg):
    r = detect_golden_sample(df, ticker, cfg)
    if r is None or not _plus1_filter(df, "골든샘플"): return None
    r["pattern"] = "골든샘플+1"; r["plus1_info"] = _plus1_label("골든샘플"); return r

def detect_red_triangle_plus1(df, ticker, cfg):
    r = detect_red_triangle(df, ticker, cfg)
    if r is None or not _plus1_filter(df, "레드삼각"): return None
    r["pattern"] = "레드삼각+1"; r["plus1_info"] = _plus1_label("레드삼각"); return r

def detect_golsami_early_plus1(df, ticker, cfg):
    r = detect_golsami_early(df, ticker, cfg)
    if r is None or not _plus1_filter(df, "골삼이(상승초입)"): return None
    r["pattern"] = "골삼이(상승초입)+1"; r["plus1_info"] = _plus1_label("골삼이(상승초입)"); return r

def detect_ma_compression_plus1(df, ticker, cfg):
    r = detect_ma_compression(df, ticker, cfg)
    if r is None or not _plus1_filter(df, "MA압축지지"): return None
    r["pattern"] = "MA압축지지+1"; r["plus1_info"] = _plus1_label("MA압축지지"); return r


def scan_all_plus1(tickers: list[str]) -> list[dict]:
    """Plus1 스캔: 수익 종목 지표 프로필 기반 필터."""
    global _PLUS1_CACHE
    _PLUS1_CACHE.clear()

    from database import get_algo_config
    cfgs = {k: get_algo_config(k) for k in
            ["골삼이(상승초입)", "골삼이", "골든샘플", "레드삼각", "MA압축지지"]}

    results: list[dict] = []
    for ticker in tickers:
        try:
            df = _read_local(ticker, n=300)
            if df is None or len(df) < 30: continue
            result = (
                detect_golsami_early_plus1(df, ticker, cfgs["골삼이(상승초입)"]) or
                detect_golsami_plus1(df, ticker, cfgs["골삼이"]) or
                detect_golden_sample_plus1(df, ticker, cfgs["골든샘플"]) or
                detect_red_triangle_plus1(df, ticker, cfgs["레드삼각"]) or
                detect_ma_compression_plus1(df, ticker, cfgs["MA압축지지"])
            )
            if result: results.append(result)
        except Exception:
            continue
    return results


# ═══════════════════════════════════════════════════════════════════════════
# Plus2 — TA 지식 기반: 각 패턴의 약점을 보조지표로 보강
# ═══════════════════════════════════════════════════════════════════════════
#
# 패턴별 보강 전략:
#   골삼이        → 추세 없는 눌림 오신호: MACD 양전환 + Stoch 상승 신호
#   골든샘플      → 분산 구간 혼동:      OBV 10일 매집 + BB중심선 우상향
#   레드삼각      → 돌파 후 재실패:      ATR 확대 + Stoch 전환 + MACD>0
#   골삼이(초입)  → 급등 고점 혼동:      RSI 50 돌파 + MACD_DIF 상승
#   MA압축지지    → 압축 더 심화:        BB 극도수축(<0.06) + Stoch 회복

def _atr_expanding(df: pd.DataFrame, window: int = 5) -> bool:
    if "ATR14" not in df.columns: return False
    atr = df["ATR14"].dropna()
    if len(atr) < window * 2: return False
    return float(atr.iloc[-window:].mean()) > float(atr.iloc[-(window*2):-window].mean())

def _bb_middle_rising(df: pd.DataFrame, days: int = 5) -> bool:
    if "BB_MIDDLE" not in df.columns: return False
    mid = df["BB_MIDDLE"].dropna()
    if len(mid) < days: return False
    return float(mid.iloc[-1]) > float(mid.iloc[-days])

def _stoch_bullish(df: pd.DataFrame) -> bool:
    k = _get_ind(df, "STOCH_K"); d = _get_ind(df, "STOCH_D")
    if k is None: return False
    if d is not None and k > d and k > 20: return True
    return k > 50

def _macd_dif_rising(df: pd.DataFrame, days: int = 3) -> bool:
    if "MACD_DIF" not in df.columns: return False
    dif = df["MACD_DIF"].dropna()
    if len(dif) < days + 1: return False
    return float(dif.iloc[-1]) > float(dif.iloc[-(days+1)])

def _rsi_above50_crossover(df: pd.DataFrame) -> bool:
    if "RSI14" not in df.columns: return False
    rsi = df["RSI14"].dropna()
    if len(rsi) < 6: return False
    recent = rsi.iloc[-5:]
    for i in range(1, len(recent)):
        if float(recent.iloc[i-1]) < 50 <= float(recent.iloc[i]): return True
    return float(rsi.iloc[-1]) > 50 and float(rsi.iloc[-6]) < 50


def detect_golsami_plus2(df: pd.DataFrame, ticker: str, cfg: dict) -> dict | None:
    """골삼이+2: 기존 + MACD 양전환 + BB수축 또는 Stoch 상승 (추세 없는 오신호 제거)"""
    r = detect_golsami(df, ticker, cfg)
    if r is None: return None
    hist = _get_ind(df, "MACD_HIST"); stoch_k = _get_ind(df, "STOCH_K")
    if hist is None or hist <= 0: return None
    if not (_bb_width_shrinking(df, 2) or _stoch_bullish(df)): return None
    if stoch_k is not None and stoch_k > 80: return None
    r["pattern"] = "골삼이+2"; return r


def detect_golden_sample_plus2(df: pd.DataFrame, ticker: str, cfg: dict) -> dict | None:
    """골든샘플+2: 기존 + OBV 10일 매집 + BB중심선 우상향 + RSI>40 (분산 구간 제거)"""
    r = detect_golden_sample(df, ticker, cfg)
    if r is None: return None
    rsi = _get_ind(df, "RSI14")
    if not _obv_rising(df, window=10): return None
    if not _bb_middle_rising(df, days=5): return None
    if rsi is not None and rsi < 40: return None
    r["pattern"] = "골든샘플+2"; return r


def detect_red_triangle_plus2(df: pd.DataFrame, ticker: str, cfg: dict) -> dict | None:
    """레드삼각+2: 기존 + ATR 확대 + MACD>0 + Stoch 전환 (재돌파 실패 제거)"""
    r = detect_red_triangle(df, ticker, cfg)
    if r is None: return None
    hist = _get_ind(df, "MACD_HIST")
    if not _atr_expanding(df, window=5): return None
    if hist is None or hist <= 0: return None
    if not _stoch_bullish(df): return None
    r["pattern"] = "레드삼각+2"; return r


def detect_golsami_early_plus2(df: pd.DataFrame, ticker: str, cfg: dict) -> dict | None:
    """골삼이(상승초입)+2: 기존 + RSI 50 돌파 + MACD_DIF 상승 (급등 고점 혼동 제거)"""
    r = detect_golsami_early(df, ticker, cfg)
    if r is None: return None
    rsi = _get_ind(df, "RSI14")
    if not _rsi_above50_crossover(df):
        if rsi is None or rsi < 48: return None
    if not _macd_dif_rising(df, days=3): return None
    if rsi is not None and rsi > 70: return None
    r["pattern"] = "골삼이(상승초입)+2"; return r


def detect_ma_compression_plus2(df: pd.DataFrame, ticker: str, cfg: dict) -> dict | None:
    """MA압축지지+2: 기존 + BB 극도수축(<0.06) + Stoch 과매도 회복 (압축 심화 함정 제거)"""
    r = detect_ma_compression(df, ticker, cfg)
    if r is None: return None
    bb_width = _get_ind(df, "BB_WIDTH"); stoch_k = _get_ind(df, "STOCH_K")
    stoch_d  = _get_ind(df, "STOCH_D")
    if bb_width is None or bb_width >= 0.06: return None
    if stoch_k is not None:
        if stoch_k > 50: return None
        if stoch_d is not None and stoch_k < stoch_d: return None
    r["pattern"] = "MA압축지지+2"; return r


def scan_all_plus2(tickers: list[str]) -> list[dict]:
    """Plus2 스캔: TA 지식 기반 강화 필터."""
    from database import get_algo_config
    cfgs = {k: get_algo_config(k) for k in
            ["골삼이(상승초입)", "골삼이", "골든샘플", "레드삼각", "MA압축지지"]}

    results: list[dict] = []
    for ticker in tickers:
        try:
            df = _read_local(ticker, n=300)
            if df is None or len(df) < 30: continue
            result = (
                detect_golsami_early_plus2(df, ticker, cfgs["골삼이(상승초입)"]) or
                detect_golsami_plus2(df, ticker, cfgs["골삼이"]) or
                detect_golden_sample_plus2(df, ticker, cfgs["골든샘플"]) or
                detect_red_triangle_plus2(df, ticker, cfgs["레드삼각"]) or
                detect_ma_compression_plus2(df, ticker, cfgs["MA압축지지"])
            )
            if result: results.append(result)
        except Exception:
            continue
    return results
