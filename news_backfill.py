"""
news_backfill.py
GDELT Project API를 활용한 역사 뉴스 백필

GDELT: Global Database of Events, Language, and Tone
  - 무료, API 키 불필요
  - 2015년~ 전세계 뉴스 커버
  - URL: https://api.gdeltproject.org/api/v2/doc/doc

용법:
  python news_backfill.py                   → 2020-01부터 현재까지 백필
  python news_backfill.py --start 2022-01   → 2022년 1월부터
  python news_backfill.py --weeks 4          → 최근 4주만
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import database

_HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; NewsBackfill/1.0)'}

# GDELT 검색 쿼리 (한국 관련 글로벌 이슈 커버)
_GDELT_QUERIES = [
    'Korea economy trade geopolitics',
    'US China trade war technology',
    'Iran war Middle East conflict',
    'Federal Reserve interest rate inflation',
    'North Korea nuclear missile',
]

GDELT_URL = 'https://api.gdeltproject.org/api/v2/doc/doc'


def _week_range(year: int, week: int) -> tuple[str, str]:
    """주 번호 → (시작일, 종료일) 문자열 (YYYYMMDDHHMMSS)"""
    jan4 = datetime(year, 1, 4)
    start = jan4 - timedelta(days=jan4.weekday()) + timedelta(weeks=week - 1)
    end   = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return start.strftime('%Y%m%d%H%M%S'), end.strftime('%Y%m%d%H%M%S')


def _current_week() -> tuple[int, int]:
    now = datetime.now()
    return now.isocalendar()[:2]  # (year, week)


def fetch_gdelt_week(year: int, week: int, max_per_query: int = 100) -> list[dict]:
    """GDELT API에서 해당 주 기사 수집"""
    start_dt, end_dt = _week_range(year, week)
    articles = []
    seen_urls = set()

    for query in _GDELT_QUERIES:
        try:
            params = {
                'query':          query,
                'mode':           'artlist',
                'maxrecords':     str(max_per_query),
                'format':         'json',
                'STARTDATETIME':  start_dt,
                'ENDDATETIME':    end_dt,
                'sort':           'DateDesc',
            }
            r = requests.get(GDELT_URL, params=params, headers=_HEADERS, timeout=15)
            if not r.ok:
                print(f'  [GDELT] {query[:30]}... HTTP {r.status_code}')
                continue
            data = r.json()
            arts = data.get('articles', [])
            for a in arts:
                url = a.get('url', '').strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                title = a.get('title', '').strip()
                pub   = a.get('seendate', '')
                # seendate format: YYYYMMDDTHHMMSSZ
                try:
                    dt = datetime.strptime(pub[:15], '%Y%m%dT%H%M%S')
                    published_at = dt.isoformat()
                except Exception:
                    published_at = datetime.now().isoformat()
                articles.append({
                    'source':      'gdelt',
                    'source_name': 'GDELT (글로벌)',
                    'source_type': 'rss_global',
                    'category':    '국제',
                    'title':       title,
                    'url':         url,
                    'description': a.get('socialimage', ''),
                    'published_at': published_at,
                    'sentiment':   '중립',
                    'tags':        json.dumps([query.split()[0]], ensure_ascii=False),
                })
        except Exception as e:
            print(f'  [GDELT] 오류: {e}')
        time.sleep(0.5)

    return articles


def backfill_one_week(year: int, week: int, claude_api_key: str = '') -> int:
    """주 1개 백필 → DB 저장. 저장된 기사 수 반환."""
    week_key = f'{year:04d}-W{week:02d}'

    # 이미 처리된 주 스킵
    status = database.get_news_backfill_status()
    if week_key in status.get('done_weeks', set()):
        print(f'  ✅ {week_key} 이미 완료')
        return 0

    print(f'  📅 {week_key} 백필 시작...')
    articles = fetch_gdelt_week(year, week)
    if not articles:
        print(f'  ⚠️  {week_key} 기사 없음')
        return 0

    saved = database.save_news_articles(articles)
    database.save_news_backfill_log(week_key, len(articles), analyzed=0)
    print(f'  ✅ {week_key}: {len(articles)}건 수집, {saved}건 저장')
    return saved


def run_backfill(start: str = '2020-01', max_weeks: int = 10,
                 claude_api_key: str = '') -> dict:
    """
    start: 'YYYY-WW' 또는 'YYYY-MM' 형식
    max_weeks: 이번 실행에서 처리할 최대 주 수
    역순으로 수집 (현재→과거 방향)
    """
    cur_year, cur_week = _current_week()

    # start 파싱
    if '-W' in start:
        sy, sw = map(int, start.split('-W'))
    elif '-' in start:
        parts = start.split('-')
        sy = int(parts[0])
        sm = int(parts[1])
        # 해당 월의 첫 번째 주 계산
        sw = datetime(sy, sm, 1).isocalendar()[1]
    else:
        sy, sw = 2020, 1

    # 이미 완료된 주 목록
    status      = database.get_news_backfill_status()
    done_weeks  = status.get('done_weeks', set())
    total_done  = 0

    # 가장 오래된 미완료 주부터 역순으로 채우기
    # 현재 주에서 start 주까지 목록 생성
    all_weeks = []
    y, w = cur_year, cur_week
    while (y, w) >= (sy, sw):
        wk = f'{y:04d}-W{w:02d}'
        if wk not in done_weeks:
            all_weeks.append((y, w))
        w -= 1
        if w == 0:
            y -= 1
            w = 52

    # 오래된 것부터 (리스트 역순)
    all_weeks.reverse()
    to_process = all_weeks[:max_weeks]

    print(f'[백필] {len(to_process)}주 처리 예정 (start={start}, max={max_weeks})')
    total_articles = 0
    for y, w in to_process:
        n = backfill_one_week(y, w, claude_api_key)
        total_articles += n
        time.sleep(1)

    total_done += len(to_process)
    remaining  = len(all_weeks) - len(to_process)
    print(f'[백필] 완료: {total_done}주, 기사: {total_articles}건, 남은 주: {remaining}주')
    return {
        'processed_weeks': total_done,
        'total_articles':  total_articles,
        'remaining_weeks': remaining,
        'next_start':      f'{to_process[-1][0]:04d}-W{to_process[-1][1]:02d}' if to_process else start,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='뉴스 역사 백필 (GDELT)')
    parser.add_argument('--start',  default='2020-01', help='시작 기간 (YYYY-MM 또는 YYYY-WW)')
    parser.add_argument('--weeks',  type=int, default=10, help='이번 실행에 처리할 최대 주 수')
    args = parser.parse_args()

    database.init_db()
    result = run_backfill(start=args.start, max_weeks=args.weeks)
    print('\n결과:', result)
