"""
fetch_indicators.py
시장 지표 자동 수집

데이터 소스:
  Yahoo Finance JSON  → 환율, 미 국채, WTI, SOXX (API 키 불필요)
  FRED API           → HY 스프레드, RRP, TGA, 10Y-2Y, MMF (무료 키 필요)
  pykrx              → 외국인 수급
  Claude API         → 관세/상법 등 정성 지표 뉴스 분석
"""

import os
import time
from datetime import datetime, timedelta

import requests

DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(DIR)

# ── 상태 임계값 ────────────────────────────────────────────────────────

def _status_above(v, thresholds, default='최상'):
    """값이 클수록 위험한 지표. thresholds = [(값, 상태), ...]  내림차순"""
    for t, s in thresholds:
        if v >= t:
            return s
    return default

def _status_below(v, thresholds, default='최상'):
    """값이 작을수록 위험한 지표."""
    for t, s in thresholds:
        if v <= t:
            return s
    return default

STATUS_THRESHOLDS = {
    'usd_krw':     lambda v: _status_above(v, [(1520,'위험'),(1490,'경고'),(1455,'관망'),(1420,'긍정')]),
    'us10y':       lambda v: _status_above(v, [(4.5,'위험'),(4.2,'경고'),(3.8,'관망'),(3.5,'긍정')]),
    'wti':         lambda v: _status_above(v, [(90,'위험'),(80,'경고'),(70,'관망'),(60,'긍정')]),
    'hy_spread':   lambda v: _status_above(v, [(5.0,'위험'),(4.0,'경고'),(3.5,'관망'),(3.0,'긍정')]),
    'yield_curve': lambda v: _status_below(v, [(-0.5,'위험'),(0,'경고')],
                             default=('최상' if v >= 1.0 else '긍정' if v >= 0.3 else '관망')),
    # mmf_total: WRMFNS(소매 MMF) 기준 T$ — 실제 범위 $1~2.5T
    # 낮을수록 현금 소진(과열) → 위험; 높을수록 관망자금 풍부 → 긍정
    'mmf_total':   lambda v: _status_below(v, [(1.5,'위험'),(1.8,'경고'),(2.1,'관망')],
                             default='긍정' if v < 2.5 else '최상'),
    # 외국인 순매수: 음수(매도)일수록 위험, 양수(매수)일수록 긍정. v = 원 단위
    'foreign_flow':lambda v: _status_below(v/1e8, [(-5000,'위험'),(-1000,'경고'),(-1,'관망')],
                             default=('최상' if v/1e8 >= 5000 else '긍정')),
    # 새 지표 (높을수록 위험)
    'vix':         lambda v: _status_above(v, [(30,'위험'),(25,'경고'),(20,'관망'),(15,'긍정')]),
    # gold: 2026년 기준 현실적 임계값 ($5000대 시장 반영)
    # 금이 이미 구조적 고가 → 급등률(MoM%)이 진짜 신호; 절대값은 상한선만 제한
    'gold':        lambda v: _status_above(v, [(4500,'위험'),(3800,'경고'),(3300,'관망'),(3000,'긍정')]),
    # 새 지표 (낮을수록 위험) — 2026년 현실화 임계값
    'btc':         lambda v: _status_below(v, [(25000,'위험'),(35000,'경고'),(50000,'관망')],
                             default='긍정' if v < 65000 else ('관망' if v < 80000 else '최상')),
    'nasdaq':      None,  # MoM 변화율로 계산 — fetch_yahoo_all에서 직접 처리
    # CMS 연동 지표
    'brent':       lambda v: _status_above(v, [(95,'위험'),(85,'경고'),(75,'관망'),(65,'긍정')]),
    'dxy':         lambda v: _status_above(v, [(108,'위험'),(105,'경고'),(102,'관망'),(99,'긍정')]),
    'ust2y':       lambda v: _status_above(v, [(5.0,'위험'),(4.5,'경고'),(4.0,'관망'),(3.5,'긍정')]),
    'kre':         None,  # MoM 변화율로 계산
    'xlf':         None,  # MoM 변화율로 계산
    'kospi':       None,  # MoM 변화율로 계산
    'kosdaq':      None,  # MoM 변화율로 계산
    # fear_greed: CNN F&G (0=극단공포,100=극단탐욕) — 역발상 지표
    # 75↑ 극단탐욕=과열위험, 25↓ 극단공포=역발상매수기회(최상)
    'fear_greed':  lambda v: ('위험' if v >= 75 else '경고' if v >= 55 else
                              '관망' if v >= 45 else '긍정' if v >= 25 else '최상'),
    # soxx: 필라델피아 반도체 ETF — 경기선행지표 (높을수록 안전)
    'soxx':        lambda v: _status_below(v, [(200,'위험'),(260,'경고'),(320,'관망'),(420,'긍정')],
                             default='최상'),
    # rrp: Fed 역RP잔고 T$ — 높을수록 과잉유동성(버블), 너무낮으면 유동성 소진
    'rrp':         lambda v: ('경고' if v > 1.0 else '긍정' if v > 0.3 else '관망'),
    # tga: 미 재무부 잔고 B$ — 너무낮으면 정부지출 예정(단기부양), 높으면 긴축
    'tga':         lambda v: ('위험' if v < 200 else '경고' if v < 400 else
                              '관망' if v < 700 else '긍정'),
}


