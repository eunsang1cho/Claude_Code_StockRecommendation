"""
future_indicators.py
11개 핵심 메가트렌드 방향성 모멘텀 지표 수집 (v2)
— FutureDirection.md의 각 방향성에 대해 프록시 ETF/자산의 모멘텀을 측정
"""

import json
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Optional

# ── 방향성 정의 (11개 핵심 축 v2) ────────────────────────────────────────

DIRECTIONS = {
    "power_grid": {
        "name": "전력망·에너지 패권",
        "icon": "⚡",
        "theme": "전기가 AI의 석유다",
        "proxy":    "XLU",         # 🇺🇸 Utilities Select Sector SPDR
        "proxy_name": "XLU",
        "proxy_kr": "015760.KS",   # 🇰🇷 한국전력 (KEPCO)
        "proxy_kr_name": "한국전력",
        "higher_better": True,
        "score_min": 30, "score_max": 90,
        "milestone_past": "재생에너지 설치 단가 90% 하락, 스마트 그리드 표준화 시작",
        "milestone_now": "AI 데이터센터 전력 수요 폭발 → 전력망 업그레이드 긴급 착수",
        "milestone_next": "SMR 첫 상업 가동, 전력 P2P 거래 플랫폼 상용화 (2028~2031)",
    },
    "computing": {
        "name": "컴퓨팅 자산화",
        "icon": "🖥️",
        "theme": "GPU가 토지다",
        "proxy":    "SOXX",        # 🇺🇸 iShares Semiconductor ETF
        "proxy_name": "SOXX",
        "proxy_kr": "091160.KS",   # 🇰🇷 KODEX 반도체
        "proxy_kr_name": "KODEX반도체",
        "higher_better": True,
        "score_min": 20, "score_max": 90,
        "milestone_past": "클라우드 대중화, GPU 채굴→AI 수요 전환",
        "milestone_now": "분산 컴퓨팅 임대 마켓 급성장",
        "milestone_next": "컴퓨팅 지분 ETF/리츠 제도화 (2028~2031)",
    },
    "robotics": {
        "name": "피지컬 AI / 로보틱스",
        "icon": "🦾",
        "theme": "소유한 로봇이 일하고 수익을 가져다 준다",
        "proxy":    "BOTZ",        # 🇺🇸 Global X Robotics & AI ETF
        "proxy_name": "BOTZ",
        "proxy_kr": "228790.KS",   # 🇰🇷 TIGER 로보틱스AI
        "proxy_kr_name": "TIGER로보틱스",
        "higher_better": True,
        "score_min": 20, "score_max": 85,
        "milestone_past": "산업용 로봇 암 표준화, 물류 자동화 대규모 도입",
        "milestone_now": "휴머노이드 로봇 공장 파일럿 단계",
        "milestone_next": "가정용 범용 로봇 첫 양산 (2029~2032)",
    },
    "biotech": {
        "name": "바이오테크 / 수명 자본",
        "icon": "🧬",
        "theme": "인생 자체가 길어진다",
        "proxy":    "ARKG",        # 🇺🇸 ARK Genomic Revolution ETF
        "proxy_name": "ARKG",
        "proxy_kr": "143860.KS",   # 🇰🇷 KODEX 바이오
        "proxy_kr_name": "KODEX바이오",
        "higher_better": True,
        "milestone_past": "게놈 분석 대중화, mRNA 백신 실증",
        "milestone_now": "CRISPR 유전자 편집 첫 임상 치료 승인",
        "milestone_next": "노화 역행 약물 임상 성공 (2028~2031)",
    },
    "food": {
        "name": "식량·농업 전환",
        "icon": "🌾",
        "theme": "먹는 것의 생산 구조가 바뀐다",
        "proxy":    "MOO",         # 🇺🇸 VanEck Agribusiness ETF
        "proxy_name": "MOO",
        "proxy_kr": "097950.KS",   # 🇰🇷 CJ제일제당 (국내 식품·애그테크 대표)
        "proxy_kr_name": "CJ제일제당",
        "higher_better": True,
        "milestone_past": "GMO 대규모 상용화, 정밀농업 시작",
        "milestone_now": "수직농장 확산, 배양육 소규모 판매 승인",
        "milestone_next": "배양육 일반 육류 수준 가격 경쟁력 (2028~2030)",
    },
    "water_resources": {
        "name": "물·자원 자산화",
        "icon": "💧",
        "theme": "물이 다음 석유다",
        "proxy":    "PHO",         # 🇺🇸 Invesco Water Resources ETF
        "proxy_name": "PHO",
        "proxy_kr": "284430.KS",   # 🇰🇷 KODEX 물
        "proxy_kr_name": "KODEX물",
        "higher_better": True,
        "score_min": 40, "score_max": 88,  # 구조적 필수 인프라 → 하한 보정
        "milestone_past": "파리협정, EU 탄소국경세(CBAM) 결정, ESG 주류화",
        "milestone_now": "자발적 탄소시장(VCM) 급성장, 수자원 거래 파일럿 시작",
        "milestone_next": "수자원 거래소 주요국 개설, 탄소시장 국제 표준화 완성 (2027~2030)",
    },
    "defi": {
        "name": "금융 시스템 재편",
        "icon": "💎",
        "theme": "프로그래밍 가능한 화폐로 금융 인프라를 직접 운영",
        "proxy":    "BTC-USD",     # 🇺🇸 Bitcoin
        "proxy_name": "BTC",
        "proxy_kr": "377300.KS",   # 🇰🇷 KODEX BTC선물H
        "proxy_kr_name": "KODEX BTC선물",
        "higher_better": True,
        "milestone_past": "비트코인 탄생, DeFi 생태계, BTC ETF 승인",
        "milestone_now": "주요국 CBDC 파일럿 운영, 실물자산 토큰화 시범",
        "milestone_next": "실물자산 토큰화 법제화, 주요국 CBDC 실거래 유통 (2027~2031)",
    },
    "cyber": {
        "name": "사이버보안·신뢰 인프라",
        "icon": "🔐",
        "theme": "디지털 세계의 성벽이 기본 인프라다",
        "proxy":    "CIBR",        # 🇺🇸 First Trust NASDAQ Cybersecurity ETF
        "proxy_name": "CIBR",
        "proxy_kr": "396520.KS",   # 🇰🇷 KODEX 미국사이버보안나스닥
        "proxy_kr_name": "KODEX사이버보안",
        "higher_better": True,
        "score_min": 42, "score_max": 90,  # 디지털 기반 인프라 → 하한 보정
        "milestone_past": "솔라윈즈·콜로니얼 파이프라인 해킹 — 인프라 취약성 실증",
        "milestone_now": "AI 기반 사이버 공격 급증, 각국 사이버 방어 의무화 입법",
        "milestone_next": "양자 내성 암호화 표준 전환 완성 (2028~2031)",
    },
    "industrial_sovereignty": {
        "name": "산업 주권·공급망 재편",
        "icon": "🏭",
        "theme": "어디서 만드느냐가 새로운 국력이다",
        "proxy":    "ITA",         # 🇺🇸 iShares U.S. Aerospace & Defense ETF
        "proxy_name": "ITA",
        "proxy_kr": "012450.KS",   # 🇰🇷 한화에어로스페이스
        "proxy_kr_name": "한화에어로",
        "higher_better": True,
        "score_min": 20, "score_max": 90,
        "milestone_past": "미중 반도체 패권 경쟁, 공급망 리쇼어링 가속",
        "milestone_now": "방산·반도체 주권 확보 경쟁, 유럽 전략 자율성 추구",
        "milestone_next": "핵심 산업 공급망 완전 내재화, 방산 수출 구조 정착 (2028~2032)",
    },
    "ai_agents": {
        "name": "자율 비즈니스 / AI 에이전트",
        "icon": "🤖",
        "theme": "나 대신 일하는 AI 소유 — Agent OS 시대",
        "proxy":    "QQQ",         # 🇺🇸 NASDAQ 100 ETF
        "proxy_name": "QQQ",
        "proxy_kr": "379810.KS",   # 🇰🇷 KODEX 나스닥100
        "proxy_kr_name": "KODEX나스닥100",
        "higher_better": True,
        "milestone_past": "챗봇·자동화 등장, GPT-4 에이전트 가능성 실증",
        "milestone_now": "AI 에이전트 마켓플레이스 초기 형성, Agent OS 경쟁 시작",
        "milestone_next": "에이전트 마켓 성숙·표준화, 에이전트 법인격 논의 (2027~2030)",
    },
    "space": {
        "name": "우주 경제",
        "icon": "🚀",
        "theme": "지구 밖이 새 프론티어",
        "proxy":    "UFO",         # 🇺🇸 Procure Space ETF
        "proxy_name": "UFO",
        "proxy_kr": "422160.KS",   # 🇰🇷 KODEX 우주항공&방산
        "proxy_kr_name": "KODEX우주항공",
        "higher_better": True,
        "score_min": 20, "score_max": 80,  # 서사 강하나 수익화 초기 → 과대평가 방지
        "milestone_past": "팰컨9 재사용 성공, 스타링크 서비스 시작",
        "milestone_now": "저궤도 위성 인터넷 글로벌 확산, 상업 우주 정거장 착수",
        "milestone_next": "달 기지 첫 상주 운영 (2029~2033)",
    },
}

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FutureBot/1.0)"}


