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
    'mmf_total':   lambda v: _status_below(v, [(7.5,'위험'),(7.8,'경고'),(8.0,'관망')],
                             default='긍정' if v < 8.5 else '최상'),
    'foreign_flow':lambda v: _status_above(v/1e8, [(-5000,'위험'),(-1000,'경고'),(-1,'관망'),(5000,'최상')],
                             default='긍정'),  # v = 억원 단위
    # 새 지표 (높을수록 위험)
    'vix':         lambda v: _status_above(v, [(30,'위험'),(25,'경고'),(20,'관망'),(15,'긍정')]),
    'gold':        lambda v: _status_above(v, [(2700,'위험'),(2550,'경고'),(2400,'관망'),(2200,'긍정')]),
    # 새 지표 (낮을수록 위험)
    'btc':         lambda v: _status_below(v, [(55000,'위험'),(70000,'경고'),(85000,'관망')],
                             default='긍정' if v < 95000 else '최상'),
    'nasdaq':      None,  # MoM 변화율로 계산 — fetch_yahoo_all에서 직접 처리
}


# ── Yahoo Finance ─────────────────────────────────────────────────────

YAHOO_SYMBOLS = {
    'usd_krw': 'KRW=X',    # 1 USD → KRW
    'us10y':   '^TNX',     # 10년 국채 금리 (%)
    'wti':     'CL=F',     # WTI 선물
    'soxx':    'SOXX',     # 필라델피아 반도체
    'btc':     'BTC-USD',  # 비트코인 (위험선호 지표)
    'vix':     '^VIX',     # CBOE 변동성 (공포 지표)
    'nasdaq':  '^IXIC',    # 나스닥 종합
    'gold':    'GC=F',     # 금 선물
}

def _yahoo_latest(symbol: str) -> float | None:
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
    params = {'interval': '1d', 'range': '5d'}
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; StockBot/1.0)'}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=12)
        r.raise_for_status()
        closes = r.json()['chart']['result'][0]['indicators']['quote'][0]['close']
        for v in reversed(closes):
            if v is not None:
                return round(v, 4)
    except Exception as e:
        print(f'[Yahoo] {symbol} 오류: {e}')
    return None


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
    """나스닥 MoM 변화율 기반 상태"""
    if len(closes) < 2:
        return (closes[-1] if closes else 0), '관망'
    current = closes[-1]
    month_ago = closes[0]
    pct = (current - month_ago) / month_ago * 100
    if pct >= 5:    status = '최상'
    elif pct >= 2:  status = '긍정'
    elif pct >= 0:  status = '관망'
    elif pct >= -3: status = '경고'
    else:           status = '위험'
    return round(current, 0), status


def fetch_yahoo_all() -> dict:
    result = {}
    for key, sym in YAHOO_SYMBOLS.items():
        if key == 'nasdaq':
            closes = _yahoo_history(sym, '1mo')
            if not closes:
                result[key] = {}
                continue
            v, status = _nasdaq_status(closes)
            month_ago = closes[0]
            pct = (v - month_ago) / month_ago * 100
            result[key] = {
                'value':  v,
                'status': status,
                'note':   f'나스닥 {v:,.0f} (1개월 {pct:+.1f}%)',
            }
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
        status_fn = STATUS_THRESHOLDS.get(key)
        note = ''
        if key == 'btc':
            note = f'BTC ${v:,.0f}'
        elif key == 'vix':
            note = f'VIX {v:.1f} ({"극도공포" if v>=30 else "공포" if v>=20 else "중립" if v>=15 else "낮음"})'
        elif key == 'gold':
            note = f'금 ${v:,.1f}/oz'
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

    # 일반 FRED 시리즈
    for key, series in FRED_SERIES.items():
        v = _fred_latest(series, api_key)
        if v is None:
            print(f'[FRED] {key}({series}) 값 없음')
            result[key] = {}
            continue
        v = round(v, 2)
        status_fn = STATUS_THRESHOLDS.get(key)
        result[key] = {
            'value':  v,
            'status': status_fn(v) if status_fn else None,
            'note':   '',
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
    """KOSPI 외국인 당일 순매수 (억원)"""
    try:
        from pykrx import stock
        today = datetime.now()
        # 장 마감 전이면 전일 데이터
        for delta in range(0, 5):
            d = (today - timedelta(days=delta)).strftime('%Y%m%d')
            try:
                df = stock.get_market_trading_value_by_investor(d, d, 'KOSPI')
                if df.empty:
                    continue
                # 외국인 행 찾기
                for label in ['외국인합계', '외국인', 'Foreigner']:
                    if label in df.index:
                        net_won = int(df.loc[label, '순매수'])
                        net_eok = round(net_won / 1e8, 0)
                        status = STATUS_THRESHOLDS['foreign_flow'](net_won)
                        return {
                            'value':  net_eok,
                            'status': status,
                            'note':   f'KOSPI 외국인 순매수 {net_eok:+,.0f}억원 ({d})',
                        }
            except Exception:
                continue
    except Exception as e:
        print(f'[pykrx] 외국인 수급 오류: {e}')
    return {}


# ── Claude 정성 지표 분석 ──────────────────────────────────────────────

QUALITATIVE_KEYS = ['tariff', 'commercial_law', 'fund_flow', 'semiconductor', 'ria', 'msci', 'tga_mmf_status']

QUALITATIVE_PROMPTS = {
    'tariff':         '미국 트럼프 행정부의 대한국/글로벌 관세 정책 현황과 한국 증시 위험도',
    'commercial_law': '한국 상법 개정(자사주 소각 의무화 등) 현황과 증시 영향',
    'fund_flow':      '한국 증시 자금 흐름: 부동산 규제 반사이익, 정책 펀드 자금 유입',
    'semiconductor':  '삼성전자·SK하이닉스 실적 전망 및 반도체 섹터 모멘텀',
    'ria':            'RIA(해외주식 복귀계좌) 양도세 감면 정책 현황과 자금 유입 효과',
    'msci':           'MSCI 선진국 지수 편입 추진 현황',
    'tga_mmf_status': '미국 TGA 잔고 추이와 MMF 유동성 방출 전망',
}

# 뉴스 수집 쿼리 (Google News RSS)
_NEWS_QUERIES = [
    ('트럼프 관세 한국 증시', 'tariff'),
    ('코스피 외국인 매매 수급', 'foreign'),
    ('한국 상법 개정 자사주', 'commercial_law'),
    ('삼성전자 SK하이닉스 실적', 'semiconductor'),
    ('MMF TGA 미국 유동성', 'liquidity'),
]


def _fetch_news_context() -> str:
    """Google News RSS에서 최근 뉴스 헤드라인 수집 — Claude 분석 컨텍스트용"""
    from xml.etree import ElementTree as ET
    headlines = []
    for query, tag in _NEWS_QUERIES:
        try:
            url = 'https://news.google.com/rss/search'
            params = {'q': query, 'hl': 'ko', 'gl': 'KR', 'ceid': 'KR:ko'}
            r = requests.get(url, params=params,
                             headers={'User-Agent': 'Mozilla/5.0 StockBot/1.0'},
                             timeout=8)
            if not r.ok:
                continue
            root = ET.fromstring(r.content)
            for item in root.findall('.//item')[:3]:
                title = item.find('title')
                pub   = item.find('pubDate')
                if title is not None and title.text:
                    date_part = (pub.text or '')[:16] if pub is not None else ''
                    headlines.append(f'[{tag}] {title.text[:120]} ({date_part})')
        except Exception as e:
            print(f'[뉴스] {query} 오류: {e}')
        time.sleep(0.15)
    return '\n'.join(headlines) if headlines else '(뉴스 수집 실패)'


def fetch_qualitative(claude_api_key: str, existing: dict) -> dict:
    """
    Claude로 정성 지표 상태 분석 + 시나리오 확률 계산.
    Google News 헤드라인을 컨텍스트로 제공하여 최신 분위기 반영.
    existing = 기존 저장된 상태값 (폴백용)
    반환: {QUALITATIVE_KEY: {status, note}, ..., 'scenarios': [...]}
    """
    if not claude_api_key:
        return {k: existing.get(k, {}) for k in QUALITATIVE_KEYS}

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=claude_api_key)
    except Exception as e:
        print(f'[Claude] 클라이언트 생성 실패: {e}')
        return {k: existing.get(k, {}) for k in QUALITATIVE_KEYS}

    today = datetime.now().strftime('%Y년 %m월 %d일')
    print('[Claude] 뉴스 헤드라인 수집 중...')
    news_ctx = _fetch_news_context()

    items_text = '\n'.join(f'- {k}: {v}' for k, v in QUALITATIVE_PROMPTS.items())

    prompt = f"""오늘({today}) 최신 뉴스 헤드라인:
{news_ctx}

위 뉴스를 참고해 아래 항목들의 한국 증시 영향도를 평가하고,
다음 시나리오의 현재 확률도 추정하세요.

평가 항목:
{items_text}

시나리오:
- 베이스(리스크온 리바운드): 4월말~6월 TGA/MMF 유동성 방출 + 관세 완화
- 강세(V자 급반등): 3~5월 전쟁 리스크 빠른 해소 + SOXX 급반등
- 약세(상승 늦림): 관세/전쟁 장기화, 크레딧 스프레드 확대, 4~8월 횡보

JSON 형식으로만 답하세요:
{{
  "tariff":         {{"status": "위험|경고|관망|긍정|최상", "note": "한 줄 요약(뉴스 기반)"}},
  "commercial_law": {{"status": "...", "note": "..."}},
  "fund_flow":      {{"status": "...", "note": "..."}},
  "semiconductor":  {{"status": "...", "note": "..."}},
  "ria":            {{"status": "...", "note": "..."}},
  "msci":           {{"status": "...", "note": "..."}},
  "tga_mmf_status": {{"status": "...", "note": "..."}},
  "scenarios": [
    {{"name": "베이스: 4월말~6월 리스크온 리바운드", "prob": 50, "desc": "핵심 근거 한 줄"}},
    {{"name": "강세: 3~5월 V자 급반등",              "prob": 30, "desc": "핵심 근거 한 줄"}},
    {{"name": "약세: 4~8월 상승 늦림",               "prob": 20, "desc": "핵심 근거 한 줄"}}
  ]
}}

status는 반드시 위험/경고/관망/긍정/최상 중 하나. scenarios의 prob 합계는 100. JSON만 출력."""

    try:
        import json, re
        resp = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=900,
            messages=[{'role': 'user', 'content': prompt}],
        )
        raw = resp.content[0].text
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            parsed = json.loads(m.group())
            result = {k: parsed.get(k, existing.get(k, {})) for k in QUALITATIVE_KEYS}
            if 'scenarios' in parsed:
                result['scenarios'] = parsed['scenarios']
            return result
    except Exception as e:
        print(f'[Claude] 정성 지표 분석 오류: {e}')

    return {k: existing.get(k, {}) for k in QUALITATIVE_KEYS}


# ── 통합 수집 ─────────────────────────────────────────────────────────

def fetch_all(fred_api_key: str = '', claude_api_key: str = '', existing: dict = None) -> dict:
    """
    모든 지표 자동 수집.
    existing: 기존 DB 저장값 (정성 지표 폴백 + 메모 유지용)
    """
    existing = existing or {}
    print('[지표] Yahoo Finance 수집 중...')
    data = fetch_yahoo_all()

    print('[지표] 외국인 수급 수집 중...')
    ff = fetch_foreign_flow()
    if ff:
        data['foreign_flow'] = ff

    print('[지표] Fear & Greed 수집 중...')
    fg = fetch_fear_greed()
    if fg:
        data['fear_greed'] = fg

    if fred_api_key:
        print('[지표] FRED 수집 중...')
        data.update(fetch_fred_all(fred_api_key))
    else:
        print('[지표] FRED_API_KEY 없음 — HY/RRP/TGA/10Y-2Y/MMF 건너뜀')

    print('[지표] 정성 지표 분석 중...')
    qualitative = fetch_qualitative(claude_api_key, existing)
    for k, v in qualitative.items():
        if not v:
            continue
        if k == 'scenarios':
            data['scenarios'] = v   # Claude가 계산한 시나리오 확률
            continue
        old_note = existing.get(k, {}).get('note', '')
        data[k] = {**v, 'note': v.get('note') or old_note}

    # Claude가 시나리오를 못 돌려줬으면 기존 것 유지
    if 'scenarios' not in data and 'scenarios' in existing:
        data['scenarios'] = existing['scenarios']

    # F&G 내부 히스토리 데이터는 저장 대상 아님 — 제거
    if 'fear_greed' in data:
        data['fear_greed'].pop('_historical', None)

    # 기존 값에서 note 보정 (수동 메모 유지)
    for k in list(data.keys()):
        if k == 'scenarios':
            continue
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
