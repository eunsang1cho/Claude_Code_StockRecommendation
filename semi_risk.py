"""
semi_risk.py
반도체 쏠림 취약 리스크 4개 신호 수집

신호별 데이터 소스:
  📉 수요 사이클  — Micron (MU) MoM, yfinance
  🇨🇳 중국 물량   — CQQQ (중국 테크 ETF) MoM + 삼성전자·SK하이닉스 상대 강도
  🇺🇸 수출규제   — news_articles DB 키워드 빈도 (반도체×규제/수출통제/ban)
  ⚔️  지정학     — ITA (방산 ETF) MoM + VIX (이미 daily_indicators에 있음)
"""

import os
import sqlite3
import time
from datetime import datetime, timedelta

import requests

DIR = os.path.dirname(os.path.abspath(__file__))

_RANK = {'최상': 4, '긍정': 3, '관망': 2, '경고': 1, '위험': 0}


def _yahoo_history(symbol: str, range_: str = '6mo') -> list[float]:
    """Yahoo Finance 일봉 종가 리스트 (오래된→최신)"""
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
    params = {'interval': '1d', 'range': range_}
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; StockBot/1.0)'}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=12)
        r.raise_for_status()
        closes = r.json()['chart']['result'][0]['indicators']['quote'][0]['close']
        return [v for v in closes if v is not None]
    except Exception as e:
        print(f'[semi_risk] {symbol} 오류: {e}')
    return []


def _mom_pct(closes: list[float], days: int = 22) -> float | None:
    """closes 리스트에서 N거래일 전 대비 변화율(%)"""
    if len(closes) < 2:
        return None
    idx = max(0, len(closes) - days - 1)
    base = closes[idx]
    if not base:
        return None
    return round((closes[-1] - base) / base * 100, 1)


def _mom_status(pct: float | None, good_above: bool = True) -> str:
    """MoM 변화율 → 상태 문자열.
    good_above=True: 상승이 좋음 (MU, ITA 등)
    good_above=False: 하락이 좋음 (CQQQ — 중국 기술 약세 = 국장 안도)
    """
    if pct is None:
        return '관망'
    if good_above:
        if pct >= 10:    return '최상'
        if pct >= 4:     return '긍정'
        if pct >= 0:     return '관망'
        if pct >= -8:    return '경고'
        return '위험'
    else:  # good_above=False: CQQQ 하락이 위험 (중국 반도체 강세 = 국장 위협)
        if pct >= 15:    return '위험'   # CQQQ 급등 = 중국 기술 부상 = 국장 위협
        if pct >= 8:     return '경고'
        if pct >= -5:    return '관망'
        if pct >= -15:   return '긍정'
        return '최상'


# ── 1. 수요 사이클: Micron (MU) ──────────────────────────────────

def fetch_demand_cycle() -> dict:
    """MU MoM으로 DRAM 수요 사이클 판단."""
    closes = _yahoo_history('MU', '6mo')
    if not closes:
        return {}
    v = round(closes[-1], 2)
    mom_1mo = _mom_pct(closes, 22)
    mom_3mo = _mom_pct(closes, 63)
    status = _mom_status(mom_1mo, good_above=True)

    note_parts = [f'MU ${v:.2f}']
    if mom_1mo is not None:
        note_parts.append(f'1개월 {mom_1mo:+.1f}%')
    if mom_3mo is not None:
        note_parts.append(f'3개월 {mom_3mo:+.1f}%')

    return {
        'value':   v,
        'status':  status,
        'note':    ' | '.join(note_parts),
        'mom_1mo': mom_1mo,
        'mom_3mo': mom_3mo,
        'signal':  'DRAM 수요 둔화 — 메모리 가격 하락 우려' if _RANK[status] <= 1 else
                   'DRAM 수요 견조 — 반도체 수익성 유지',
    }


# ── 2. 중국 물량 압박: CQQQ + 삼성·하이닉스 상대강도 ──────────────