def _fetch_yahoo_closes(symbol: str, range_: str = "1y") -> list[float]:
    """Yahoo Finance에서 종가 리스트 반환"""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval=1d&range={range_}"
    )
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read())
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        return [c for c in closes if c is not None]
    except Exception:
        return []


def _momentum_score(closes: list[float], higher_better: bool = True) -> dict:
    """1개월·12개월 수익률로 모멘텀 점수(0~100) 계산"""
    if len(closes) < 5:
        return {"score": 50, "mo1_pct": 0.0, "yr1_pct": 0.0, "latest": None}

    latest = closes[-1]

    # 1개월 수익률 (약 21 거래일)
    idx_1mo = max(0, len(closes) - 22)
    mo1_pct = (latest / closes[idx_1mo] - 1) * 100 if closes[idx_1mo] else 0.0

    # 12개월 수익률
    yr1_pct = (latest / closes[0] - 1) * 100 if closes[0] else 0.0

    # 방향 보정
    if not higher_better:
        mo1_pct = -mo1_pct
        yr1_pct = -yr1_pct

    # 점수 = 50 + (1mo 기여) + (12mo 기여), 클램프 5~95
    raw = 50 + mo1_pct * 2.0 + yr1_pct * 0.4
    score = round(max(5, min(95, raw)), 1)

    return {
        "score": score,
        "mo1_pct": round(mo1_pct, 2),
        "yr1_pct": round(yr1_pct, 2),
        "latest": round(latest, 2),
    }