# ── Yahoo Finance ─────────────────────────────────────────────────────

YAHOO_SYMBOLS = {
    'usd_krw': 'KRW=X',    # 1 USD → KRW
    'us10y':   '^TNX',     # 10년 국채 금리 (%)
    'wti':     'CL=F',     # WTI 선물
    'soxx':    'SOXX',     # 필라델피아 반도체 (MoM)
    'btc':     'BTC-USD',  # 비트코인 (위험선호 지표)
    'vix':     '^VIX',     # CBOE 변동성 (공포 지표)
    'nasdaq':  '^IXIC',    # 나스닥 종합
    'gold':    'GC=F',     # 금 선물
    # CMS 연동 지표
    'brent':   'BZ=F',     # 브렌트 원유
    'dxy':     'DX-Y.NYB', # 달러 인덱스
    'kre':     'KRE',      # 미국 지역은행 ETF (MoM)
    'xlf':     'XLF',      # 미국 금융섹터 ETF (MoM)
    # 국장 모멘텀
    'kospi':   '^KS11',    # KOSPI 지수 (MoM)
    'kosdaq':  '^KQ11',    # KOSDAQ 지수 (MoM)
}

def _yahoo_latest(symbol: str, realtime: bool = False) -> float | None:
    """Yahoo Finance chart API로 최신 가격 수집.
    realtime=True  → interval=5m, range=1d  (장중 현재가)
    realtime=False → interval=1d, range=5d  (일봉 종가)
    """
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
    if realtime:
        params = {'interval': '5m', 'range': '1d'}
    else:
        params = {'interval': '1d', 'range': '5d'}
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; StockBot/1.0)'}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=12)
        r.raise_for_status()
        js = r.json()['chart']['result'][0]
        quote = js['indicators']['quote'][0]
        # 일부 선물/인덱스 심볼은 'close' 대신 'adjclose' 혹은 다른 필드 반환
        closes = (quote.get('close') or
                  [v['adjclose'] for v in js['indicators'].get('adjclose', [{}])] or
                  [])
        for v in reversed(closes):
            if v is not None:
                return round(v, 4)
    except Exception as e:
        print(f'[Yahoo] {symbol} 오류: {e}')
    return None



def fetch_yahoo_realtime() -> dict:
    """실시간 탭용 — interval=5m/range=1d 로 장중 현재가 수집.
    fetch_yahoo_all() 과 동일 구조 반환.
    """
    # 히스토리(1개월 비교) 필요 심볼은 일봉 유지
    hist_keys = {'nasdaq', 'kre', 'xlf', 'soxx', 'kospi', 'kosdaq'}

    result = {}
    for key, sym in YAHOO_SYMBOLS.items():
        if key in hist_keys:
            closes = _yahoo_history(sym, '6mo')
            if not closes:
                continue
            if key == 'nasdaq':
                v_r, status = _nasdaq_status(closes)
                idx_1mo = max(0, len(closes) - 22)
                pct = (v_r - closes[idx_1mo]) / closes[idx_1mo] * 100 if closes[idx_1mo] else 0
                result[key] = {'value': v_r, 'status': status, 'note': f'나스닥 {v_r:,.0f} (1개월 {pct:+.1f}%)'}
            elif key in ('kre', 'xlf'):
                label = 'KRE 지역은행' if key == 'kre' else 'XLF 금융'
                r = _mom_etf_status(closes, label)
                if r:
                    result[key] = r
            elif key == 'soxx':
                r = _soxx_status(closes)
                if r:
                    result[key] = r
            elif key in ('kospi', 'kosdaq'):
                label = 'KOSPI' if key == 'kospi' else 'KOSDAQ'
                r = _kr_index_status(closes, label)
                if r:
                    result[key] = r
        else:
            # 5분봉 장중 현재가
            price = _yahoo_latest(sym, realtime=True)
            if price is None:
                continue
            if key == 'usd_krw':
                v = round(price, 0)
            elif key == 'btc':
                v = round(price, 0)
            elif key == 'gold':
                v = round(price, 1)
            elif key == 'brent':
                v = round(price, 1)
            elif key == 'dxy':
                v = round(price, 2)
            else:
                v = round(price, 2)

            status_fn = STATUS_THRESHOLDS.get(key)
            note = ''
            if key == 'btc':
                note = f'BTC ${v:,.0f}'
            elif key == 'vix':
                note = f'VIX {v:.1f} ({"극도공포" if v>=30 else "공포" if v>=20 else "중립" if v>=15 else "낮음"})'
            elif key == 'gold':
                note = f'금 ${v:,.1f}/oz'
            elif key == 'brent':
                note = f'브렌트 ${v:.1f}/배럴'
            elif key == 'dxy':
                note = f'달러 인덱스 {v:.2f}'
            elif key == 'usd_krw':
                note = f'원/달러 {v:,.0f}원'
            elif key == 'us10y':
                note = f'미 국채 10년 {v:.3f}%'
            elif key == 'wti':
                note = f'WTI ${v:.2f}/배럴'

            result[key] = {
                'value':  v,
                'status': status_fn(v) if status_fn else None,
                'note':   note,
            }
        time.sleep(0.2)

    return result


