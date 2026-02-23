"""
news_analyzer.py
Google News RSS + DART 공시 + Claude AI 분석
"""

import time
import urllib.parse
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup
import anthropic

_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}


# ── Google News RSS ───────────────────────────────────────────────────

def fetch_google_news(stock_name: str, hours: int = 18) -> list[str]:
    """Google News RSS로 종목 관련 뉴스 수집 (최근 N시간)"""
    query = urllib.parse.quote(stock_name)
    url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
    titles: list[str] = []
    cutoff = datetime.now().astimezone() - timedelta(hours=hours)

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        soup = BeautifulSoup(resp.content, "lxml-xml")

        for item in soup.find_all("item"):
            title_el = item.find("title")
            pub_el = item.find("pubDate")
            if not title_el:
                continue

            title = title_el.get_text(strip=True)

            # 시간 필터 (파싱 실패 시 포함)
            if pub_el:
                try:
                    pub_dt = parsedate_to_datetime(pub_el.get_text())
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pass

            titles.append(title)

        return titles[:8]
    except Exception:
        return []


# ── DART 공시 ─────────────────────────────────────────────────────────

def fetch_dart(ticker: str, dart_key: str, hours: int = 18) -> list[str]:
    """DART 전자공시 조회"""
    if not dart_key:
        return []

    since = (datetime.now() - timedelta(hours=hours)).strftime("%Y%m%d")
    today = datetime.now().strftime("%Y%m%d")

    url = "https://opendart.fss.or.kr/api/list.json"
    params = {
        "crtfc_key": dart_key,
        "stock_code": ticker,
        "bgn_de": since,
        "end_de": today,
        "page_count": 10,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("status") == "000":
            return [item["report_nm"] for item in data.get("list", [])]
    except Exception:
        pass

    return []


# ── Claude AI 분석 ────────────────────────────────────────────────────

def fetch_naver_news(ticker: str, hours: int = 18) -> list[str]:
    """하위 호환용 — fetch_google_news로 위임 (종목명 없을 때 빈 리스트)"""
    return []


def analyze_with_claude(
    ticker: str,
    name: str,
    news: list[str],
    disclosures: list[str],
    client: anthropic.Anthropic,
) -> str | None:
    """뉴스 + 공시를 Claude로 분석해 호재/악재 판단"""
    if not news and not disclosures:
        return None

    news_block = "\n".join(f"- {n}" for n in news) or "없음"
    disc_block = "\n".join(f"- {d}" for d in disclosures) or "없음"

    prompt = f"""한국 주식 {name}({ticker})의 최근 뉴스와 공시를 분석해줘.

[뉴스]
{news_block}

[공시]
{disc_block}

다음 형식으로 3줄 이내로 간결하게 답해줘:
1. 판단: [강한호재 / 호재 / 중립 / 악재 / 강한악재]
2. 핵심: (한 줄 요약)
3. 주가영향: (간단히)"""

    try:
        resp = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"분석 실패: {e}"


# ── 워치리스트 전체 뉴스 분석 ────────────────────────────────────────

def analyze_watchlist(
    stocks: list[dict],
    dart_key: str,
    claude_client: anthropic.Anthropic,
) -> list[dict]:
    """
    워치리스트 종목 순회 → 뉴스/공시 수집 → Claude 분석
    returns: [{ticker, name, pattern, news, disclosures, analysis}]
    """
    results = []

    for s in stocks:
        ticker = s["ticker"]
        name = s["name"]

        news = fetch_google_news(name)
        disclosures = fetch_dart(ticker, dart_key)
        time.sleep(0.3)

        if not news and not disclosures:
            continue

        analysis = analyze_with_claude(ticker, name, news, disclosures, claude_client)
        results.append({
            "ticker": ticker,
            "name": name,
            "pattern": s.get("pattern", ""),
            "news": news,
            "disclosures": disclosures,
            "analysis": analysis,
        })

    return results


def format_news_result(r: dict) -> str:
    """뉴스 분석 결과 텔레그램 포맷"""
    p = r.get("pattern", "")
    emoji = {"골삼이": "🔵", "골든샘플": "🟢", "레드삼각": "🔴", "골삼이(상승초입)": "🚀"}.get(p, "📌")

    lines = [
        f"{emoji} *{r['name']}* \\({r['ticker']}\\)",
        f"```",
        r["analysis"] or "분석 없음",
        f"```",
    ]
    return "\n".join(lines)