def fetch_china_supply() -> dict:
    """CQQQ MoM (중국 테크 부상) + 삼성·하이닉스 vs SOXX 상대강도."""
    results = {}

    # CQQQ: 중국 테크 ETF
    cqqq_closes = _yahoo_history('CQQQ', '6mo')
    time.sleep(0.3)

    if cqqq_closes:
        cqqq_v   = round(cqqq_closes[-1], 2)
        cqqq_mom = _mom_pct(cqqq_closes, 22)
        # CQQQ 강세 = 중국 기술 부상 = 국장 반도체 위협
        cqqq_st  = _mom_status(cqqq_mom, good_above=False)
        results['cqqq'] = {
            'value':   cqqq_v,
            'status':  cqqq_st,
            'note':    f'CQQQ ${cqqq_v:.2f} (1개월 {cqqq_mom:+.1f}%)' if cqqq_mom else f'CQQQ ${cqqq_v:.2f}',
            'mom_1mo': cqqq_mom,
        }

    # 삼성전자 + SK하이닉스 vs SOXX 상대강도
    sam_closes = _yahoo_history('005930.KS', '6mo')
    time.sleep(0.3)
    hyn_closes = _yahoo_history('000660.KS', '6mo')
    time.sleep(0.3)
    soxx_closes = _yahoo_history('SOXX', '6mo')

    # KOSPI 반도체 대표 2종목 상대 강도 (vs SOXX)
    kr_mom = None
    if sam_closes and hyn_closes:
        sam_mom  = _mom_pct(sam_closes, 22)
        hyn_mom  = _mom_pct(hyn_closes, 22)
        if sam_mom is not None and hyn_mom is not None:
            kr_mom = round((sam_mom + hyn_mom) / 2, 1)

    soxx_mom = _mom_pct(soxx_closes, 22) if soxx_closes else None

    rs = None  # Relative Strength vs SOXX
    if kr_mom is not None and soxx_mom is not None:
        rs = round(kr_mom - soxx_mom, 1)

    kr_status = _mom_status(kr_mom, good_above=True) if kr_mom is not None else '관망'
    results['kr_semi'] = {
        'value':  kr_mom,
        'status': kr_status,
        'note':   (f'삼성+하이닉스 평균 {kr_mom:+.1f}% (vs SOXX {rs:+.1f}p)' if rs is not None
                   else f'삼성+하이닉스 평균 {kr_mom:+.1f}%' if kr_mom is not None
                   else '데이터 없음'),
        'rs_vs_soxx': rs,
        'samsung_mom': _mom_pct(sam_closes, 22) if sam_closes else None,
        'hynix_mom':   _mom_pct(hyn_closes, 22) if hyn_closes else None,
    }

    # 전체 중국물량 리스크 = CQQQ 강세 + 국장 반도체 약세 동시 발생 시 고위험
    cqqq_st  = results.get('cqqq', {}).get('status', '관망')
    kr_st    = results.get('kr_semi', {}).get('status', '관망')
    if _RANK.get(cqqq_st, 2) <= 1 and _RANK.get(kr_st, 2) <= 2:
        overall = '위험'
    elif _RANK.get(cqqq_st, 2) <= 2 or _RANK.get(kr_st, 2) <= 1:
        overall = '경고'
    else:
        overall = '관망'

    return {
        'status':  overall,
        'detail':  results,
        'note':    (f'국장 반도체 {kr_mom:+.1f}% | CQQQ {cqqq_mom:+.1f}%'
                    if kr_mom is not None and cqqq_closes
                    else '데이터 부족'),
        'signal':  ('CQQQ 급등+국장 반도체 약세 — 중국 기술 부상 징후' if overall == '위험' else
                    '중국 기술 압박 주의 관찰 중' if overall == '경고' else
                    '중국 물량 압박 현재 낮음'),
    }


# ── 3. 수출규제 신호: 뉴스 DB 키워드 ──────────────────────────────

