"""
block_deal_tracker.py
대인(VIP) 대규모 블록딜 추적기

데이터 소스:
  1. House Stock Watcher  — 미 하원 STOCK Act 공시 (housestockwatcher.com)
  2. Senate Stock Watcher — 미 상원 STOCK Act 공시 (senatestockwatcher.com)
  3. SEC EDGAR Form 4     — 상장사 내부자(임원·10%+ 주주) 거래 공시

필터 기준:
  - 의회 의원: $15,001 이상 거래 (공시 최소 단위)
  - SEC Form 4: 직접 신고 내부자 거래 (최근 7일)

스케줄: 매일 07:00 (장 시작 전)
"""

import re
import time
from datetime import datetime, timedelta

import requests

_HEADERS = {
    'User-Agent': 'BlockDealTracker research@stockbot.local',
    'Accept': 'application/json',
}

# 금액 범위 → 중간값 매핑 (의회 공시 표준)
_AMOUNT_MAP: dict[str, int] = {
    '$1,001 - $15,000':       8000,
    '$15,001 - $50,000':     32500,
    '$50,001 - $100,000':    75000,
    '$100,001 - $250,000':  175000,
    '$250,001 - $500,000':  375000,
    '$500,001 - $1,000,000': 750000,
    '$1,000,001 - $5,000,000': 3000000,
    '$5,000,001 - $25,000,000': 15000000,
    '$25,000,001 - $50,000,000': 37500000,
    'Over $50,000,000': 75000000,
}

# 소액 거래 필터 (이 금액 미만은 제외)
_MIN_AMOUNT = 15001


def _parse_amount(amount_str: str) -> int:
    """공시 금액 문자열 → 정수(중간값) 변환."""
    if not amount_str:
        return 0
    s = amount_str.strip()
    # 정확한 금액이 있으면 파싱
    clean = re.sub(r'[$,]', '', s)
    if clean.isdigit():
        return int(clean)
    # 범위 문자열 → 매핑
    return _AMOUNT_MAP.get(s, 0)


def _fmt_amount(val: int) -> str:
    """정수 금액 → 읽기 좋은 문자열."""
    if val >= 1_000_000:
        return f'${val / 1_000_000:.1f}M'
    if val >= 1_000:
        return f'${val / 1_000:.0f}K'
    return f'${val:,}'


# ── 1. House Stock Watcher ────────────────────────────────────────────

def fetch_house_trades(days: int = 30) -> list[dict]:
    """
    하원 STOCK Act 공시 수집.
    반환: [{source, person, role, ticker, trade_type, amount_val, amount_str,
             trade_date, filed_date, asset_description, party, state}]
    """
    url = 'https://house-stock-watcher-data.s3-us-east-2.amazonaws.com/data/all_transactions.json'
    try:
        r = requests.get(url, headers=_HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f'[블록딜] 하원 API 오류: {e}')
        return []

    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    results = []

    for tx in data:
        trade_date = tx.get('transaction_date', '') or ''
        # 날짜 파싱 (MM/DD/YYYY 또는 YYYY-MM-DD)
        if '/' in trade_date:
            try:
                dt = datetime.strptime(trade_date, '%m/%d/%Y')
                trade_date = dt.strftime('%Y-%m-%d')
            except ValueError:
                pass
        if trade_date < cutoff:
            continue

        ticker = (tx.get('ticker') or '').strip().upper()
        if not ticker or ticker in ('--', 'N/A', ''):
            ticker = ''

        amount_str = tx.get('amount', '') or ''
        amount_val = _parse_amount(amount_str)
        if amount_val < _MIN_AMOUNT:
            continue

        trade_type = tx.get('type', '') or ''
        # 매수/매도 한글 변환
        type_kr = _normalize_type(trade_type)

        results.append({
            'source':            'House',
            'person':            tx.get('representative', ''),
            'role':              '하원의원',
            'party':             tx.get('party', ''),
            'state':             tx.get('state', ''),
            'ticker':            ticker,
            'asset_description': tx.get('asset_description', ''),
            'trade_type':        type_kr,
            'trade_type_raw':    trade_type,
            'amount_val':        amount_val,
            'amount_str':        _fmt_amount(amount_val),
            'amount_raw':        amount_str,
            'trade_date':        trade_date,
            'filed_date':        tx.get('disclosure_date', ''),
            'district':          tx.get('district', ''),
            'link':              tx.get('ptr_link', ''),
        })

    results.sort(key=lambda x: x['trade_date'], reverse=True)
    return results


# ── 2. Senate Stock Watcher ───────────────────────────────────────────

