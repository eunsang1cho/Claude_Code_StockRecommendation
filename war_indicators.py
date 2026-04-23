"""
war_indicators.py  v2.1  —  전쟁 위기 스코어 시스템
=======================================================
그룹 구성 (가중치)
  [A] 에너지  35%  : Brent/WTI 5단계 트리거, OVX, Brent-WTI 스프레드
  [B] 해운    35%  : STNG/FRO/DHT 탱커 ETF (운임·보험 proxy), XLE
  [C] 군사    30%  : ITA 방산 ETF, 금(안전자산), 공격 뉴스 강도(감쇄)
전쟁 위기 스코어 0-100 + 5단계 전쟁 단계 모델
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests

# ── 유가 5단계 트리거 ──────────────────────────────────────────────────────
OIL_TRIGGERS = [
    (200, 5, '글로벌 위기',   '위험'),
    (160, 4, '봉쇄 공포',    '위험'),
    (130, 3, '호르무즈 리스크','경고'),
    (110, 2, '군사 리스크',   '경고'),
    (95,  1, '긴장 시작',    '관망'),
]

def _oil_level(price: float) -> tuple[int, str, str]:
    """(level 1-5, label, status)"""
    for threshold, lvl, label, status in OIL_TRIGGERS:
        if price >= threshold:
            return lvl, label, status
    return 0, '안정', '최상' if price < 75 else ('긍정' if price < 85 else '관망')

# ── 상태 → 점수 매핑 ────────────────────────────────────────────────────────
STATUS_SCORE = {'최상': 0, '긍정': 15, '관망': 35, '경고': 65, '위험': 90}

def _s(d: dict, key: str) -> int:
    return STATUS_SCORE.get(d.get(key, {}).get('status', '관망'), 35)

# ── Yahoo Finance 심볼 ────────────────────────────────────────────────────────
WAR_YAHOO = {
    'brent': ('BZ=F',  '브렌트 원유'),
    'wti':   ('CL=F',  'WTI 원유'),
    'ovx':   ('^OVX',  '원유 변동성 OVX'),
    'stng':  ('STNG',  'Scorpio Tankers'),
    'fro':   ('FRO',   'Frontline 탱커'),
    'dht':   ('DHT',   'DHT 탱커'),
    'xle':   ('XLE',   'S&P 에너지 섹터 ETF'),
    'ita':   ('ITA',   '미국 방산 ETF'),
    'gold':  ('GC=F',  '금 선물'),
    'sblk':  ('SBLK',  'Star Bulk — BDI 대리지표 (건화물 운임)'),
}

# ── 뉴스 쿼리 (카테고리별) ────────────────────────────────────────────────────
_ATTACK_QUERIES = [
    ('Iran US military airstrike attack',           'iran'),
    ('Iran drone missile attack warship',           'iran'),
    ('Strait Hormuz Iran mine tanker seized',       'hormuz'),
    ('Hormuz blockade Iran oil shipping',           'hormuz'),
    ('B2 bomber Iran US strike military deployment','usmil'),
    ('US carrier strike group Iran Middle East',   'usmil'),
    ('war risk marine insurance premium tanker',   'insurance'),
    ('Russia Qatar China Iran ceasefire mediate',  'diplomacy'),
    ('Strategic Petroleum Reserve SPR IEA release','spr'),
]

# ── 후티/홍해 전용 쿼리 ───────────────────────────────────────────────────────
_HOUTHI_QUERIES = [
    ('Houthi attack Red Sea ship missile drone',         'houthi'),
    ('Houthi Yemen Red Sea tanker cargo vessel',         'houthi'),
    ('Red Sea shipping reroute Cape Good Hope insurance','redsea'),
    ('Bab el-Mandeb Houthi blockade disruption',        'redsea'),
    ('Iran Houthi weapons supply proxy war',             'iran_proxy'),
]

# ── 이란 핵 전용 쿼리 ────────────────────────────────────────────────────────
_IRAN_NUCLEAR_QUERIES = [
    ('Iran nuclear enrichment IAEA 60 percent uranium',  'nuclear'),
    ('Iran nuclear breakout bomb centrifuge',            'nuclear'),
    ('Trump Iran nuclear ultimatum deadline attack',     'nuclear_threat'),
    ('Iran nuclear deal negotiations diplomacy talks',   'nuclear_diplomacy'),
    ('IAEA Iran inspection nuclear facility',            'nuclear'),
]

# 이란 핵 키워드 가중치
_IRAN_NUCLEAR_WEIGHTS = {
    'weapons grade': 10, '90 percent': 10, 'nuclear warhead': 10,
    'breakout': 8,  'bomb':8,
    'ultimatum': 7, 'deadline': 6, 'strike':6,
    'centrifuge': 5, 'enrichment': 4, 'iaea': 3,
    'deal': -2, 'talks': -2, 'negotiations': -2,  # 외교 진전 감점
}

# ── 상태 평가 함수 ────────────────────────────────────────────────────────────

def _spread_status(spread: float) -> str:
    if spread >= 12: return '위험'
    if spread >= 8:  return '경고'
    if spread >= 5:  return '관망'
    if spread >= 3:  return '긍정'
    return '최상'

def _ovx_status(v: float) -> str:
    # OVX는 % 단위 (정상 20~35). Yahoo 간혹 100배 스케일 오류 → 자동 보정
    if v > 150:
        v = v / 100.0
    if v >= 55: return '위험'
    if v >= 40: return '경고'
    if v >= 28: return '관망'
    if v >= 18: return '긍정'
    return '최상'

def _tanker_status(pct: float) -> str:
    """탱커 ETF 1개월 상승 = 운임·보험 급등 신호"""
    if pct >= 30:  return '위험'
    if pct >= 15:  return '경고'
    if pct >= 3:   return '관망'
    if pct >= -5:  return '긍정'
    return '최상'

def _ita_status(pct: float) -> str:
    if pct >= 15:  return '위험'
    if pct >= 7:   return '경고'
    if pct >= 2:   return '관망'
    if pct >= -3:  return '긍정'
    return '최상'

def _gold_status(v: float) -> str:
    if v >= 3400: return '위험'
    if v >= 3100: return '경고'
    if v >= 2800: return '관망'
    if v >= 2400: return '긍정'
    return '최상'

def _xle_status(pct: float) -> str:
    """에너지 섹터 상승 = 유가 불안 반영"""
    if pct >= 15:  return '위험'
    if pct >= 7:   return '경고'
    if pct >= 1:   return '관망'
    if pct >= -5:  return '긍정'
    return '최상'

def _sblk_status(pct: float) -> str:
    """SBLK (건화물 운임 BDI 대리) — 글로벌 해운 스트레스"""
    if pct >= 40:  return '위험'
    if pct >= 20:  return '경고'
    if pct >= 5:   return '관망'
    if pct >= -10: return '긍정'
    return '최상'

def _houthi_status(score: float) -> str:
    if score >= 60: return '위험'
    if score >= 30: return '경고'
    if score >= 12: return '관망'
    if score >= 4:  return '긍정'
    return '최상'

def _iran_nuclear_status(score: float) -> str:
    if score >= 50: return '위험'
    if score >= 25: return '경고'
    if score >= 10: return '관망'
    if score >= 3:  return '긍정'
    return '최상'

# 뉴스 강도 키워드 가중치 (단순 빈도 카운트 보정)
_NEWS_INTENSITY_WEIGHTS = {
    'blockade':   5,   # 봉쇄 선언 — 최고 위험
    'seized':     4,   # 선박 나포
    'airstrike':  4,   # 공습
    'attack':     3,   # 공격
    'missile':    3,   # 미사일
    'warning':    2,   # 경고
    'deployment': 2,   # 전력 전개
    'carrier':    2,   # 항모
    'threat':     1.5, # 위협
    'tension':    1,   # 긴장
}

def _calc_news_intensity(count: int, title: str = '') -> float:
    """뉴스 개수 × 강도 가중치. 제목 키워드로 보정."""
    base = float(count)
    title_lower = title.lower()
    multiplier = 1.0
    for kw, w in _NEWS_INTENSITY_WEIGHTS.items():
        if kw in title_lower:
            multiplier = max(multiplier, w)
    return base * multiplier

def _attack_status(weighted_score: float) -> str:
    """강도 가중 점수 기준 상태 판정 (단순 빈도 대비 임계값 ×2 완화)"""
    if weighted_score >= 80:  return '위험'
    if weighted_score >= 40:  return '경고'
    if weighted_score >= 16:  return '관망'
    if weighted_score >= 6:   return '긍정'
    return '최상'

# ── Yahoo Finance 헬퍼 ────────────────────────────────────────────────────────

_YF_HEADERS = {'User-Agent': 'Mozilla/5.0 StockBot/2.0'}

def _yahoo_latest(symbol: str) -> float | None:
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
    try:
        r = requests.get(url, params={'interval': '1d', 'range': '5d'},
                         headers=_YF_HEADERS, timeout=12)
        r.raise_for_status()
        closes = r.json()['chart']['result'][0]['indicators']['quote'][0]['close']
        for v in reversed(closes):
            if v is not None:
                return round(float(v), 2)
    except Exception as e:
        print(f'[War/Yahoo] {symbol}: {e}')
    return None

def _yahoo_history(symbol: str, range_: str = '1mo') -> list[float]:
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
    try:
        r = requests.get(url, params={'interval': '1d', 'range': range_},
                         headers=_YF_HEADERS, timeout=12)
        r.raise_for_status()
        closes = r.json()['chart']['result'][0]['indicators']['quote'][0]['close']
        return [float(v) for v in closes if v is not None]
    except Exception as e:
        print(f'[War/Yahoo hist] {symbol}: {e}')
    return []

# ── [A+B+C] 프록시 지표 수집 ─────────────────────────────────────────────────

def fetch_war_proxy() -> dict:
    result: dict = {}

    # ── 브렌트 원유 (5단계 트리거)
    brent = _yahoo_latest('BZ=F')
    time.sleep(0.3)
    if brent:
        lvl, label, status = _oil_level(brent)
        result['brent'] = {
            'value':         brent,
            'status':        status,
            'trigger_level': lvl,
            'trigger_label': label,
            'note': f'브렌트 ${brent:.1f}/bbl — 레벨{lvl} "{label}" (임계: $95경고 $110리스크 $130봉쇄 $160위기)',
        }

    # ── WTI 원유 (5단계 트리거)
    wti = _yahoo_latest('CL=F')
    time.sleep(0.3)
    if wti:
        lvl, label, status = _oil_level(wti)
        result['wti'] = {
            'value':         wti,
            'status':        status,
            'trigger_level': lvl,
            'trigger_label': label,
            'note': f'WTI ${wti:.1f}/bbl — 레벨{lvl} "{label}"',
        }

    # ── 브렌트-WTI 스프레드 (지정학 프리미엄)
    if brent and wti:
        spread = round(brent - wti, 2)
        result['brent_wti_spread'] = {
            'value':  spread,
            'status': _spread_status(spread),
            'note':   f'지정학 프리미엄 ${spread:.2f} (정상 ~$3, 전쟁 위기 >$8)',
        }

    # ── OVX 원유 변동성 (정상 20~35 — 100배 스케일 자동 보정)
    ovx_raw = _yahoo_latest('^OVX')
    time.sleep(0.3)
    if ovx_raw:
        ovx = ovx_raw / 100.0 if ovx_raw > 150 else ovx_raw
        ovx = round(ovx, 1)
        result['ovx'] = {
            'value':  ovx,
            'status': _ovx_status(ovx),
            'note':   f'원유 변동성 OVX {ovx:.1f} (정상 <28, 공포 >55)',
        }

    # ── 탱커 ETF 3종 (1개월 변화율 — 운임·보험 proxy)
    for key, sym, label in [('stng','STNG','Scorpio Tankers'),
                             ('fro', 'FRO', 'Frontline'),
                             ('dht', 'DHT', 'DHT Holdings')]:
        closes = _yahoo_history(sym, '1mo')
        time.sleep(0.3)
        if len(closes) >= 2:
            cur, mon = closes[-1], closes[0]
            pct = round((cur - mon) / mon * 100, 1)
            result[key] = {
                'value':    round(cur, 2),
                'pct_1m':   pct,
                'status':   _tanker_status(pct),
                'note':     f'{label} ${cur:.2f} (1개월 {pct:+.1f}%) — 탱커 운임/보험 proxy',
            }

    # ── XLE 에너지 섹터 ETF
    closes = _yahoo_history('XLE', '1mo')
    time.sleep(0.3)
    if len(closes) >= 2:
        cur, mon = closes[-1], closes[0]
        pct = round((cur - mon) / mon * 100, 1)
        result['xle'] = {
            'value':  round(cur, 2),
            'pct_1m': pct,
            'status': _xle_status(pct),
            'note':   f'S&P 에너지 섹터 ${cur:.2f} (1개월 {pct:+.1f}%) — 유가 불안 반영',
        }

    # ── ITA 방산 ETF
    closes = _yahoo_history('ITA', '1mo')
    time.sleep(0.3)
    if len(closes) >= 2:
        cur, mon = closes[-1], closes[0]
        pct = round((cur - mon) / mon * 100, 1)
        result['ita'] = {
            'value':  round(cur, 2),
            'pct_1m': pct,
            'status': _ita_status(pct),
            'note':   f'방산 ETF ITA ${cur:.2f} (1개월 {pct:+.1f}%) — 전쟁 기대 수혜 지수',
        }

    # ── 금 (안전자산 수요)
    gold = _yahoo_latest('GC=F')
    time.sleep(0.3)
    if gold:
        result['gold'] = {
            'value':  round(gold, 1),
            'status': _gold_status(gold),
            'note':   f'금 ${gold:,.1f}/oz — 안전자산 수요',
        }

    # ── SBLK 건화물 운임 (BDI 대리지표)
    closes = _yahoo_history('SBLK', '1mo')
    time.sleep(0.3)
    if len(closes) >= 2:
        cur, mon = closes[-1], closes[0]
        pct = round((cur - mon) / mon * 100, 1)
        result['sblk'] = {
            'value':  round(cur, 2),
            'pct_1m': pct,
            'status': _sblk_status(pct),
            'note':   f'Star Bulk ${cur:.2f} (1개월 {pct:+.1f}%) — BDI 건화물 운임 대리 지표',
        }

    return result

# ── [D] 뉴스 공격 빈도 ────────────────────────────────────────────────────────

def fetch_attack_counts(days: int = 7) -> dict:
    since     = datetime.utcnow() - timedelta(days=days)
    today_str = datetime.now().strftime('%Y-%m-%d')
    daily     = defaultdict(lambda: defaultdict(int))
    daily_w   = defaultdict(float)   # 강도 가중 점수
    hourly    = defaultdict(int)
    cats      = set()
    intensity_total = 0.0

    for query, cat in _ATTACK_QUERIES:
        cats.add(cat)
        try:
            r = requests.get('https://news.google.com/rss/search',
                             params={'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'},
                             headers={'User-Agent': 'Mozilla/5.0 StockBot/2.0'},
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
                title_el = item.find('title')
                title = title_el.text if title_el is not None else ''
                # 강도 가중치 계산
                weight = 1.0
                for kw, w in _NEWS_INTENSITY_WEIGHTS.items():
                    if kw in title.lower():
                        weight = max(weight, w)
                daily[ds][cat]     += 1
                daily[ds]['total'] += 1
                daily_w[ds]        += weight
                intensity_total    += weight
                if ds == today_str:
                    hourly[dt.strftime('%H')] += 1
        except Exception as e:
            print(f'[War/공격뉴스] {query}: {e}')
        time.sleep(0.15)

    sorted_daily = {
        d: {c: daily[d].get(c, 0) for c in list(cats) + ['total']}
        for d in sorted(daily.keys())
    }
    total_7d = {c: sum(daily[d].get(c, 0) for d in daily) for c in cats}
    total_7d['total'] = sum(total_7d.values())

    return {
        'daily':           sorted_daily,
        'hourly':          {h: hourly[h] for h in sorted(hourly.keys())},
        'total_7d':        total_7d,
        'intensity_score': round(intensity_total, 1),   # 강도 가중 점수
        'status':          _attack_status(intensity_total),
    }

# ── [D-1] 후티/홍해 공격 빈도 ────────────────────────────────────────────────

def fetch_houthi_redsea(days: int = 7) -> dict:
    """후티 공격 + 홍해 해운 혼란 뉴스 빈도 수집."""
    since = datetime.utcnow() - timedelta(days=days)
    total_score = 0.0
    counts = {'houthi': 0, 'redsea': 0, 'iran_proxy': 0}
    headlines = []

    _HOUTHI_WEIGHTS = {
        'attack': 3, 'missile': 3, 'drone': 2, 'strike': 3,
        'seized': 4, 'blockade': 5, 'sunk': 5,
        'reroute': 2, 'insurance': 2, 'disruption': 2,
    }

    for query, cat in _HOUTHI_QUERIES:
        try:
            r = requests.get('https://news.google.com/rss/search',
                             params={'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'},
                             headers={'User-Agent': 'Mozilla/5.0 StockBot/2.0'}, timeout=8)
            if not r.ok:
                continue
            root = ET.fromstring(r.content)
            for item in root.findall('.//item')[:15]:
                pub = item.find('pubDate')
                if pub is None or not pub.text:
                    continue
                try:
                    dt = parsedate_to_datetime(pub.text).replace(tzinfo=None)
                except Exception:
                    continue
                if dt < since:
                    continue
                title_el = item.find('title')
                title = title_el.text if title_el is not None else ''
                weight = 1.0
                for kw, w in _HOUTHI_WEIGHTS.items():
                    if kw in title.lower():
                        weight = max(weight, w)
                total_score += weight
                counts[cat] = counts.get(cat, 0) + 1
                if len(headlines) < 5:
                    headlines.append(title[:80])
        except Exception as e:
            print(f'[후티뉴스] {query}: {e}')
        time.sleep(0.15)

    status = _houthi_status(total_score)
    return {
        'score':     round(total_score, 1),
        'status':    status,
        'counts':    counts,
        'headlines': headlines,
        'note': f'7일 후티/홍해 뉴스 강도 {total_score:.0f} ({status}) — houthi:{counts.get("houthi",0)} redsea:{counts.get("redsea",0)}',
    }


# ── [D-2] 이란 핵 위험도 ─────────────────────────────────────────────────────

def fetch_iran_nuclear(days: int = 7) -> dict:
    """이란 핵 프로그램 + 협상/위협 뉴스 스코어."""
    since = datetime.utcnow() - timedelta(days=days)
    total_score = 0.0
    counts: dict[str, int] = {}
    headlines = []

    for query, cat in _IRAN_NUCLEAR_QUERIES:
        try:
            r = requests.get('https://news.google.com/rss/search',
                             params={'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'},
                             headers={'User-Agent': 'Mozilla/5.0 StockBot/2.0'}, timeout=8)
            if not r.ok:
                continue
            root = ET.fromstring(r.content)
            for item in root.findall('.//item')[:15]:
                pub = item.find('pubDate')
                if pub is None or not pub.text:
                    continue
                try:
                    dt = parsedate_to_datetime(pub.text).replace(tzinfo=None)
                except Exception:
                    continue
                if dt < since:
                    continue
                title_el = item.find('title')
                title = (title_el.text or '').lower()
                weight = 1.0
                for kw, w in _IRAN_NUCLEAR_WEIGHTS.items():
                    if kw in title:
                        weight += w
                weight = max(0.0, weight)
                total_score += weight
                counts[cat] = counts.get(cat, 0) + 1
                if len(headlines) < 5:
                    full = item.find('title')
                    headlines.append((full.text or '')[:80])
        except Exception as e:
            print(f'[이란핵뉴스] {query}: {e}')
        time.sleep(0.15)

    status = _iran_nuclear_status(total_score)
    return {
        'score':     round(total_score, 1),
        'status':    status,
        'counts':    counts,
        'headlines': headlines,
        'note': f'7일 이란핵 뉴스 강도 {total_score:.0f} ({status}) — nuclear:{counts.get("nuclear",0)} threat:{counts.get("nuclear_threat",0)}',
    }




# ── [E] 전쟁 위기 스코어 계산 ──────────────────────────────────────────────────

def compute_war_score(proxy: dict, attacks: dict,
                      houthi: dict = None, iran_nuclear: dict = None) -> dict:
    """
    0-100 전쟁 위기 스코어 + 5단계 전쟁 단계 모델
    그룹별 가중치: 에너지 28% / 해운 27% / 군사 25% / 이란·후티 20%
    """
    def ps(key): return _s(proxy, key)

    # ── 에너지 그룹 (28%)
    energy_scores = [ps('brent'), ps('wti'), ps('ovx'), ps('brent_wti_spread')]
    energy_avg = sum(energy_scores) / len(energy_scores) if energy_scores else 35

    # ── 해운 그룹 (27%) — 탱커 + 에너지 섹터 + 건화물(SBLK)
    shipping_scores = [ps('stng'), ps('fro'), ps('dht'), ps('xle')]
    if proxy.get('sblk'):
        shipping_scores.append(ps('sblk'))
    shipping_avg = sum(shipping_scores) / len(shipping_scores) if shipping_scores else 35

    # ── 군사 그룹 (25%) — ITA 방산, 금, 기존 뉴스 강도(감쇄)
    attack_score = STATUS_SCORE.get(attacks.get('status', '관망'), 35)
    military_scores = [
        ps('ita'), ps('gold'),
        attack_score * 0.6,   # 뉴스 과대반영 방지 40% 감쇄
    ]
    military_avg = sum(military_scores) / len(military_scores) if military_scores else 35

    # ── 이란·후티 그룹 (20%) — 신규
    iran_houthi_scores = []
    if houthi:
        iran_houthi_scores.append(STATUS_SCORE.get(houthi.get('status', '관망'), 35))
    if iran_nuclear:
        iran_houthi_scores.append(STATUS_SCORE.get(iran_nuclear.get('status', '관망'), 35))
    iran_houthi_avg = sum(iran_houthi_scores) / len(iran_houthi_scores) if iran_houthi_scores else 35

    # ── 가중 평균
    total = (energy_avg      * 0.28 +
             shipping_avg    * 0.27 +
             military_avg    * 0.25 +
             iran_houthi_avg * 0.20)
    score = round(min(100, max(0, total)))

    # ── 5단계 전쟁 단계 모델
    if score <= 20:
        stage, stage_label = 1, '외교 긴장'
    elif score <= 40:
        stage, stage_label = 2, '군사 충돌'
    elif score <= 60:
        stage, stage_label = 3, '해협 위협'
    elif score <= 78:
        stage, stage_label = 4, '물류 충격'
    else:
        stage, stage_label = 5, '글로벌 위기'

    # ── 종합 status
    if score >= 70:   overall = '위험'
    elif score >= 50: overall = '경고'
    elif score >= 30: overall = '관망'
    elif score >= 15: overall = '긍정'
    else:             overall = '최상'

    return {
        'score':       score,
        'overall':     overall,
        'stage':       stage,
        'stage_label': stage_label,
        'groups': {
            'energy':      round(energy_avg),
            'shipping':    round(shipping_avg),
            'military':    round(military_avg),
            'iran_houthi': round(iran_houthi_avg),
        },
    }


# ── EIA 전략 비축유 (SPR) ───────────────────────────────────────────────────

def fetch_spr_eia(eia_api_key: str = '') -> dict:
    """EIA API에서 미국 전략비축유(SPR) 최신 재고 수집.
    반환: {level_mb: float, prev_mb: float, change_mb: float, status: str}
    """
    if not eia_api_key:
        return {}
    try:
        url = (
            'https://api.eia.gov/v2/petroleum/stoc/wstk/data/'
            '?api_key=' + eia_api_key +
            '&frequency=weekly&data[0]=value'
            '&facets[product][]=WEPSRW'          # SPR total
            '&sort[0][column]=period&sort[0][direction]=desc'
            '&offset=0&length=2'
        )
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        rows = r.json().get('response', {}).get('data', [])
        if len(rows) < 1:
            return {}
        cur  = float(rows[0]['value'])           # 백만 배럴
        prev = float(rows[1]['value']) if len(rows) >= 2 else cur
        chg  = round(cur - prev, 2)
        # 정상 SPR ~370Mb, 2022 방출 후 ~350Mb → 경계치
        if cur < 300:
            status = '위험'
        elif cur < 340:
            status = '경고'
        elif cur < 370:
            status = '관망'
        else:
            status = '정상'
        return {
            'level_mb': round(cur, 1),
            'prev_mb':  round(prev, 1),
            'change_mb': chg,
            'status':   status,
        }
    except Exception as e:
        print(f'[SPR] EIA 수집 실패: {e}')
        return {}


# ── 통합 수집 ──────────────────────────────────────────────────────────────────

def fetch_all_war(claude_api_key: str = '', existing: dict = None,
                  eia_api_key: str = '') -> dict:
    print('[전쟁지표] ① Yahoo 프록시 지표 수집 (SBLK 포함)...')
    proxy = fetch_war_proxy()

    print('[전쟁지표] ② 뉴스 공격 빈도 수집...')
    attacks = fetch_attack_counts(days=7)

    print('[전쟁지표] ③ 후티/홍해 뉴스 수집...')
    houthi = fetch_houthi_redsea(days=7)

    print('[전쟁지표] ④ 이란 핵 뉴스 수집...')
    iran_nuclear = fetch_iran_nuclear(days=7)

    spr = {}
    if eia_api_key:
        print('[전쟁지표] ⑤ EIA SPR 비축유 수집...')
        spr = fetch_spr_eia(eia_api_key)

    print('[전쟁지표] ⑥ 전쟁 위기 스코어 계산...')
    war_score = compute_war_score(proxy, attacks, houthi, iran_nuclear)

    print('[전쟁지표] ⑦ IranWarLive OSINT 수집...')
    iran_war = fetch_iran_war_live()

    return {
        'proxy':        proxy,
        'attacks':      attacks,
        'houthi':       houthi,
        'iran_nuclear': iran_nuclear,
        'spr':          spr,
        'war_score':    war_score,
        'iran_war':     iran_war,
        'updated':      datetime.now().strftime('%Y-%m-%d %H:%M'),
    }


# ── IranWarLive OSINT ────────────────────────────────────────────────────

_IRAN_SHEET = (
    'https://docs.google.com/spreadsheets/d/e/'
    '2PACX-1vSyinXiL-Ur469RUBFbu19pDta2jcrmPkJPBdPzlIlENpK_-DInxKtkM_PdxhUzG0ei0-yHhc9aqPRI'
    '/pub?single=true&output=csv&gid={gid}'
)
_GID_EVENTS    = '0'
_GID_MILITARY  = '2133098001'
_GID_DIPLOM    = '1935573357'
_GID_AIRSPACE  = '1498621766'


def _fetch_sheet_csv(gid: str) -> list[dict]:
    """Google Sheets CSV 다운로드 → list[dict]."""
    import csv, io
    url = _IRAN_SHEET.format(gid=gid)
    try:
        r = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.text))
        return [row for row in reader]
    except Exception as e:
        print(f'[IranWarLive] Sheet gid={gid} 오류: {e}')
        return []


def fetch_iran_war_live() -> dict:
    """IranWarLive OSINT 데이터 수집 및 DB 저장.
    반환: summary dict (최근 이벤트 요약, 병력 현황, 영공 상태)
    """
    import database as _db
    today = datetime.now().strftime('%Y-%m-%d')

    # ── 1. 이벤트 (공습/요격 등) ──
    raw_events = _fetch_sheet_csv(_GID_EVENTS)
    events = []
    for row in raw_events:
        try:
            lat = float(row.get('Latitude') or 0) or None
            lon = float(row.get('Longitude') or 0) or None
            cas = int(row.get('Casualties') or 0)
        except Exception:
            lat = lon = None
            cas = 0
        events.append({
            'event_id':   row.get('Event_ID', ''),
            'timestamp':  row.get('Timestamp', ''),
            'lat':        lat,
            'lon':        lon,
            'strike_type': row.get('Strike_Type', ''),
            'target_desc': row.get('Target_Description', ''),
            'source_url':  row.get('Source_URL', ''),
            'verified_by': row.get('Verified_By', ''),
            'casualties':  cas,
            'context':     row.get('Escalation_Context', ''),
        })
    if events:
        _db.upsert_iran_war_events(events)

    # ── 2. 병력 현황 ──
    raw_mil = _fetch_sheet_csv(_GID_MILITARY)
    military = []
    for row in raw_mil:
        try:
            military.append({
                'country':          row.get('Country', ''),
                'alliance':         row.get('Alliance', ''),
                'est_troops':       row.get('Est_Troops', ''),
                'est_aircraft':     row.get('Est_Aircraft', ''),
                'military_deaths':  int(row.get('Military_Deaths') or 0),
                'civilian_deaths':  int(row.get('Civilian_Deaths') or 0),
                'status':           row.get('Status', ''),
            })
        except Exception:
            pass
    if military:
        _db.save_iran_war_military(today, military)

    # ── 3. 영공 현황 ──
    raw_air = _fetch_sheet_csv(_GID_AIRSPACE)
    airspace = []
    for row in raw_air:
        airspace.append({
            'timestamp': row.get('Timestamp', ''),
            'country':   row.get('Country', ''),
            'status':    row.get('Status', ''),
            'source':    row.get('Source_URL', ''),
        })
    if airspace:
        _db.save_iran_war_airspace(today, airspace)

    # ── 4. 요약 계산 ──
    total_events     = len(events)
    total_casualties = sum(e['casualties'] for e in events)
    recent_events    = sorted(events, key=lambda x: x['timestamp'], reverse=True)[:5]
    closed_airspace  = [a['country'] for a in airspace if 'Closed' in a.get('status', '')]

    # 서방 vs 이란 사망자
    western = next((m for m in military if m['country'] == 'United States'), {})
    iran_m   = next((m for m in military if m['country'] == 'Iran'), {})

    return {
        'total_events':     total_events,
        'total_casualties': total_casualties,
        'recent_events':    recent_events,
        'military':         military[:10],
        'airspace_closed':  closed_airspace,
        'us_deaths':        western.get('military_deaths', 0),
        'iran_deaths':      iran_m.get('military_deaths', 0),
        'updated':          today,
    }
