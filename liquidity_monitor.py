"""
liquidity_monitor.py
FRED API로 유동성 지표 수집

지표:
  WALCL      - Fed 총자산 (M$ → B$)
  M2SL       - M2 통화량 (B$)
  RRPONTSYD  - 역레포 잔고 (B$)
  WRESBAL    - 은행 지준 (B$)
  WTREGEN    - TGA 잔고 (M$ → B$)

종합 유동성 = WALCL + WRESBAL + M2SL*0.3 − RRPONTSYD − WTREGEN (B$)
M2는 간접 영향(신용 창출)이므로 30% 가중치 적용
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


def fetch_liquidity_historical(api_key: str, periods: int = 13) -> list[dict]:
    """과거 N주 유동성 스냅샷 목록 반환 (WALCL 날짜 기준으로 forward-fill 보간).
    periods: 주 단위 포인트 수 (13 ≈ 3개월)
    반환: [{'date', 'total', 'components'}, ...] 오래된 → 최신 순
    """
    if not api_key:
        return []

    # 충분한 포인트 수집 (버퍼 포함)
    limit_weekly = max(periods + 5, 20)
    limit_daily  = periods * 10  # 일간 시리즈는 더 많이 필요

    walcl_obs   = _fred_obs('WALCL',      api_key, limit_weekly)
    time.sleep(0.3)
    wresbal_obs = _fred_obs('WRESBAL',    api_key, limit_weekly)
    time.sleep(0.3)
    rrp_obs     = _fred_obs('RRPONTSYD',  api_key, limit_daily)
    time.sleep(0.3)
    tga_obs     = _fred_obs('WTREGEN',    api_key, limit_daily)

    if not walcl_obs:
        return []

    def nearest_val(obs_list, target_date):
        """target_date 이하의 가장 최근 값 반환 (forward-fill)."""
        for date, val in obs_list:  # 내림차순 정렬됨
            if date <= target_date:
                return val
        return None

    result = []
    for date, walcl_raw in walcl_obs[:periods]:
        walcl_b   = walcl_raw / 1000
        wresbal_r = nearest_val(wresbal_obs, date)
        rrp       = nearest_val(rrp_obs, date)
        tga_r     = nearest_val(tga_obs, date)

        if None in (wresbal_r, tga_r):
            continue

        rrp       = rrp if rrp is not None else 0.0
        wresbal_b = wresbal_r / 1000
        tga_b     = tga_r / 1000
        total     = round(walcl_b + wresbal_b - rrp - tga_b, 1)

        result.append({
            'date':  date,
            'total': total,
            'components': {
                'walcl':   {'value': round(walcl_b, 1),   'unit': 'B$', 'label': 'Fed 총자산'},
                'wresbal': {'value': round(wresbal_b, 1), 'unit': 'B$', 'label': '은행 지준'},
                'rrp':     {'value': round(rrp, 1),       'unit': 'B$', 'label': '역레포(RRP)'},
                'tga':     {'value': round(tga_b, 1),     'unit': 'B$', 'label': 'TGA 잔고'},
            },
        })

    result.sort(key=lambda x: x['date'])
    return result


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

    # WRESBAL: M$ → B$
    obs = _fred_obs('WRESBAL', api_key, 4)
    if obs:
        v = obs[0][1] / 1000
        prev = obs[1][1] / 1000 if len(obs) > 1 else None
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

    # 종합 유동성 = WALCL + WRESBAL + M2SL*0.3 - RRPONTSYD - WTREGEN (B$)
    # M2는 간접 영향이므로 30% 가중; RRP 없으면 0으로 대체 (FRED API 장애 대응)
    total = total_prev = None
    core = ('walcl', 'wresbal', 'tga')
    if all(k in raw for k in core):
        rrp_val  = raw['rrp']['value']  if 'rrp' in raw else 0.0
        rrp_prev = raw['rrp']['prev']   if 'rrp' in raw else 0.0
        m2_val   = raw['m2sl']['value'] * 0.3 if 'm2sl' in raw else 0.0
        m2_prev  = (raw['m2sl']['prev'] * 0.3
                    if 'm2sl' in raw and raw['m2sl']['prev'] is not None else 0.0)
        total = round(
            raw['walcl']['value'] + raw['wresbal']['value'] + m2_val
            - rrp_val - raw['tga']['value'], 1
        )
        pvs = [raw[k]['prev'] for k in core]
        if all(p is not None for p in pvs):
            total_prev = round(pvs[0] + pvs[1] + m2_prev - (rrp_prev or 0) - pvs[2], 1)

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
