"""
us_tickers.py
S&P 500 + NASDAQ-100 정적 티커 목록 (2026 Q1 기준)

market 값:
  'US_NASDAQ' — NASDAQ-100 구성 종목 (S&P500 포함 여부 불문)
  'US_SP500'  — S&P500 전용 (NASDAQ-100 미포함)
"""

# ── NASDAQ-100 ────────────────────────────────────────────────────────
NASDAQ100: list[str] = [
    'AAPL','MSFT','NVDA','AMZN','META','TSLA','GOOGL','GOOG',
    'AVGO','COST','NFLX','AMD','ASML','CSCO','ADBE','QCOM',
    'INTU','TMUS','TXN','AMGN','BKNG','ISRG','VRTX','HON',
    'SBUX','LRCX','GILD','REGN','MDLZ','PANW','SNPS','ADI',
    'MRVL','KLAC','CDNS','CTAS','CSX','MAR','ORLY','MELI',
    'ROP','NXPI','FTNT','PAYX','CRWD','DXCM','MNST','CHTR',
    'PCAR','ABNB','ODFL','KDP','WDAY','IDXX','EXC','XEL',
    'VRSK','LULU','FAST','BIIB','CEG','GEHC','ON','CSGP',
    'DLTR','CDW','EA','TEAM','DDOG','ANSS','MDB','ZS','AEP',
    'WBA','ILMN','FANG','GFS','AZN','PDD','SIRI','TTD',
    'APP','PLTR','ARM','MU','INTC',
]

# ── S&P 500 전용 (NASDAQ-100 미포함) ─────────────────────────────────
SP500_ONLY: list[str] = [
    # Financials
    'JPM','BAC','WFC','GS','MS','C','BLK','SCHW','AXP','V','MA',
    'COF','USB','TFC','SPGI','MCO','ICE','CME','FIS','FISV',
    'AIG','PRU','MET','AFL','ALL','CB','PGR','TRV','CINF','HIG',
    'SYF','DFS','RF','KEY','HBAN','MTB','CFG','NTRS','STT','BK',
    'FITB','ZION','CMA','WRB','L','RE','MKL','ERIE','AON','MMC',
    # Healthcare
    'JNJ','UNH','LLY','ABBV','MRK','TMO','ABT','DHR','SYK','MDT',
    'BSX','EW','BDX','IQV','ZBH','HUM','CI','ELV','CVS','MCK',
    'ABC','CAH','HCA','ZTS','BAX','RMD','HOLX','HSIC','COO','STE',
    'ALGN','PKI','TECH','ABMD','MTD','WAT','IDXX','CRL','PODD',
    # Consumer Discretionary
    'HD','MCD','NKE','CMG','TGT','ROST','TJX','LOW','YUM','DRI',
    'HLT','MAR','WYNN','MGM','LVS','RCL','CCL','NCLH','EXPE',
    'BOOKING','PHM','LEN','DHI','NVR','TOL','MDC','KBH','BEN',
    'GPS','AEO','ANF','URBN','RL','PVH','HBI','UA','KATE',
    'F','GM','TSCO','FIVE','DG','DLTR',
    # Consumer Staples
    'WMT','PG','KO','PEP','PM','MO','KHC','CL','GIS','K',
    'SJM','HRL','MKC','STZ','EL','CLX','CHD','NWL','CAG','CPB',
    'MKC','MNST','HSY','TR','SFM','COTY','AVP','KR','SYY','PFGC',
    # Energy
    'XOM','CVX','COP','EOG','SLB','PSX','VLO','MPC','HAL','BKR',
    'OXY','DVN','HES','APA','MRO','PXD','CTRA','SM','RIG','FTI',
    'NOV','HP','WHD','LBRT','PTEN','NE','VAL','DT',
    # Industrials
    'UPS','RTX','BA','CAT','DE','MMM','GE','LMT','NOC','GD',
    'EMR','ETN','PH','ITW','ROK','AME','CMI','FDX','UNP','NSC',
    'JCI','CARR','OTIS','IR','XYL','GNRC','TRMB','HUBB','ALLE',
    'SWK','GWW','MSC','WAB','TDG','AXON','SAIC','LDOS','BAH',
    'MANT','CACI','DRS','ACM','PWR','EME','WFRD',
    # Technology (S&P500만, NASDAQ100 제외)
    'IBM','ORCL','HPE','HPQ','DELL','NCR','WDC','STX','NTAP',
    'JNPR','KEYS','TER','MPWR','SWKS','QRVO','AKAM','ANET',
    'CTSH','EPAM','GLOB','VRT','PTC','ANSS','CDNS',
    # Materials
    'LIN','APD','ECL','SHW','PPG','NEM','FCX','CTVA','CF',
    'MOS','NUE','STLD','RS','AA','X','CLF','ATI','CMC',
    'IFF','RPM','SON','SEE','AMCR','IP','PKG','WRK',
    # Utilities
    'NEE','DUK','SO','D','PCG','PEG','SRE','ES','AWK',
    'WEC','ED','PPL','EIX','ETR','FE','CMS','NI','LNT',
    'CNP','EVRG','OGE','PNW','SWX','NWE','AVA','MGEE',
    # Real Estate
    'PLD','AMT','EQIX','CCI','SPG','WY','AVB','EQR','DLR',
    'O','WELL','PSA','IRM','VTR','ARE','BXP','SLG','KIM',
    'REG','FRT','UDR','MAA','ESS','CPT','NNN','STAG','ELS',
    'SUI','AMH','INVH','TRNO','REXR','EGP','FR','LTC',
    # Communication (non-NASDAQ100)
    'DIS','CMCSA','T','VZ','OMC','IPG','PARA','WBD','FOXA','FOX',
    'NYT','SSP','NWSA','NWS','GCI','IAC',
    # Misc / Conglomerates
    'BRK.B','BRK.A','ABB','MMC','TT','ITT','SPX','RHI',
]

