"""
calendar_result_fetcher.py
경제 캘린더 발표 결과 수집 + Claude 분석

3단계 체크:
  1. 오늘(또는 최근 N일) 이벤트 목록 확인
  2. 이벤트 발표 시간(KST)이 지났는지 확인 (+ 2시간 버퍼)
  3. FRED API에서 실제 결과값 확인 (월별 데이터: 전월 데이터 존재 여부)
"""

import calendar
import time
from datetime import date, datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))

# ── FRED 시리즈 매핑 ────────────────────────────────────────────────────
# (series_id, 한국어 레이블, 처리방식)
# mode: level / mom(전월비 계산) / diff(절대 차이) / fomc(금리 변동 전용)
FRED_SERIES_MAP: dict[str, list[tuple[str, str, str]]] = {
    'CPI':    [('CPIAUCSL',        'CPI 전월비',          'mom'),
               ('CPILFESL',        '코어CPI 전월비',       'mom')],
    'PPI':    [('PPIFIS',          'PPI 최종수요 전월비',   'mom')],
    'PCE':    [('PCEPILFE',        '코어PCE 전월비',       'mom')],
    'NFP':    [('PAYEMS',          '비농업고용 변화(천)',    'diff'),
               ('UNRATE',          '실업률(%)',             'level')],
    'FOMC':   [('DFEDTARU',        '연준 기준금리 상단(%)', 'fomc'),
               ('DFEDTARL',        '연준 기준금리 하단(%)', 'fomc')],
    'GDP':    [('A191RL1Q225SBEA', 'GDP 성장률(QoQ%)',     'level')],
    'JOLTS':  [('JTSJOL',          '구인건수(천)',           'level')],
    'RETAIL': [('RSXFS',           '소매판매 전월비',        'mom')],
}

_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"


# ── 시간 파싱 ──────────────────────────────────────────────────────────

def _parse_event_dt_kst(ev: dict) -> datetime | None:
    """이벤트의 KST 기준 발표 시각 파싱.
    '02:00+1' → 이벤트 날짜 다음날 새벽 2시 KST (FOMC 등)
    '21:30'   → 이벤트 날짜 당일 21:30 KST
    """
    date_str = ev.get('date', '')
    time_str = ev.get('time', '').strip()
    if not date_str:
        return None

    base = datetime.strptime(date_str, '%Y-%m-%d')

    if not time_str:
        return datetime(base.year, base.month, base.day, 23, 59, tzinfo=KST)

    next_day = '+1' in time_str
    t = time_str.replace('+1', '').strip()
    try:
        h, m = (int(x) for x in t.split(':')[:2])
    except ValueError:
        return datetime(base.year, base.month, base.day, 23, 59, tzinfo=KST)

    dt = datetime(base.year, base.month, base.day, h, m, tzinfo=KST)
    if next_day:
        dt += timedelta(days=1)
    return dt


def is_event_time_passed(ev: dict, buffer_hours: int = 2) -> bool:
    """이벤트 발표 시각 + buffer_hours 가 현재 KST 시각보다 이른지 확인."""
    event_dt = _parse_event_dt_kst(ev)
    if not event_dt:
        return False
    return datetime.now(KST) >= event_dt + timedelta(hours=buffer_hours)


# ── FRED 데이터 조회 ───────────────────────────────────────────────────

def _fred_get(series_id: str, api_key: str, **extra) -> list[dict]:
    """FRED API 호출 → 유효한 관측치 리스트 반환."""
    try:
        params = {'series_id': series_id, 'api_key': api_key,
                  'file_type': 'json', **extra}
        r = requests.get(_FRED_BASE, params=params, timeout=12)
        if not r.ok:
            return []
        return [o for o in r.json().get('observations', [])
                if o.get('value') not in ('.', None, '')]
    except Exception:
        return []


def _ref_month(event_date: str) -> tuple[int, int]:
    """이벤트 발표 날짜 → 참조 월 (전월). 반환: (year, month)"""
    dt = datetime.strptime(event_date, '%Y-%m-%d')
    if dt.month == 1:
        return dt.year - 1, 12
    return dt.year, dt.month - 1