def _yahoo_history(symbol: str, range_: str = '1mo') -> list[float]:
    """Yahoo Finance 종가 리스트 반환 (오래된→최신 순)"""
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
    params = {'interval': '1d', 'range': range_}
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; StockBot/1.0)'}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=12)
        r.raise_for_status()
        closes = r.json()['chart']['result'][0]['indicators']['quote'][0]['close']
        return [v for v in closes if v is not None]
    except Exception as e:
        print(f'[Yahoo history] {symbol} 오류: {e}')
    return []


def _nasdaq_status(closes: list[float]) -> tuple[float, str]:
    """나스닥 1개월 + 3개월 모멘텀 조합 상태.
    closes는 6mo 기간 데이터 (약 126 거래일).
    1개월(21영업일) 기준 상태를 먼저 산출, 3개월(63영업일) 이 경고 이하면 관망으로 올림.
    """
    if len(closes) < 2:
        return (closes[-1] if closes else 0), '관망'
    current = closes[-1]

    # 1개월 기준 (마지막 21 거래일)
    idx_1mo = max(0, len(closes) - 22)
    month_ago = closes[idx_1mo]
    pct_1mo = (current - month_ago) / month_ago * 100

    if pct_1mo >= 5:    status_1mo = '최상'
    elif pct_1mo >= 2:  status_1mo = '긍정'
    elif pct_1mo >= 0:  status_1mo = '관망'
    elif pct_1mo >= -3: status_1mo = '경고'
    else:               status_1mo = '위험'

    # 3개월 기준 (마지막 63 거래일)
    idx_3mo = max(0, len(closes) - 64)
    three_ago = closes[idx_3mo]
    pct_3mo = (current - three_ago) / three_ago * 100

    if pct_3mo >= 8:    status_3mo = '최상'
    elif pct_3mo >= 3:  status_3mo = '긍정'
    elif pct_3mo >= 0:  status_3mo = '관망'
    elif pct_3mo >= -5: status_3mo = '경고'
    else:               status_3mo = '위험'

    _RANK = {'최상': 4, '긍정': 3, '관망': 2, '경고': 1, '위험': 0}
    # 1개월이 긍정/최상이어도 3개월이 경고 이하면 관망으로 조정
    if _RANK[status_1mo] >= _RANK['긍정'] and _RANK[status_3mo] <= _RANK['경고']:
        status = '관망'
    else:
        status = status_1mo

    return round(current, 0), status


def _mom_etf_status(closes: list[float], label: str) -> dict:
    """KRE/XLF용 1개월+3개월 모멘텀 조합 상태 계산.
    closes: 6mo 기간 데이터.
    """
    if not closes:
        return {}
    v = round(closes[-1], 2)

    # 1개월 (21 거래일)
    idx_1mo = max(0, len(closes) - 22)
    pct_1mo = round((v - closes[idx_1mo]) / closes[idx_1mo] * 100, 1) if closes[idx_1mo] else 0

    # 3개월 (63 거래일)
    idx_3mo = max(0, len(closes) - 64)
    pct_3mo = round((v - closes[idx_3mo]) / closes[idx_3mo] * 100, 1) if closes[idx_3mo] else 0

    if   pct_1mo >= 10: status_1mo = '최상'
    elif pct_1mo >= 4:  status_1mo = '긍정'
    elif pct_1mo >= 0:  status_1mo = '관망'
    elif pct_1mo >= -5: status_1mo = '경고'
    else:               status_1mo = '위험'

    if   pct_3mo >= 15: status_3mo = '최상'
    elif pct_3mo >= 6:  status_3mo = '긍정'
    elif pct_3mo >= 0:  status_3mo = '관망'
    elif pct_3mo >= -8: status_3mo = '경고'
    else:               status_3mo = '위험'

    _RANK = {'최상': 4, '긍정': 3, '관망': 2, '경고': 1, '위험': 0}
    if _RANK[status_1mo] >= _RANK['긍정'] and _RANK[status_3mo] <= _RANK['경고']:
        status = '관망'
    else:
        status = status_1mo

    return {
        'value':  v,
        'status': status,
        'note':   f'{label} ${v:.2f} (1개월 {pct_1mo:+.1f}%, 3개월 {pct_3mo:+.1f}%)',
    }


