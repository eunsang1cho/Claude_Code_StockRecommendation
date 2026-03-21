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


def fetch_kr_short(date_str: str = None) -> list[dict]:
    """KR 공매도 Top20 수집 (pykrx)."""
    try:
        from pykrx import stock
        if not date_str:
            date_str = _latest_trading_day()

        df = stock.get_shorting_volume_top50(date_str)
        if df is None or df.empty:
            return []

        result = []
        for i, (ticker, row) in enumerate(df.iterrows()):
            try:
                # pykrx 버전에 따라 컬럼명 다를 수 있음
                rate = float(
                    row.get('공매도비중') or row.get('ShortingVolumeRate') or 0
                )
                vol = int(
                    row.get('공매도') or row.get('ShortingVolume') or 0
                )
                name = str(row.get('종목명') or row.get('Name') or ticker)
                result.append({
                    'rank':   i + 1,
                    'ticker': str(ticker),
                    'name':   name,
                    'rate':   round(rate, 2),  # %
                    'volume': vol,
                })
            except Exception:
                pass

        return result[:20]
    except Exception as e:
        print(f'[공매도KR] 오류: {e}')
    return []


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
    kr = fetch_kr_short()

    print('[공매도] US 추적 종목 수집 중...')
    us = fetch_us_short()

    return {
        'date':       datetime.now().strftime('%Y-%m-%d'),
        'kr':         kr,
        'us':         us,
        'updated_at': datetime.now().isoformat(timespec='seconds'),
    }