def _get_monthly_actual(series_id: str, api_key: str, event_date: str,
                         mode: str) -> str | None:
    """
    월별 FRED 시리즈에서 이벤트 기준 전월 데이터 조회.
    예) 3월 발표 이벤트 → 2월 관측치 (obs date = 2026-02-01)
    """
    ref_y, ref_m = _ref_month(event_date)
    last_day = calendar.monthrange(ref_y, ref_m)[1]
    start = f"{ref_y:04d}-{ref_m:02d}-01"
    end   = f"{ref_y:04d}-{ref_m:02d}-{last_day:02d}"

    obs = _fred_get(series_id, api_key,
                    observation_start=start, observation_end=end)
    if not obs:
        return None

    try:
        v0 = float(obs[0]['value'])
        if mode == 'level':
            return f"{v0:.4g}"

        # mom/diff: 이전 달 관측치도 필요
        prev_y, prev_m = (ref_y - 1, 12) if ref_m == 1 else (ref_y, ref_m - 1)
        prev_last = calendar.monthrange(prev_y, prev_m)[1]
        prev_obs = _fred_get(series_id, api_key,
                             observation_start=f"{prev_y:04d}-{prev_m:02d}-01",
                             observation_end=f"{prev_y:04d}-{prev_m:02d}-{prev_last:02d}")
        if not prev_obs:
            return f"{v0:.4g}"
        v1 = float(prev_obs[0]['value'])

        if mode == 'mom' and v1 != 0:
            return f"{(v0 - v1) / abs(v1) * 100:+.2f}%"
        if mode == 'diff':
            return f"{(v0 - v1):+.0f}"
    except Exception:
        pass
    return None


def _get_fomc_actual(api_key: str, event_date: str) -> str | None:
    """
    FOMC 결과: 이벤트 날짜 다음날 DFEDTARU 값 vs 이전 값 비교.
    - 변화 있으면: '3.50% (↓0.25%p)'
    - 동결이면:    '동결 3.75%'
    - 아직 데이터 없으면: None
    """
    event_dt = datetime.strptime(event_date, '%Y-%m-%d')
    # FOMC 결과는 발표 익일부터 FRED에 반영 (EST 기준)
    day_after   = (event_dt + timedelta(days=1)).strftime('%Y-%m-%d')
    day_before  = (event_dt - timedelta(days=5)).strftime('%Y-%m-%d')

    after_obs  = _fred_get('DFEDTARU', api_key,
                            observation_start=day_after,
                            observation_end=(event_dt + timedelta(days=3)).strftime('%Y-%m-%d'),
                            sort_order='asc', limit=1)
    before_obs = _fred_get('DFEDTARU', api_key,
                            observation_start=day_before,
                            observation_end=event_date,
                            sort_order='desc', limit=1)

    if not before_obs:
        return None

    v_before = float(before_obs[0]['value'])

    if not after_obs:
        # 아직 익일 데이터 없음 → FOMC 결과 미반영
        return None

    v_after = float(after_obs[0]['value'])
    diff = v_after - v_before

    if abs(diff) < 0.001:
        return f"동결 {v_after:.2f}%"
    direction = '↑' if diff > 0 else '↓'
    return f"{v_after:.2f}% ({direction}{abs(diff):.2f}%p)"


# ── 메인 수집 함수 ─────────────────────────────────────────────────────