def _soxx_status(closes: list[float]) -> dict:
    """SOXX 1개월+3개월 모멘텀 기반 상태 계산."""
    if not closes:
        return {}
    v = round(closes[-1], 2)
    idx_1mo = max(0, len(closes) - 22)
    pct_1mo = round((v - closes[idx_1mo]) / closes[idx_1mo] * 100, 1) if closes[idx_1mo] else 0
    idx_3mo = max(0, len(closes) - 64)
    pct_3mo = round((v - closes[idx_3mo]) / closes[idx_3mo] * 100, 1) if closes[idx_3mo] else 0

    # MoM 기반 경보 (절대값 임계보다 우선)
    if   pct_1mo <= -15: status = '위험'
    elif pct_1mo <= -8:  status = '경고'
    elif pct_1mo <= -3:  status = '관망'
    elif pct_1mo >= 10:  status = '최상'
    elif pct_1mo >= 4:   status = '긍정'
    else:
        # MoM 중립이면 절대값 임계 사용
        fn = STATUS_THRESHOLDS.get('soxx')
        status = fn(v) if fn else '관망'

    return {
        'value':   v,
        'status':  status,
        'note':    f'SOXX ${v:.2f} (1개월 {pct_1mo:+.1f}%, 3개월 {pct_3mo:+.1f}%)',
        'mom_1mo': pct_1mo,
        'mom_3mo': pct_3mo,
    }


def _kr_index_status(closes: list[float], label: str) -> dict:
    """KOSPI/KOSDAQ 1개월 모멘텀 기반 상태."""
    if not closes:
        return {}
    v = round(closes[-1], 1)
    idx_1mo = max(0, len(closes) - 22)
    pct_1mo = round((v - closes[idx_1mo]) / closes[idx_1mo] * 100, 1) if closes[idx_1mo] else 0

    if   pct_1mo >= 5:    status = '최상'
    elif pct_1mo >= 2:    status = '긍정'
    elif pct_1mo >= 0:    status = '관망'
    elif pct_1mo >= -3:   status = '경고'
    else:                 status = '위험'

    return {
        'value':   v,
        'status':  status,
        'note':    f'{label} {v:,.1f} (1개월 {pct_1mo:+.1f}%)',
        'mom_1mo': pct_1mo,
    }


def fetch_yahoo_all() -> dict:
    result = {}
    for key, sym in YAHOO_SYMBOLS.items():
        if key == 'nasdaq':
            closes = _yahoo_history(sym, '6mo')  # 3개월 모멘텀 계산용
            if not closes:
                result[key] = {}
                continue
            v, status = _nasdaq_status(closes)
            idx_1mo = max(0, len(closes) - 22)
            pct = (v - closes[idx_1mo]) / closes[idx_1mo] * 100 if closes[idx_1mo] else 0
            result[key] = {
                'value':  v,
                'status': status,
                'note':   f'나스닥 {v:,.0f} (1개월 {pct:+.1f}%)',
            }
            time.sleep(0.3)
            continue

        if key in ('kre', 'xlf'):
            closes = _yahoo_history(sym, '6mo')  # 3개월 모멘텀 계산용
            if not closes:
                result[key] = {}
                continue
            label = 'KRE 지역은행' if key == 'kre' else 'XLF 금융'
            r = _mom_etf_status(closes, label)
            result[key] = r if r else {}
            time.sleep(0.3)
            continue

        if key == 'soxx':
            closes = _yahoo_history(sym, '6mo')
            if not closes:
                result[key] = {}
                continue
            result[key] = _soxx_status(closes)
            time.sleep(0.3)
            continue

        if key in ('kospi', 'kosdaq'):
            closes = _yahoo_history(sym, '6mo')
            if not closes:
                result[key] = {}
                continue
            label = 'KOSPI' if key == 'kospi' else 'KOSDAQ'
            result[key] = _kr_index_status(closes, label)
            time.sleep(0.3)
            continue

        v = _yahoo_latest(sym)
        if v is None:
            result[key] = {}
            continue
        if key == 'usd_krw':
            v = round(v, 0)
        elif key == 'btc':
            v = round(v, 0)
        elif key == 'gold':
            v = round(v, 1)
        elif key == 'brent':
            v = round(v, 1)
        elif key == 'dxy':
            v = round(v, 2)
        status_fn = STATUS_THRESHOLDS.get(key)
        note = ''
        if key == 'btc':
            note = f'BTC ${v:,.0f}'
        elif key == 'vix':
            note = f'VIX {v:.1f} ({"극도공포" if v>=30 else "공포" if v>=20 else "중립" if v>=15 else "낮음"})'
        elif key == 'gold':
            note = f'금 ${v:,.1f}/oz'
        elif key == 'brent':
            note = f'브렌트 ${v:.1f}/배럴'
        elif key == 'dxy':
            note = f'달러 인덱스 {v:.2f}'
        result[key] = {
            'value':  v,
            'status': status_fn(v) if status_fn else None,
            'note':   note,
        }
        time.sleep(0.3)
    return result


# ── FRED API ──────────────────────────────────────────────────────────

