"""
calendar_fetcher.py
경제 캘린더 수집 모듈

데이터 소스:
  1. Forex Factory JSON API (thismonth/nextmonth, 무료·키 불필요)
  2. 하드코딩된 FOMC·BOK 2026 연간 일정
  3. 알고리즘 계산: 미국 옵션만기(3번째 금요일), KOSPI200 옵션만기(2번째 목요일)
  4. 근사 계산: NFP(매월 첫 금요일), CPI(익월 2번째 수요일)
"""

import json
import time
from datetime import date, datetime, timedelta
import requests

# ── 이벤트별 주목 포인트 ─────────────────────────────────────────────────

EVENT_META: dict[str, dict] = {
    "FOMC": {
        "icon": "🏦", "country": "US",
        "summary": "연준 기준금리 결정",
        "focus": "성명문 어조(매파↑/비둘기↓), 분기 점도표, 파월 기자회견. 금리 변화폭보다 미래 경로(forward guidance)·중립금리 추정치가 더 중요",
        "impact": "high",
    },
    "CPI": {
        "icon": "📊", "country": "US",
        "summary": "미국 소비자물가",
        "focus": "코어CPI(에너지·식품 제외) YoY가 핵심. 예상 대비 ±0.1%p에도 채권·달러 급변. 주거비 제외 슈퍼코어(서비스물가) 추세 확인",
        "impact": "high",
    },
    "NFP": {
        "icon": "👷", "country": "US",
        "summary": "미국 비농업 고용",
        "focus": "고용 수(예상 ±50K 기준), 실업률(U-3), 시간당임금 YoY 3개 동시 확인. '골디락스' = 고용 견조 + 임금 안정",
        "impact": "high",
    },
    "PCE": {
        "icon": "📈", "country": "US",
        "summary": "개인소비지출 물가(연준 공식 기준)",
        "focus": "연준의 공식 인플레이션 지표. CPI 발표 약 2주 후. 코어PCE YoY 2%가 목표. CPI와 괴리 여부·소비 지출 증가율 동반 확인",
        "impact": "high",
    },
    "GDP": {
        "icon": "🌐", "country": "US",
        "summary": "미국 GDP 성장률",
        "focus": "속보→수정→확정 3차 발표. PCE 소비·민간투자 세부항목이 핵심. 2분기 연속 마이너스 = 기술적 경기침체. GDP 디플레이터도 인플레 참고",
        "impact": "high",
    },
    "PPI": {
        "icon": "🏭", "country": "US",
        "summary": "미국 생산자물가",
        "focus": "CPI 1~2개월 선행지표. 서비스 PPI(의료·금융·운송)가 PCE로 직결. 파이프라인 인플레이션 방향 점검",
        "impact": "medium",
    },
    "JOLTS": {
        "icon": "📋", "country": "US",
        "summary": "구인·이직 보고서",
        "focus": "구인 건수 vs 실업자 수 비율(노동시장 타이트니스). 자발적 이직률(quit rate) = 노동자 자신감 지표. NFP보다 1개월 선행",
        "impact": "medium",
    },
    "ISM_MFG": {
        "icon": "🔧", "country": "US",
        "summary": "ISM 제조업 PMI",
        "focus": "50 기준선 상하 여부. 신규주문·재고 세부항목이 경기 방향 선행. 물가 세부항목은 PPI와 교차 확인",
        "impact": "medium",
    },
    "ISM_SVC": {
        "icon": "🛎️", "country": "US",
        "summary": "ISM 서비스업 PMI",
        "focus": "미국 GDP의 70%가 서비스. 고용·물가 세부항목 특히 주목. 제조업 PMI와 괴리가 클 때 서비스 쪽이 실물 경기를 더 잘 반영",
        "impact": "medium",
    },
    "RETAIL": {
        "icon": "🛒", "country": "US",
        "summary": "미국 소매판매",
        "focus": "소비자 지출 직접 지표. 근원소매판매(자동차·주유 제외)가 GDP 소비 항목에 직결. MoM 0.3% 기준선 상하 여부",
        "impact": "medium",
    },
    "BOK": {
        "icon": "🇰🇷", "country": "KR",
        "summary": "한국은행 금통위 기준금리 결정",
        "focus": "기준금리 결정. 미-한 금리차(환율·자본유출 압력), 가계부채, 수출·성장률 사이 균형. 소수의견 수 변화로 다음 방향성 파악",
        "impact": "high",
    },
    "OPTIONS_US": {
        "icon": "⚠️", "country": "US",
        "summary": "미국 옵션 만기일",
        "focus": "감마 헤징으로 장중 변동성 확대. 대형주 핀닝(가격 고정) 현상. 3·6·9·12월 = Triple Witching(지수선물·지수옵션·주식옵션 동시만기). 만기 전날 저녁 포지션 조정 집중",
        "impact": "medium",
    },
    "OPTIONS_KR": {
        "icon": "🇰🇷⚠️", "country": "KR",
        "summary": "KOSPI200 옵션/선물 만기",
        "focus": "프로그램 매매 청산·롤오버. 외국인 선물 포지션 방향이 핵심. 만기일 오전 동시호가(08:45~09:00) 수급 변동 집중. 분기말 = 대규모 청산 주의",
        "impact": "medium",
    },
}