def _get_actual_for_event(ev: dict, api_key: str) -> dict | None:
    """
    이벤트에 대한 실제 발표값 조회.
    반환: {actual, previous, fred_data} 또는 None
    """
    key        = ev.get('key', '')
    event_date = ev.get('date', '')
    series_list = FRED_SERIES_MAP.get(key, [])

    if not series_list:
        return None

    fred_data: dict[str, str] = {}
    actual_val: str | None = None

    # FOMC 전용 처리
    if key == 'FOMC':
        actual_val = _get_fomc_actual(api_key, event_date)
        if actual_val is None:
            return None  # 아직 FRED에 결과 없음
        fred_data[series_list[0][1]] = actual_val
        # 하단 금리도 추가
        if len(series_list) > 1:
            sid2, label2, _ = series_list[1]
            before_obs = _fred_get(sid2, api_key,
                                   observation_start=(datetime.strptime(event_date, '%Y-%m-%d') - timedelta(days=5)).strftime('%Y-%m-%d'),
                                   observation_end=event_date,
                                   sort_order='desc', limit=1)
            day_after = (datetime.strptime(event_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
            after_obs = _fred_get(sid2, api_key,
                                  observation_start=day_after,
                                  observation_end=(datetime.strptime(event_date, '%Y-%m-%d') + timedelta(days=3)).strftime('%Y-%m-%d'),
                                  sort_order='asc', limit=1)
            if before_obs and after_obs:
                v_b = float(before_obs[0]['value'])
                v_a = float(after_obs[0]['value'])
                diff = v_a - v_b
                if abs(diff) < 0.001:
                    fred_data[label2] = f"동결 {v_a:.2f}%"
                else:
                    d = '↑' if diff > 0 else '↓'
                    fred_data[label2] = f"{v_a:.2f}% ({d}{abs(diff):.2f}%p)"
        return {'actual': actual_val, 'previous': '', 'fred_data': fred_data}

    # 월별 시리즈 (CPI, PPI, NFP 등)
    sid0, label0, mode0 = series_list[0]
    actual_val = _get_monthly_actual(sid0, api_key, event_date, mode0)
    if not actual_val:
        return None  # 전월 데이터 아직 없음

    fred_data[label0] = actual_val

    # 추가 시리즈
    for sid, label, mode in series_list[1:]:
        val = _get_monthly_actual(sid, api_key, event_date, mode)
        if val:
            fred_data[label] = val
        time.sleep(0.25)

    # previous: 이전달 데이터 (ref_month - 1 → ref_month - 2 비교)
    ref_y, ref_m = _ref_month(event_date)
    if ref_m == 1:
        prev2_y, prev2_m = ref_y - 1, 12
    else:
        prev2_y, prev2_m = ref_y, ref_m - 1
    prev2_last = calendar.monthrange(prev2_y, prev2_m)[1]
    if prev2_m == 1:
        prev3_y, prev3_m = prev2_y - 1, 12
    else:
        prev3_y, prev3_m = prev2_y, prev2_m - 1
    prev3_last = calendar.monthrange(prev3_y, prev3_m)[1]

    p2_obs = _fred_get(sid0, api_key,
                       observation_start=f"{prev2_y:04d}-{prev2_m:02d}-01",
                       observation_end=f"{prev2_y:04d}-{prev2_m:02d}-{prev2_last:02d}")
    p3_obs = _fred_get(sid0, api_key,
                       observation_start=f"{prev3_y:04d}-{prev3_m:02d}-01",
                       observation_end=f"{prev3_y:04d}-{prev3_m:02d}-{prev3_last:02d}")

    previous = ''
    if p2_obs and p3_obs:
        try:
            v2 = float(p2_obs[0]['value'])
            v3 = float(p3_obs[0]['value'])
            if mode0 == 'level':
                previous = f"{v2:.4g}"
            elif mode0 == 'mom' and v3 != 0:
                previous = f"{(v2 - v3) / abs(v3) * 100:+.2f}%"
            elif mode0 == 'diff':
                previous = f"{(v2 - v3):+.0f}"
        except Exception:
            pass

    return {'actual': actual_val, 'previous': previous, 'fred_data': fred_data}


def get_events_to_analyze(days_back: int = 30) -> list[dict]:
    """
    분석 대상 이벤트 목록:
    1. 최근 days_back일 이내 이벤트
    2. 발표 시각 + 2시간 이미 지난 것만
    (options/만기일은 결과값 없으므로 제외)
    """
    import calendar_fetcher as _cf

    today  = date.today()
    # days_back 만큼 이전부터 오늘까지
    start_dt = today - timedelta(days=days_back)
    # 시작 월 첫날부터 빌드
    start_month = date(start_dt.year, start_dt.month, 1)

    all_events = _cf._build_merged(start_month, today)

    result = []
    skip_keys = {'OPTIONS_US', 'OPTIONS_KR'}  # 결과값 없는 이벤트

    for ev in all_events:
        if ev.get('key') in skip_keys:
            continue
        ev_date = ev.get('date', '')
        if not ev_date or ev_date < start_dt.isoformat() or ev_date > today.isoformat():
            continue
        # 발표 시간 + 2h 지났는지 체크
        if not is_event_time_passed(ev, buffer_hours=2):
            continue
        result.append(ev)

    return result


def build_analysis_prompt(ev: dict) -> str:
    """웹 검색 기반 뉴스 톤 분석 프롬프트 생성."""
    extra = ev.get('_fred', {})
    fred_section = ''
    if extra:
        lines = [f"  - {k}: {v}" for k, v in extra.items()]
        fred_section = '\n참고 데이터:\n' + '\n'.join(lines)

    title   = ev.get('title', '')
    date    = ev.get('date', '')
    actual  = ev.get('actual', '')
    forecast = ev.get('forecast', '') or '미제공'
    previous = ev.get('previous', '') or '미제공'

    return f"""다음 경제지표 발표에 대해 웹에서 최신 뉴스·반응을 검색한 뒤, 시장 톤 위주로 분석해주세요.

이벤트: {title}
발표일: {date}
예상치: {forecast}
이전치: {previous}
실제치: {actual}{fred_section}

【검색 지시】
- "{title} {date}" 또는 영문 키워드(예: "US PPI February 2026 reaction")로 뉴스 검색
- FOMC라면 점도표·성명서 변화, CPI/PPI라면 핫/쿨 판단, NFP라면 임금·실업률 톤 포함
- 발표 직후 시장(선물·채권·환율) 반응 뉴스 우선 참조

【분석 형식】(총 600자 이내, 한국어)
**1. 뉴스 톤** — 언론·전문가의 전반적 평가 (hawkish/dovish, 우려/안도, 서프라이즈 여부)
**2. 시장 반응** — 미국 주식·채권·달러 실제 반응 (뉴스 기반)
**3. 한국 시장** — 코스피·원달러 영향, 주목 섹터
**4. 핵심 포인트** — 향후 1~2주 투자자가 주시할 1가지"""


def analyze_event_with_claude(ev: dict, claude_api_key: str) -> str:
    """Claude + 웹검색으로 뉴스 톤 기반 이벤트 분석."""
    import anthropic

    prompt = build_analysis_prompt(ev)
    client = anthropic.Anthropic(api_key=claude_api_key)

    msg = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=1024,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{'role': 'user', 'content': prompt}],
    )

    # 텍스트 블록만 추출 (tool_use / tool_result 블록 제외)
    texts = [
        block.text for block in msg.content
        if hasattr(block, 'text') and block.text
    ]
    return '\n'.join(texts).strip()