# FRED 시리즈 ID (TGA·MMF 제외 — 별도 소스 사용)
FRED_SERIES = {
    'hy_spread':   'BAMLH0A0HYM2',  # ICE BofA HY OAS (%)
    'rrp':         'RRPONTSYD',      # Overnight RRP (B$)
    'yield_curve': 'T10Y2Y',         # 10Y-2Y 금리차 (%)
    'ust2y':       'DGS2',           # 미국 2년 국채 금리 (%)
}

# MMF 주간 시리즈 후보 (순서대로 시도)
_MMF_SERIES_CANDIDATES = ['WRMFNS', 'MMMFFAQ027S']


def _fred_latest(series_id: str, api_key: str) -> float | None:
    url = 'https://api.stlouisfed.org/fred/series/observations'
    params = {
        'series_id':  series_id,
        'api_key':    api_key,
        'file_type':  'json',
        'sort_order': 'desc',
        'limit':      20,
    }
    try:
        r = requests.get(url, params=params, timeout=12)
        r.raise_for_status()
        js = r.json()
        if 'error_code' in js:
            print(f'[FRED] {series_id} 오류: {js.get("error_message")}')
            return None
        for obs in js.get('observations', []):
            if obs['value'] not in ('.', ''):
                return float(obs['value'])
    except Exception as e:
        print(f'[FRED] {series_id} 오류: {e}')
    return None


_TGA_URL = ('https://api.fiscaldata.treasury.gov/services/api/fiscal_service'
            '/v1/accounting/dts/operating_cash_balance')

def _fetch_tga() -> float | None:
    """미국 재무부 공식 API — TGA 잔고 (B$)"""
    try:
        r = requests.get(
            _TGA_URL,
            params={
                'fields': 'record_date,account_type,open_today_bal',
                'filter': 'account_type:eq:Treasury General Account (TGA) Closing Balance',
                'sort':   '-record_date',
                'limit':  '5',
            },
            headers={'User-Agent': 'StockBot/1.0'},
            timeout=12,
        )
        r.raise_for_status()
        for row in r.json().get('data', []):
            v = row.get('open_today_bal')
            if v not in (None, '', 'null'):
                return round(float(v) / 1000, 1)  # M$ → B$
    except Exception as e:
        print(f'[TGA] 오류: {e}')
    return None


def _fetch_mmf(api_key: str) -> float | None:
    """MMF 총자산 (T$) — FRED 복수 시리즈 시도"""
    for series in _MMF_SERIES_CANDIDATES:
        v = _fred_latest(series, api_key)
        if v is not None:
            # WRMFNS 는 B$, MMMFFAQ027S 는 M$ 단위
            if series == 'MMMFFAQ027S':
                return round(v / 1_000_000, 2)   # M$ → T$
            return round(v / 1000, 2)             # B$ → T$
    return None


def fetch_fred_all(api_key: str) -> dict:
    if not api_key:
        return {}
    result = {}

    _FRED_NOTE = {
        'hy_spread':   lambda v: f'HY 스프레드 {v:.2f}%',
        'ust2y':       lambda v: f'미 국채 2년 {v:.2f}%',
        'yield_curve': lambda v: f'10Y-2Y {v:+.2f}%',
    }

    # 일반 FRED 시리즈
    for key, series in FRED_SERIES.items():
        v = _fred_latest(series, api_key)
        if v is None:
            print(f'[FRED] {key}({series}) 값 없음')
            result[key] = {}
            continue
        v = round(v, 2)
        status_fn = STATUS_THRESHOLDS.get(key)
        note_fn   = _FRED_NOTE.get(key)
        result[key] = {
            'value':  v,
            'status': status_fn(v) if status_fn else None,
            'note':   note_fn(v) if note_fn else '',
        }
        time.sleep(0.3)

    # TGA — 재무부 공식 API
    tga_v = _fetch_tga()
    if tga_v is not None:
        result['tga'] = {'value': tga_v, 'status': '관망',
                         'note': f'TGA 잔고 {tga_v:.1f}B$ (재무부 DTS)'}
    else:
        result['tga'] = {}

    # MMF — FRED 복수 후보
    mmf_v = _fetch_mmf(api_key)
    if mmf_v is not None:
        status_fn = STATUS_THRESHOLDS.get('mmf_total')
        result['mmf_total'] = {
            'value':  mmf_v,
            'status': status_fn(mmf_v) if status_fn else '관망',
            'note':   f'MMF 총자산 {mmf_v:.2f}T$',
        }
    else:
        result['mmf_total'] = {}

    return result


# ── CNN Fear & Greed Index ────────────────────────────────────────────

_FG_URLS = [
    'https://production.dataviz.cnn.io/index/fearandgreed/graphdata/',
    'https://production.dataviz.cnn.io/index/fearandgreed/graphdata',
    'https://fear-and-greed-index.p.rapidapi.com/v1/fgi',  # fallback (키 없으면 실패)
]

