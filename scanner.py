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

from data_fetcher import get_ohlcv, get_ohlcv_us, get_stock_name

# MA240을 위해 최소 250 거래일 (≈ 390 달력일) 필요
_OHLCV_DAYS = 390
_MIN_ROWS = 250  # 최소 데이터 수


# ── 공통 지표 계산 ────────────────────────────────────────────────────

def _indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["MA20"]  = df["Close"].rolling(20).mean()
    df["MA60"]  = df["Close"].rolling(60).mean()
    df["MA240"] = df["Close"].rolling(240).mean()
    df["VMA20"] = df["Volume"].rolling(20).mean()
    df["pct"]   = df["Close"].pct_change()

    # RSI 14
    delta = df["Close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    df["RSI14"] = 100 - (100 / (1 + gain / loss.replace(0, float("nan"))))

    # MACD histogram (EMA12 - EMA26) - EMA9_signal
    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    df["MACD_HIST"] = macd_line - macd_line.ewm(span=9, adjust=False).mean()

    return df


def _macd_ok(df: pd.DataFrame) -> bool:
    """MACD 모멘텀 확인: 히스토그램이 양수이거나 상승 전환 중이면 통과.

    눌림목·지지 패턴은 본질적으로 모멘텀이 식는 구간이라 'MACD>0' 단독 조건은
    충족 불가에 가깝다(실측: 모든 종목 탈락). 따라서 '양수 OR 직전 대비 상승'으로
    완화해 모멘텀 '반등 시작'을 포착한다. 데이터 없으면 통과.
    """
    if "MACD_HIST" not in df.columns:
        return True
    h = df["MACD_HIST"]
    v = h.iloc[-1]
    if pd.isna(v):
        return True
    if float(v) > 0:
        return True
    # 음수라도 직전 대비 상승(반등 시작)이면 통과
    if len(h) >= 2 and not pd.isna(h.iloc[-2]) and float(v) > float(h.iloc[-2]):
        return True
    return False


def _rsi_val(df: pd.DataFrame) -> float | None:
    """현재 RSI14 값. 없으면 None."""
    if "RSI14" not in df.columns:
        return None
    v = df["RSI14"].iloc[-1]
    return None if pd.isna(v) else float(v)


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

    # MACD 양전환 + RSI 과열 제외 (골삼이+의 84% win 조건)
    if not _macd_ok(df):
        return None
    rsi = _rsi_val(df)
    if rsi is not None and rsi >= 65:
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

    # MACD 양전환 확인
    if not _macd_ok(df):
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

    # MACD 양전환 확인 (39% win → 필터로 개선 목표)
    if not _macd_ok(df):
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

    # MACD 양전환 확인
    if not _macd_ok(df):
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


# ── 눌림목_매집 (NEW — 골삼이(상승초입) 대체) ─────────────────────────────

def detect_pullback_setup(df: pd.DataFrame, ticker: str, cfg: dict) -> dict | None:
    """
    눌림목_매집: MA 정배열 상승 구조에서 3~15% 눌림 후 MA20 지지 반등 포착.

    데이터 분석 결과:
    - 골삼이(상승초입)은 36% win / 40% loss (모든 conf 구간에서 랜덤 이하)
    - 이 패턴은 같은 아이디어를 구조적으로 재설계:
      MA정배열 확인 → 적절한 눌림폭 → 거래량 수축 → MA20 지지 반등

    조건:
    1. MA 정배열: MA20 > MA60 > MA240 (확인된 상승 구조)
    2. MA20이 MA240 대비 8% 이상 위 (충분한 추세 형성)
    3. 현재가 MA20 ±3% 이내 (지지선 근접)
    4. MA20 우상향 (최근 5일 기울기 > 0)
    5. 최근 5~15일 고점 대비 3~15% 눌림
    6. 눌림 구간 평균 거래량 < 20일 평균의 65% (매도세 약화)
    """
    if df is None or len(df) < _MIN_ROWS:
        return None

    df = _indicators(df)
    cur   = df.iloc[-1]
    price = float(cur["Close"])
    ma20  = float(cur["MA20"])
    ma60  = float(cur["MA60"])
    ma240 = float(cur["MA240"])

    if price < 1000:
        return None
    for v in (ma20, ma60, ma240):
        if pd.isna(v) or v == 0:
            return None

    # US 강화 파라미터 (cfg에 _us_ 키로 전달)
    ma_ratio_min  = float(cfg.get("_us_ma_ratio_min", 1.08))   # MA20/MA240 최소 비율
    rsi_min       = float(cfg.get("_us_rsi_min",      42))     # RSI 최솟값
    pullback_min  = float(cfg.get("_us_pullback_min", 0.03))   # 눌림폭 최솟값

    # 조건 1: MA 정배열
    if not (ma20 > ma60 > ma240):
        return None

    # 조건 2: MA20이 MA240보다 ma_ratio_min 이상 위
    if ma20 / ma240 < ma_ratio_min:
        return None

    # 조건 3: 현재가 MA20 ±3% 이내
    if abs(price - ma20) / ma20 > 0.03:
        return None

    # 조건 4: MA20 우상향
    if len(df) < 6:
        return None
    ma20_5d = float(df["MA20"].iloc[-6])
    if pd.isna(ma20_5d) or ma20_5d == 0 or ma20 <= ma20_5d:
        return None

    # 조건 5: 최근 5~15일 고점 찾기 → 눌림폭 pullback_min~15%
    peak_window = df.iloc[-(15 + 1):-1]
    if len(peak_window) < 5:
        return None
    peak_idx  = peak_window["High"].idxmax()
    peak_high = float(peak_window.loc[peak_idx, "High"])
    if peak_high == 0:
        return None
    pullback_pct = (peak_high - price) / peak_high
    if not (pullback_min <= pullback_pct <= 0.15):
        return None

    # 고점 이후 최소 2거래일 경과
    after_peak = df[df.index > peak_idx]
    if len(after_peak) < 2:
        return None

    # 조건 6: 눌림 구간 거래량 < 20일 평균 × 65%
    vma20 = float(cur["VMA20"]) if not pd.isna(cur["VMA20"]) else 0
    if vma20 > 0:
        avg_vol_after = float(after_peak["Volume"].mean())
        if avg_vol_after > vma20 * 0.65:
            return None

    # MACD 양전환 + RSI rsi_min~65 (눌림목에서 모멘텀 회복 확인)
    if not _macd_ok(df):
        return None
    rsi = _rsi_val(df)
    if rsi is not None and not (rsi_min <= rsi <= 65):
        return None

    # ── 신뢰도 계산 ──────────────────────────────────────────────────
    conf = 72

    if price > ma60:                              # 현재가 MA60 위 (더 강한 정배열)
        conf += 5
    if 0.05 <= pullback_pct <= 0.10:              # 5~10% 눌림 (적정 눌림폭)
        conf += 8
    if vma20 > 0 and float(after_peak["Volume"].mean()) < vma20 * 0.50:
        conf += 7                                  # 거래량 50% 미만 수축
    ma20_slope = (ma20 - ma20_5d) / ma20_5d
    if ma20_slope > 0.015:
        conf += 5                                  # MA20 가파른 상승

    name = get_stock_name(ticker)
    return {
        "ticker":       ticker,
        "name":         name,
        "pattern":      "눌림목_매집",
        "bc_date":      peak_idx.strftime("%m/%d") if hasattr(peak_idx, "strftime") else str(peak_idx),
        "bc_pct":       f"-{pullback_pct * 100:.1f}%",
        "bc_low":       int(price),
        "days_after":   len(after_peak),
        "current":      int(price),
        "ma20":         int(ma20),
        "ma60":         int(ma60),
        "ma240":        int(ma240),
        "entry":        (int(ma20 * 0.98), int(ma20 * 1.02)),
        "stop":         int(ma60 * 0.97),
        "target":       int(peak_high * 1.05),
        "week52_high":  int(df["High"].tail(252).max()),
        "week52_low":   int(df["Low"].tail(252).min()),
        "conf":         min(conf, 92),
    }


# ── 챔피언 점수 ──────────────────────────────────────────────────────

def _champion_score(r: dict, df: pd.DataFrame) -> tuple[float, dict]:
    """
    종합 챔피언 점수 (0~100).
    전체 유니버스(KR+US)에서 단 하나를 뽑기 위한 복합 지표.

    반환: (총점, breakdown_dict)
    구성:
      1. 패턴 품질      (0~25)
      2. 신뢰도 보너스  (0~15)
      3. RSI 스위트스팟 (0~15)  ← 52~62 최적
      4. MACD 히스트 강도 (0~15) ← 현재값 / 최근20일 최대
      5. 거래량 수축    (0~10)  ← 낮을수록 스프링 압축
      6. MA정배열 건강도 (0~10) ← MA20/MA240 spread
      7. 52주 고점 근접도 (0~10) ← 신고가 직전 = 최고
    """
    if df is None or len(df) < 20:
        return 0.0, {}

    df_i = _indicators(df) if "RSI14" not in df.columns else df
    cur = df_i.iloc[-1]
    price = float(cur["Close"])
    bd: dict[str, float] = {}

    # 1. 패턴 품질 (0-25)
    bd["패턴"] = float({
        "텐배거":     25,
        "눌림목_매집": 22,
        "골삼이":     20,
        "골든샘플":   18,
        "MA압축지지":  16,
        "레드삼각":    14,
    }.get(r.get("pattern", ""), 10))

    # 2. 신뢰도 보너스 (0-15): conf 83 = 0pt, 97 = 15pt
    conf = r.get("conf", 83)
    bd["신뢰도"] = round(min(15.0, max(0.0, (conf - 83) * 1.07)), 1)

    # 3. RSI 스위트스팟 (0-15): 52~62 최적 (모멘텀 회복 중, 과열 직전)
    rsi = _rsi_val(df_i)
    rsi_pt = 0.0
    if rsi is not None:
        if   52 <= rsi <= 62: rsi_pt = 15
        elif 47 <= rsi <  52: rsi_pt = 11
        elif 62 <  rsi <= 67: rsi_pt =  8
        elif 43 <= rsi <  47: rsi_pt =  4
    bd["RSI"] = rsi_pt

    # 4. MACD 히스토그램 강도 (0-15): 현재 / 최근 20일 최대 비율
    macd_pt = 0.0
    if "MACD_HIST" in df_i.columns:
        v  = float(df_i["MACD_HIST"].iloc[-1])
        mx = float(df_i["MACD_HIST"].tail(20).abs().max())
        if v > 0 and mx > 0:
            macd_pt = round(min(15.0, (v / mx) * 15.0), 1)
    bd["MACD"] = macd_pt

    # 5. 거래량 수축 (0-10): VMA20 대비 낮을수록 코일 압축
    vma20 = float(cur["VMA20"]) if "VMA20" in df_i.columns and not pd.isna(cur["VMA20"]) else 0.0
    vol_pt = 0.0
    if vma20 > 0:
        vr = float(cur["Volume"]) / vma20
        if   vr < 0.35: vol_pt = 10
        elif vr < 0.50: vol_pt =  7
        elif vr < 0.65: vol_pt =  4
    bd["거래량"] = vol_pt

    # 6. MA정배열 건강도 (0-10): MA20/MA240 = 1.10~1.30 최적
    ma20  = float(cur["MA20"])  if "MA20"  in df_i.columns and not pd.isna(cur["MA20"])  else 0.0
    ma240 = float(cur["MA240"]) if "MA240" in df_i.columns and not pd.isna(cur["MA240"]) else 0.0
    ma_pt = 0.0
    if ma20 > 0 and ma240 > 0:
        sp = ma20 / ma240 - 1.0
        if   0.10 <= sp <= 0.30: ma_pt = 10
        elif 0.08 <= sp <  0.10: ma_pt =  7
        elif 0.30 <  sp <= 0.50: ma_pt =  5
        elif 0.05 <= sp <  0.08: ma_pt =  3
    bd["MA정배열"] = ma_pt

    # 7. 52주 고점 근접도 (0-10): 신고가 돌파 직전 = 폭발 직전
    h52 = r.get("week52_high", 0)
    h52_pt = 0.0
    if h52 > 0 and price > 0:
        px = price / h52
        if   px >= 0.97: h52_pt = 10
        elif px >= 0.93: h52_pt =  8
        elif px >= 0.88: h52_pt =  5
        elif px >= 0.82: h52_pt =  2
    bd["52주고점"] = h52_pt

    total = round(sum(bd.values()), 1)
    return total, bd


def pick_champion(results: list[dict]) -> dict | None:
    """results 중 champion_score 최고 종목 반환."""
    if not results:
        return None
    return max(results, key=lambda r: r.get("champion_score", 0.0))


# ── 전체 스캔 ─────────────────────────────────────────────────────────

def _base_ok_us(df: pd.DataFrame, price: float, ma240: float) -> bool:
    """미국 주식용 기본 조건 (가격 $10+, MA240 위, MA240 30d 우상향, MA60 위)"""
    if price < 10.0:          # $10 미만 저가주 제외
        return False
    if pd.isna(ma240) or ma240 == 0:
        return False
    if price <= ma240:
        return False
    if len(df) < 270:
        return False
    # MA240 우상향: 30거래일 기준으로 강화
    ma240_30d = df["MA240"].iloc[-30]
    if pd.isna(ma240_30d) or ma240 <= ma240_30d:
        return False
    # MA60 위에 있어야 (단기 추세 확인)
    if "MA60" in df.columns:
        ma60 = df["MA60"].iloc[-1]
        if not pd.isna(ma60) and ma60 > 0 and price <= ma60:
            return False
    return True


def scan_all_us(ticker_market: dict[str, str],
                us_big_pct: float = 0.12,
                us_vol_mult: float = 8.0,
                ticker_names: dict[str, str] | None = None) -> list[dict]:
    """
    미국 주식 패턴 스캔. 기존 알고리즘 재사용, 미국 시장 특성에 맞게 강화된 필터 적용.
    ticker_market: {ticker: market}  예) {'NVDA': 'US_NASDAQ'}
    us_big_pct: 대양봉 기준 12% (어닝 10% 움직임 노이즈 제거)
    us_vol_mult: 거래량 배수 8배 (미국 유동성 주식 기준 의미있는 급증)
    """
    from database import get_algo_config
    cfg_golsami     = get_algo_config("골삼이")
    cfg_golden      = get_algo_config("골든샘플")
    cfg_red         = get_algo_config("레드삼각")
    cfg_ma_compress = get_algo_config("MA압축지지")
    cfg_ten_bagger  = get_algo_config("텐배거")

    # US 전용 파라미터로 오버라이드
    for cfg in (cfg_golsami, cfg_golden, cfg_red, cfg_ma_compress, cfg_ten_bagger):
        cfg['big_pct']  = us_big_pct
        cfg['vol_mult'] = us_vol_mult

    # 텐배거 US 강화: RS 기준 상향, 52주 고점 근접도 강화
    cfg_ten_bagger['rs_6m_min']      = 0.25   # 6개월 +25% (한국 20%→US bull market 기준)
    cfg_ten_bagger['rs_3m_min']      = 0.12   # 3개월 +12%
    cfg_ten_bagger['high52w_ratio']  = 0.92   # 52주 고점 92% 이상
    cfg_ten_bagger['vol_contract']   = 0.60   # 거래량 수축 60% 미만 (더 타이트)
    cfg_ten_bagger['brkout_vol_mult'] = 2.0   # 돌파 거래량 2배 (1.8→2.0)

    # 눌림목_매집 US 강화: MA기준 상향, RSI 범위 축소, 눌림폭 최소 5%
    cfg_pullback_us = dict(cfg_golsami)
    cfg_pullback_us['_us_ma_ratio_min'] = 1.15   # MA20/MA240 ≥ 15% (기본 8%)
    cfg_pullback_us['_us_rsi_min']      = 50     # RSI 50~ (기본 42~)
    cfg_pullback_us['_us_pullback_min'] = 0.05   # 눌림 최소 5% (기본 3%)

    # US 최소 신뢰도 기준
    CONF_MIN_US = 80

    results: list[dict] = []

    for ticker, market in ticker_market.items():
        try:
            df = get_ohlcv_us(ticker, days=_OHLCV_DAYS)
            if df is None or len(df) < 30:
                continue

            df_ind = _indicators(df)
            cur    = df_ind.iloc[-1]
            price  = float(cur["Close"])
            ma240  = float(cur["MA240"]) if not pd.isna(cur["MA240"]) else 0.0

            # 미국 주식 기본 조건 패스 여부 사전 확인 (시간 절약)
            if not _base_ok_us(df_ind, price, ma240):
                continue

            # 실용적 접근: Close를 ×1000 하면 _base_ok(₩1000 체크) 통과
            # 패턴 계산은 비율 기반이라 스케일 무관
            df_scaled = df.copy()
            df_scaled["Open"]  = df_scaled["Open"]  * 1000
            df_scaled["High"]  = df_scaled["High"]  * 1000
            df_scaled["Low"]   = df_scaled["Low"]   * 1000
            df_scaled["Close"] = df_scaled["Close"] * 1000

            result = (
                detect_golsami(df_scaled, ticker, cfg_golsami) or
                detect_golden_sample(df_scaled, ticker, cfg_golden) or
                detect_red_triangle(df_scaled, ticker, cfg_red) or
                detect_ma_compression(df_scaled, ticker, cfg_ma_compress) or
                detect_ten_bagger(df_scaled, ticker, cfg_ten_bagger) or
                detect_pullback_setup(df_scaled, ticker, cfg_pullback_us)
            )

            # US 최소 신뢰도 필터
            if result and result.get("conf", 0) < CONF_MIN_US:
                result = None

            if result:
                score, bd = _champion_score(result, df_ind)
                result["champion_score"] = score
                result["champion_breakdown"] = bd

                # 스케일 복원: 저장 단위를 달러 정수로
                for key in ('current', 'ma20', 'ma60', 'ma240',
                            'bc_open', 'bc_low', 'bc_high',
                            'entry', 'stop', 'target',
                            'week52_high', 'week52_low', 'box_top'):
                    if key == 'entry' and isinstance(result.get(key), tuple):
                        result[key] = (
                            round(result[key][0] / 1000),
                            round(result[key][1] / 1000),
                        )
                    elif key in result and isinstance(result[key], (int, float)):
                        result[key] = round(result[key] / 1000)

                result['name']   = (ticker_names or {}).get(ticker, ticker)
                result['ticker'] = ticker
                result['market'] = market
                results.append(result)

            time.sleep(0.15)
        except Exception:
            continue

    return results


def scan_all(tickers: list[str]) -> list[dict]:
    """
    후보 종목 전체 스캔 (동기 — asyncio.to_thread로 호출).
    스캔 시작 시 DB에서 파라미터를 한 번만 로드해 사용.
    """
    from database import get_algo_config
    cfg_golsami      = get_algo_config("골삼이")
    cfg_golden       = get_algo_config("골든샘플")
    cfg_red          = get_algo_config("레드삼각")
    cfg_ma_compress  = get_algo_config("MA압축지지")
    cfg_ten_bagger   = get_algo_config("텐배거")

    # 한국장 최소 신뢰도: 낮은 신뢰도 신호는 무조건 차단 (완화 83→78)
    CONF_MIN_KR = 78

    results: list[dict] = []

    for ticker in tickers:
        try:
            # 국장 티커는 6자리 숫자 (US 티커 혼입 방어)
            if not ticker.isdigit():
                continue

            df = get_ohlcv(ticker, days=_OHLCV_DAYS)
            if df is None or len(df) < 30:
                continue

            result = (
                detect_golsami(df, ticker, cfg_golsami) or
                detect_golden_sample(df, ticker, cfg_golden) or
                detect_red_triangle(df, ticker, cfg_red) or
                detect_ma_compression(df, ticker, cfg_ma_compress) or
                detect_ten_bagger(df, ticker, cfg_ten_bagger) or
                detect_pullback_setup(df, ticker, cfg_golsami)
            )
            if result and result.get("conf", 0) >= CONF_MIN_KR:
                score, bd = _champion_score(result, df)
                result["champion_score"] = score
                result["champion_breakdown"] = bd
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
        # 눌림목_매집
        "눌림목_매집":          "🎯",
        "눌림목_매집+":         "🎯✨",
        "눌림목_매집+1":        "🎯📈",
        "눌림목_매집+2":        "🎯🔬",
        # Plus1 (MACD 양전환)
        "골삼이+1":             "📊📈", "골든샘플+1":           "🔑📈",
        "레드삼각+1":           "📐📈", "골삼이(상승초입)+1":   "🚀📈",
        "MA압축지지+1":         "📦📈",
        # Plus2 (MACD+RSI 복합)
        "골삼이+2":             "📊🔬", "골든샘플+2":           "🔑🔬",
        "레드삼각+2":           "📐🔬", "골삼이(상승초입)+2":   "🚀🔬",
        "MA압축지지+2":         "📦🔬",
    }.get(p, "⚪")

    # 패턴별 매수존 (Plus 패턴도 기존과 동일한 entry 구조 사용)
    base_p = p.split("+")[0]   # "골삼이+1" → "골삼이", "눌림목_매집+2" → "눌림목_매집"
    if base_p in ("골삼이", "레드삼각", "골삼이(상승초입)", "MA압축지지", "텐배거", "눌림목_매집"):
        entry_str = f"₩{r['entry'][0]:,}~{r['entry'][1]:,}"
    else:  # 골든샘플 계열
        entry_str = f"₩{r['ma20']:,} 부근"

    return (
        f"[{emoji}{p}({conf}%)/{name}] : "
        f"현(₩{current:,}) 240(₩{ma240:,}), "
        f"매수존({entry_str}), 손절가(₩{stop:,})"
    )


