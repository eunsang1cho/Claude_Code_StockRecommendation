"""
liquidity_monitor.py
FRED API로 유동성 지표 수집

지표:
  WALCL      - Fed 총자산 (M$ → B$)
  M2SL       - M2 통화량 (B$)
  RRPONTSYD  - 역레포 잔고 (B$)
  WRESBAL    - 은행 지준 (B$)
  WTREGEN    - TGA 잔고 (M$ → B$)

종합 유동성 = WALCL + WRESBAL − RRPONTSYD − WTREGEN (B$)
스케줄: 매일 06:00
"""

import time
from datetime import datetime

import requests

_HEADERS = {'User-Agent': 'StockBot/1.0'}
_FRED_BASE = 'https://api.stlouisfed.org/fred/series/observations'


def _fred_obs(series_id: str, api_key: str, limit: int = 10) -> list[tuple[str, float]]:
    """FRED 시리즈 최신 observations. [(date, value), ...] 내림차순 반환."""
    try:
        r = requests.get(_FRED_BASE, params={
            'series_id':  series_id,
            'api_key':    api_key,
            'file_type':  'json',
            'sort_order': 'desc',
            'limit':      limit,
        }, headers=_HEADERS, timeout=12)
        r.raise_for_status()
        js = r.json()
        if 'error_code' in js:
            print(f'[유동성] {series_id}: {js.get("error_message")}')
            return []
        return [
            (o['date'], float(o['value']))
            for o in js.get('observations', [])
            if o['value'] not in ('.', '')
        ]
    except Exception as e:
        print(f'[유동성] {series_id} 오류: {e}')
    return []


def fetch_liquidity(api_key: str) -> dict:
    """유동성 지표 수집 및 종합 유동성 계산."""
    if not api_key:
        return {}

    raw = {}

    # WALCL: M$ → B$
    obs = _fred_obs('WALCL', api_key, 4)
    if obs:
        v = obs[0][1] / 1000
        prev = obs[1][1] / 1000 if len(obs) > 1 else None
        raw['walcl'] = {
            'date': obs[0][0], 'value': round(v, 1),
            'prev': round(prev, 1) if prev is not None else None,
            'unit': 'B$', 'label': 'Fed 총자산',
        }
    time.sleep(0.3)

    # M2SL: B$
    obs = _fred_obs('M2SL', api_key, 4)
    if obs:
        v = obs[0][1]
        prev = obs[1][1] if len(obs) > 1 else None
        raw['m2sl'] = {
            'date': obs[0][0], 'value': round(v, 1),
            'prev': round(prev, 1) if prev is not None else None,
            'unit': 'B$', 'label': 'M2 통화량',
        }
    time.sleep(0.3)

    # RRPONTSYD: B$
    obs = _fred_obs('RRPONTSYD', api_key, 4)
    if obs:
        v = obs[0][1]
        prev = obs[1][1] if len(obs) > 1 else None
        raw['rrp'] = {
            'date': obs[0][0], 'value': round(v, 1),
            'prev': round(prev, 1) if prev is not None else None,
            'unit': 'B$', 'label': '역레포(RRP)',
        }
    time.sleep(0.3)

    # WRESBAL: B$
    obs = _fred_obs('WRESBAL', api_key, 4)
    if obs:
        v = obs[0][1]
        prev = obs[1][1] if len(obs) > 1 else None
        raw['wresbal'] = {
            'date': obs[0][0], 'value': round(v, 1),
            'prev': round(prev, 1) if prev is not None else None,
            'unit': 'B$', 'label': '은행 지준',
        }
    time.sleep(0.3)

    # WTREGEN: M$ → B$
    obs = _fred_obs('WTREGEN', api_key, 4)
    if obs:
        v = obs[0][1] / 1000
        prev = obs[1][1] / 1000 if len(obs) > 1 else None
        raw['tga'] = {
            'date': obs[0][0], 'value': round(v, 1),
            'prev': round(prev, 1) if prev is not None else None,
            'unit': 'B$', 'label': 'TGA 잔고',
        }

    # 종합 유동성 = WALCL + WRESBAL - RRPONTSYD - WTREGEN (B$)
    total = total_prev = None
    needed = ('walcl', 'wresbal', 'rrp', 'tga')
    if all(k in raw for k in needed):
        total = round(
            raw['walcl']['value'] + raw['wresbal']['value']
            - raw['rrp']['value'] - raw['tga']['value'], 1
        )
        pvs = [raw[k]['prev'] for k in needed]
        if all(p is not None for p in pvs):
            total_prev = round(pvs[0] + pvs[1] - pvs[2] - pvs[3], 1)

    direction = None
    if total is not None and total_prev is not None:
        delta = total - total_prev
        if delta > 0:
            direction = '↑ 유동성 공급'
        elif delta < 0:
            direction = '↓ 유동성 회수'
        else:
            direction = '→ 변화 없음'

    return {
        'components': raw,
        'total':      total,
        'total_prev': total_prev,
        'direction':  direction,
        'updated_at': datetime.now().isoformat(timespec='seconds'),
    }