def fetch_fear_greed() -> dict:
    """CNN Fear & Greed Index (0=극단적 공포, 100=극단적 탐욕)"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
        'Referer': 'https://edition.cnn.com/markets/fear-and-greed',
        'Accept': 'application/json, text/plain, */*',
    }
    for url in _FG_URLS[:2]:
        try:
            r = requests.get(url, headers=headers, timeout=12)
            if not r.ok:
                continue
            js = r.json()
            fg = js.get('fear_and_greed') or js.get('fgi', {})
            score_raw = fg.get('score') or fg.get('now', {}).get('value')
            if score_raw is None:
                continue
            score  = round(float(score_raw), 1)
            rating = fg.get('rating') or fg.get('now', {}).get('valueText', '')

            if score >= 75:   status = '위험'
            elif score >= 55: status = '경고'
            elif score >= 45: status = '관망'
            elif score >= 25: status = '긍정'
            else:             status = '최상'

            result = {
                'value':  score,
                'status': status,
                'note':   f'CNN F&G {score} ({rating}) — 25↓ 역발상매수, 75↑ 과열주의',
            }
            # 히스토리 데이터 첨부 (백필 시 활용)
            hist_raw = js.get('fear_and_greed_historical', {}).get('data', [])
            if hist_raw:
                result['_historical'] = hist_raw  # [{x: ms, y: score, rating}, ...]
            return result
        except Exception as e:
            print(f'[F&G] {url} 오류: {e}')
    print('[F&G] 모든 URL 실패')
    return {}


# ── pykrx 외국인 수급 ─────────────────────────────────────────────────

def fetch_foreign_flow() -> dict:
    """KOSPI 외국인 당일 순매수 (억원).

    Naver 모바일 지수 투자자 동향 API 사용.
    (pykrx의 KRX 배치 API는 2026년부터 'LOGOUT' 차단되어 사용 불가)
    반환 foreignValue 단위 = 백만원 → 원 환산 ×1e6, 억원 = ÷1e8.
    """
    try:
        url = 'https://m.stock.naver.com/api/index/KOSPI/trend'
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.naver.com/'}
        r = requests.get(url, params={'pageSize': 5, 'page': 1},
                         headers=headers, timeout=12)
        r.raise_for_status()
        js = r.json()
        # 리스트로 올 수도, 단일 dict로 올 수도 있음
        row = js[0] if isinstance(js, list) and js else js
        fv = str(row.get('foreignValue', '')).replace(',', '').replace('+', '').strip()
        if not fv or fv in ('-', ''):
            print('[외국인수급] Naver foreignValue 없음')
            return {}
        net_million = float(fv)          # 백만원
        net = int(net_million * 1e6)     # 원
        net_eok = round(net / 1e8, 0)    # 억원
        bizdate = row.get('bizdate', '')
        fn = STATUS_THRESHOLDS.get('foreign_flow')
        return {
            'value':  net_eok,
            'status': fn(net) if fn else '관망',
            'note':   f'외국인 {net_eok:+,.0f}억원 (KOSPI, {bizdate})',
        }
    except Exception as e:
        print(f'[외국인수급] Naver 오류: {e}')
    return {}


# ── 통합 수집 ─────────────────────────────────────────────────────────

def fetch_all(fred_api_key: str = '', existing: dict = None) -> dict:
    """모든 지표 자동 수집."""
    existing = existing or {}
    print('[지표] Yahoo Finance 수집 중...')
    data = fetch_yahoo_all()

    print('[지표] 외국인 수급 수집 중...')
    ff = fetch_foreign_flow()
    if ff:
        data['foreign_flow'] = ff
    elif existing.get('foreign_flow'):
        data['foreign_flow'] = existing['foreign_flow']

    print('[지표] Fear & Greed 수집 중...')
    fg = fetch_fear_greed()
    if fg:
        data['fear_greed'] = fg

    if fred_api_key:
        print('[지표] FRED 수집 중...')
        data.update(fetch_fred_all(fred_api_key))
    else:
        print('[지표] FRED_API_KEY 없음 — HY/RRP/TGA/10Y-2Y/MMF 건너뜀')

    # F&G 내부 히스토리 데이터는 저장 대상 아님 — 제거
    if 'fear_greed' in data:
        data['fear_greed'].pop('_historical', None)

    # 기존 값에서 note 보정 (수동 메모 유지)
    for k in list(data.keys()):
        if not data[k].get('note') and existing.get(k, {}).get('note'):
            data[k]['note'] = existing[k]['note']

    return data


# ── 3개월 백필 ────────────────────────────────────────────────────────

def fetch_backfill(fred_api_key: str = '', days: int = 90) -> dict[str, dict]:
    """
    과거 N일치 일별 데이터를 일괄 수집.
    반환: { 'YYYY-MM-DD': { indicator_key: {value, status, note} } }
    """
    from datetime import date as _date

    end   = datetime.now()
    start = end - timedelta(days=days)
    start_str = start.strftime('%Y-%m-%d')
    end_str   = end.strftime('%Y-%m-%d')

    # 날짜 인덱스 초기화
    daily: dict[str, dict] = {}
    cur = start
    while cur <= end:
        daily[cur.strftime('%Y-%m-%d')] = {}
        cur += timedelta(days=1)

    # ── Yahoo Finance (1y 히스토리) ──────────────────────────────
    print('[백필] Yahoo Finance 수집 중...')
    for key, sym in YAHOO_SYMBOLS.items():
        try:
            url = f'https://query1.finance.yahoo.com/v8/finance/chart/{sym}'
            params = {'interval': '1d', 'range': '1y'}
            headers = {'User-Agent': 'Mozilla/5.0 (compatible; StockBot/1.0)'}
            r = requests.get(url, params=params, headers=headers, timeout=15)
            r.raise_for_status()
            res = r.json()['chart']['result'][0]
            timestamps = res['timestamp']
            closes = res['indicators']['quote'][0]['close']
            close_list = [(datetime.fromtimestamp(ts).strftime('%Y-%m-%d'), cv)
                          for ts, cv in zip(timestamps, closes) if cv is not None]

            if key == 'nasdaq':
                # MoM 변화율 계산을 위해 정렬된 closes 리스트 필요
                sorted_closes = [(d, cv) for d, cv in close_list if d in daily]
                sorted_closes.sort(key=lambda x: x[0])
                all_dates = [d for d, _ in close_list]
                all_vals  = [cv for _, cv in close_list]
                for i, (d, cv) in enumerate(sorted_closes):
                    # 약 20거래일 전 값으로 MoM 계산
                    idx_all = next((j for j, (dd,_) in enumerate(close_list) if dd == d), None)
                    if idx_all is None or idx_all < 20:
                        status = '관망'
                    else:
                        month_ago_v = close_list[idx_all - 20][1]
                        pct = (cv - month_ago_v) / month_ago_v * 100
                        if pct >= 5:    status = '최상'
                        elif pct >= 2:  status = '긍정'
                        elif pct >= 0:  status = '관망'
                        elif pct >= -3: status = '경고'
                        else:           status = '위험'
                    daily[d][key] = {'value': round(cv, 0), 'status': status, 'note': f'나스닥 {cv:,.0f}'}
            else:
                for d, cv in close_list:
                    if d not in daily:
                        continue
                    if key == 'usd_krw':   v = round(cv, 0)
                    elif key == 'btc':     v = round(cv, 0)
                    elif key == 'gold':    v = round(cv, 1)
                    else:                  v = round(cv, 4)
                    fn = STATUS_THRESHOLDS.get(key)
                    daily[d][key] = {'value': v, 'status': fn(v) if fn else None, 'note': ''}
            time.sleep(0.4)
        except Exception as e:
            print(f'[백필Yahoo] {key} 오류: {e}')

    # ── CNN Fear & Greed (히스토리 포함) ─────────────────────────
    print('[백필] Fear & Greed 수집 중...')
    try:
        fg_result = fetch_fear_greed()
        hist_raw = fg_result.pop('_historical', [])
        # 오늘 값
        if fg_result:
            today_str = datetime.now().strftime('%Y-%m-%d')
            if today_str in daily:
                daily[today_str]['fear_greed'] = fg_result

        def _fg_status(score):
            if score >= 75:   return '위험'
            elif score >= 55: return '경고'
            elif score >= 45: return '관망'
            elif score >= 25: return '긍정'
            else:             return '최상'

        for pt in hist_raw:
            try:
                ts_ms = pt.get('x', 0)
                d = datetime.fromtimestamp(ts_ms / 1000).strftime('%Y-%m-%d')
                score = round(float(pt['y']), 1)
                if d in daily:
                    daily[d]['fear_greed'] = {
                        'value': score,
                        'status': _fg_status(score),
                        'note': f"CNN F&G {score} ({pt.get('rating','')})",
                    }
            except Exception:
                pass
        cnt = sum(1 for d in daily if 'fear_greed' in daily[d])
        print(f'[F&G] {cnt}일 수집')
    except Exception as e:
        print(f'[백필F&G] 오류: {e}')

    # ── FRED (날짜 파라미터 없이, 최근 200개 가져와 로컬 필터) ────
    if fred_api_key:
        print('[백필] FRED 수집 중...')
        for key, series in FRED_SERIES.items():
            try:
                r = requests.get(
                    'https://api.stlouisfed.org/fred/series/observations',
                    params={
                        'series_id':  series,
                        'api_key':    fred_api_key,
                        'file_type':  'json',
                        'sort_order': 'desc',   # 최신부터
                        'limit':      200,       # 넉넉히
                    },
                    timeout=15,
                )
                r.raise_for_status()
                js = r.json()
                if 'error_code' in js:
                    print(f'[백필FRED] {series}: {js.get("error_message")}')
                    continue
                # desc → asc 변환 후 daily 범위만 필터
                obs_list = [o for o in js.get('observations', [])
                            if o['value'] not in ('.', '') and o['date'] >= start_str]
                obs_list.sort(key=lambda o: o['date'])
                last_v = None
                for obs in obs_list:
                    v = round(float(obs['value']), 2)
                    last_v = v
                    d = obs['date']
                    if d not in daily:
                        continue
                    fn = STATUS_THRESHOLDS.get(key)
                    daily[d][key] = {'value': v, 'status': fn(v) if fn else None, 'note': ''}
                # 날짜 보간 (주말·공휴일에 이전 값 채우기)
                if last_v is not None:
                    for d in sorted(daily.keys()):
                        if key not in daily[d] and last_v is not None:
                            daily[d][key] = {
                                'value': last_v,
                                'status': STATUS_THRESHOLDS.get(key, lambda _: None)(last_v),
                                'note': '(보간)',
                            }
                        elif key in daily[d]:
                            last_v = daily[d][key]['value']
                print(f'[FRED] {key}: {sum(1 for d in daily if key in daily[d])}일 수집')
                time.sleep(0.4)
            except Exception as e:
                print(f'[백필FRED] {key} 오류: {e}')

        # TGA — 재무부 공식 API (Closing Balance)
        tga_ok = False
        try:
            r = requests.get(
                _TGA_URL,
                params={
                    'fields': 'record_date,account_type,open_today_bal',
                    'filter': f'account_type:eq:Treasury General Account (TGA) Closing Balance,record_date:gte:{start_str}',
                    'sort':   'record_date',
                    'limit':  '200',
                    'page[size]': '200',
                },
                headers={'User-Agent': 'StockBot/1.0'},
                timeout=15,
            )
            r.raise_for_status()
            rows = r.json().get('data', [])
            for row in rows:
                d = str(row.get('record_date', ''))[:10]
                v = row.get('open_today_bal')
                if d in daily and v not in (None, '', 'null'):
                    try:
                        tga_b = round(float(v) / 1000, 1)  # M$ → B$
                        daily[d]['tga'] = {'value': tga_b, 'status': '관망',
                                           'note': f'TGA {tga_b:.1f}B$'}
                        tga_ok = True
                    except (ValueError, TypeError):
                        pass
            if tga_ok:
                print(f'[TGA] {sum(1 for d in daily if "tga" in daily[d])}일 수집')
        except Exception as e:
            print(f'[백필TGA] 오류: {e}')
        if not tga_ok:
            print('[백필TGA] TGA 수집 실패 — 건너뜀')

        # MMF — FRED 복수 후보 시리즈
        for series in _MMF_SERIES_CANDIDATES:
            try:
                r = requests.get(
                    'https://api.stlouisfed.org/fred/series/observations',
                    params={
                        'series_id':  series,
                        'api_key':    fred_api_key,
                        'file_type':  'json',
                        'sort_order': 'desc',
                        'limit':      50,
                    },
                    timeout=15,
                )
                r.raise_for_status()
                js = r.json()
                if 'error_code' in js:
                    continue
                last_v = None
                ok = False
                for obs in js.get('observations', []):
                    if obs['value'] in ('.', ''):
                        if last_v is not None and obs['date'] in daily:
                            daily[obs['date']]['mmf_total'] = {
                                'value': last_v, 'note': f'MMF {last_v:.2f}T$',
                                'status': STATUS_THRESHOLDS.get('mmf_total', lambda v: '관망')(last_v),
                            }
                        continue
                    raw = float(obs['value'])
                    v = round(raw / 1_000_000 if series == 'MMMFFAQ027S' else raw / 1000, 2)
                    last_v = v
                    d = obs['date']
                    if d not in daily:
                        continue
                    fn = STATUS_THRESHOLDS.get('mmf_total')
                    daily[d]['mmf_total'] = {'value': v, 'status': fn(v) if fn else '관망',
                                              'note': f'MMF {v:.2f}T$'}
                    ok = True
                if ok:
                    break  # 성공한 시리즈 사용
            except Exception as e:
                print(f'[백필MMF] {series} 오류: {e}')

    # ── pykrx 외국인 수급 (일별 루프) ────────────────────────────
    print('[백필] pykrx 외국인 수급 수집 중...')
    try:
        from pykrx import stock as _stock
        cur = start
        while cur <= end:
            d_krx  = cur.strftime('%Y%m%d')
            d_dash = cur.strftime('%Y-%m-%d')
            if d_dash in daily:
                try:
                    df = _stock.get_market_trading_value_by_investor(d_krx, d_krx, 'KOSPI')
                    if not df.empty:
                        for label in ['외국인합계', '외국인', 'Foreigner']:
                            if label in df.index:
                                net = int(df.loc[label, '순매수'])
                                net_eok = round(net / 1e8, 0)
                                fn = STATUS_THRESHOLDS.get('foreign_flow')
                                daily[d_dash]['foreign_flow'] = {
                                    'value':  net_eok,
                                    'note':   f'외국인 {net_eok:+,.0f}억원',
                                    'status': fn(net) if fn else '관망',
                                }
                                break
                except Exception:
                    pass  # 휴일·거래 없는 날 무시
            cur += timedelta(days=1)
            time.sleep(0.12)
    except Exception as e:
        print(f'[백필pykrx] 오류: {e}')

    # 데이터 없는 날짜 제거
    daily = {d: v for d, v in daily.items() if v}
    print(f'[백필] 완료: {len(daily)}일치 데이터')
    return daily