# ── 2026 하드코딩 일정 ───────────────────────────────────────────────────

# FOMC 2026 (연준 공식 발표 기준, 결과 발표일 = 2번째 날)
FOMC_2026 = [
    date(2026, 1, 28),   # Jan 27-28
    date(2026, 3, 18),   # Mar 17-18
    date(2026, 5, 6),    # May 5-6
    date(2026, 6, 17),   # Jun 16-17
    date(2026, 7, 29),   # Jul 28-29
    date(2026, 9, 16),   # Sep 15-16
    date(2026, 10, 28),  # Oct 27-28
    date(2026, 12, 16),  # Dec 15-16
]

# BOK 금통위 2026 (8회, 대략 6주 간격 목요일)
BOK_2026 = [
    date(2026, 1, 16),
    date(2026, 2, 27),
    date(2026, 4, 17),
    date(2026, 5, 29),
    date(2026, 7, 17),
    date(2026, 8, 28),
    date(2026, 10, 16),
    date(2026, 11, 27),
]


# ── 날짜 계산 헬퍼 ───────────────────────────────────────────────────────

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """지정 월의 n번째 요일 반환. weekday: 0=월 ~ 6=일"""
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    first_occurrence = first + timedelta(days=delta)
    return first_occurrence + timedelta(weeks=n - 1)


def _options_expiry_us(year: int, month: int) -> date:
    """미국 표준 옵션만기: 해당 월의 3번째 금요일 (weekday=4)"""
    return _nth_weekday(year, month, 4, 3)


def _options_expiry_kr(year: int, month: int) -> date:
    """KOSPI200 옵션만기: 해당 월의 2번째 목요일 (weekday=3)"""
    return _nth_weekday(year, month, 3, 2)


def _first_friday(year: int, month: int) -> date:
    """해당 월의 첫 번째 금요일 (NFP 근사일)"""
    return _nth_weekday(year, month, 4, 1)


def _second_wednesday(year: int, month: int) -> date:
    """해당 월의 두 번째 수요일 (CPI 근사일)"""
    return _nth_weekday(year, month, 2, 2)


def _third_wednesday(year: int, month: int) -> date:
    """해당 월의 세 번째 수요일 (PPI 근사일)"""
    return _nth_weekday(year, month, 2, 3)


# ── 이벤트 생성 ──────────────────────────────────────────────────────────