_NASDAQ100_SET = frozenset(NASDAQ100)

# ── 전체 스캔 대상 (ticker → market) ─────────────────────────────────
def get_us_ticker_market() -> dict[str, str]:
    """S&P500 + NASDAQ100 전체 티커 → market 코드 dict"""
    result: dict[str, str] = {}
    for t in NASDAQ100:
        result[t] = 'US_NASDAQ'
    for t in SP500_ONLY:
        if t not in result:
            result[t] = 'US_SP500'
    return result


def get_russell2000_tickers() -> dict[str, str]:
    """
    iShares IWM ETF 홀딩스에서 Russell 2000 티커 + 종목명 실시간 수집.
    반환: {ticker: 'US_RUSSELL'}
    """
    markets, _ = _fetch_russell2000_data()
    return markets


def get_russell2000_names() -> dict[str, str]:
    """Russell 2000 티커 → 회사명 dict"""
    _, names = _fetch_russell2000_data()
    return names


def _fetch_russell2000_data() -> tuple[dict[str, str], dict[str, str]]:
    """iShares IWM CSV에서 {ticker: market}, {ticker: name} 동시 수집"""
    import urllib.request, csv as _csv
    url = (
        "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf"
        "/1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund"
    )
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read().decode('utf-8', errors='ignore')
        lines = data.strip().split('\n')
        header_idx = next(
            (i for i, l in enumerate(lines) if 'Ticker' in l and 'Name' in l), None
        )
        if header_idx is None:
            return {}, {}
        markets: dict[str, str] = {}
        names: dict[str, str] = {}
        reader = _csv.DictReader(lines[header_idx:])
        for row in reader:
            t = row.get('Ticker', '').strip()
            if t and t != '-' and t != 'USD' and '.' not in t:
                markets[t] = 'US_RUSSELL'
                n = row.get('Name', '').strip()
                names[t] = n if n else t
        return markets, names
    except Exception as e:
        print(f'[Russell2000] 티커 수집 실패: {e}')
        return {}, {}


# 전체 티커 목록 (중복 제거, 순서 유지)
ALL_US_TICKERS: list[str] = list(dict.fromkeys(NASDAQ100 + SP500_ONLY))