def _score_label(score: float) -> str:
    if score >= 75: return "가속"
    if score >= 60: return "상승"
    if score >= 40: return "유지"
    if score >= 25: return "둔화"
    return "퇴조"


def fetch_future_indicators() -> dict[str, dict]:
    """
    모든 방향성 지표 수집 (미국장 + 국내장 각각).
    반환: {key: {score, score_us, score_kr, mo1_pct, yr1_pct, mo1_pct_kr, yr1_pct_kr,
                 latest, latest_kr, proxy, proxy_kr, label, label_kr, ...}}
    """
    result = {}

    for key, d in DIRECTIONS.items():
        # 🇺🇸 미국장
        closes_us = _fetch_yahoo_closes(d["proxy"], "1y")
        mom_us = _momentum_score(closes_us, d["higher_better"])
        time.sleep(0.3)

        # 🇰🇷 국내장
        proxy_kr = d.get("proxy_kr")
        if proxy_kr:
            closes_kr = _fetch_yahoo_closes(proxy_kr, "1y")
            mom_kr = _momentum_score(closes_kr, d["higher_better"])
            time.sleep(0.3)
        else:
            mom_kr = {"score": None, "mo1_pct": None, "yr1_pct": None, "latest": None}

        # 방향성별 상한/하한 보정
        s_min = d.get("score_min", 5)
        s_max = d.get("score_max", 95)
        score_us = round(max(s_min, min(s_max, mom_us["score"])), 1)
        score_kr = round(max(s_min, min(s_max, mom_kr["score"])), 1) if mom_kr["score"] is not None else None
        # 대표 score: US 기준 (히스토리 차트 연속성 유지)
        score = score_us

        result[key] = {
            "score":       score,
            "score_us":    score_us,
            "score_kr":    score_kr,
            "mo1_pct":     mom_us["mo1_pct"],
            "yr1_pct":     mom_us["yr1_pct"],
            "latest":      mom_us["latest"],
            "mo1_pct_kr":  mom_kr["mo1_pct"],
            "yr1_pct_kr":  mom_kr["yr1_pct"],
            "latest_kr":   mom_kr["latest"],
            "proxy":       d["proxy_name"],
            "proxy_kr":    d.get("proxy_kr_name"),
            "label":       _score_label(score_us),
            "label_kr":    _score_label(score_kr) if score_kr is not None else "N/A",
            "name":        d["name"],
            "icon":        d["icon"],
            "theme":       d["theme"],
            "milestone_past":  d["milestone_past"],
            "milestone_now":   d["milestone_now"],
            "milestone_next":  d["milestone_next"],
        }

    return result


