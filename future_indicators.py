"""
future_indicators.py
13개 메가트렌드 방향성 모멘텀 지표 수집
— FutureDirection.md의 각 방향성에 대해 프록시 ETF/자산의 모멘텀을 측정
"""

import json
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Optional

# ── 방향성 정의 (13개 축) ────────────────────────────────────────────────

DIRECTIONS = {
    "energy": {
        "name": "에너지 패권 전환",
        "icon": "🔵",
        "theme": "전기가 화폐가 된다",
        "proxy": "ICLN",       # iShares Global Clean Energy ETF
        "proxy_name": "ICLN",
        "higher_better": True,
        "milestone_past": "태양광 단가 90% 하락, 전기차 대중화",
        "milestone_now": "전고체 배터리 양산 경쟁 / 가정용 ESS 확산",
        "milestone_next": "SMR 첫 상업 가동 (2028~2030)",
    },
    "computing": {
        "name": "컴퓨팅 자산화",
        "icon": "🟣",
        "theme": "GPU가 토지다",
        "proxy": "SOXX",       # iShares Semiconductor ETF
        "proxy_name": "SOXX",
        "higher_better": True,
        "milestone_past": "클라우드 대중화, GPU 채굴→AI 수요 전환",
        "milestone_now": "분산 컴퓨팅 임대 마켓 급성장",
        "milestone_next": "컴퓨팅 지분 ETF/리츠 제도화 (2028~2031)",
    },
    "robotics": {
        "name": "피지컬 AI / 로보틱스",
        "icon": "🟠",
        "theme": "기계가 내 손발이 된다",
        "proxy": "BOTZ",       # Global X Robotics & AI ETF
        "proxy_name": "BOTZ",
        "higher_better": True,
        "milestone_past": "산업용 로봇 암 표준화, 물류 자동화 대규모 도입",
        "milestone_now": "휴머노이드 로봇 공장 파일럿 단계",
        "milestone_next": "가정용 범용 로봇 첫 양산 (2029~2032)",
    },
    "biotech": {
        "name": "바이오테크 / 수명 자본",
        "icon": "🟡",
        "theme": "인생 자체가 길어진다",
        "proxy": "ARKG",       # ARK Genomic Revolution ETF
        "proxy_name": "ARKG",
        "higher_better": True,
        "milestone_past": "게놈 분석 대중화, mRNA 백신 실증",
        "milestone_now": "CRISPR 유전자 편집 첫 임상 치료 승인",
        "milestone_next": "노화 역행 약물 임상 성공 (2028~2031)",
    },
    "food": {
        "name": "식량·농업 전환",
        "icon": "🔴",
        "theme": "먹는 것의 생산 구조가 바뀐다",
        "proxy": "MOO",        # VanEck Agribusiness ETF
        "proxy_name": "MOO",
        "higher_better": True,
        "milestone_past": "GMO 대규모 상용화, 정밀농업 시작",
        "milestone_now": "수직농장 확산, 배양육 소규모 판매 승인",
        "milestone_next": "배양육 일반 육류 수준 가격 경쟁력 (2028~2030)",
    },
    "climate": {
        "name": "기후·자원 자산",
        "icon": "🟢",
        "theme": "탄소·물·생태계가 화폐가 된다",
        "proxy": "KRBN",       # KraneShares Global Carbon Strategy ETF
        "proxy_name": "KRBN",
        "higher_better": True,
        "milestone_past": "파리협정, K-ETS 시행, ESG 주류화",
        "milestone_now": "자발적 탄소시장(VCM) 급성장",
        "milestone_next": "탄소시장 국제 표준화 완성 (2027~2029)",
    },
    "space": {
        "name": "우주 경제",
        "icon": "🌌",
        "theme": "지구 밖이 새 프론티어",
        "proxy": "UFO",        # Procure Space ETF
        "proxy_name": "UFO",
        "higher_better": True,
        "milestone_past": "팰컨9 재사용 성공, 스타링크 서비스 시작",
        "milestone_now": "저궤도 위성 인터넷 글로벌 확산",
        "milestone_next": "달 기지 첫 상주 운영 (2029~2033)",
    },
    "defi": {
        "name": "금융 시스템 재편",
        "icon": "🟣",
        "theme": "돈의 구조가 바뀐다",
        "proxy": "BTC-USD",    # Bitcoin (암호화폐 금융 프록시)
        "proxy_name": "BTC",
        "higher_better": True,
        "milestone_past": "비트코인 탄생, DeFi 생태계, BTC ETF 승인",
        "milestone_now": "주요국 CBDC 파일럿 운영",
        "milestone_next": "실물자산 토큰화 법제화 (2028~2031)",
    },
    "geopolitics": {
        "name": "지정학 / 주권적 개인",
        "icon": "🟠",
        "theme": "어디에 있느냐가 운명을 바꾼다",
        "proxy": "EWY",        # iShares MSCI South Korea (지정학적 한국 노출 프록시)
        "proxy_name": "EWY",
        "higher_better": True,
        "milestone_past": "인터넷 국경 초월, 원격근무 글로벌 실험",
        "milestone_now": "디지털 노마드 비자 30개국 이상",
        "milestone_next": "복수 시민권 포트폴리오 서비스화 (2029~2032)",
    },
    "ai_agents": {
        "name": "자율 비즈니스 / AI 에이전트",
        "icon": "🤖",
        "theme": "나 대신 일하는 AI 소유",
        "proxy": "QQQ",        # NASDAQ 100 (기술주/AI 프록시)
        "proxy_name": "QQQ",
        "higher_better": True,
        "milestone_past": "챗봇·자동화 등장, GPT-4 에이전트 가능성 실증",
        "milestone_now": "AI 에이전트 마켓플레이스 초기 형성",
        "milestone_next": "에이전트 마켓 성숙·표준화 (2027~2029)",
    },
    "community": {
        "name": "커뮤니티 / 사회 자본",
        "icon": "🔴",
        "theme": "신뢰 네트워크가 자산이 된다",
        "proxy": "FCOM",       # Fidelity MSCI Communication Services ETF
        "proxy_name": "FCOM",
        "higher_better": True,
        "milestone_past": "소셜미디어로 개인 브랜드 측정 시작",
        "milestone_now": "로컬 커뮤니티 공동 투자 플랫폼 등장",
        "milestone_next": "DAO 기반 공동체 소유 인프라 제도화 (2027~2030)",
    },
    "institutional": {
        "name": "제도·지정학 전환",
        "icon": "🟤",
        "theme": "허용 속도가 타임라인을 결정",
        "proxy": "TLT",        # iShares 20+ Year Treasury Bond (제도 안정성 역프록시)
        "proxy_name": "TLT",
        "higher_better": False,  # TLT↑ = 불확실성↑ = 제도 전환 압력 높음
        "milestone_past": "파리협정, GDPR, 미중 반도체 패권 경쟁",
        "milestone_now": "AI 규제 프레임워크 각국 입법 경쟁",
        "milestone_next": "AI 에이전트 법인격 논의 본격화 (2027~2030)",
    },
    "basic_income": {
        "name": "기본소득 +α 설계",
        "icon": "🟢",
        "theme": "일하지 않아도 되는 구조",
        "proxy": None,          # 복합 계산 (다른 방향성 평균)
        "proxy_name": "복합",
        "higher_better": True,
        "milestone_past": "배당·임대 소득으로 첫 자산소득 시작",
        "milestone_now": "복수 수입 채널 실험 단계",
        "milestone_next": "자산소득으로 생활비 20~30% 충당 (2027~2029)",
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
    모든 방향성 지표 수집.
    반환: {key: {score, mo1_pct, yr1_pct, latest, proxy, label, ...}}
    """
    result = {}
    scores_for_composite = []

    for key, d in DIRECTIONS.items():
        if d["proxy"] is None:
            # basic_income은 나중에 채움
            result[key] = {
                "score": 50,
                "mo1_pct": 0.0,
                "yr1_pct": 0.0,
                "latest": None,
                "proxy": d["proxy_name"],
                "label": "유지",
                "name": d["name"],
                "icon": d["icon"],
                "theme": d["theme"],
                "milestone_past": d["milestone_past"],
                "milestone_now": d["milestone_now"],
                "milestone_next": d["milestone_next"],
            }
            continue

        closes = _fetch_yahoo_closes(d["proxy"], "1y")
        momentum = _momentum_score(closes, d["higher_better"])
        score = momentum["score"]
        scores_for_composite.append(score)

        result[key] = {
            **momentum,
            "proxy": d["proxy_name"],
            "label": _score_label(score),
            "name": d["name"],
            "icon": d["icon"],
            "theme": d["theme"],
            "milestone_past": d["milestone_past"],
            "milestone_now": d["milestone_now"],
            "milestone_next": d["milestone_next"],
        }
        time.sleep(0.3)  # Yahoo Finance 부하 방지

    # basic_income: 다른 방향성 평균
    if scores_for_composite:
        composite = round(sum(scores_for_composite) / len(scores_for_composite), 1)
    else:
        composite = 50
    result["basic_income"]["score"] = composite
    result["basic_income"]["label"] = _score_label(composite)

    return result


# ── 추천 포트폴리오 정의 ─────────────────────────────────────────────────
# 각 방향성별 추천 진입 ETF/자산과 추천 시점 (현재 ✅ 포지션 기준)
PORTFOLIO_DEFS = {
    "energy":       {"symbol": "ICLN",    "name": "iShares Clean Energy ETF",     "entry_date": "2023-01-03",  "reason": "IRA 법안 통과 후 클린에너지 전환 가속 확인"},
    "computing":    {"symbol": "SOXX",    "name": "iShares Semiconductor ETF",    "entry_date": "2022-10-14",  "reason": "반도체 사이클 저점, AI 수요 폭발 직전"},
    "robotics":     {"symbol": "BOTZ",    "name": "Global X Robotics & AI ETF",   "entry_date": "2023-01-03",  "reason": "ChatGPT 이후 물리 AI 가속화 신호"},
    "biotech":      {"symbol": "ARKG",    "name": "ARK Genomic Revolution ETF",   "entry_date": "2023-06-01",  "reason": "CRISPR 치료 임상 성공 시점"},
    "food":         {"symbol": "MOO",     "name": "VanEck Agribusiness ETF",      "entry_date": "2023-01-03",  "reason": "식량 안보 이슈 부각, 애그테크 관심 급증"},
    "climate":      {"symbol": "KRBN",    "name": "KraneShares Global Carbon ETF","entry_date": "2022-01-03",  "reason": "탄소시장 제도화 가속, 글로벌 탄소세 논의 본격화"},
    "space":        {"symbol": "UFO",     "name": "Procure Space ETF",            "entry_date": "2023-01-03",  "reason": "스타링크 글로벌 확산, 우주경제 원년"},
    "defi":         {"symbol": "BTC-USD", "name": "Bitcoin",                      "entry_date": "2023-01-03",  "reason": "FTX 붕괴 저점, BTC ETF 승인 기대 구간"},
    "geopolitics":  {"symbol": "EWY",     "name": "iShares MSCI South Korea ETF", "entry_date": "2023-01-03",  "reason": "반도체·K-콘텐츠 수출 회복 기대"},
    "ai_agents":    {"symbol": "QQQ",     "name": "NASDAQ 100 ETF",               "entry_date": "2023-01-03",  "reason": "ChatGPT 출시 직후 AI 에이전트 시대 개막"},
    "community":    {"symbol": "FCOM",    "name": "Fidelity Comm. Services ETF",  "entry_date": "2023-01-03",  "reason": "소셜 플랫폼 수익성 회복, 커뮤니티 자산화 시작"},
    "institutional":{"symbol": "TLT",    "name": "iShares 20+ Yr Treasury ETF",  "entry_date": "2023-10-23",  "reason": "연준 금리 피크아웃 신호, 채권 저점"},
    "basic_income": {"symbol": "VT",      "name": "Vanguard Total World ETF",     "entry_date": "2023-01-03",  "reason": "글로벌 분산 자동 재투자 복리 기반"},
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


def fetch_portfolio_performance() -> list[dict]:
    """
    각 방향성 추천 ETF의 진입일 → 현재 수익률 계산.
    반환: [{key, name, icon, symbol, etf_name, entry_date, entry_price,
             current_price, return_pct, invest_krw, current_value_krw, reason}]
    """
    INVEST_USD = INVEST_KRW / 1350  # 환율 약 1350원 기준 USD 환산

    rows = []
    for key, pdef in PORTFOLIO_DEFS.items():
        d = DIRECTIONS.get(key, {})
        symbol = pdef["symbol"]

        # 현재가 (최신 1일)
        closes_recent = _fetch_yahoo_closes(symbol, "5d")
        current_price = round(closes_recent[-1], 4) if closes_recent else None
        time.sleep(0.3)

        # 진입가 (추천 날짜 기준)
        entry_price = _fetch_price_at_date(symbol, pdef["entry_date"])
        time.sleep(0.3)

        if current_price and entry_price:
            return_pct = round((current_price / entry_price - 1) * 100, 2)
            # USD 기준 수익률을 KRW에 적용 (환율 변동 미반영 — 단순화)
            current_value = round(INVEST_KRW * (1 + return_pct / 100))
            profit = current_value - INVEST_KRW
        else:
            return_pct = None
            current_value = INVEST_KRW
            profit = 0

        rows.append({
            "key":              key,
            "name":             d.get("name", key),
            "icon":             d.get("icon", ""),
            "symbol":           symbol,
            "etf_name":         pdef["name"],
            "entry_date":       pdef["entry_date"],
            "entry_price":      entry_price,
            "current_price":    current_price,
            "return_pct":       return_pct,
            "invest_krw":       INVEST_KRW,
            "current_value_krw":current_value,
            "profit_krw":       profit,
            "reason":           pdef["reason"],
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
