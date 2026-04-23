"""
smart_money.py
SEC EDGAR 13F 파일링 파싱 — 스마트머니 추적

추적 투자자 (CIK):
  버핏 (버크셔)          0001067983
  버리 (사이언)          0001649339
  애크먼 (퍼싱스퀘어)     0001336528
  르네상스 테크           0001037389
  브리지워터             0001350694
  소로스 펀드            0001029160
  드러켄밀러 (듀케인)     0001536411
  코아튜                0001135730
  타이거 글로벌          0001167483
  로웹 (서드포인트)      0001040273

스케줄: 매주 월요일 08:00 (13F는 분기별 제출)
"""

import time
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

SMART_MONEY = {
    '0001067983': '버핏 (버크셔)',
    '0001649339': '버리 (사이언)',
    '0001336528': '애크먼 (퍼싱스퀘어)',
    '0001037389': '르네상스 테크',
    '0001350694': '브리지워터',
    '0001029160': '소로스 펀드',
    '0001536411': '드러켄밀러 (듀케인)',
    '0001135730': '코아튜',
    '0001167483': '타이거 글로벌',
    '0001040273': '로웹 (서드포인트)',
}

_HEADERS = {'User-Agent': 'StockBot research@stockbot.local'}


def _get_all_13f_filings(cik: str, max_filings: int = 4) -> list[tuple[str, str]]:
    """CIK에서 최근 N개 13F-HR accession_number + filed_date 목록 반환 (최신 순)."""
    url = f'https://data.sec.gov/submissions/CIK{cik}.json'
    try:
        r = requests.get(url, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        js = r.json()
        filings = js.get('filings', {}).get('recent', {})
        forms      = filings.get('form', [])
        accessions = filings.get('accessionNumber', [])
        dates      = filings.get('filingDate', [])

        result = []
        for i, form in enumerate(forms):
            if form in ('13F-HR', '13F-HR/A'):
                accn = accessions[i].replace('-', '')
                result.append((accn, dates[i]))
                if len(result) >= max_filings:
                    break
        return result
    except Exception as e:
        print(f'[스마트머니] CIK {cik} submissions 오류: {e}')
    return []


def _get_latest_13f(cik: str) -> tuple[str | None, str | None]:
    """CIK에서 최신 13F-HR accession_number + filed_date 반환."""
    filings = _get_all_13f_filings(cik, max_filings=1)
    if filings:
        return filings[0]
    return None, None


def _parse_13f_xml(cik: str, accn: str) -> list[dict]:
    """13F-HR infotable XML 파싱 → 보유 목록 반환.
    accn: 대시 없는 18자리 (예: 000119312526054580)
    """
    import re as _re
    cik_int = int(cik)
    base_url = f'https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn}'

    # 디렉토리 HTML을 파싱해서 XML 파일명 획득
    xml_name = None
    try:
        r = requests.get(base_url + '/', headers=_HEADERS, timeout=15)
        r.raise_for_status()
        # href 에서 .xml 파일 추출 (infotable 우선)
        hrefs = _re.findall(r'href="([^"]*\.xml)"', r.text, _re.IGNORECASE)
        xml_files = [h.split('/')[-1] for h in hrefs if '/' in h or h.endswith('.xml')]
        for fname in xml_files:
            if 'infotable' in fname.lower():
                xml_name = fname
                break
        if not xml_name:
            # primary_doc.xml 제외하고 첫 번째 XML
            for fname in xml_files:
                if fname.lower() not in ('primary_doc.xml',):
                    xml_name = fname
                    break
        if not xml_name and xml_files:
            xml_name = xml_files[0]
    except Exception as e:
        print(f'[스마트머니] CIK {cik} directory 오류: {e}')
        return []

    if not xml_name:
        return []

    time.sleep(0.5)
    xml_url = f'{base_url}/{xml_name}'
    try:
        r = requests.get(xml_url, headers=_HEADERS, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        print(f'[스마트머니] CIK {cik} XML 오류: {e}')
        return []

    # XML 네임스페이스 처리
    ns = ''
    if root.tag.startswith('{'):
        ns = root.tag.split('}')[0] + '}'

    # CUSIP별 합산 (버크셔처럼 자회사별 분리 신고하는 경우)
    cusip_map: dict[str, dict] = {}
    for entry in root.findall(f'.//{ns}infoTable'):
        try:
            name   = (entry.findtext(f'{ns}nameOfIssuer') or '').strip()
            cusip  = (entry.findtext(f'{ns}cusip') or '').strip()
            val_el = entry.findtext(f'{ns}value') or '0'
            # SEC 13F value 필드: 달러 단위
            value  = int(str(val_el).replace(',', ''))

            shr_el = (
                entry.findtext(f'{ns}sshPrnamt')
                or entry.findtext(f'.//{ns}sshPrnamt')
                or '0'
            )
            shares = int(str(shr_el).replace(',', ''))

            if name and value > 0:
                key = cusip or name
                if key in cusip_map:
                    cusip_map[key]['value']  += value
                    cusip_map[key]['shares'] += shares
                else:
                    cusip_map[key] = {'name': name, 'cusip': cusip, 'value': value, 'shares': shares}
        except Exception:
            pass

    holdings = sorted(cusip_map.values(), key=lambda x: x['value'], reverse=True)

    # 단위 자동 감지: SEC 규정은 천 달러 단위이나 일부 대형 펀드는 USD로 직접 신고.
    # 최대 단일 보유가치가 $1M 미만이면 천 달러 단위 → 1000 곱해 USD로 변환.
    if holdings and holdings[0]['value'] < 1_000_000:
        for h in holdings:
            h['value'] *= 1000

    return holdings


def _compute_changes(current: list[dict], prev: list[dict]) -> dict:
    """이전 분기와 비교 — 신규매수 / 완전매도 / 비중변화."""
    cur_map  = {h['cusip']: h for h in current if h['cusip']}
    prev_map = {h['cusip']: h for h in prev    if h['cusip']}

    cur_total  = sum(h['value'] for h in current) or 1
    prev_total = sum(h['value'] for h in prev)    or 1

    new_buys   = []
    full_sells = []
    changes    = []

    # 비중 기반 임계값 (비중이 매우 작은 포지션은 의미 없는 거대 delta_pct 방지)
    MIN_WEIGHT = 0.1  # 0.1% 미만 포지션은 신규매수/완전매도로 처리

    for cusip, h in cur_map.items():
        weight_now  = h['value'] / cur_total * 100
        if cusip not in prev_map:
            new_buys.append(h)
        else:
            p = prev_map[cusip]
            weight_prev = p['value'] / prev_total * 100

            # 이전 비중이 최소 임계값 미만이면 신규매수로 취급
            if weight_prev < MIN_WEIGHT and weight_now >= MIN_WEIGHT:
                new_buys.append(h)
                continue

            # 비중 변화 (pp, percentage points) 기준으로 계산
            delta_weight = round(weight_now - weight_prev, 2)
            if abs(delta_weight) >= 0.5:  # 0.5pp 이상 비중 변화만 표시
                changes.append({
                    'name':        h['name'],
                    'cusip':       cusip,
                    'delta_pct':   delta_weight,      # 비중 변화 (pp)
                    'weight_now':  round(weight_now, 2),
                    'weight_prev': round(weight_prev, 2),
                })

    for cusip, p in prev_map.items():
        if cusip not in cur_map:
            full_sells.append(p)

    return {
        'new_buys':   new_buys[:10],
        'full_sells': full_sells[:10],
        'changes':    sorted(changes, key=lambda x: abs(x['delta_pct']), reverse=True)[:20],
    }


def fetch_investor(cik: str, name: str, prev_holdings: list[dict] = None) -> dict:
    """단일 투자자 최신 13F 수집."""
    accn, filed_date = _get_latest_13f(cik)
    if not accn:
        return {'cik': cik, 'name': name, 'error': '13F 없음', 'holdings': [], 'changes': {}}

    try:
        dt = datetime.strptime(filed_date, '%Y-%m-%d')
        quarter = f"{dt.year}Q{(dt.month - 1) // 3 + 1}"
    except Exception:
        quarter = (filed_date[:7] if filed_date else 'unknown')

    time.sleep(0.5)
    holdings = _parse_13f_xml(cik, accn)

    changes = {}
    if prev_holdings:
        changes = _compute_changes(holdings, prev_holdings)

    return {
        'cik':         cik,
        'name':        name,
        'accn':        accn,
        'filed_date':  filed_date,
        'quarter':     quarter,
        'holdings':    holdings[:50],
        'changes':     changes,
        'total_value': sum(h['value'] for h in holdings),
    }


def fetch_investor_history(cik: str, name: str, max_quarters: int = 4) -> list[dict]:
    """단일 투자자의 과거 N분기 13F 데이터를 시간순으로 반환.
    분기별 변화(신규매수/매도/비중변화)도 계산됨.
    """
    filings = _get_all_13f_filings(cik, max_filings=max_quarters)
    if not filings:
        return []

    # 오래된 분기부터 처리 (reverse: newest-first → oldest-first)
    result = []
    prev_holdings: list[dict] = []

    for accn, filed_date in reversed(filings):
        try:
            dt = datetime.strptime(filed_date, '%Y-%m-%d')
            quarter = f"{dt.year}Q{(dt.month - 1) // 3 + 1}"
        except Exception:
            quarter = filed_date[:7] if filed_date else 'unknown'

        print(f'  [스마트머니] {name} {quarter} ({filed_date}) 수집...')
        time.sleep(0.5)
        holdings = _parse_13f_xml(cik, accn)

        changes = {}
        if prev_holdings:
            changes = _compute_changes(holdings, prev_holdings)

        entry = {
            'cik':         cik,
            'name':        name,
            'accn':        accn,
            'filed_date':  filed_date,
            'quarter':     quarter,
            'holdings':    holdings[:50],
            'changes':     changes,
            'total_value': sum(h['value'] for h in holdings),
        }
        result.append(entry)
        prev_holdings = holdings
        time.sleep(1.0)

    return result


def fetch_all_smart_money(prev_data: dict = None) -> dict:
    """모든 추적 투자자 13F 수집."""
    prev_data = prev_data or {}
    result = {}

    for cik, name in SMART_MONEY.items():
        print(f'[스마트머니] {name} ({cik}) 수집 중...')
        prev_h = prev_data.get(cik, {}).get('holdings', [])
        data = fetch_investor(cik, name, prev_h)
        result[cik] = data
        time.sleep(1.0)

    return result
