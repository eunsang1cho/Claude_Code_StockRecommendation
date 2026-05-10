#!/usr/bin/env python3
"""
backfill_historical.py
과거 데이터 백필 스크립트 (1회 실행)

- 유동성: 13주(~3개월) FRED 과거 데이터
- 스마트머니: 최근 4분기 13F 파일링 (변화 포함)
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

import database
import liquidity_monitor as lm
import smart_money as sm

FRED_KEY = os.getenv('FRED_API_KEY', '')


def backfill_liquidity(periods: int = 13):
    """FRED에서 과거 13주 유동성 데이터를 수집해 DB에 저장."""
    print('=== 유동성 과거 데이터 백필 ===')
    if not FRED_KEY:
        print('  FRED_API_KEY 없음 — 건너뜀')
        return

    print(f'  {periods}주치 데이터 수집 중...')
    snapshots = lm.fetch_liquidity_historical(FRED_KEY, periods=periods)
    print(f'  수집된 스냅샷: {len(snapshots)}개')

    saved = skipped = 0
    for snap in snapshots:
        date = snap['date']
        data = {
            'total':      snap['total'],
            'total_prev': None,
            'components': snap['components'],
            'direction':  None,
            'updated_at': date,
        }
        try:
            database.save_liquidity_snapshot(date, data)
            saved += 1
            print(f'  [{date}] total={snap["total"]:,} B$')
        except Exception as e:
            skipped += 1
            print(f'  [{date}] 오류 (이미 있을 수 있음): {e}')

    print(f'  완료: 저장 {saved}개, 건너뜀 {skipped}개\n')


def backfill_smart_money(max_quarters: int = 4):
    """각 투자자별 최근 N분기 13F를 수집해 DB에 저장."""
    print('=== 스마트머니 과거 데이터 백필 ===')

    for cik, name in sm.SMART_MONEY.items():
        print(f'\n  {name} ({cik})')
        history = sm.fetch_investor_history(cik, name, max_quarters=max_quarters)

        if not history:
            print(f'    13F 없음 (건너뜀)')
            continue

        for entry in history:
            quarter = entry['quarter']
            n_hold  = len(entry['holdings'])
            total   = entry['total_value']
            try:
                database.save_smart_money(cik, quarter, entry)
                print(f'    [{quarter}] holdings={n_hold}, total=${total:,}')
            except Exception as e:
                print(f'    [{quarter}] 저장 오류: {e}')

        time.sleep(2.0)  # 투자자 간 간격

    print('\n  스마트머니 백필 완료')


if __name__ == '__main__':
    database.init_db()

    print(f'FRED_API_KEY: {"있음" if FRED_KEY else "없음"}\n')

    backfill_liquidity(periods=13)
    backfill_smart_money(max_quarters=4)

    print('\n=== 백필 완료 ===')