# ── 추천 포트폴리오 정의 ─────────────────────────────────────────────────
# 각 방향성별 추천 진입 ETF/자산과 추천 시점 (현재 ✅ 포지션 기준)
PORTFOLIO_DEFS = {
    # 🇺🇸 미국장 (symbol_us) / 🇰🇷 국내장 (symbol_kr)
    "power_grid": {
        "symbol": "XLU",          "name": "Utilities Select Sector SPDR",
        "symbol_kr": "015760.KS", "name_kr": "한국전력(KEPCO)",
        "entry_date": "2024-01-02",  "entry_date_kr": "2024-01-02",
        "reason": "AI 데이터센터 전력 수요 폭발 — 전력 인프라 수혜 확인",
        "reason_kr": "AI·데이터센터 국내 전력 공급 의무 확대 수혜",
    },
    "computing": {
        "symbol": "SOXX",         "name": "iShares Semiconductor ETF",
        "symbol_kr": "091160.KS", "name_kr": "KODEX 반도체",
        "entry_date": "2022-10-14", "entry_date_kr": "2022-10-14",
        "reason": "반도체 사이클 저점, AI 수요 폭발 직전",
        "reason_kr": "삼성·SK하이닉스 중심 반도체 반등 구간",
    },
    "robotics": {
        "symbol": "BOTZ",         "name": "Global X Robotics & AI ETF",
        "symbol_kr": "228790.KS", "name_kr": "TIGER 로보틱스AI",
        "entry_date": "2023-01-03", "entry_date_kr": "2023-01-03",
        "reason": "ChatGPT 이후 물리 AI 가속화 신호",
        "reason_kr": "국내 로보틱스·자동화 수요 부각",
    },
    "biotech": {
        "symbol": "ARKG",         "name": "ARK Genomic Revolution ETF",
        "symbol_kr": "143860.KS", "name_kr": "KODEX 바이오",
        "entry_date": "2023-06-01", "entry_date_kr": "2023-06-01",
        "reason": "CRISPR 치료 임상 성공 시점",
        "reason_kr": "K-바이오 수출 급증, 국내 임상 성과 확인",
    },
    "food": {
        "symbol": "MOO",          "name": "VanEck Agribusiness ETF",
        "symbol_kr": "097950.KS", "name_kr": "CJ제일제당",
        "entry_date": "2023-01-03", "entry_date_kr": "2023-01-03",
        "reason": "식량 안보 이슈 부각, 애그테크 관심 급증",
        "reason_kr": "국내 식품·대체단백질 선도 기업",
    },
    "water_resources": {
        "symbol": "PHO",          "name": "Invesco Water Resources ETF",
        "symbol_kr": "284430.KS", "name_kr": "KODEX 물",
        "entry_date": "2023-01-03", "entry_date_kr": "2023-01-03",
        "reason": "수자원 부족 위기 가시화, 물 인프라 투자 확대",
        "reason_kr": "국내 수처리·환경 인프라 투자 확대",
    },
    "defi": {
        "symbol": "BTC-USD",      "name": "Bitcoin",
        "symbol_kr": "377300.KS", "name_kr": "KODEX BTC선물H",
        "entry_date": "2023-01-03", "entry_date_kr": "2024-01-02",
        "reason": "FTX 붕괴 저점, BTC ETF 승인 기대 구간",
        "reason_kr": "국내 BTC 선물 ETF 상장 초기",
    },
    "cyber": {
        "symbol": "CIBR",         "name": "First Trust NASDAQ Cybersecurity ETF",
        "symbol_kr": "396520.KS", "name_kr": "KODEX 미국사이버보안나스닥",
        "entry_date": "2024-01-02", "entry_date_kr": "2024-01-02",
        "reason": "AI 해킹 위협 급증, 사이버 방어 의무화 입법 가속",
        "reason_kr": "국내 투자자용 사이버보안 ETF 초기 진입",
    },
    "industrial_sovereignty": {
        "symbol": "ITA",          "name": "iShares U.S. Aerospace & Defense ETF",
        "symbol_kr": "012450.KS", "name_kr": "한화에어로스페이스",
        "entry_date": "2022-02-24", "entry_date_kr": "2022-02-24",
        "reason": "러우 전쟁 발발 — 방산·공급망 주권 투자 가속",
        "reason_kr": "K-방산 수출 급등 선도 기업",
    },
    "ai_agents": {
        "symbol": "QQQ",          "name": "NASDAQ 100 ETF",
        "symbol_kr": "379810.KS", "name_kr": "KODEX 나스닥100",
        "entry_date": "2023-01-03", "entry_date_kr": "2023-01-03",
        "reason": "ChatGPT 출시 직후 AI 에이전트 시대 개막",
        "reason_kr": "나스닥100 국내 환헤지 버전",
    },
    "space": {
        "symbol": "UFO",          "name": "Procure Space ETF",
        "symbol_kr": "422160.KS", "name_kr": "KODEX 우주항공&방산",
        "entry_date": "2023-01-03", "entry_date_kr": "2023-06-01",
        "reason": "스타링크 글로벌 확산, 우주경제 원년",
        "reason_kr": "국내 우주·방산 복합 ETF 상장",
    },
}