def check_and_store_actuals(fred_api_key: str, days_back: int = 3) -> list[dict]:
    """
    스케줄러용: FRED 실제값만 DB 저장 (Claude 분석 없음).
    이미 actual 값이 있는 레코드는 건너뜀.
    """
    import database

    candidates = get_events_to_analyze(days_back=days_back)
    if not candidates:
        return []

    stored = []
    for ev in candidates:
        event_key  = ev.get('key', '')
        event_date = ev.get('date', '')

        # FRED 매핑 없는 이벤트는 스킵
        if event_key not in FRED_SERIES_MAP:
            continue

        # 이미 actual 있으면 스킵
        existing = database.get_calendar_analysis(event_key, event_date)
        if existing and existing.get('actual'):
            continue

        if not fred_api_key:
            continue

        try:
            result = _get_actual_for_event(ev, fred_api_key)
            if not result:
                print(f"  [캘린더] FRED 데이터 없음: {ev.get('title')} ({event_date})")
                continue

            database.save_calendar_actual(
                event_key=event_key,
                event_date=event_date,
                title=ev.get('title', ''),
                forecast=ev.get('forecast', ''),
                previous=result.get('previous', ''),
                actual=result['actual'],
            )
            stored.append({**ev, 'actual': result['actual']})
            print(f"  ✅ FRED 저장: {ev.get('title')} actual={result['actual']}")
        except Exception as e:
            print(f"  ⚠️  FRED 조회 실패 ({ev.get('title')}): {e}")
        time.sleep(0.5)

    return stored


def check_and_analyze_recent_events(claude_api_key: str,
                                     fred_api_key: str = '',
                                     days_back: int = 3) -> list[dict]:
    """
    3단계 체크 후 미분석 이벤트를 Claude로 분석.
    1. 최근 days_back일 이벤트 목록
    2. 발표 시각 + 2h 지났는지 확인
    3. FRED에서 실제 결과값 확인
    """
    import database

    # Step 1 + 2: 시간 지난 이벤트 목록
    candidates = get_events_to_analyze(days_back=days_back)
    print(f"  [캘린더] 체크 대상: {len(candidates)}개")

    analyzed = []

    for ev in candidates:
        event_key  = ev.get('key', '')
        event_date = ev.get('date', '')

        # 이미 분석된 건 스킵
        if database.get_calendar_analysis(event_key, event_date):
            continue

        # Step 3: 실제 결과값 확인
        actual = ev.get('actual', '')  # FF actual (있으면 우선)
        fred_result = None

        if not actual and fred_api_key:
            fred_result = _get_actual_for_event(ev, fred_api_key)
            if fred_result:
                actual = fred_result['actual']
                ev = {**ev,
                      'actual':   actual,
                      'previous': ev.get('previous') or fred_result.get('previous', ''),
                      '_fred':    fred_result.get('fred_data', {})}
            time.sleep(0.5)

        if not actual:
            print(f"  [캘린더] 결과 미확인: {ev['title']} ({event_date})")
            continue

        print(f"  [캘린더] 분석 시작: {ev['title']} ({event_date}) actual={actual}")
        try:
            analysis = analyze_event_with_claude(ev, claude_api_key)
            database.save_calendar_analysis(
                event_key  = event_key,
                event_date = event_date,
                title      = ev.get('title', ''),
                forecast   = ev.get('forecast', ''),
                previous   = ev.get('previous', ''),
                actual     = actual,
                analysis   = analysis,
            )
            analyzed.append({**ev, 'analysis': analysis})
            print(f"  ✅ 저장: {ev['title']}")
        except Exception as e:
            print(f"  ⚠️  분석 실패 ({ev['title']}): {e}")
        time.sleep(1)

    return analyzed
