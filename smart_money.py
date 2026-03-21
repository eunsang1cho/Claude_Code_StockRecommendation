"""
smart_money.py
SEC EDGAR 13F 파일링 파싱 — 스마트머니 추적

추적 투자자 (CIK):
  버핏 (버크셔)       0001067983
  버리 (사이언)       0001326110
  애크먼 (퍼싱스퀘어)  0001336528
  테퍼 (아팔루사)     0000356213
  르네상스 테크        0001037389
  브리지워터          0001350694

스케줄: 매주 월요일 08:00 (13F는 분기별 제출)
"""

import time
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

SMART_MONEY = {
    '0001067983': '버핏 (버크셔)',
    '0001326110': '버리 (사이언)',
    '0001336528': '애크먼 (퍼싱스퀘어)',
    '0000356213': '테퍼 (아팔루사)',
    '0001037389': '르네상스 테크',
    '0001350694': '브리지워터',
}

_HEADERS = {'User-Agent': 'StockBot research@stockbot.local'}


def _get_latest_13f(cik: str) -> tuple[str | None, str | None]:
    """CIK에서 최신 13F-HR accession_number + filed_date 반환."""
    url = f'https://data.sec.gov/submissions/CIK{cik}.json'
    try:
        r = requests.get(url, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        js = r.json()
        filings = js.get('filings', {}).get('recent', {})
        forms      = filings.get('form', [])
        accessions = filings.get('accessionNumber', [])
        dates      = filings.get('filingDate', [])

        for i, form in enumerate(forms):
            if form in ('13F-HR', '13F-HR/A'):
                accn = accessions[i].replace('-', '')
                return accn, dates[i]
    except Exception as e:
        print(f'[스마트머니] CIK {cik} submissions 오류: {e}')
    return None, None


def _parse_13f_xml(cik: str, accn: str) -> list[dict]:
    """13F-HR infotable XML 파싱 → 보유 목록 반환."""
    cik_int = int(cik)
    base_url = f'https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn}'

    # 인덱스 JSON에서 XML 파일명 획득
    idx_url = f'{base_url}/{accn}-index.json'
    xml_name = None
    try:
        r = requests.get(idx_url, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        idx = r.json()
        items = idx.get('directory', {}).get('item', [])
        # infotable.xml 우선, 없으면 임의 .xml
        for f in items:
            name = f.get('name', '').lower()
            if 'infotable' in name and name.endswith('.xml'):
                xml_name = f['name']
                break
        if not xml_name:
            for f in items:
                if f.get('name', '').lower().endswith('.xml'):
                    xml_name = f['name']
                    break
    except Exception as e:
        print(f'[스마트머니] CIK {cik} index 오류: {e}')
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

    holdings = []
    for entry in root.findall(f'.//{ns}infoTable'):
        try:
            name   = (entry.findtext(f'{ns}nameOfIssuer') or '').strip()
            cusip  = (entry.findtext(f'{ns}cusip') or '').strip()
            # value는 $1000 단위
            val_el = entry.findtext(f'{ns}value') or '0'
            value  = int(str(val_el).replace(',', '')) * 1000

            # 주수 — 노드 경로가 버전마다 다름
            shr_el = (
                entry.findtext(f'{ns}sshPrnamt')
                or entry.findtext(f'.//{ns}sshPrnamt')
                or '0'
            )
            shares = int(str(shr_el).replace(',', ''))

            if name and value > 0:
                holdings.append({
                    'name':   name,
                    'cusip':  cusip,
                    'value':  value,
                    'shares': shares,
                })
        except Exception:
            pass

    holdings.sort(key=lambda x: x['value'], reverse=True)
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

    for cusip, h in cur_map.items():
        if cusip not in prev_map:
            new_buys.append(h)
        else:
            p = prev_map[cusip]
            delta_pct   = round((h['value'] - p['value']) / p['value'] * 100, 1)
            weight_now  = round(h['value'] / cur_total * 100, 2)
            weight_prev = round(p['value'] / prev_total * 100, 2)
            if abs(delta_pct) >= 5:
                changes.append({
                    'name':        h['name'],
                    'cusip':       cusip,
                    'delta_pct':   delta_pct,
                    'weight_now':  weight_now,
                    'weight_prev': weight_prev,
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
