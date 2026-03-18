"""
calendar_result_fetcher.py
경제 캘린더 발표 결과 수집 + Claude 분석

흐름:
  1. 캘린더에서 최근 N일 이내 발생한 이벤트 목록 가져오기
  2. FRED API로 실제 발표값 조회 (주요 미국 지표)
  3. Claude API로 한국 투자자 관점 분석 텍스트 생성
  4. DB 저장
"""

import time
from datetime import date, datetime, timedelta

import requests

# ── FRED 시리즈 매핑 ────────────────────────────────────────────────────
# (series_id, 한국어 레이블, 처리방식: level/mom/diff)
FRED_SERIES_MAP: dict[str, list[tuple[str, str, str]]] = {
    'CPI':     [('CPIAUCSL',        'CPI 전월비',             'mom'),
                ('CPILFESL',        '코어CPI 전월비',          'mom')],
    'PPI':     [('PPIFIS',          'PPI 최종수요 전월비',      'mom')],
    'PCE':     [('PCEPILFE',        '코어PCE 전월비',          'mom'),
                ('PCE',             'PCE 전월비',              'mom')],
    'NFP':     [('PAYEMS',          '비농업고용 변화(천)',       'diff'),
                ('UNRATE',          '실업률(%)',               'level')],
    'FOMC':    [('DFEDTARU',        '연준 기준금리 상단(%)',    'level'),
                ('DFEDTARL',        '연준 기준금리 하단(%)',    'level')],
    'GDP':     [('A191RL1Q225SBEA', 'GDP 성장률(QoQ%)',        'level')],
    'JOLTS':   [('JTSJOL',         '구인건수(천)',              'level')],
    'RETAIL':  [('RSXFS',          '소매판매 전월비',           'mom')],
    'ISM_MFG': [('MANEMP',         'ISM 제조업 PMI (근사)',    'level')],
}

_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"


def _fred_obs(series_id: str, api_key: str, obs_start: str,
              limit: int = 3) -> list[dict]:
    """FRED에서 obs_start 이후 최근 limit개 관측치 반환."""
    try:
        r = requests.get(
            _FRED_BASE,
            params={
                'series_id':        series_id,
                'api_key':          api_key,
                'observation_start': obs_start,
                'sort_order':       'desc',
                'limit':            limit,
                'file_type':        'json',
            },
            timeout=12,
        )
        if not r.ok:
            return []
        return [o for o in r.json().get('observations', [])
                if o.get('value') not in ('.', None, '')]
    except Exception:
        return []


def _format_value(obs: list[dict], mode: str) -> str | None:
    """관측치 리스트를 mode에 따라 포맷."""
    if not obs:
        return None
    try:
        v0 = float(obs[0]['value'])
        if mode == 'level':
            return f"{v0:.4g}"
        if mode == 'mom' and len(obs) >= 2:
            v1 = float(obs[1]['value'])
            if v1 != 0:
                return f"{(v0 - v1) / abs(v1) * 100:+.2f}%"
        if mode == 'diff' and len(obs) >= 2:
            v1 = float(obs[1]['value'])
            return f"{(v0 - v1):+.0f}"
    except Exception:
        pass
    return None


def get_fred_actuals(event_key: str, api_key: str,
                     event_date: str = '') -> dict[str, str]:
    """FRED에서 이벤트 키에 해당하는 최신 지표 수집."""
    cutoff = event_date if event_date else (
        date.today() - timedelta(days=90)
    ).isoformat()
    # 90일 이전부터 검색 (충분한 이전 데이터 포함)
    obs_start = (datetime.strptime(cutoff, '%Y-%m-%d') - timedelta(days=90)).strftime('%Y-%m-%d')

    result: dict[str, str] = {}
    for sid, label, mode in FRED_SERIES_MAP.get(event_key, []):
        obs = _fred_obs(sid, api_key, obs_start, limit=3)
        val = _format_value(obs, mode)
        if val:
            result[label] = val
        time.sleep(0.3)
    return result


def _get_fred_actual_for_event(event_key: str, event_date: str,
                                api_key: str) -> dict | None:
    """
    이벤트 날짜 기준으로 FRED에서 실제 발표값 확인.
    이벤트 날짜 ±7일 이내 관측치가 있으면 actual 확인으로 간주.
    반환: {actual, forecast_note, fred_data} 또는 None
    """
    series_list = FRED_SERIES_MAP.get(event_key, [])
    if not series_list:
        return None

    event_dt  = datetime.strptime(event_date, '%Y-%m-%d')
    obs_start = (event_dt - timedelta(days=30)).strftime('%Y-%m-%d')

    sid, label, mode = series_list[0]
    obs = _fred_obs(sid, api_key, obs_start, limit=3)
    if not obs:
        return None

    # 최신 관측치가 이벤트 날짜 기준 ±7일 이내인지 확인
    latest_dt = datetime.strptime(obs[0]['date'], '%Y-%m-%d')
    if abs((latest_dt - event_dt).days) > 7:
        return None

    actual_val = _format_value(obs, mode)
    if not actual_val:
        return None

    # 추가 시리즈 데이터 수집
    fred_data: dict[str, str] = {label: actual_val}
    for sid2, label2, mode2 in series_list[1:]:
        obs2 = _fred_obs(sid2, api_key, obs_start, limit=3)
        val2 = _format_value(obs2, mode2)
        if val2:
            fred_data[label2] = val2
        time.sleep(0.2)

    # 이전치(previous)는 obs[1] 기반
    prev_val = None
    if len(obs) >= 2:
        prev_obs = [obs[1]] + (obs[2:] if len(obs) > 2 else [])
        prev_val = _format_value(prev_obs, mode)

    return {
        'actual':    actual_val,
        'previous':  prev_val or '',
        'fred_data': fred_data,
    }


