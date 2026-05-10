#!/usr/bin/env python3
"""
backfill_iran_war.py
IranWarLive OSINT 이벤트 전체 임포트 + 전쟁 1달 전부터 재무지표 백필

실행: python backfill_iran_war.py
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

import database
import war_indicators as wi
from datetime import datetime, timedelta

database.init_db()


def backfill_iran_war_events():
    """IranWarLive 구글시트 전체 이벤트 임포트."""
    print('=== IranWarLive OSINT 이벤트 임포트 ===')

    print('  이벤트 시트 다운로드...')
    raw = wi._fetch_sheet_csv(wi._GID_EVENTS)
    print(f'  원본 행 수: {len(raw)}')

    events = []
    for row in raw:
        try:
            lat = float(row.get('Latitude') or 0) or None
            lon = float(row.get('Longitude') or 0) or None
            cas = int(row.get('Casualties') or 0)
        except Exception:
            lat = lon = None
            cas = 0
        events.append({
            'event_id':    row.get('Event_ID', ''),
            'timestamp':   row.get('Timestamp', ''),
            'lat':         lat,
            'lon':         lon,
            'strike_type': row.get('Strike_Type', ''),
            'target_desc': row.get('Target_Description', ''),
            'source_url':  row.get('Source_URL', ''),
            'verified_by': row.get('Verified_By', ''),
            'casualties':  cas,
            'context':     row.get('Escalation_Context', ''),
        })

    saved = database.upsert_iran_war_events(events)
    print(f'  저장 완료: {saved}개 (신규)')

    # 날짜 범위 출력
    timestamps = sorted([e['timestamp'] for e in events if e['timestamp']])
    if timestamps:
        print(f'  기간: {timestamps[0][:10]} ~ {timestamps[-1][:10]}')

    # 병력 + 영공
    print('  병력 현황 저장...')
    raw_mil = wi._fetch_sheet_csv(wi._GID_MILITARY)
    military = []
    for row in raw_mil:
        try:
            military.append({
                'country':         row.get('Country', ''),
                'alliance':        row.get('Alliance', ''),
                'est_troops':      row.get('Est_Troops', ''),
                'est_aircraft':    row.get('Est_Aircraft', ''),
                'military_deaths': int(row.get('Military_Deaths') or 0),
                'civilian_deaths': int(row.get('Civilian_Deaths') or 0),
                'status':          row.get('Status', ''),
            })
        except Exception:
            pass
    database.save_iran_war_military(datetime.now().strftime('%Y-%m-%d'), military)
    print(f'  병력 현황: {len(military)}개국')

    print('  영공 현황 저장...')
    raw_air = wi._fetch_sheet_csv(wi._GID_AIRSPACE)
    airspace = [{
        'timestamp': r.get('Timestamp', ''),
        'country':   r.get('Country', ''),
        'status':    r.get('Status', ''),
        'source':    r.get('Source_URL', ''),
    } for r in raw_air]
    database.save_iran_war_airspace(datetime.now().strftime('%Y-%m-%d'), airspace)
    print(f'  영공 현황: {len(airspace)}개국')


def backfill_war_financials():
    """전쟁 시작 1달 전(2026-01-28)부터 재무 지표 백필.
    Yahoo Finance로 유가/금/방산ETF/탱커 일별 종가를 가져와 war_indicators에 저장.
    """
    print('\n=== 전쟁 전후 재무지표 백필 (2026-01-28 ~ 오늘) ===')
    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        print('  yfinance 미설치 — 건너뜀')
        return

    start = '2026-01-28'
    end   = datetime.now().strftime('%Y-%m-%d')

    symbols = {
        'brent': 'BZ=F',
        'wti':   'CL=F',
        'gold':  'GC=F',
        'ita':   'ITA',
        'xle':   'XLE',
        'stng':  'STNG',
        'fro':   'FRO',
    }

    print(f'  Yahoo Finance 다운로드: {start} ~ {end}')
    try:
        tickers = list(symbols.values())
        df_all = yf.download(tickers, start=start, end=end,
                             auto_adjust=True, progress=False)
        close = df_all['Close'] if 'Close' in df_all else df_all
    except Exception as e:
        print(f'  yfinance 오류: {e}')
        return

    saved = skipped = 0
    dates = [d.strftime('%Y-%m-%d') for d in close.index]

    for date in dates:
        row = close.loc[date] if date in close.index.strftime('%Y-%m-%d').tolist() else None
        if row is None:
            continue

        proxy = {}
        for key, sym in symbols.items():
            try:
                val = float(row.get(sym, 0) if hasattr(row, 'get') else row[sym])
                if val and val > 0:
                    proxy[key] = {
                        'value': round(val, 2),
                        'label': wi.WAR_YAHOO.get(key, (sym, sym))[1],
                        'symbol': sym,
                    }
            except Exception:
                pass

        if not proxy:
            continue

        # war_score 간단 추정 (유가 기반)
        brent = proxy.get('brent', {}).get('value', 0)
        lvl, label, status = wi._oil_level(brent)
        war_score = {'score': min(100, lvl * 20), 'level': lvl, 'label': label}

        data = {
            'proxy':     proxy,
            'war_score': war_score,
            'source':    'backfill_yahoo',
            'updated':   date,
        }
        try:
            database.save_war_indicators(date, data)
            saved += 1
        except Exception:
            skipped += 1

    print(f'  완료: 저장 {saved}개, 건너뜀 {skipped}개')

    # 일별 요약 출력
    print('\n  날짜별 브렌트 원유 추이:')
    for date in dates[::7]:  # 주 1회 샘플
        try:
            idx = close.index[close.index.strftime('%Y-%m-%d') == date]
            if len(idx):
                brent_val = float(close.loc[idx[0], 'BZ=F'])
                gold_val  = float(close.loc[idx[0], 'GC=F'])
                print(f'    {date}  브렌트=${brent_val:.1f}  금=${gold_val:.0f}')
        except Exception:
            pass


if __name__ == '__main__':
    backfill_iran_war_events()
    backfill_war_financials()
    print('\n=== 백필 완료 ===')
