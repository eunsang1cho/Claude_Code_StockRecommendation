"""
news_collector.py
종합 뉴스 수집 & Claude AI 정세 분석

수집 소스:
  [국내 RSS]  연합뉴스(전체/경제/국제/정치), 한겨레, 경향, 매일경제, 한국경제
  [해외 RSS]  Reuters(세계/경제), BBC, Guardian, Al Jazeera, WSJ
  [YouTube]   JTBC, YTN, MBC, KBS, SBS, 뉴스공장, 매불쇼 (YouTube RSS, API 키 불필요)
  [AI 분석]   Claude Haiku → 핵심요약, 리스크, 단기전망, 한국시장영향
"""

import json
import re
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests

# ── RSS 소스 정의 ─────────────────────────────────────────────────────────
# (url, 표시명, source_type, category)
RSS_SOURCES = {
    # 국내
    'yna_all':       ('https://www.yna.co.kr/rss/all.xml',              '연합뉴스',     'rss_kr',    '종합'),
    'yna_economy':   ('https://www.yna.co.kr/rss/economy.xml',          '연합뉴스경제', 'rss_kr',    '경제'),
    'yna_intl':      ('https://www.yna.co.kr/rss/international.xml',    '연합뉴스국제', 'rss_kr',    '국제'),
    'yna_politics':  ('https://www.yna.co.kr/rss/politics.xml',         '연합뉴스정치', 'rss_kr',    '정치'),
    'hani':          ('https://www.hani.co.kr/rss/',                    '한겨레',       'rss_kr',    '종합'),
    'khan':          ('https://www.khan.co.kr/rss/rssdata/khanman.xml', '경향신문',     'rss_kr',    '종합'),
    'mk_economy':    ('https://www.mk.co.kr/rss/40300001/',             '매일경제',     'rss_kr',    '경제'),
    'hankyung':      ('https://www.hankyung.com/feed/economy',          '한국경제',     'rss_kr',    '경제'),
    # 해외
    'reuters_world': ('https://feeds.reuters.com/Reuters/worldNews',    'Reuters',      'rss_global', '국제'),
    'reuters_biz':   ('https://feeds.reuters.com/reuters/businessNews', 'Reuters경제',  'rss_global', '경제'),
    'bbc_world':     ('http://feeds.bbci.co.uk/news/world/rss.xml',     'BBC',          'rss_global', '국제'),
    'guardian':      ('https://www.theguardian.com/world/rss',          'Guardian',     'rss_global', '국제'),
    'aljazeera':     ('https://www.aljazeera.com/xml/rss/all.xml',      'AlJazeera',    'rss_global', '국제'),
    'wsj':           ('https://feeds.a.dj.com/rss/RSSWorldNews.xml',    'WSJ',          'rss_global', '국제'),
}

# ── YouTube 채널 (YouTube RSS: API 키 불필요) ─────────────────────────────
# (channel_id, 표시명, source_type, category)
# channel_id 추정값 - 잘못된 경우 자동 스킵
YOUTUBE_CHANNELS = {
    # 방송사 뉴스 (채널 ID 검증 완료)
    'ytn':         ('UChlgI3UHCOnwUGzWzbJ3H5w',  'YTN 뉴스24',  'yt_news_kr',  '뉴스'),
    'sbs_news':    ('UCkinYTS9IHqOEwR1Sze2JTw',   'SBS 뉴스',    'yt_news_kr',  '뉴스'),
    # 팟캐스트 (채널 ID 검증 완료)
    'maebulshow':  ('UCMYhq9OyGI5UEz_NTAoHY7A',  '매불쇼',      'yt_podcast',  '팟캐스트'),
    # 아래는 직접 추가 가능 (https://www.youtube.com/@채널핸들 → 소스에서 externalId 확인)
    # 'jtbc_news': ('CHANNEL_ID', 'JTBC 뉴스룸', 'yt_news_kr', '뉴스'),
    # 'kbs_news':  ('CHANNEL_ID', 'KBS 뉴스',    'yt_news_kr', '뉴스'),
}

_HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; NewsCollector/1.0)'}

# ── 감성 키워드 분류 ─────────────────────────────────────────────────────
_ALERT_KW    = ['핵폭탄', '전면전', '대량살상', '긴급', '비상선포', 'nuclear', 'emergency', 'catastrophe', 'invasion', 'explosion']
_DANGER_KW   = ['위험', '위기', '하락', '폭락', '붕괴', '손실', '우려', '경고', '갈등', '전쟁', '충돌',
                'war', 'crisis', 'crash', 'collapse', 'risk', 'warning', 'threat', 'attack', 'conflict', 'sanctions']