def fetch_senate_trades(days: int = 30) -> list[dict]:
    """
    상원 STOCK Act 공시 수집.
    반환: House와 동일 구조
    """
    url = 'https://senate-stock-watcher-data.s3-us-east-2.amazonaws.com/aggregate/all_transactions.json'
    try:
        r = requests.get(url, headers=_HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f'[블록딜] 상원 API 오류: {e}')
        return []

    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    results = []

    for tx in data:
        trade_date = tx.get('transaction_date', '') or ''
        if '/' in trade_date:
            try:
                dt = datetime.strptime(trade_date, '%m/%d/%Y')
                trade_date = dt.strftime('%Y-%m-%d')
            except ValueError:
                pass
        if trade_date < cutoff:
            continue

        ticker = (tx.get('ticker') or '').strip().upper()
        if ticker in ('--', 'N/A', ''):
            ticker = ''

        amount_str = tx.get('amount', '') or ''
        amount_val = _parse_amount(amount_str)
        if amount_val < _MIN_AMOUNT:
            continue

        trade_type = tx.get('type', '') or ''
        type_kr = _normalize_type(trade_type)

        results.append({
            'source':            'Senate',
            'person':            tx.get('senator', ''),
            'role':              '상원의원',
            'party':             tx.get('party', ''),
            'state':             tx.get('state', ''),
            'ticker':            ticker,
            'asset_description': tx.get('asset_description', ''),
            'trade_type':        type_kr,
            'trade_type_raw':    trade_type,
            'amount_val':        amount_val,
            'amount_str':        _fmt_amount(amount_val),
            'amount_raw':        amount_str,
            'trade_date':        trade_date,
            'filed_date':        tx.get('disclosure_date', ''),
            'district':          '',
            'link':              tx.get('ptr_link', ''),
        })

    results.sort(key=lambda x: x['trade_date'], reverse=True)
    return results


# ── 3. SEC EDGAR Form 4 ───────────────────────────────────────────────

_FORM4_SEARCH = 'https://efts.sec.gov/LATEST/search-index?q=%22form+4%22&forms=4&dateRange=custom&startdt={start}&enddt={end}&hits.hits._source=file_date,entity_name,file_num,period_of_report'

_FORM4_SUBMISSIONS = 'https://efts.sec.gov/LATEST/search-index?forms=4&dateRange=custom&startdt={start}&enddt={end}&hits.hits.total=true&hits.hits._source=period_of_report,entity_name,file_date&hits.hits.highlight=false'

# EDGAR 최신 Form 4 전용 엔드포인트
_EDGAR_EFTS = 'https://efts.sec.gov/LATEST/search-index'

# 대형 내부자 추적 대상 (이름 → 역할)
_TRACKED_INSIDERS: dict[str, str] = {
    # 전 대통령 관련 기업 임원
    'TRUMP': '트럼프 관련',
    'MUSK': '머스크 관련',
    'BEZOS': '베조스 관련',
    'SOROS': '소로스 관련',
    'ICAHN': '아이칸 관련',
    # 빅테크 CEO
    'ZUCKERBERG': '주커버그',
    'COOK TIM': '팀 쿡',
    'NADELLA': '나델라',
    'PICHAI': '피차이',
}


def _fetch_edgar_form4_recent(days: int = 7) -> list[dict]:
    """
    SEC EDGAR 풀텍스트 검색으로 최근 Form 4 수집.
    반환: [{entity_name, cik, filed_date, period, accession}]
    """
    end_dt = datetime.now().strftime('%Y-%m-%d')
    start_dt = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    url = (
        f'https://efts.sec.gov/LATEST/search-index?forms=4'
        f'&dateRange=custom&startdt={start_dt}&enddt={end_dt}'
        f'&hits.hits._source=file_date,entity_name,period_of_report,file_num'
        f'&hits.hits.total=true&hits.hits.highlight=false'
        f'&hits.hits.size=200'
    )

    # EDGAR 공식 검색 API 사용
    search_url = f'https://efts.sec.gov/LATEST/search-index?forms=4&dateRange=custom&startdt={start_dt}&enddt={end_dt}'

    try:
        params = {
            'forms': '4',
            'dateRange': 'custom',
            'startdt': start_dt,
            'enddt': end_dt,
        }
        r = requests.get(
            'https://efts.sec.gov/LATEST/search-index',
            params=params,
            headers=_HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        return r.json().get('hits', {}).get('hits', [])
    except Exception as e:
        print(f'[블록딜] EDGAR Form4 검색 오류: {e}')
        return []


def _parse_form4_xml(cik_int: int, accn: str) -> list[dict]:
    """
    Form 4 XML 파싱 → 개별 거래 목록 반환.
    반환: [{trade_type, ticker, shares, price_per_share, total_value, trade_date}]
    """
    import xml.etree.ElementTree as ET
    accn_clean = accn.replace('-', '')
    base = f'https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn_clean}'
    idx_url = f'{base}/{accn_clean}-index.json'

    xml_name = None
    try:
        r = requests.get(idx_url, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        for f in r.json().get('directory', {}).get('item', []):
            name = f.get('name', '').lower()
            if name.endswith('.xml') and name != 'primary_doc.xml':
                xml_name = f['name']
                break
    except Exception:
        return []

    if not xml_name:
        return []

    time.sleep(0.3)
    try:
        r = requests.get(f'{base}/{xml_name}', headers=_HEADERS, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception:
        return []

    ns = ''
    if root.tag.startswith('{'):
        ns = root.tag.split('}')[0] + '}'

    trades = []

    def _txt(el, tag):
        found = el.find(f'{ns}{tag}')
        return found.text.strip() if found is not None and found.text else ''

    # nonDerivativeTransaction
    for tx in root.findall(f'.//{ns}nonDerivativeTransaction'):
        try:
            shares = float(_txt(tx, 'transactionShares') or '0')
            price  = float(_txt(tx, 'transactionPricePerShare') or '0')
            total  = shares * price
            if total < 50_000:  # $5만 미만 소액 제외
                continue
            code = _txt(tx, 'transactionCode')
            trades.append({
                'trade_type':   _normalize_form4_code(code),
                'trade_code':   code,
                'ticker':       _txt(tx, 'securityTitle'),
                'shares':       int(shares),
                'price':        price,
                'amount_val':   int(total),
                'amount_str':   _fmt_amount(int(total)),
                'trade_date':   _txt(tx, 'transactionDate'),
            })
        except (ValueError, TypeError):
            continue

    return trades


def fetch_form4_insiders(days: int = 7) -> list[dict]:
    """
    SEC EDGAR Form 4 최근 대형 내부자 거래 수집.
    EDGAR 제출 RSS를 통해 최신 파일링을 수집하고,
    대규모 거래($50K+)만 필터링.
    """
    end_dt = datetime.now().strftime('%Y-%m-%d')
    start_dt = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    # EDGAR submissions RSS (Form 4 전용)
    rss_url = (
        f'https://www.sec.gov/cgi-bin/browse-edgar'
        f'?action=getcurrent&type=4&dateb=&owner=include&count=100'
        f'&search_text=&output=atom'
    )

    results = []
    try:
        import xml.etree.ElementTree as ET
        r = requests.get(rss_url, headers={
            'User-Agent': 'BlockDealTracker research@stockbot.local',
            'Accept': 'application/atom+xml, text/xml',
        }, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        ns_atom = 'http://www.w3.org/2005/Atom'

        for entry in root.findall(f'{{{ns_atom}}}entry'):
            title_el = entry.find(f'{{{ns_atom}}}title')
            link_el  = entry.find(f'{{{ns_atom}}}link')
            updated_el = entry.find(f'{{{ns_atom}}}updated')
            summ_el  = entry.find(f'{{{ns_atom}}}summary')

            title   = title_el.text if title_el is not None else ''
            link    = link_el.get('href', '') if link_el is not None else ''
            updated = (updated_el.text or '')[:10] if updated_el is not None else ''
            summary = summ_el.text if summ_el is not None else ''

            if updated < start_dt:
                continue

            # 이름 + CIK 파싱: "4 - COMPANY NAME (0001234567) (Filed: ...)"
            m = re.search(r'4 - (.+?) \((\d+)\)', title)
            if not m:
                continue
            entity_name = m.group(1).strip()
            cik_str     = m.group(2).strip()

            # accession number 추출 (링크에서)
            accn_m = re.search(r'/(\d{18})/', link)
            if not accn_m:
                continue
            accn = accn_m.group(1)
            # 18자리 → xx-xxxxxx-xxxxxxxx 형식
            accn_fmt = f'{accn[:10]}-{accn[10:12]}-{accn[12:]}'

            results.append({
                'source':      'Form4',
                'entity_name': entity_name,
                'cik':         cik_str,
                'accession':   accn_fmt,
                'filed_date':  updated,
                'link':        link,
            })

    except Exception as e:
        print(f'[블록딜] EDGAR RSS 오류: {e}')

    # 대형 거래만 상세 파싱 (처음 50건, 과부하 방지)
    output = []
    seen_cik = set()
    for filing in results[:50]:
        cik_int = int(filing['cik'])
        accn    = filing['accession']
        entity  = filing['entity_name']

        if cik_int in seen_cik:
            continue
        seen_cik.add(cik_int)

        time.sleep(0.2)
        trades = _parse_form4_xml(cik_int, accn)
        for t in trades:
            output.append({
                'source':            'Form4',
                'person':            entity,
                'role':              'SEC 내부자',
                'party':             '',
                'state':             '',
                'ticker':            t['ticker'],
                'asset_description': t['ticker'],
                'trade_type':        t['trade_type'],
                'trade_type_raw':    t.get('trade_code', ''),
                'amount_val':        t['amount_val'],
                'amount_str':        t['amount_str'],
                'amount_raw':        f"{t['shares']:,} shares @ ${t['price']:.2f}",
                'trade_date':        t['trade_date'],
                'filed_date':        filing['filed_date'],
                'district':          '',
                'link':              filing['link'],
            })

    output.sort(key=lambda x: x['trade_date'], reverse=True)
    return output


# ── 유틸 ──────────────────────────────────────────────────────────────

def _normalize_type(raw: str) -> str:
    """의회 공시 거래 유형 → 한글."""
    r = (raw or '').lower().strip()
    if 'purchase' in r or 'buy' in r:
        return '매수'
    if 'sale' in r or 'sell' in r:
        return '매도'
    if 'exchange' in r:
        return '교환'
    if 'receive' in r:
        return '수령'
    return raw or '기타'


def _normalize_form4_code(code: str) -> str:
    """Form 4 거래 코드 → 한글."""
    mapping = {
        'P': '매수', 'S': '매도', 'A': '부여', 'D': '반환',
        'F': '세금납부', 'G': '증여', 'I': '상속', 'J': '기타',
        'M': '옵션행사', 'X': '옵션행사', 'C': '전환', 'E': '만료',
        'W': '권리행사', 'Z': '신탁',
    }
    return mapping.get((code or '').upper(), code or '기타')


# ── 통합 수집 ────────────────────────────────────────────────────────

def fetch_all_block_deals(days: int = 30) -> dict:
    """
    모든 소스에서 블록딜 수집.
    반환: {
        'house':   [...],
        'senate':  [...],
        'form4':   [...],
        'fetched_at': 'YYYY-MM-DD HH:MM',
        'summary': {total, buy_count, sell_count, top_tickers: [...]}
    }
    """
    print('[블록딜] 하원 거래 수집 중...')
    house = fetch_house_trades(days=days)
    print(f'[블록딜] 하원 {len(house)}건')

    time.sleep(1)

    print('[블록딜] 상원 거래 수집 중...')
    senate = fetch_senate_trades(days=days)
    print(f'[블록딜] 상원 {len(senate)}건')

    time.sleep(1)

    print('[블록딜] SEC Form 4 수집 중...')
    form4 = fetch_form4_insiders(days=7)
    print(f'[블록딜] Form4 {len(form4)}건')

    all_trades = house + senate + form4

    # 요약 통계
    buy_count  = sum(1 for t in all_trades if t['trade_type'] == '매수')
    sell_count = sum(1 for t in all_trades if t['trade_type'] == '매도')

    # 티커별 거래 횟수 집계
    ticker_counts: dict[str, int] = {}
    for t in all_trades:
        tk = t.get('ticker', '').strip()
        if tk:
            ticker_counts[tk] = ticker_counts.get(tk, 0) + 1
    top_tickers = sorted(ticker_counts, key=ticker_counts.get, reverse=True)[:10]

    return {
        'house':        house,
        'senate':       senate,
        'form4':        form4,
        'fetched_at':   datetime.now().strftime('%Y-%m-%d %H:%M'),
        'summary': {
            'total':       len(all_trades),
            'house_count': len(house),
            'senate_count': len(senate),
            'form4_count': len(form4),
            'buy_count':   buy_count,
            'sell_count':  sell_count,
            'top_tickers': top_tickers,
        },
    }


if __name__ == '__main__':
    result = fetch_all_block_deals(days=14)
    s = result['summary']
    print(f"\n[블록딜 요약] 총 {s['total']}건 | 매수 {s['buy_count']} | 매도 {s['sell_count']}")
    print(f"  하원 {s['house_count']} | 상원 {s['senate_count']} | Form4 {s['form4_count']}")
    print(f"  상위 티커: {', '.join(s['top_tickers'])}")

    print("\n[최근 5건]")
    all_trades = result['house'][:2] + result['senate'][:2] + result['form4'][:1]
    for t in all_trades:
        print(f"  {t['source']} | {t['person']} | {t.get('ticker','?')} | {t['trade_type']} | {t['amount_str']} | {t['trade_date']}")