def get_recent_events_with_actuals(days_back: int = 2,
                                    fred_api_key: str = '') -> list[dict]:
    """
    최근 N일 이내 발생한 이벤트 중 실제값을 확인할 수 있는 것 반환.
    - Forex Factory actual 우선
    - 없으면 FRED API로 보완
    """
    import calendar_fetcher as _cf

    today  = date.today()
    start  = date(today.year, today.month, 1)
    if today.day <= 7:
        prev  = today.replace(day=1) - timedelta(days=1)
        start = date(prev.year, prev.month, 1)

    all_events = _cf._build_merged(start, today)
    cutoff = today - timedelta(days=days_back)

    results = []
    for ev in all_events:
        ev_date = ev.get('date', '')
        if not ev_date or ev_date < cutoff.isoformat() or ev_date > today.isoformat():
            continue

        # Forex Factory actual 있으면 그대로 사용
        if ev.get('actual'):
            results.append(ev)
            continue

        # FRED에서 실제값 확인
        if fred_api_key and ev.get('key') in FRED_SERIES_MAP:
            fred_result = _get_fred_actual_for_event(ev['key'], ev_date, fred_api_key)
            if fred_result:
                enriched = dict(ev)
                enriched['actual']   = fred_result['actual']
                enriched['previous'] = enriched.get('previous') or fred_result['previous']
                enriched['_fred']    = fred_result['fred_data']
                results.append(enriched)
            time.sleep(0.5)

    return results


def build_analysis_prompt(ev: dict, fred_data: dict | None = None) -> str:
    """Claude에게 전달할 분석 프롬프트 생성."""
    extra = fred_data or ev.get('_fred', {})
    fred_section = ''
    if extra:
        lines = [f"  - {k}: {v}" for k, v in extra.items()]
        fred_section = '\n추가 지표:\n' + '\n'.join(lines)

    return f"""다음 경제지표 발표 결과를 한국 주식 투자자 관점에서 분석해주세요.

이벤트: {ev['title']}
발표일: {ev['date']}
예상치: {ev.get('forecast') or '미제공'}
이전치: {ev.get('previous') or '미제공'}
실제치: {ev.get('actual')}
영향도: {ev.get('impact', 'high').upper()}{fred_section}

다음 형식으로 간결하게 작성하세요 (총 500자 이내, 마크다운 사용):
**1. 결과 요약** — 예상 대비 실제 (서프라이즈 여부, 방향)
**2. 미국 시장** — 주식/채권/달러 단기 방향성
**3. 한국 시장** — 코스피·원달러 영향, 주목 섹터
**4. 핵심 포인트** — 향후 1~2주 가장 중요한 1가지

팩트 중심으로 작성하고 단정적 예측은 피하세요."""


def analyze_event_with_claude(ev: dict, claude_api_key: str,
                               fred_api_key: str = '') -> str:
    """Claude API로 이벤트 분석 텍스트 생성."""
    import anthropic

    prompt = build_analysis_prompt(ev)
    client = anthropic.Anthropic(api_key=claude_api_key)
    msg = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=700,
        messages=[{'role': 'user', 'content': prompt}],
    )
    return msg.content[0].text.strip()


def check_and_analyze_recent_events(claude_api_key: str,
                                     fred_api_key: str = '',
                                     days_back: int = 2) -> list[dict]:
    """
    최근 N일 이내 actual 값이 있고 DB에 분석이 없는 이벤트를 분석.
    분석 완료된 이벤트 목록 반환.
    """
    import database

    events = get_recent_events_with_actuals(
        days_back=days_back, fred_api_key=fred_api_key
    )
    analyzed = []

    for ev in events:
        event_key  = ev.get('key', '')
        event_date = ev.get('date', '')
        if not event_key or not event_date:
            continue

        existing = database.get_calendar_analysis(event_key, event_date)
        if existing:
            continue

        print(f"  📊 분석 중: {ev['title']} ({event_date})")
        try:
            analysis = analyze_event_with_claude(ev, claude_api_key, fred_api_key)
            database.save_calendar_analysis(
                event_key  = event_key,
                event_date = event_date,
                title      = ev.get('title', ''),
                forecast   = ev.get('forecast', ''),
                previous   = ev.get('previous', ''),
                actual     = ev.get('actual', ''),
                analysis   = analysis,
            )
            analyzed.append({**ev, 'analysis': analysis})
            print(f"  ✅ 저장 완료: {ev['title']}")
        except Exception as e:
            print(f"  ⚠️  분석 실패 ({ev['title']}): {e}")
        time.sleep(1)

    return analyzed