_POSITIVE_KW = ['상승', '호조', '성장', '개선', '타결', '합의', '회복', '급등', '돌파', '기대',
                'rise', 'growth', 'recovery', 'deal', 'agreement', 'surge', 'breakthrough', 'rally', 'gain']


def _detect_sentiment(title: str, desc: str = '') -> str:
    text = (title + ' ' + desc).lower()
    if any(k in text for k in _ALERT_KW):
        return '경고'
    if any(k in text for k in _DANGER_KW):
        return '부정'
    if any(k in text for k in _POSITIVE_KW):
        return '긍정'
    return '중립'


def _parse_dt(text: str) -> datetime | None:
    """RFC 2822 또는 ISO 8601 날짜 파싱"""
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).replace(tzinfo=None)
    except Exception:
        pass
    for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(text[:19], fmt)
        except Exception:
            pass
    return None


def _strip_html(text: str) -> str:
    """HTML 태그 제거"""
    if not text:
        return ''
    return re.sub(r'<[^>]+>', '', text).strip()[:300]


# ── RSS 수집 ─────────────────────────────────────────────────────────────

def fetch_rss_articles(days: int = 1) -> list[dict]:
    """모든 RSS 소스에서 기사 수집. 최근 N일 이내 기사만 반환."""
    since  = datetime.utcnow() - timedelta(days=days)
    seen   = set()
    result = []

    for key, (url, name, stype, category) in RSS_SOURCES.items():
        try:
            r = requests.get(url, headers=_HEADERS, timeout=10)
            if not r.ok:
                continue
            root = ET.fromstring(r.content)
            for item in root.findall('.//item'):
                link_el  = item.find('link')
                title_el = item.find('title')
                if title_el is None or link_el is None:
                    continue
                title = (title_el.text or '').strip()
                link  = (link_el.text or '').strip()
                if not title or not link or link in seen:
                    continue

                pub_el = item.find('pubDate')
                dt     = _parse_dt(pub_el.text if pub_el is not None else None)
                if dt and dt < since:
                    continue

                desc_el = item.find('description')
                desc    = _strip_html(desc_el.text if desc_el is not None else '')

                seen.add(link)
                result.append({
                    'source':      key,
                    'source_name': name,
                    'source_type': stype,
                    'category':    category,
                    'title':       title,
                    'url':         link,
                    'description': desc,
                    'published_at': dt.isoformat() if dt else datetime.now().isoformat(),
                    'sentiment':   _detect_sentiment(title, desc),
                    'tags':        '[]',
                })
        except Exception as e:
            print(f'[뉴스RSS] {key} 오류: {e}')
        time.sleep(0.25)

    result.sort(key=lambda x: x['published_at'], reverse=True)
    print(f'[뉴스RSS] {len(result)}건 수집 ({len(RSS_SOURCES)}개 소스)')
    return result


# ── YouTube RSS 수집 ──────────────────────────────────────────────────────

def fetch_youtube_videos(days: int = 3) -> list[dict]:
    """YouTube 채널 RSS에서 최근 영상 목록 수집."""
    since  = datetime.utcnow() - timedelta(days=days)
    seen   = set()
    result = []
    yt_ns  = 'http://www.youtube.com/xml/schemas/2015'
    media_ns = 'http://search.yahoo.com/mrss/'

    for key, (ch_id, name, stype, category) in YOUTUBE_CHANNELS.items():
        url = f'https://www.youtube.com/feeds/videos.xml?channel_id={ch_id}'
        try:
            r = requests.get(url, headers=_HEADERS, timeout=10)
            if not r.ok:
                print(f'[YT] {name} 스킵 ({r.status_code})')
                continue
            root = ET.fromstring(r.content)
            ns = {'atom': 'http://www.w3.org/2005/Atom',
                  'yt':   yt_ns,
                  'media': media_ns}
            for entry in root.findall('atom:entry', ns):
                link_el = entry.find('atom:link', ns)
                title_el = entry.find('atom:title', ns)
                if link_el is None or title_el is None:
                    continue
                link  = link_el.get('href', '').strip()
                title = (title_el.text or '').strip()
                if not link or not title or link in seen:
                    continue

                pub_el = entry.find('atom:published', ns)
                dt     = _parse_dt(pub_el.text if pub_el is not None else None)
                if dt and dt < since:
                    continue

                desc = ''
                media_group = entry.find('media:group', ns)
                if media_group is not None:
                    desc_el = media_group.find('media:description', ns)
                    if desc_el is not None:
                        desc = _strip_html(desc_el.text or '')

                seen.add(link)
                result.append({
                    'source':      key,
                    'source_name': name,
                    'source_type': stype,
                    'category':    category,
                    'title':       title,
                    'url':         link,
                    'description': desc,
                    'published_at': dt.isoformat() if dt else datetime.now().isoformat(),
                    'sentiment':   _detect_sentiment(title, desc),
                    'tags':        '[]',
                })
        except Exception as e:
            print(f'[YT] {name} 오류: {e}')
        time.sleep(0.2)

    result.sort(key=lambda x: x['published_at'], reverse=True)
    print(f'[YT] {len(result)}건 수집 ({len(YOUTUBE_CHANNELS)}개 채널)')
    return result


