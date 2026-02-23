"""
sector_info.py
종목 업종(카테고리) + 테마 + 시장 흐름 신호등

- 업종: pykrx get_market_sector_classification (24h 캐시)
- 테마: Claude Haiku 추론 (스캔 세션 내 in-memory 캐시)
- 신호등: Claude Haiku + Google News (스캔 세션 내 in-memory 캐시)
"""

import json
import os
import time
from datetime import datetime, timedelta

import anthropic

DIR = os.path.dirname(os.path.abspath(__file__))
SECTOR_CACHE_FILE = os.path.join(DIR, "sector_cache.json")

# ── 업종 (pykrx) ──────────────────────────────────────────────────────

def _load_sector_cache() -> dict | None:
    if not os.path.exists(SECTOR_CACHE_FILE):
        return None
    with open(SECTOR_CACHE_FILE, "r", encoding="utf-8") as f:
        cache = json.load(f)
    age = (datetime.now() - datetime.fromisoformat(cache["timestamp"])).total_seconds()
    return cache["data"] if age < 86400 else None  # 24h


def _save_sector_cache(data: dict) -> None:
    with open(SECTOR_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"timestamp": datetime.now().isoformat(), "data": data},
            f,
            ensure_ascii=False,
            indent=2,
        )


def _build_sector_map() -> dict:
    """pykrx로 전 종목 업종 맵 빌드"""
    from pykrx import stock

    sector_map: dict[str, str] = {}
    date = datetime.now()

    for _ in range(5):  # 최근 거래일 탐색
        date_str = date.strftime("%Y%m%d")
        found = False
        for market in ["KOSPI", "KOSDAQ"]:
            try:
                df = stock.get_market_sector_classifications(date_str, market=market)
                if df.empty:
                    continue
                found = True
                # 컬럼명이 버전마다 다를 수 있음
                sector_col = next(
                    (c for c in df.columns if "업종" in c or "sector" in c.lower()),
                    df.columns[0],
                )
                for ticker, row in df.iterrows():
                    sector_map[str(ticker)] = str(row.get(sector_col, "기타"))
            except Exception:
                pass
            time.sleep(0.2)
        if found:
            break
        date -= timedelta(days=1)

    return sector_map


def get_sector_map() -> dict:
    cached = _load_sector_cache()
    if cached:
        return cached
    data = _build_sector_map()
    if data:
        _save_sector_cache(data)
    return data or {}


def get_stock_sector(ticker: str) -> str:
    return get_sector_map().get(str(ticker), "기타")


# ── 테마 (Claude Haiku 추론) — AI 자원 절약을 위해 비활성화 ───────────

# _theme_cache: dict[str, str] = {}  # {ticker: theme_str}
#
#
# def get_stock_theme(
#     ticker: str,
#     name: str,
#     sector: str,
#     client: anthropic.Anthropic,
# ) -> str:
#     """Claude Haiku로 종목 투자 테마 추론 (세션 내 캐시)"""
#     if ticker in _theme_cache:
#         return _theme_cache[ticker]
#
#     prompt = (
#         f"한국 주식 '{name}'({sector} 업종)의 주요 투자 테마를 2~4 단어로 알려줘.\n"
#         f"예시: '방산/드론', 'AI반도체', '2차전지/EV', '로봇/자동화'\n"
#         f"테마 단어만 답해 (설명 없이):"
#     )
#
#     try:
#         resp = client.messages.create(
#             model="claude-haiku-4-5-20251001",
#             max_tokens=25,
#             messages=[{"role": "user", "content": prompt}],
#         )
#         theme = resp.content[0].text.strip().strip("'\"")
#     except Exception:
#         theme = sector  # 실패 시 업종으로 대체
#
#     _theme_cache[ticker] = theme
#     return theme


# ── 신호등 (Claude Haiku + 뉴스) — AI 자원 절약을 위해 비활성화 ────────

# _signal_cache: dict[str, str] = {}  # {sector_or_theme: "🟢"/"🟠"/"🔴"}
#
#
# def get_sector_signal(
#     sector: str,
#     theme: str,
#     client: anthropic.Anthropic,
# ) -> str:
#     """Claude Haiku + Google News로 섹터/테마 시장 흐름 신호등"""
#     from news_analyzer import fetch_google_news
#
#     key = theme if theme and theme != sector else sector
#     if key in _signal_cache:
#         return _signal_cache[key]
#
#     news = fetch_google_news(key, hours=72)
#
#     if not news:
#         _signal_cache[key] = "🟠"
#         return "🟠"
#
#     news_text = "\n".join(f"- {n}" for n in news[:6])
#
#     prompt = (
#         f"한국 주식시장에서 '{key}' 섹터/테마의 최근 3일 시장 흐름을 판단해줘.\n\n"
#         f"뉴스:\n{news_text}\n\n"
#         f"아래 중 하나만 답해 (다른 말 없이):\n"
#         f"GREEN (상승 흐름 / 호재 우세)\n"
#         f"ORANGE (혼조 / 관망)\n"
#         f"RED (하락 흐름 / 악재 우세)"
#     )
#
#     try:
#         resp = client.messages.create(
#             model="claude-haiku-4-5-20251001",
#             max_tokens=15,
#             messages=[{"role": "user", "content": prompt}],
#         )
#         answer = resp.content[0].text.strip().upper()
#         if "GREEN" in answer:
#             signal = "🟢"
#         elif "RED" in answer:
#             signal = "🔴"
#         else:
#             signal = "🟠"
#     except Exception:
#         signal = "🟠"
#
#     _signal_cache[key] = signal
#     return signal


# ── 결과 보강 ─────────────────────────────────────────────────────────

def enrich_results(
    results: list[dict],
    client: anthropic.Anthropic,
) -> list[dict]:
    """
    스캔 결과에 업종/테마/신호등 추가.
    — 테마(AI) + 신호등(AI+뉴스)은 비활성화, 업종만 남김.
    """
    sector_map = get_sector_map()

    for r in results:
        ticker = r["ticker"]
        sector = sector_map.get(str(ticker), "기타")
        r["sector"] = sector
        # theme = get_stock_theme(ticker, name, sector, client)  # AI 호출 비활성화
        # signal = get_sector_signal(sector, theme, client)      # AI 호출 비활성화

    return results