def _build_static_events(start: date, end: date) -> list[dict]:
    """하드코딩·계산 기반 이벤트 생성."""
    events = []

    cur = date(start.year, start.month, 1)
    while cur <= end + timedelta(days=40):
        y, m = cur.year, cur.month

        # ── 미국 옵션 만기 (매월 3번째 금요일) ──
        exp_us = _options_expiry_us(y, m)
        if start <= exp_us <= end:
            is_triple = m in (3, 6, 9, 12)
            events.append({
                "date": exp_us.isoformat(),
                "time": "15:00",
                "key": "OPTIONS_US",
                "title": "Triple Witching" if is_triple else "미국 옵션만기",
                "note": "지수선물+지수옵션+주식옵션 동시만기" if is_triple else "주식·ETF 표준 옵션만기",
            })

        # ── KOSPI200 옵션 만기 (매월 2번째 목요일) ──
        exp_kr = _options_expiry_kr(y, m)
        if start <= exp_kr <= end:
            is_quarterly = m in (3, 6, 9, 12)
            events.append({
                "date": exp_kr.isoformat(),
                "time": "09:00",
                "key": "OPTIONS_KR",
                "title": "KOSPI200 선물/옵션 만기" + (" (분기)" if is_quarterly else ""),
                "note": "분기 대규모 롤오버" if is_quarterly else "월간 옵션 만기",
            })

        # ── NFP 근사일 (매월 첫 금요일, 전달 고용 발표) ──
        nfp_date = _first_friday(y, m)
        if start <= nfp_date <= end:
            ref_month = date(y, m, 1) - timedelta(days=1)
            events.append({
                "date": nfp_date.isoformat(),
                "time": "21:30",
                "key": "NFP",
                "title": f"미국 고용보고서 ({ref_month.month}월)",
                "note": "비농업 고용·실업률·시간당임금",
            })

        # ── CPI 근사일 (매월 두 번째 수요일, 전달 물가 발표) ──
        cpi_date = _second_wednesday(y, m)
        if start <= cpi_date <= end:
            ref_month = date(y, m, 1) - timedelta(days=1)
            events.append({
                "date": cpi_date.isoformat(),
                "time": "21:30",
                "key": "CPI",
                "title": f"미국 CPI ({ref_month.month}월)",
                "note": "소비자물가·코어CPI YoY",
            })

        # ── PPI 근사일 (매월 세 번째 수요일) ──
        ppi_date = _third_wednesday(y, m)
        if start <= ppi_date <= end:
            ref_month = date(y, m, 1) - timedelta(days=1)
            events.append({
                "date": ppi_date.isoformat(),
                "time": "21:30",
                "key": "PPI",
                "title": f"미국 PPI ({ref_month.month}월)",
                "note": "생산자물가·서비스PPI",
            })

        # 월 이동
        if m == 12:
            cur = date(y + 1, 1, 1)
        else:
            cur = date(y, m + 1, 1)

    # ── FOMC (하드코딩) ──
    for d in FOMC_2026:
        if start <= d <= end:
            is_press = True   # 모든 FOMC 회의 후 기자회견 (2024~ 정책)
            is_dotplot = d.month in (3, 6, 9, 12)
            events.append({
                "date": d.isoformat(),
                "time": "02:00+1",   # 한국시간 다음날 새벽 (미국 오후 2시 ET)
                "key": "FOMC",
                "title": "FOMC 금리 결정" + (" + 점도표" if is_dotplot else ""),
                "note": "점도표·경제전망 동반" if is_dotplot else "성명문·기자회견",
            })

    # ── BOK 금통위 (하드코딩) ──
    for d in BOK_2026:
        if start <= d <= end:
            events.append({
                "date": d.isoformat(),
                "time": "10:00",
                "key": "BOK",
                "title": "한국은행 금통위",
                "note": "기준금리 결정 (오전 발표)",
            })

    return events


# ── Forex Factory 실시간 보강 ─────────────────────────────────────────────

_FF_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thismonth.json",
    "https://nfs.faireconomy.media/ff_calendar_nextmonth.json",
]
_FF_CACHE: dict = {"data": [], "ts": 0}
_FF_TTL = 3600 * 6   # 6시간 캐시


def _fetch_ff() -> list[dict]:
    """Forex Factory에서 이번 달 + 다음 달 고영향 USD/KRW 이벤트 가져오기."""
    now = time.time()
    if now - _FF_CACHE["ts"] < _FF_TTL and _FF_CACHE["data"]:
        return _FF_CACHE["data"]

    results = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for url in _FF_URLS:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                items = r.json()
                for item in items:
                    if item.get("impact") != "High":
                        continue
                    if item.get("country") not in ("USD", "KRW"):
                        continue
                    results.append(item)
        except Exception:
            pass

    _FF_CACHE["data"] = results
    _FF_CACHE["ts"] = now
    return results


