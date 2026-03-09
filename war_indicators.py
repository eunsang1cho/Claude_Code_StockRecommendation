"""
war_indicators.py
US-이란 전쟁 특집 지표 수집

지표 구성:
  [정량-Yahoo]   브렌트-WTI 스프레드, 탱커 ETF(STNG·FRO·DHT), 원유 변동성(OVX), 방산 ETF(ITA)
  [정량-뉴스]   Google News RSS → 드론/선박 공격 기사 수 (날짜별·시간대별)
  [정성-Claude] 공격 강도, 홍해, 호르무즈, 배 전쟁보험, 확전, 핵 프로그램, 미군 대응, 유가 공급
"""

import json
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests

# ── Yahoo Finance 프록시 지표 ────────────────────────────────────────────

WAR_YAHOO = {
    'brent':   ('BZ=F',  '브렌트 원유'),
    'wti':     ('CL=F',  'WTI 원유'),
    'ovx':     ('^OVX',  '원유 변동성 지수'),
    'stng':    ('STNG',  'Scorpio Tankers'),
    'fro':     ('FRO',   'Frontline 탱커'),
    'dht':     ('DHT',   'DHT 원유 탱커'),
    'ita':     ('ITA',   '미국 방산 ETF'),
    'gold':    ('GC=F',  '금 선물'),
}

# ── 공격 빈도 뉴스 쿼리 ──────────────────────────────────────────────────

_ATTACK_QUERIES = [
    ('Iran drone missile attack',          'iran'),
    ('Iran US military strike warship',    'iran'),
    ('Houthi ship attack Red Sea',         'houthi'),
    ('Red Sea shipping attack tanker',     'houthi'),
    ('Strait Hormuz Iran seized',          'hormuz'),
    ('war risk marine insurance premium',  'insurance'),
]

# ── 상태 평가 함수 ───────────────────────────────────────────────────────

def _brent_wti_status(spread: float) -> str:
    if spread >= 12: return '위험'
    if spread >= 8:  return '경고'
    if spread >= 5:  return '관망'
    if spread >= 3:  return '긍정'
    return '최상'

def _ovx_status(v: float) -> str:
    if v >= 50: return '위험'
    if v >= 35: return '경고'
    if v >= 25: return '관망'
    if v >= 15: return '긍정'
    return '최상'

def _tanker_mom_status(pct: float) -> str:
    if pct >= 25:  return '위험'
    if pct >= 10:  return '경고'
    if pct >= 0:   return '관망'
    if pct >= -10: return '긍정'
    return '최상'

def _ita_mom_status(pct: float) -> str:
    """방산 ETF 급등 = 전쟁 위험 고조"""
    if pct >= 15:  return '위험'
    if pct >= 7:   return '경고'
    if pct >= 2:   return '관망'
    if pct >= -3:  return '긍정'
    return '최상'

def _attack_count_status(cnt: int) -> str:
    if cnt >= 40: return '위험'
    if cnt >= 20: return '경고'
    if cnt >= 8:  return '관망'
    if cnt >= 3:  return '긍정'
    return '최상'

# ── Yahoo Finance 수집 헬퍼 ─────────────────────────────────────────────

def _yahoo_latest(symbol: str) -> float | None:
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
    try:
        r = requests.get(url, params={'interval': '1d', 'range': '5d'},
                         headers={'User-Agent': 'Mozilla/5.0 StockBot/1.0'}, timeout=12)
        r.raise_for_status()
        closes = r.json()['chart']['result'][0]['indicators']['quote'][0]['close']
        for v in reversed(closes):
            if v is not None:
                return round(v, 2)
    except Exception as e:
        print(f'[War/Yahoo] {symbol}: {e}')
    return None

def _yahoo_history(symbol: str, range_: str = '1mo') -> list[float]:
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
    try:
        r = requests.get(url, params={'interval': '1d', 'range': range_},
                         headers={'User-Agent': 'Mozilla/5.0 StockBot/1.0'}, timeout=12)
        r.raise_for_status()
        closes = r.json()['chart']['result'][0]['indicators']['quote'][0]['close']
        return [v for v in closes if v is not None]
    except Exception as e:
        print(f'[War/Yahoo history] {symbol}: {e}')
    return []

# ── 프록시 지표 수집 ────────────────────────────────────────────────────

