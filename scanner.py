"""
scanner.py
골삼이 / 골든샘플 / 레드삼각 패턴 탐지

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


# ── 전체 스캔 ─────────────────────────────────────────────────────────

def scan_all(tickers: list[str]) -> list[dict]:
    """
    후보 종목 전체 스캔 (동기 — asyncio.to_thread로 호출).
    스캔 시작 시 DB에서 파라미터를 한 번만 로드해 사용.
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
            df = get_ohlcv(ticker, days=_OHLCV_DAYS)
            if df is None or len(df) < 30:
                continue

            result = (
                detect_golsami_early(df, ticker, cfg_early) or
                detect_golsami(df, ticker, cfg_golsami) or
                detect_golden_sample(df, ticker, cfg_golden) or
                detect_red_triangle(df, ticker, cfg_red) or
                detect_ma_compression(df, ticker, cfg_ma_compress)
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
        "골삼이": "📊", "골든샘플": "🔑", "레드삼각": "📐",
        "골삼이(상승초입)": "🚀", "MA압축지지": "📦",
    }.get(p, "⚪")

    # 패턴별 매수존
    if p in ("골삼이", "레드삼각", "골삼이(상승초입)", "MA압축지지"):
        entry_str = f"₩{r['entry'][0]:,}~{r['entry'][1]:,}"
    else:  # 골든샘플
        entry_str = f"₩{r['ma20']:,} 부근"

    return (
        f"[{emoji}{p}({conf}%)/{name}] : "
        f"현(₩{current:,}) 240(₩{ma240:,}), "
        f"매수존({entry_str}), 손절가(₩{stop:,})"
    )