_FF_TITLE_MAP = {
    "Non-Farm Employment Change": "NFP",
    "Unemployment Rate": None,           # NFP에 포함
    "CPI m/m": "CPI",
    "Core CPI m/m": "CPI",
    "PPI m/m": "PPI",
    "Core PPI m/m": "PPI",
    "FOMC Statement": "FOMC",
    "Federal Funds Rate": "FOMC",
    "FOMC Press Conference": "FOMC",
    "GDP q/q": "GDP",
    "Advance GDP q/q": "GDP",
    "Core PCE Price Index m/m": "PCE",
    "JOLTS Job Openings": "JOLTS",
    "Retail Sales m/m": "RETAIL",
    "ISM Manufacturing PMI": "ISM_MFG",
    "ISM Services PMI": "ISM_SVC",
    "BOK Interest Rate Decision": "BOK",
}


def _parse_ff_date(date_str: str) -> str | None:
    """Forex Factory 날짜 문자열 → YYYY-MM-DD"""
    try:
        dt = datetime.fromisoformat(date_str)
        return dt.date().isoformat()
    except Exception:
        return None


# ── 메인 API ─────────────────────────────────────────────────────────────

_MAIN_CACHE: dict = {"data": [], "ts": 0}
_MAIN_TTL = 3600 * 4   # 4시간 캐시


def fetch_calendar(days_ahead: int = 60) -> list[dict]:
    """
    향후 days_ahead일 이벤트 목록 반환.
    각 항목: {date, time, key, title, note, impact, icon, summary, focus, country,
              forecast, previous, actual}
    """
    now_ts = time.time()
    if now_ts - _MAIN_CACHE["ts"] < _MAIN_TTL and _MAIN_CACHE["data"]:
        return _MAIN_CACHE["data"]

    today = date.today()
    end   = today + timedelta(days=days_ahead)

    # 1) 정적 이벤트 생성
    static = _build_static_events(today, end)

    # 2) FF 실시간 데이터 (forecast/previous/actual 보강용)
    ff_items = _fetch_ff()
    ff_lookup: dict[str, list] = {}   # key: "date|key" → list of ff items
    for item in ff_items:
        d = _parse_ff_date(item.get("date", ""))
        if not d:
            continue
        mapped_key = None
        for title_pat, k in _FF_TITLE_MAP.items():
            if title_pat.lower() in item.get("title", "").lower():
                mapped_key = k
                break
        if mapped_key:
            slot = f"{d}|{mapped_key}"
            ff_lookup.setdefault(slot, []).append(item)

    # 3) 정적 이벤트에 FF 데이터 merge
    merged: dict[str, dict] = {}
    for ev in static:
        slot = f"{ev['date']}|{ev['key']}"
        ff = ff_lookup.get(slot, [{}])[0]
        meta = EVENT_META.get(ev["key"], {})

        entry = {
            "date":     ev["date"],
            "time":     ev.get("time", ""),
            "key":      ev["key"],
            "title":    ev["title"],
            "note":     ev.get("note", ""),
            "impact":   meta.get("impact", "medium"),
            "icon":     meta.get("icon", "📅"),
            "country":  meta.get("country", "US"),
            "summary":  meta.get("summary", ev["title"]),
            "focus":    meta.get("focus", ""),
            "forecast": ff.get("forecast") or "",
            "previous": ff.get("previous") or "",
            "actual":   ff.get("actual") or "",
        }
        # 같은 날 같은 키는 중복 제거 (첫 번째 우선)
        if slot not in merged:
            merged[slot] = entry

    # 4) FF에만 있는 추가 고영향 이벤트 (정적 목록에 없는 것)
    for item in ff_items:
        d = _parse_ff_date(item.get("date", ""))
        if not d or d < today.isoformat() or d > end.isoformat():
            continue
        mapped_key = None
        for title_pat, k in _FF_TITLE_MAP.items():
            if title_pat.lower() in item.get("title", "").lower():
                mapped_key = k
                break
        if not mapped_key:
            continue
        slot = f"{d}|{mapped_key}"
        if slot in merged:
            continue
        meta = EVENT_META.get(mapped_key, {})
        merged[slot] = {
            "date":     d,
            "time":     item.get("time", ""),
            "key":      mapped_key,
            "title":    item.get("title", mapped_key),
            "note":     "",
            "impact":   meta.get("impact", "high"),
            "icon":     meta.get("icon", "📅"),
            "country":  "US" if item.get("country") == "USD" else "KR",
            "summary":  meta.get("summary", item.get("title", "")),
            "focus":    meta.get("focus", ""),
            "forecast": item.get("forecast") or "",
            "previous": item.get("previous") or "",
            "actual":   item.get("actual") or "",
        }

    result = sorted(merged.values(), key=lambda x: x["date"])
    _MAIN_CACHE["data"] = result
    _MAIN_CACHE["ts"] = now_ts
    return result