def fetch_war_proxy() -> dict:
    result = {}

    # 브렌트-WTI 스프레드
    brent = _yahoo_latest('BZ=F')
    wti   = _yahoo_latest('CL=F')
    time.sleep(0.3)
    if brent and wti:
        spread = round(brent - wti, 2)
        result['brent_wti_spread'] = {
            'value':  spread,
            'status': _brent_wti_status(spread),
            'note':   f'브렌트 ${brent:.1f} / WTI ${wti:.1f} → 지정학 프리미엄 ${spread:.2f} (정상 ~$3)',
        }
    else:
        result['brent_wti_spread'] = {}

    # OVX (원유 변동성)
    ovx = _yahoo_latest('^OVX')
    time.sleep(0.3)
    if ovx:
        result['ovx'] = {
            'value':  round(ovx, 1),
            'status': _ovx_status(ovx),
            'note':   f'원유 변동성 OVX {ovx:.1f} (정상 <25, 공포 >50)',
        }
    else:
        result['ovx'] = {}

    # 탱커 ETF (1개월 변화율)
    for key, sym, label in [('stng', 'STNG', 'Scorpio Tankers'),
                             ('fro',  'FRO',  'Frontline'),
                             ('dht',  'DHT',  'DHT Holdings')]:
        closes = _yahoo_history(sym, '1mo')
        time.sleep(0.3)
        if len(closes) >= 2:
            cur = closes[-1]; mon = closes[0]
            pct = round((cur - mon) / mon * 100, 1)
            result[key] = {
                'value':  round(cur, 2),
                'status': _tanker_mom_status(pct),
                'note':   f'{label} ${cur:.2f} (1개월 {pct:+.1f}%) — 탱커 운임 프록시',
            }
        else:
            result[key] = {}

    # 방산 ETF ITA (1개월)
    closes = _yahoo_history('ITA', '1mo')
    time.sleep(0.3)
    if len(closes) >= 2:
        cur = closes[-1]; mon = closes[0]
        pct = round((cur - mon) / mon * 100, 1)
        result['ita'] = {
            'value':  round(cur, 2),
            'status': _ita_mom_status(pct),
            'note':   f'방산 ETF ITA ${cur:.2f} (1개월 {pct:+.1f}%) — 전쟁 수혜 지수',
        }
    else:
        result['ita'] = {}

    # 금 (안전자산 수요)
    gold = _yahoo_latest('GC=F')
    time.sleep(0.3)
    if gold:
        result['gold'] = {
            'value':  round(gold, 1),
            'status': ('위험' if gold >= 3200 else '경고' if gold >= 2900 else
                       '관망' if gold >= 2600 else '긍정' if gold >= 2300 else '최상'),
            'note':   f'금 ${gold:,.1f}/oz — 안전자산 수요 지표',
        }
    else:
        result['gold'] = {}

    return result

# ── 공격 빈도 수집 (뉴스 기사 수) ───────────────────────────────────────

def fetch_attack_counts(days: int = 7) -> dict:
    """
    Google News RSS에서 드론/선박 공격 관련 기사를 수집해
    날짜별·시간대별로 집계.
    반환:
      daily    : {날짜: {iran, houthi, hormuz, insurance, total}}
      hourly   : {HH: n}  — 오늘 기사만
      total_7d : {iran, houthi, hormuz, total}
      status   : str
    """
    since     = datetime.utcnow() - timedelta(days=days)
    today_str = datetime.now().strftime('%Y-%m-%d')
    daily     = defaultdict(lambda: defaultdict(int))
    hourly    = defaultdict(int)

    for query, cat in _ATTACK_QUERIES:
        try:
            r = requests.get('https://news.google.com/rss/search',
                             params={'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'},
                             headers={'User-Agent': 'Mozilla/5.0 StockBot/1.0'},
                             timeout=8)
            if not r.ok:
                continue
            root = ET.fromstring(r.content)
            for item in root.findall('.//item')[:20]:
                pub = item.find('pubDate')
                if pub is None or not pub.text:
                    continue
                try:
                    dt = parsedate_to_datetime(pub.text).replace(tzinfo=None)
                except Exception:
                    continue
                if dt < since:
                    continue
                ds = dt.strftime('%Y-%m-%d')
                daily[ds][cat]    += 1
                daily[ds]['total'] += 1
                if ds == today_str:
                    hourly[dt.strftime('%H')] += 1
        except Exception as e:
            print(f'[War/공격뉴스] {query}: {e}')
        time.sleep(0.15)

    sorted_daily = {
        d: {'iran':   daily[d].get('iran', 0),
            'houthi': daily[d].get('houthi', 0),
            'hormuz': daily[d].get('hormuz', 0),
            'insurance': daily[d].get('insurance', 0),
            'total':  daily[d].get('total', 0)}
        for d in sorted(daily.keys())
    }
    total_7d = {
        'iran':    sum(v.get('iran', 0)   for v in daily.values()),
        'houthi':  sum(v.get('houthi', 0) for v in daily.values()),
        'hormuz':  sum(v.get('hormuz', 0) for v in daily.values()),
        'total':   sum(v.get('total', 0)  for v in daily.values()),
    }
    return {
        'daily':    sorted_daily,
        'hourly':   {h: hourly[h] for h in sorted(hourly.keys())},
        'total_7d': total_7d,
        'status':   _attack_count_status(total_7d['total']),
    }

# ── Claude 정성 분석 ─────────────────────────────────────────────────────