INVEST_KRW = 1_000_000  # 방향성당 100만원


def _fetch_price_at_date(symbol: str, date_str: str) -> float | None:
    """Yahoo Finance에서 특정 날짜 이후 첫 거래일 종가 반환"""
    # date_str 기준 60일 범위로 조회 (주말/휴장 고려)
    from datetime import datetime as _dt
    import time as _t
    try:
        start_dt = _dt.strptime(date_str, "%Y-%m-%d")
        start_ts = int(start_dt.timestamp())
        end_ts   = int((start_dt.replace(year=start_dt.year) + timedelta(days=60)).timestamp())
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
            f"?interval=1d&period1={start_ts}&period2={end_ts}"
        )
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read())
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        return round(closes[0], 4) if closes else None
    except Exception:
        return None


def _portfolio_perf_one(symbol: str, entry_date: str, etf_name: str, reason: str) -> dict:
    """단일 종목 수익률 계산 헬퍼"""
    closes_recent = _fetch_yahoo_closes(symbol, "5d")
    current_price = round(closes_recent[-1], 4) if closes_recent else None
    time.sleep(0.3)
    entry_price = _fetch_price_at_date(symbol, entry_date)
    time.sleep(0.3)
    if current_price and entry_price:
        return_pct = round((current_price / entry_price - 1) * 100, 2)
        current_value = round(INVEST_KRW * (1 + return_pct / 100))
        profit = current_value - INVEST_KRW
    else:
        return_pct = None
        current_value = INVEST_KRW
        profit = 0
    return {
        "symbol": symbol, "etf_name": etf_name, "entry_date": entry_date,
        "entry_price": entry_price, "current_price": current_price,
        "return_pct": return_pct, "invest_krw": INVEST_KRW,
        "current_value_krw": current_value, "profit_krw": profit,
        "reason": reason,
    }


def fetch_portfolio_performance() -> list[dict]:
    """
    각 방향성 추천 ETF의 진입일 → 현재 수익률 계산 (미국장 + 국내장).
    반환: [{key, name, icon, us:{...}, kr:{...}}]
    """
    rows = []
    for key, pdef in PORTFOLIO_DEFS.items():
        d = DIRECTIONS.get(key, {})
        us = _portfolio_perf_one(pdef["symbol"], pdef["entry_date"], pdef["name"], pdef["reason"])
        kr = _portfolio_perf_one(pdef["symbol_kr"], pdef["entry_date_kr"], pdef["name_kr"], pdef["reason_kr"])
        rows.append({
            "key":  key,
            "name": d.get("name", key),
            "icon": d.get("icon", ""),
            "us":   us,
            "kr":   kr,
        })
    return rows


def compute_changes(current: dict, previous: dict) -> list[dict]:
    """
    두 스냅샷 간 점수 변화가 큰 방향성 목록 반환.
    [{key, name, icon, delta, direction}]
    """
    changes = []
    for key, cur in current.items():
        prev = previous.get(key, {})
        delta = round(cur.get("score", 50) - prev.get("score", 50), 1)
        if abs(delta) >= 1.0:
            changes.append({
                "key": key,
                "name": cur.get("name", key),
                "icon": cur.get("icon", ""),
                "delta": delta,
                "direction": "up" if delta > 0 else "down",
            })
    changes.sort(key=lambda x: abs(x["delta"]), reverse=True)
    return changes[:6]  # 상위 6개