# ── 연도 전체 캐시 ─────────────────────────────────────────────────────────

_YEAR_CACHE: dict[int, list] = {}
_YEAR_CACHE_TS: dict[int, float] = {}
_YEAR_TTL = 3600 * 6  # 6시간


def _build_merged(start: date, end: date) -> list[dict]:
    """start~end 범위 정적+FF 이벤트 병합 (내부 공통 로직)."""
    static = _build_static_events(start, end)
    ff_items = _fetch_ff()

    ff_lookup: dict[str, list] = {}
    for item in ff_items:
        d = _parse_ff_date(item.get("date", ""))
        if not d:
            continue
        mapped_key = None
        for title_pat, k in _FF_TITLE_MAP.items():
            if title_pat.lower() in item.get("title", "").lower():
                mapped_key = k
                break
        if mapped_key:
            ff_lookup.setdefault(f"{d}|{mapped_key}", []).append(item)

    merged: dict[str, dict] = {}
    for ev in static:
        slot = f"{ev['date']}|{ev['key']}"
        ff = ff_lookup.get(slot, [{}])[0]
        meta = EVENT_META.get(ev["key"], {})
        if slot not in merged:
            merged[slot] = {
                "date":     ev["date"],
                "time":     ev.get("time", ""),
                "key":      ev["key"],
                "title":    ev["title"],
                "note":     ev.get("note", ""),
                "impact":   meta.get("impact", "medium"),
                "icon":     meta.get("icon", "📅"),
                "country":  meta.get("country", "US"),
                "summary":  meta.get("summary", ev["title"]),
                "focus":    meta.get("focus", ""),
                "forecast": ff.get("forecast") or "",
                "previous": ff.get("previous") or "",
                "actual":   ff.get("actual") or "",
            }

    for item in ff_items:
        d = _parse_ff_date(item.get("date", ""))
        if not d or not (start.isoformat() <= d <= end.isoformat()):
            continue
        mapped_key = None
        for title_pat, k in _FF_TITLE_MAP.items():
            if title_pat.lower() in item.get("title", "").lower():
                mapped_key = k
                break
        if not mapped_key:
            continue
        slot = f"{d}|{mapped_key}"
        if slot in merged:
            continue
        meta = EVENT_META.get(mapped_key, {})
        merged[slot] = {
            "date":     d,
            "time":     item.get("time", ""),
            "key":      mapped_key,
            "title":    item.get("title", mapped_key),
            "note":     "",
            "impact":   meta.get("impact", "high"),
            "icon":     meta.get("icon", "📅"),
            "country":  "US" if item.get("country") == "USD" else "KR",
            "summary":  meta.get("summary", item.get("title", "")),
            "focus":    meta.get("focus", ""),
            "forecast": item.get("forecast") or "",
            "previous": item.get("previous") or "",
            "actual":   item.get("actual") or "",
        }

    return sorted(merged.values(), key=lambda x: x["date"])


def fetch_calendar_year(year: int) -> list[dict]:
    """특정 연도(1월~12월) 전체 이벤트 반환. 6시간 캐시."""
    now_ts = time.time()
    if (year in _YEAR_CACHE and _YEAR_CACHE[year]
            and now_ts - _YEAR_CACHE_TS.get(year, 0) < _YEAR_TTL):
        return _YEAR_CACHE[year]

    result = _build_merged(date(year, 1, 1), date(year, 12, 31))
    _YEAR_CACHE[year] = result
    _YEAR_CACHE_TS[year] = now_ts
    return result