WAR_QUALITATIVE_KEYS = [
    'iran_attack_intensity',  # 드론/미사일 공격 강도
    'red_sea_risk',           # 홍해 통항 위험
    'hormuz_risk',            # 호르무즈 해협 위험
    'ship_war_insurance',     # 배 전쟁보험료 수준
    'escalation_risk',        # 전면전 확전 위험
    'iran_nuclear',           # 이란 핵 프로그램 진전
    'us_military_response',   # 미군 대응 강도
    'oil_supply_risk',        # 유가 공급 차질 위험
]

def _fetch_war_news_headlines() -> str:
    queries = [
        ('Iran US attack drone missile today',      'iran'),
        ('Houthi ship Red Sea attack today',        'houthi'),
        ('Hormuz Iran warship tanker seized',       'hormuz'),
        ('marine war risk insurance premium surge', 'insurance'),
        ('Iran nuclear enrichment IAEA',            'nuclear'),
        ('US carrier strike group Iran deployment', 'usmil'),
    ]
    lines = []
    for query, tag in queries:
        try:
            r = requests.get('https://news.google.com/rss/search',
                             params={'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'},
                             headers={'User-Agent': 'Mozilla/5.0 StockBot/1.0'},
                             timeout=8)
            if not r.ok:
                continue
            root = ET.fromstring(r.content)
            for item in root.findall('.//item')[:3]:
                t   = item.find('title')
                pub = item.find('pubDate')
                if t is not None and t.text:
                    dp = (pub.text or '')[:16] if pub is not None else ''
                    lines.append(f'[{tag}] {t.text[:130]} ({dp})')
        except Exception as e:
            print(f'[War/뉴스] {query}: {e}')
        time.sleep(0.15)
    return '\n'.join(lines) if lines else '(뉴스 수집 실패)'

def fetch_war_qualitative(claude_api_key: str, existing: dict) -> dict:
    if not claude_api_key:
        return {k: existing.get(k, {}) for k in WAR_QUALITATIVE_KEYS}
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=claude_api_key)
    except Exception as e:
        print(f'[War/Claude] 클라이언트 실패: {e}')
        return {k: existing.get(k, {}) for k in WAR_QUALITATIVE_KEYS}

    today    = datetime.now().strftime('%Y년 %m월 %d일')
    news_ctx = _fetch_war_news_headlines()

    prompt = f"""오늘({today}) 미국-이란 전쟁 관련 최신 뉴스:
{news_ctx}

위 뉴스를 참고해 아래 8개 전쟁 위험 지표를 평가하세요.
각 항목의 status는 위험/경고/관망/긍정/최상 중 하나, note는 뉴스 근거 포함 한 줄 요약.

- iran_attack_intensity : 이란·후티 드론/미사일 공격 강도 (최근 24~48시간)
- red_sea_risk          : 홍해 상선 통항 위험 (운항 중단 수준)
- hormuz_risk           : 호르무즈 해협 봉쇄/충돌 위험
- ship_war_insurance    : 중동 항로 배 전쟁보험료 수준 (정상=최상, 급등=위험)
- escalation_risk       : 미국-이란 전면전 확전 가능성
- iran_nuclear          : 이란 핵 프로그램 진전 및 IAEA 긴장
- us_military_response  : 미군 항모·타격 자산 대응 수준
- oil_supply_risk       : 호르무즈 통과 원유 공급 차질 가능성

JSON만 출력:
{{
  "iran_attack_intensity": {{"status":"...", "note":"..."}},
  "red_sea_risk":          {{"status":"...", "note":"..."}},
  "hormuz_risk":           {{"status":"...", "note":"..."}},
  "ship_war_insurance":    {{"status":"...", "note":"..."}},
  "escalation_risk":       {{"status":"...", "note":"..."}},
  "iran_nuclear":          {{"status":"...", "note":"..."}},
  "us_military_response":  {{"status":"...", "note":"..."}},
  "oil_supply_risk":       {{"status":"...", "note":"..."}}
}}"""

    try:
        resp = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=800,
            messages=[{'role': 'user', 'content': prompt}],
        )
        raw = resp.content[0].text
        m   = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            parsed = json.loads(m.group())
            return {k: parsed.get(k, existing.get(k, {})) for k in WAR_QUALITATIVE_KEYS}
    except Exception as e:
        print(f'[War/Claude] 정성 분석 오류: {e}')
    return {k: existing.get(k, {}) for k in WAR_QUALITATIVE_KEYS}

# ── 통합 수집 ─────────────────────────────────────────────────────────────

def fetch_all_war(claude_api_key: str = '', existing: dict = None) -> dict:
    existing = existing or {}
    print('[전쟁지표] ① Yahoo 프록시 지표 수집...')
    proxy = fetch_war_proxy()
    print('[전쟁지표] ② 공격 빈도 뉴스 수집...')
    attacks = fetch_attack_counts(days=7)
    print('[전쟁지표] ③ Claude 정성 분석...')
    qual = fetch_war_qualitative(claude_api_key, existing.get('qual', {}))
    return {
        'proxy':   proxy,
        'attacks': attacks,
        'qual':    qual,
        'updated': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }
