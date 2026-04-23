"""
short_radar.py
공매도 레이더 데이터 수집

- KR: pykrx get_shorting_volume_top50 (무료, 키 불필요)
- US: yfinance shortPercentOfFloat / shortRatio (추적 종목 한정)

스케줄: 매일 09:30
"""

import time
from datetime import datetime, timedelta

# 추적 미국 종목 (AI·테크·바이오 혼합)
US_TICKERS = [
    'AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'META', 'GOOGL', 'AMD', 'PLTR', 'SMCI',
    'MSTR', 'COIN', 'IONQ', 'SOUN', 'BBAI', 'RKLB', 'ACHR', 'JOBY', 'SOFI', 'RIVN',
]


def _latest_trading_day() -> str:
    """가장 최근 영업일 (YYYYMMDD). 장 마감 전이면 전일 기준."""
    d = datetime.now()
    if d.hour < 17:
        d -= timedelta(days=1)
    for _ in range(7):
        if d.weekday() < 5:
            return d.strftime('%Y%m%d')
        d -= timedelta(days=1)
    return d.strftime('%Y%m%d')


def fetch_kr_short(date_str: str = None) -> dict:
    """KR 공매도 Top20 수집 시도.
    KRX API는 2025년 이후 서버측 접근 시 로그인 요구 (400 LOGOUT).
    pykrx, data.krx.co.kr, short.krx.co.kr 모두 차단됨.
    반환: {'data': [...], 'unavailable': bool, 'reason': str}
    """
    if not date_str:
        date_str = _latest_trading_day()

    # KRX 직접 API 시도 (data.krx.co.kr)
    try:
        import requests
        resp = requests.post(
            'http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd',
            data={
                'bld': 'dbms/MDC/STAT/standard/MDCSTAT30101',
                'locale': 'ko_KR',
                'trdDd': date_str,
                'share': '1',
                'money': '1',
                'csvxls_isNo': 'false',
            },
            headers={
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'http://data.krx.co.kr/',
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            timeout=10,
        )
        if resp.status_code == 200:
            js = resp.json()
            rows = js.get('output') or js.get('OutBlock_1') or []
            if rows:
                result = []
                for i, row in enumerate(rows[:20]):
                    try:
                        name   = row.get('ISU_ABBRV') or row.get('종목명', '')
                        ticker = row.get('ISU_SRT_CD') or row.get('종목코드', '')
                        rate   = float(row.get('SHRT_SELN_RGHT_RT') or row.get('공매도비중') or 0)
                        vol    = int(str(row.get('SHRT_SELN_QTY') or row.get('공매도') or 0).replace(',', ''))
                        result.append({'rank': i + 1, 'ticker': ticker, 'name': name,
                                       'rate': round(rate, 2), 'volume': vol})
                    except Exception:
                        pass
                if result:
                    return {'data': result, 'unavailable': False, 'reason': ''}
    except Exception as e:
        print(f'[공매도KR] KRX API 오류: {e}')

    # pykrx 시도
    try:
        from pykrx import stock
        df = stock.get_shorting_volume_top50(date_str)
        if df is not None and not df.empty:
            result = []
            for i, (ticker, row) in enumerate(df.iterrows()):
                try:
                    rate   = float(row.get('공매도비중') or row.get('ShortingVolumeRate') or 0)
                    vol    = int(row.get('공매도') or row.get('ShortingVolume') or 0)
                    name   = str(row.get('종목명') or row.get('Name') or ticker)
                    result.append({'rank': i + 1, 'ticker': str(ticker), 'name': name,
                                   'rate': round(rate, 2), 'volume': vol})
                except Exception:
                    pass
            if result:
                return {'data': result[:20], 'unavailable': False, 'reason': ''}
    except Exception as e:
        print(f'[공매도KR] pykrx 오류: {e}')

    print('[공매도KR] KRX API 접근 불가 (로그인 필요)')
    return {
        'data': [],
        'unavailable': True,
        'reason': 'KRX 공매도 API는 로그인 세션이 필요합니다. KIS Open API(apiportal.koreainvestment.com) 연동 시 자동 수집 가능합니다.',
    }


def fetch_us_short(tickers: list[str] = None) -> list[dict]:
    """US 공매도 비율 수집 (yfinance)."""
    try:
        import yfinance as yf
    except ImportError:
        print('[공매도US] yfinance 미설치')
        return []

    tickers = tickers or US_TICKERS
    result = []

    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info
            short_float = info.get('shortPercentOfFloat')
            short_ratio = info.get('shortRatio')

            if short_float is None and short_ratio is None:
                continue

            result.append({
                'ticker':      ticker,
                'name':        info.get('shortName', ticker),
                'short_float': round(float(short_float) * 100, 2) if short_float else None,  # %
                'short_ratio': round(float(short_ratio), 2) if short_ratio else None,
            })
            time.sleep(0.3)
        except Exception as e:
            print(f'[공매도US] {ticker} 오류: {e}')

    # short_float 내림차순 정렬
    result.sort(key=lambda x: x.get('short_float') or 0, reverse=True)
    return result


def fetch_short_radar() -> dict:
    """공매도 데이터 전체 수집."""
    print('[공매도] KR Top50 수집 중...')
    kr_result = fetch_kr_short()

    print('[공매도] US 추적 종목 수집 중...')
    us = fetch_us_short()

    return {
        'date':            datetime.now().strftime('%Y-%m-%d'),
        'kr':              kr_result.get('data', []),
        'kr_unavailable':  kr_result.get('unavailable', False),
        'kr_reason':       kr_result.get('reason', ''),
        'us':              us,
        'updated_at':      datetime.now().isoformat(timespec='seconds'),
    }