def fetch_export_ctrl_signal(days_back: int = 30) -> dict:
    """news_articles DB에서 반도체 수출규제 관련 뉴스 빈도 집계."""
    db_path = os.path.join(DIR, 'stocks.db')
    if not os.path.exists(db_path):
        return {}

    cutoff = (datetime.now() - timedelta(days=days_back)).isoformat()
    try:
        conn = sqlite3.connect(db_path)

        # 수출규제/제재/BIS/엔티티 관련 키워드
        restrict_cnt = conn.execute('''
            SELECT COUNT(*) FROM news_articles
            WHERE (title LIKE "%반도체%" OR title LIKE "%HBM%" OR title LIKE "%DRAM%"
                   OR title LIKE "%semiconductor%" OR title LIKE "%chipmaker%")
              AND (title LIKE "%규제%" OR title LIKE "%제재%" OR title LIKE "%수출통제%"
                   OR title LIKE "%export control%" OR title LIKE "%entity list%"
                   OR title LIKE "%BIS%" OR title LIKE "%ban%")
              AND published_at >= ?
        ''', (cutoff,)).fetchone()[0]

        # 반도체×미중 갈등 키워드
        conflict_cnt = conn.execute('''
            SELECT COUNT(*) FROM news_articles
            WHERE (title LIKE "%반도체%" OR title LIKE "%HBM%")
              AND (title LIKE "%중국%" OR title LIKE "%미중%" OR title LIKE "%china%"
                   OR title LIKE "%trade war%" OR title LIKE "%tariff%")
              AND published_at >= ?
        ''', (cutoff,)).fetchone()[0]

        conn.close()

        total = restrict_cnt + conflict_cnt
        # 30일 기준 임계: 3건↑ 주의, 8건↑ 경고, 15건↑ 위험
        if total >= 15:    status = '위험'
        elif total >= 8:   status = '경고'
        elif total >= 3:   status = '관망'
        else:              status = '긍정'

        return {
            'value':        total,
            'status':       status,
            'note':         f'수출규제 뉴스 {days_back}일: {restrict_cnt}건 | 미중갈등 {conflict_cnt}건',
            'restrict_cnt': restrict_cnt,
            'conflict_cnt': conflict_cnt,
            'signal':       (f'{days_back}일간 관련 뉴스 {total}건 — 모니터링 강화 필요'
                             if total >= 3 else
                             f'{days_back}일간 주요 이슈 없음 ({total}건)'),
        }
    except Exception as e:
        print(f'[export_ctrl] DB 오류: {e}')
        return {}


# ── 4. 지정학: ITA (방산 ETF) + 기존 VIX ───────────────────────

def fetch_geopolitical() -> dict:
    """ITA MoM으로 지정학 긴장도 판단.
    방산 ETF 강세 = 지정학 위기 고조 가능성.
    """
    closes = _yahoo_history('ITA', '6mo')
    if not closes:
        return {}
    v = round(closes[-1], 2)
    mom_1mo = _mom_pct(closes, 22)
    # ITA 강세 = 지정학 위험 고조
    if mom_1mo is None:
        status = '관망'
    elif mom_1mo >= 15:   status = '위험'   # 방산 급등 = 전쟁 리스크 고조
    elif mom_1mo >= 7:    status = '경고'
    elif mom_1mo >= 2:    status = '관망'
    else:                 status = '긍정'   # 방산 하락 = 지정학 안정

    return {
        'value':   v,
        'status':  status,
        'note':    f'ITA(방산) ${v:.2f} (1개월 {mom_1mo:+.1f}%)' if mom_1mo is not None else f'ITA ${v:.2f}',
        'mom_1mo': mom_1mo,
        'signal':  ('방산 ETF 급등 — 지정학 긴장 고조. 반도체 공급망 리스크 주시' if _RANK[status] <= 1 else
                    '방산 ETF 안정 — 지정학 리스크 현재 낮음'),
    }


# ── 통합 수집 ────────────────────────────────────────────────────

def fetch_all_semi_risk() -> dict:
    """4개 반도체 취약 리스크 신호 통합 수집."""
    print('[반도체리스크] 수요사이클 (MU) 수집 중...')
    demand = fetch_demand_cycle()
    time.sleep(0.3)

    print('[반도체리스크] 중국물량 (CQQQ+삼성+하이닉스) 수집 중...')
    china = fetch_china_supply()
    time.sleep(0.3)

    print('[반도체리스크] 수출규제 뉴스 신호 수집 중...')
    export_ctrl = fetch_export_ctrl_signal(days_back=30)

    print('[반도체리스크] 지정학 (ITA) 수집 중...')
    geopolitical = fetch_geopolitical()

    # 전체 리스크 레벨: 4개 중 가장 나쁜 신호 기준
    statuses = [
        demand.get('status', '관망'),
        china.get('status', '관망'),
        export_ctrl.get('status', '관망'),
        geopolitical.get('status', '관망'),
    ]
    worst = min(_RANK.get(s, 2) for s in statuses)
    overall_status = {v: k for k, v in _RANK.items()}.get(worst, '관망')

    return {
        'updated_at':  datetime.now().isoformat(),
        'overall':     overall_status,
        'demand':      demand,
        'china':       china,
        'export_ctrl': export_ctrl,
        'geopolitical':geopolitical,
    }


if __name__ == '__main__':
    import json
    result = fetch_all_semi_risk()
    print(json.dumps(result, ensure_ascii=False, indent=2))