# ── Claude AI 분석 ────────────────────────────────────────────────────────

def analyze_with_claude(articles: list[dict], claude_api_key: str) -> dict:
    """
    수집된 기사 목록을 Claude Haiku로 분석.
    반환: {key_summary, geopolitical_risks, economic_outlook, korea_impact,
           trending_keywords, sentiment_score, prediction_1w, prediction_1m}
    """
    if not claude_api_key or not articles:
        return {}
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=claude_api_key)
    except Exception as e:
        print(f'[뉴스AI] Claude 초기화 오류: {e}')
        return {}

    today = datetime.now().strftime('%Y년 %m월 %d일')

    # 국내/해외 기사 분리하여 상위 30개씩 추출
    kr_articles  = [a for a in articles if a['source_type'].endswith('_kr') or a['source_type'] == 'rss_kr'][:30]
    gl_articles  = [a for a in articles if a['source_type'] == 'rss_global'][:20]
    yt_articles  = [a for a in articles if 'yt' in a['source_type']][:15]

    def fmt_articles(arts: list[dict]) -> str:
        return '\n'.join(f'- [{a["source_name"]}] {a["title"]}' for a in arts)

    prompt = f"""오늘({today}) 수집된 국내외 주요 뉴스를 분석하여 정세 흐름과 경제 전망을 종합 평가해주세요.

[국내 뉴스 ({len(kr_articles)}건)]
{fmt_articles(kr_articles) or '(없음)'}

[해외 뉴스 ({len(gl_articles)}건)]
{fmt_articles(gl_articles) or '(없음)'}

[유튜브/팟캐스트 ({len(yt_articles)}건)]
{fmt_articles(yt_articles) or '(없음)'}

위 뉴스를 분석하여 아래 JSON 형식으로 반환하세요 (한국어로):

{{
  "key_summary": ["핵심 포인트 1", "핵심 포인트 2", "핵심 포인트 3", "핵심 포인트 4", "핵심 포인트 5"],
  "geopolitical_risks": ["지정학 리스크 1", "지정학 리스크 2", "지정학 리스크 3"],
  "economic_outlook": "경제 전망 2-3문장",
  "korea_impact": "한국 주식시장/경제 영향 2-3문장",
  "trending_keywords": ["키워드1", "키워드2", "키워드3", "키워드4", "키워드5"],
  "sentiment_score": 0,
  "prediction_1w": "1주 이내 주요 이벤트/방향성 예측",
  "prediction_1m": "1개월 이내 흐름 예측"
}}

sentiment_score: -100(매우 부정)~0(중립)~+100(매우 긍정), JSON만 출력"""

    try:
        resp = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=1200,
            messages=[{'role': 'user', 'content': prompt}],
        )
        raw = resp.content[0].text
        m   = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        print(f'[뉴스AI] Claude 분석 오류: {e}')
    return {}


# ── 통합 수집 ─────────────────────────────────────────────────────────────

def fetch_and_analyze(claude_api_key: str = '', days: int = 1) -> dict:
    """
    메인 진입점. RSS + YouTube 수집 후 Claude 분석.
    반환: {articles: [...], analysis: {...}, collected_at: str}
    """
    print('[뉴스수집] ① RSS 수집...')
    rss_articles = fetch_rss_articles(days=days)
    print('[뉴스수집] ② YouTube 수집...')
    yt_articles  = fetch_youtube_videos(days=max(days, 3))
    all_articles = rss_articles + yt_articles

    print(f'[뉴스수집] ③ Claude 분석 ({len(all_articles)}건)...')
    analysis = analyze_with_claude(all_articles, claude_api_key) if claude_api_key else {}

    return {
        'articles':     all_articles,
        'analysis':     analysis,
        'article_count': len(all_articles),
        'collected_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }
