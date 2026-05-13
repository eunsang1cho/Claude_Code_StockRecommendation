"""
etf_flow_tracker.py
ETF 자금 흐름 → 4방향 종목별 유입/유출액 계산

4가지 카테고리:
  us_us  : 🇺🇸 미국 ETF  → 🇺🇸 미국 종목  (SPY/QQQ/SOXX 등 → NVDA/AAPL 등)
  kr_kr  : 🇰🇷 한국 ETF  → 🇰🇷 한국 종목  (KODEX200 등   → 삼성전자 등)
  us_kr  : 🇺🇸 미국 ETF  → 🇰🇷 한국 종목  (EWY/FLKR 등  → 삼성전자 등, USD기준)
  kr_us  : 🇰🇷 한국 ETF  → 🇺🇸 미국 종목  (TIGER나스닥100 등 → NVDA 등, KRW기준)

공식:
  net_flow = ΔAUM - prev_AUM × price_return
  stock_impact = Σ(ETF_net_flow × 편입비중)

스케줄: 매일 06:30
"""

import time
from datetime import datetime

_HEADERS = {'User-Agent': 'ETFFlowTracker/1.0'}

# ── 1. 미국 상장 ETF — 미국 종목 투자 ────────────────────────────────────
US_ETFS_US: dict[str, dict] = {
    'SPY':  {'name': 'SPDR S&P500',         'category': 'broad'},
    'QQQ':  {'name': 'Invesco NASDAQ-100',  'category': 'broad'},
    'IVV':  {'name': 'iShares S&P500',      'category': 'broad'},
    'VTI':  {'name': 'Vanguard 전체시장',   'category': 'broad'},
    'IWM':  {'name': 'iShares 소형주',      'category': 'broad'},
    'XLK':  {'name': '기술 (XLK)',          'category': 'sector'},
    'XLF':  {'name': '금융 (XLF)',          'category': 'sector'},
    'XLE':  {'name': '에너지 (XLE)',        'category': 'sector'},
    'XLV':  {'name': '헬스케어 (XLV)',      'category': 'sector'},
    'XLI':  {'name': '산업재 (XLI)',        'category': 'sector'},
    'XLC':  {'name': '통신 (XLC)',          'category': 'sector'},
    'XLY':  {'name': '소비재 (XLY)',        'category': 'sector'},
    'XLB':  {'name': '소재 (XLB)',          'category': 'sector'},
    'XLRE': {'name': '부동산 (XLRE)',       'category': 'sector'},
    'XLU':  {'name': '유틸리티 (XLU)',      'category': 'sector'},
    'XLP':  {'name': '필수소비 (XLP)',      'category': 'sector'},
    'SOXX': {'name': '반도체 (SOXX)',       'category': 'theme'},
    'ITA':  {'name': '방산 (ITA)',          'category': 'theme'},
    'ARKK': {'name': 'ARK Innovation',     'category': 'theme'},
    'ARKG': {'name': 'ARK Genomics',       'category': 'theme'},
    'GLD':  {'name': '금 (GLD)',            'category': 'safe'},
    'TLT':  {'name': '장기국채 (TLT)',      'category': 'safe'},
    'SHY':  {'name': '단기국채 (SHY)',      'category': 'safe'},
    'TQQQ': {'name': '3×불 나스닥',        'category': 'leverage'},
    'SQQQ': {'name': '3×베어 나스닥',      'category': 'leverage'},
    'UPRO': {'name': '3×불 S&P500',        'category': 'leverage'},
    'SPXU': {'name': '3×베어 S&P500',      'category': 'leverage'},
}

# ── 2. 미국 상장 ETF — 한국 종목 투자 ────────────────────────────────────
US_ETFS_KR: dict[str, dict] = {
    'EWY':  {'name': 'iShares MSCI 한국',   'category': 'broad_kr',  'ref_aum': 15_700_000_000},
    'FLKR': {'name': 'Franklin FTSE 한국',  'category': 'broad_kr',  'ref_aum':    420_000_000},
    'DRAM': {'name': 'VanEck Memory Chip',  'category': 'sector_kr', 'ref_aum':    120_000_000},
}

# EWY/FLKR/KORU 편입 종목 (KRX 티커, USD 플로우)
US_KR_HOLDINGS: dict[str, list[dict]] = {
    'EWY': [
        {'ticker': '005930', 'name': '삼성전자',        'weight': 0.2380},
        {'ticker': '000660', 'name': 'SK하이닉스',      'weight': 0.0920},
        {'ticker': '207940', 'name': '삼성바이오로직스','weight': 0.0540},
        {'ticker': '005380', 'name': '현대차',          'weight': 0.0450},
        {'ticker': '051910', 'name': 'LG화학',          'weight': 0.0360},
        {'ticker': '035420', 'name': 'NAVER',           'weight': 0.0320},
        {'ticker': '006400', 'name': '삼성SDI',         'weight': 0.0300},
        {'ticker': '035720', 'name': '카카오',          'weight': 0.0280},
        {'ticker': '028260', 'name': '삼성물산',        'weight': 0.0240},
        {'ticker': '017670', 'name': 'SK텔레콤',        'weight': 0.0220},
        {'ticker': '000270', 'name': '기아',            'weight': 0.0210},
        {'ticker': '055550', 'name': '신한지주',        'weight': 0.0195},
        {'ticker': '086790', 'name': '하나금융',        'weight': 0.0188},
        {'ticker': '105560', 'name': 'KB금융',          'weight': 0.0175},
        {'ticker': '068270', 'name': '셀트리온',        'weight': 0.0162},
    ],
    'FLKR': [  # Franklin FTSE Korea = 더 분산된 KOSPI 구성
        {'ticker': '005930', 'name': '삼성전자',        'weight': 0.2210},
        {'ticker': '000660', 'name': 'SK하이닉스',      'weight': 0.0850},
        {'ticker': '207940', 'name': '삼성바이오로직스','weight': 0.0480},
        {'ticker': '005380', 'name': '현대차',          'weight': 0.0420},
        {'ticker': '051910', 'name': 'LG화학',          'weight': 0.0340},
        {'ticker': '035420', 'name': 'NAVER',           'weight': 0.0310},
        {'ticker': '006400', 'name': '삼성SDI',         'weight': 0.0290},
        {'ticker': '035720', 'name': '카카오',          'weight': 0.0265},
        {'ticker': '028260', 'name': '삼성물산',        'weight': 0.0230},
        {'ticker': '000270', 'name': '기아',            'weight': 0.0200},
    ],
    'DRAM': [  # VanEck Memory Chip — 한국 반도체 편입
        {'ticker': '005930', 'name': '삼성전자',        'weight': 0.2960},
        {'ticker': '000660', 'name': 'SK하이닉스',      'weight': 0.1850},
        {'ticker': '042700', 'name': '한미반도체',      'weight': 0.0410},
    ],
    'HEWY': [
        {'ticker': '005930', 'name': '삼성전자',        'weight': 0.2500},
        {'ticker': '000660', 'name': 'SK하이닉스',      'weight': 0.0900},
        {'ticker': '005380', 'name': '현대차',          'weight': 0.0460},
        {'ticker': '035420', 'name': 'NAVER',           'weight': 0.0330},
        {'ticker': '051910', 'name': 'LG화학',          'weight': 0.0310},
        {'ticker': '000270', 'name': '기아',            'weight': 0.0260},
        {'ticker': '086790', 'name': '하나금융',        'weight': 0.0230},
        {'ticker': '055550', 'name': '신한지주',        'weight': 0.0220},
    ],
}

# ── 3. 한국 상장 ETF — 한국 종목 투자 ────────────────────────────────────
# ref_tv: 참조 거래대금(₩) — 주말/장 외 trading_value=0 시 백필용 기준값
KR_ETFS_KR: dict[str, dict] = {
    '069500': {'name': 'KODEX 200',     'yf': '069500.KS', 'category': 'broad',    'ref_tv': 200_000_000_000},
    '102110': {'name': 'TIGER 200',     'yf': '102110.KS', 'category': 'broad',    'ref_tv':  80_000_000_000},
    '091160': {'name': 'KODEX 반도체',  'yf': '091160.KS', 'category': 'sector',   'ref_tv':  50_000_000_000},
    '305720': {'name': 'KODEX 2차전지', 'yf': '305720.KS', 'category': 'sector',   'ref_tv':  30_000_000_000},
    '278530': {'name': 'KODEX 바이오',  'yf': '278530.KS', 'category': 'sector',   'ref_tv':  15_000_000_000},
    '140710': {'name': 'KODEX 레버리지','yf': '140710.KS', 'category': 'leverage', 'ref_tv': 300_000_000_000},
    '252670': {'name': 'KODEX 인버스',  'yf': '252670.KS', 'category': 'leverage', 'ref_tv':  40_000_000_000},
}

KR_ETF_KR_HOLDINGS: dict[str, list[dict]] = {
    '069500': [
        {'ticker': '005930', 'name': '삼성전자',        'weight': 0.2651},
        {'ticker': '000660', 'name': 'SK하이닉스',      'weight': 0.0820},
        {'ticker': '005935', 'name': '삼성전자우',      'weight': 0.0278},
        {'ticker': '035420', 'name': 'NAVER',           'weight': 0.0267},
        {'ticker': '005380', 'name': '현대차',          'weight': 0.0259},
        {'ticker': '000270', 'name': '기아',            'weight': 0.0232},
        {'ticker': '051910', 'name': 'LG화학',          'weight': 0.0207},
        {'ticker': '068270', 'name': '셀트리온',        'weight': 0.0185},
        {'ticker': '207940', 'name': '삼성바이오로직스','weight': 0.0181},
        {'ticker': '035720', 'name': '카카오',          'weight': 0.0176},
        {'ticker': '055550', 'name': '신한지주',        'weight': 0.0131},
        {'ticker': '086790', 'name': '하나금융',        'weight': 0.0138},
        {'ticker': '105560', 'name': 'KB금융',          'weight': 0.0128},
        {'ticker': '028260', 'name': '삼성물산',        'weight': 0.0145},
        {'ticker': '003550', 'name': 'LG',              'weight': 0.0115},
    ],
    '102110': [  # TIGER200 = KOSPI200 동일
        {'ticker': '005930', 'name': '삼성전자',        'weight': 0.2651},
        {'ticker': '000660', 'name': 'SK하이닉스',      'weight': 0.0820},
        {'ticker': '005935', 'name': '삼성전자우',      'weight': 0.0278},
        {'ticker': '035420', 'name': 'NAVER',           'weight': 0.0267},
        {'ticker': '005380', 'name': '현대차',          'weight': 0.0259},
        {'ticker': '000270', 'name': '기아',            'weight': 0.0232},
        {'ticker': '051910', 'name': 'LG화학',          'weight': 0.0207},
        {'ticker': '068270', 'name': '셀트리온',        'weight': 0.0185},
        {'ticker': '207940', 'name': '삼성바이오로직스','weight': 0.0181},
        {'ticker': '035720', 'name': '카카오',          'weight': 0.0176},
    ],
    '091160': [  # KODEX 반도체
        {'ticker': '005930', 'name': '삼성전자',        'weight': 0.3100},
        {'ticker': '000660', 'name': 'SK하이닉스',      'weight': 0.2180},
        {'ticker': '042700', 'name': '한미반도체',      'weight': 0.0720},
        {'ticker': '009150', 'name': '삼성전기',        'weight': 0.0610},
        {'ticker': '058470', 'name': '리노공업',        'weight': 0.0380},
        {'ticker': '357780', 'name': '솔브레인',        'weight': 0.0230},
        {'ticker': '254090', 'name': '잉크테크',        'weight': 0.0210},
    ],
    '305720': [  # KODEX 2차전지
        {'ticker': '051910', 'name': 'LG화학',          'weight': 0.2510},
        {'ticker': '373220', 'name': 'LG에너지솔루션',  'weight': 0.2100},
        {'ticker': '247540', 'name': '에코프로비엠',    'weight': 0.0820},
        {'ticker': '086520', 'name': '에코프로',        'weight': 0.0680},
        {'ticker': '006400', 'name': '삼성SDI',         'weight': 0.0640},
        {'ticker': '096770', 'name': 'SK이노베이션',    'weight': 0.0530},
        {'ticker': '003670', 'name': '포스코퓨처엠',    'weight': 0.0370},
    ],
    '278530': [  # KODEX 바이오
        {'ticker': '207940', 'name': '삼성바이오로직스','weight': 0.2820},
        {'ticker': '068270', 'name': '셀트리온',        'weight': 0.2210},
        {'ticker': '326030', 'name': 'SK바이오팜',      'weight': 0.0680},
        {'ticker': '091990', 'name': '셀트리온헬스케어','weight': 0.0530},
        {'ticker': '145720', 'name': '덴티움',          'weight': 0.0380},
        {'ticker': '214150', 'name': '클래시스',        'weight': 0.0350},
    ],
    '140710': [],  # 레버리지/인버스 = 선물 기반, 개별 종목 없음
    '252670': [],
}

# ── 4. 한국 상장 ETF — 미국 종목 투자 ────────────────────────────────────
KR_ETFS_US: dict[str, dict] = {
    '133690': {'name': 'TIGER 나스닥100',    'yf': '133690.KS', 'category': 'broad_us',  'ref_tv': 100_000_000_000},
    '364980': {'name': 'TIGER 미국S&P500',   'yf': '364980.KS', 'category': 'broad_us',  'ref_tv':  30_000_000_000},
    '460700': {'name': 'KODEX 미국반도체',   'yf': '460700.KS', 'category': 'sector_us', 'ref_tv':  15_000_000_000},
    '219390': {'name': 'KODEX 미국서학개미', 'yf': '219390.KS', 'category': 'broad_us',  'ref_tv':  20_000_000_000},
    '381170': {'name': 'TIGER 미국나스닥100','yf': '381170.KS', 'category': 'broad_us',  'ref_tv':  25_000_000_000},
}

KR_ETF_US_HOLDINGS: dict[str, list[dict]] = {
    '133690': [  # TIGER 나스닥100 = QQQ 구성
        {'ticker': 'NVDA',  'name': 'NVIDIA',        'weight': 0.0868},
        {'ticker': 'AAPL',  'name': 'Apple',         'weight': 0.0763},
        {'ticker': 'MSFT',  'name': 'Microsoft',     'weight': 0.0563},
        {'ticker': 'AMZN',  'name': 'Amazon',        'weight': 0.0458},
        {'ticker': 'TSLA',  'name': 'Tesla',         'weight': 0.0380},
        {'ticker': 'META',  'name': 'Meta',          'weight': 0.0346},
        {'ticker': 'GOOGL', 'name': 'Alphabet A',    'weight': 0.0343},
        {'ticker': 'AVGO',  'name': 'Broadcom',      'weight': 0.0301},
        {'ticker': 'COST',  'name': 'Costco',        'weight': 0.0253},
        {'ticker': 'NFLX',  'name': 'Netflix',       'weight': 0.0248},
    ],
    '364980': [  # TIGER 미국S&P500 = SPY 구성
        {'ticker': 'NVDA',  'name': 'NVIDIA',        'weight': 0.0639},
        {'ticker': 'AAPL',  'name': 'Apple',         'weight': 0.0620},
        {'ticker': 'MSFT',  'name': 'Microsoft',     'weight': 0.0560},
        {'ticker': 'AMZN',  'name': 'Amazon',        'weight': 0.0390},
        {'ticker': 'META',  'name': 'Meta',          'weight': 0.0282},
        {'ticker': 'GOOGL', 'name': 'Alphabet A',    'weight': 0.0270},
        {'ticker': 'TSLA',  'name': 'Tesla',         'weight': 0.0251},
        {'ticker': 'AVGO',  'name': 'Broadcom',      'weight': 0.0242},
        {'ticker': 'BRK-B', 'name': 'Berkshire',     'weight': 0.0187},
        {'ticker': 'JPM',   'name': 'JPMorgan',      'weight': 0.0163},
    ],
    '460700': [  # KODEX 미국반도체 = SOXX 유사
        {'ticker': 'NVDA',  'name': 'NVIDIA',        'weight': 0.0868},
        {'ticker': 'AVGO',  'name': 'Broadcom',      'weight': 0.0820},
        {'ticker': 'AMD',   'name': 'AMD',           'weight': 0.0560},
        {'ticker': 'QCOM',  'name': 'Qualcomm',      'weight': 0.0480},
        {'ticker': 'AMAT',  'name': 'AMAT',          'weight': 0.0350},
        {'ticker': 'MU',    'name': 'Micron',        'weight': 0.0320},
        {'ticker': 'LRCX',  'name': 'Lam Research',  'weight': 0.0310},
        {'ticker': 'KLAC',  'name': 'KLA Corp',      'weight': 0.0285},
        {'ticker': 'TSM',   'name': 'TSMC ADR',      'weight': 0.0272},
    ],
    '219390': [  # KODEX 미국서학개미 = 테크 집중
        {'ticker': 'TSLA',  'name': 'Tesla',         'weight': 0.1200},
        {'ticker': 'NVDA',  'name': 'NVIDIA',        'weight': 0.0980},
        {'ticker': 'AAPL',  'name': 'Apple',         'weight': 0.0820},
        {'ticker': 'AMZN',  'name': 'Amazon',        'weight': 0.0760},
        {'ticker': 'META',  'name': 'Meta',          'weight': 0.0680},
        {'ticker': 'MSFT',  'name': 'Microsoft',     'weight': 0.0620},
        {'ticker': 'GOOGL', 'name': 'Alphabet A',    'weight': 0.0580},
        {'ticker': 'AMD',   'name': 'AMD',           'weight': 0.0420},
        {'ticker': 'PLTR',  'name': 'Palantir',      'weight': 0.0380},
        {'ticker': 'COIN',  'name': 'Coinbase',      'weight': 0.0280},
    ],
    '381170': [  # TIGER 미국나스닥100 = 133690과 동일 지수
        {'ticker': 'NVDA',  'name': 'NVIDIA',        'weight': 0.0868},
        {'ticker': 'AAPL',  'name': 'Apple',         'weight': 0.0763},
        {'ticker': 'MSFT',  'name': 'Microsoft',     'weight': 0.0563},
        {'ticker': 'AMZN',  'name': 'Amazon',        'weight': 0.0458},
        {'ticker': 'TSLA',  'name': 'Tesla',         'weight': 0.0380},
        {'ticker': 'META',  'name': 'Meta',          'weight': 0.0346},
        {'ticker': 'GOOGL', 'name': 'Alphabet A',    'weight': 0.0343},
        {'ticker': 'AVGO',  'name': 'Broadcom',      'weight': 0.0301},
        {'ticker': 'COST',  'name': 'Costco',        'weight': 0.0253},
        {'ticker': 'NFLX',  'name': 'Netflix',       'weight': 0.0248},
    ],
}


# ── AUM 스냅샷 ─────────────────────────────────────────────────────────────

def _safe_float(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except Exception:
        return 0.0


def _fetch_us_etf_snap_batch(etf_dict: dict) -> dict:
    """US 상장 ETF 스냅샷 (AUM, price). etf_dict = US_ETFS_US 또는 US_ETFS_KR."""
    try:
        import yfinance as yf
    except ImportError:
        return {}
    result = {}
    syms = list(etf_dict.keys())
    try:
        hist = yf.download(syms, period='2d', auto_adjust=True, progress=False)
        closes = hist['Close'] if 'Close' in hist else None
    except Exception:
        closes = None

    for sym, meta in etf_dict.items():
        try:
            info  = yf.Ticker(sym).info
            aum   = _safe_float(info.get('totalAssets'))
            nav   = _safe_float(info.get('navPrice'))
            price = _safe_float(info.get('regularMarketPrice') or info.get('previousClose'))
            if price == 0 and closes is not None and sym in (closes.columns if closes is not None else []):
                price = _safe_float(closes[sym].dropna().iloc[-1])
            # AUM=0 이면 ref_aum 사용
            if aum == 0:
                aum = _safe_float(meta.get('ref_aum', 0))
            result[sym] = {
                'aum': round(aum, 0), 'price': round(price, 4),
                'nav': round(nav, 4), 'name': meta['name'], 'category': meta['category'],
            }
            time.sleep(0.15)
        except Exception as e:
            print(f'[ETF스냅샷] {sym} 오류: {e}')
    return result


def _fetch_kr_etf_snap_batch(etf_dict: dict) -> dict:
    """KR 상장 ETF 스냅샷 (price, trading_value)."""
    try:
        import yfinance as yf
    except ImportError:
        return {}
    result = {}
    for krx, meta in etf_dict.items():
        try:
            hist = yf.Ticker(meta['yf']).history(period='2d')
            if hist is None or hist.empty:
                continue
            last  = hist.iloc[-1]
            prev  = hist.iloc[-2] if len(hist) >= 2 else last
            price = _safe_float(last['Close'])
            vol   = _safe_float(last['Volume'])
            result[krx] = {
                'price':         round(price, 2),
                'prev_price':    round(_safe_float(prev['Close']), 2),
                'volume':        int(vol),
                'trading_value': round(price * vol, 0),
                'name':          meta['name'],
                'category':      meta['category'],
            }
            time.sleep(0.1)
        except Exception as e:
            print(f'[ETF스냅샷KR] {krx} 오류: {e}')
    return result


# ── 홀딩스 수집 ──────────────────────────────────────────────────────────

def _fetch_us_etf_holdings_yf(etf_dict: dict) -> dict:
    """yfinance top_holdings로 US ETF 편입 종목 수집 (US→US용)."""
    try:
        import yfinance as yf
    except ImportError:
        return {}
    result = {}
    for sym in etf_dict:
        try:
            top = yf.Ticker(sym).funds_data.top_holdings
            if top is not None and not top.empty:
                result[sym] = [
                    {'ticker': str(s), 'name': str(r.get('Name', s)),
                     'weight': round(float(r.get('Holding Percent', 0)), 6)}
                    for s, r in top.iterrows()
                ]
            time.sleep(0.1)
        except Exception:
            pass
    return result


# ── 플로우 계산 ──────────────────────────────────────────────────────────

def _calc_us_flows(today: dict, yesterday: dict) -> dict[str, float]:
    """US 상장 ETF 순플로우 (USD)."""
    flows = {}
    for sym, cur in today.items():
        prev = yesterday.get(sym)
        if not prev or prev.get('aum', 0) == 0 or prev.get('price', 0) == 0:
            flows[sym] = 0.0
            continue
        ret = (cur['price'] - prev['price']) / prev['price']
        flows[sym] = round(cur['aum'] - prev['aum'] * (1 + ret), 0)
    return flows


def _calc_kr_flows(today: dict, yesterday: dict) -> dict[str, float]:
    """KR 상장 ETF 순플로우 (KRW)."""
    flows = {}
    for krx, cur in today.items():
        prev = yesterday.get(krx)
        if not prev:
            # 첫 수집: 가격방향 × 거래대금 × 10% 프록시
            d = 1 if cur['price'] > cur['prev_price'] else -1
            flows[krx] = round(cur['trading_value'] * d * 0.10, 0)
            continue
        p_prev = prev.get('price', 0)
        if p_prev == 0:
            flows[krx] = 0.0
            continue
        ret = (cur['price'] - p_prev) / p_prev
        flows[krx] = round(cur['trading_value'] - prev.get('trading_value', cur['trading_value']) * (1 + ret), 0)
    return flows


# ── 종목별 유입액 집계 ───────────────────────────────────────────────────

def _aggregate_impact(etf_flows, holdings_map, etf_meta_map,
                      currency='USD', min_etf_flow=1_000_000, min_stock_flow=10_000):
    """공통 집계 로직. 반환: {stock_ticker: {name,total_flow,by_etf,currency}}"""
    agg = {}
    for etf_sym, flow in etf_flows.items():
        if abs(flow) < min_etf_flow:
            continue
        for h in holdings_map.get(etf_sym, []):
            sym    = h['ticker']
            w      = h['weight']
            share  = round(flow * w, 0)
            if abs(share) < min_stock_flow:
                continue
            if sym not in agg:
                agg[sym] = {'name': h['name'], 'total_flow': 0.0,
                             'by_etf': [], 'currency': currency}
            agg[sym]['total_flow'] += share
            agg[sym]['by_etf'].append({
                'etf':        etf_sym,
                'etf_name':   etf_meta_map.get(etf_sym, {}).get('name', etf_sym),
                'flow_share': share,
                'weight_pct': round(w * 100, 3),
            })
    for s in agg.values():
        s['total_flow'] = round(s['total_flow'], 0)
        s['by_etf'].sort(key=lambda x: abs(x['flow_share']), reverse=True)
    return agg


def _rank(impact_dict, top_n=60):
    items = [{'ticker': k, **v} for k, v in impact_dict.items() if v['total_flow'] != 0]
    # 절댓값 큰 순으로 저장 (유입/유출 모두 포함)
    items.sort(key=lambda x: abs(x['total_flow']), reverse=True)
    return items[:top_n]


# ── 메인 수집 함수 ──────────────────────────────────────────────────────

def fetch_all_etf_flows(prev_data: dict = None) -> dict:
    """4방향 ETF 플로우 전체 수집."""
    prev_data = prev_data or {}
    today_str = datetime.now().strftime('%Y-%m-%d')

    # ① 스냅샷 수집
    print('[ETF] US→US ETF 스냅샷...')
    snap_us_us = _fetch_us_etf_snap_batch(US_ETFS_US)
    print(f'[ETF] US→KR ETF 스냅샷...')
    snap_us_kr = _fetch_us_etf_snap_batch(US_ETFS_KR)
    print(f'[ETF] KR→KR ETF 스냅샷...')
    snap_kr_kr = _fetch_kr_etf_snap_batch(KR_ETFS_KR)
    print(f'[ETF] KR→US ETF 스냅샷...')
    snap_kr_us = _fetch_kr_etf_snap_batch(KR_ETFS_US)

    # ② 홀딩스 수집 (US→US만 yfinance, 나머지 하드코드)
    print('[ETF] US ETF 홀딩스 수집...')
    hold_us_us = _fetch_us_etf_holdings_yf(US_ETFS_US)

    # ③ 플로우 계산
    prev_us_us = prev_data.get('snap_us_us', {})
    prev_us_kr = prev_data.get('snap_us_kr', {})
    prev_kr_kr = prev_data.get('snap_kr_kr', {})
    prev_kr_us = prev_data.get('snap_kr_us', {})

    flow_us_us = _calc_us_flows(snap_us_us, prev_us_us)
    flow_us_kr = _calc_us_flows(snap_us_kr, prev_us_kr)
    flow_kr_kr = _calc_kr_flows(snap_kr_kr, prev_kr_kr)
    flow_kr_us = _calc_kr_flows(snap_kr_us, prev_kr_us)

    # 전일 데이터 없으면 볼륨 기반 근사치로 대체
    def _vol_fallback_us(snap: dict, etf_dict: dict) -> dict[str, float]:
        """prev 없을 때 오늘 볼륨 기반 근사 플로우."""
        try:
            import yfinance as yf
            import numpy as np
            syms = list(etf_dict.keys())
            hist = yf.download(syms, period='5d', auto_adjust=True, progress=False)
            closes = hist.get('Close'); volumes = hist.get('Volume')
            if closes is None or closes.empty: return {}
            result = {}
            for sym in syms:
                aum = (snap.get(sym) or {}).get('aum', 0)
                if not aum or sym not in closes.columns: continue
                try:
                    prices = closes[sym].dropna()
                    vols   = volumes[sym].dropna() if volumes is not None else None
                    if len(prices) < 2: continue
                    p1, p0 = float(prices.iloc[-1]), float(prices.iloc[-2])
                    direction = 1 if p1 > p0 else -1
                    vol_ratio = 1.0
                    if vols is not None and sym in vols.index and len(vols) >= 2:
                        v_mean = float(vols.mean()) or 1.0
                        v_today = float(vols.iloc[-1])
                        if not np.isnan(v_today) and v_mean > 0:
                            vol_ratio = max(0.1, min(4.0, v_today / v_mean))
                    result[sym] = round(aum * 0.003 * vol_ratio * direction, 0)
                except Exception:
                    pass
            return result
        except Exception:
            return {}

    if all(v == 0 for v in flow_us_us.values()):
        flow_us_us = _vol_fallback_us(snap_us_us, US_ETFS_US)
    if all(v == 0 for v in flow_us_kr.values()):
        flow_us_kr = _vol_fallback_us(snap_us_kr, US_ETFS_KR)

    # ④ 종목별 유입액 집계
    # us_us: 미국 ETF → 미국 종목 (USD)
    imp_us_us = _aggregate_impact(flow_us_us, hold_us_us, US_ETFS_US,
                                   currency='USD', min_etf_flow=1_000_000, min_stock_flow=10_000)
    # us_kr: 미국 ETF → 한국 종목 (USD)
    imp_us_kr = _aggregate_impact(flow_us_kr, US_KR_HOLDINGS, US_ETFS_KR,
                                   currency='USD', min_etf_flow=100_000, min_stock_flow=1_000)
    # kr_kr: 한국 ETF → 한국 종목 (KRW)
    imp_kr_kr = _aggregate_impact(flow_kr_kr, KR_ETF_KR_HOLDINGS, KR_ETFS_KR,
                                   currency='KRW', min_etf_flow=100_000_000, min_stock_flow=10_000_000)
    # kr_us: 한국 ETF → 미국 종목 (KRW)
    imp_kr_us = _aggregate_impact(flow_kr_us, KR_ETF_US_HOLDINGS, KR_ETFS_US,
                                   currency='KRW', min_etf_flow=10_000_000, min_stock_flow=1_000_000)

    # ⑤ 레버리지 센티멘트 (TQQQ vs SQQQ)
    tqqq = flow_us_us.get('TQQQ', 0)
    sqqq = flow_us_us.get('SQQQ', 0)
    bull_bear = None
    total_lev = abs(tqqq) + abs(sqqq)
    if total_lev > 0:
        bull_bear = round((tqqq - sqqq) / total_lev * 100, 1)

    # ⑥ 삼성전자 / SK하이닉스 — 미국 ETF 기반 장시작 영향 신호
    prev_us_kr_snap = prev_data.get('snap_us_kr', {})
    kr_open_signal: dict = {}
    _targets = {
        '005930': ('삼성전자',  {'EWY': 0.2380, 'FLKR': 0.2210, 'DRAM': 0.2960}),
        '000660': ('SK하이닉스',{'EWY': 0.0920, 'FLKR': 0.0850, 'DRAM': 0.1850}),
    }
    etf_ret_us_kr: dict[str, float] = {}
    for sym in US_ETFS_KR:
        today_p = (snap_us_kr.get(sym) or {}).get('price', 0)
        prev_p  = (prev_us_kr_snap.get(sym) or {}).get('price', 0)
        if today_p and prev_p and prev_p > 0:
            etf_ret_us_kr[sym] = round((today_p / prev_p - 1) * 100, 3)  # %
    for ticker, (name, weights) in _targets.items():
        w_sum = ret_sum = 0.0
        signals = []
        for etf, w in weights.items():
            if etf in etf_ret_us_kr:
                ret_sum += etf_ret_us_kr[etf] * w
                w_sum   += w
                signals.append({'etf': etf, 'return_pct': etf_ret_us_kr[etf], 'weight_pct': round(w * 100, 1)})
        if w_sum > 0:
            kr_open_signal[ticker] = {
                'name':         name,
                'expected_pct': round(ret_sum / w_sum, 3),  # 가중평균 수익률%
                'signals':      signals,
            }

    return {
        'date':         today_str,
        # 스냅샷 (전일 비교용)
        'snap_us_us':   snap_us_us,
        'snap_us_kr':   snap_us_kr,
        'snap_kr_kr':   snap_kr_kr,
        'snap_kr_us':   snap_kr_us,
        # ETF별 순플로우
        'flow_us_us':   {k: round(v) for k, v in flow_us_us.items()},
        'flow_us_kr':   {k: round(v) for k, v in flow_us_kr.items()},
        'flow_kr_kr':   {k: round(v) for k, v in flow_kr_kr.items()},
        'flow_kr_us':   {k: round(v) for k, v in flow_kr_us.items()},
        # 종목별 유입액 (4방향)
        'imp_us_us':    _rank(imp_us_us),
        'imp_us_kr':    _rank(imp_us_kr),
        'imp_kr_kr':    _rank(imp_kr_kr),
        'imp_kr_us':    _rank(imp_kr_us),
        'bull_bear':      bull_bear,
        'kr_open_signal': kr_open_signal,
        'updated_at':     datetime.now().isoformat(timespec='seconds'),
    }


def backfill_historical_flows(days: int = 90) -> int:
    """90일치 역사 데이터 백필 (ETF 일간 수익률 근사치).

    정확한 AUM 히스토리가 없어 ETF 가격 수익률로 근사:
      approx_flow_t = current_aum × price_return_t
    각 ETF가 독립적 수익률을 가지므로 종목별 추이가 차별화됨
    (XLK=기술 vs XLF=금융 → NVDA vs JPM 패턴 다름).
    """
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import database
    try:
        import yfinance as yf
        import numpy as np
    except ImportError:
        print('yfinance/numpy 없음'); return 0

    # 최신 데이터에서 현재 AUM 가져오기
    latest_row = database.get_etf_flows_latest()
    if not latest_row:
        print('[백필] 오늘 데이터 없음 — 먼저 fetch_all_etf_flows() 실행')
        return 0
    today_data  = latest_row.get('data', {})
    today_str   = latest_row.get('date', '')
    snap_us_us  = today_data.get('snap_us_us', {})
    snap_us_kr  = today_data.get('snap_us_kr', {})
    snap_kr_kr  = today_data.get('snap_kr_kr', {})
    snap_kr_us  = today_data.get('snap_kr_us', {})

    # US ETF 홀딩스 (yfinance)
    print('[백필] US ETF 홀딩스 수집...')
    hold_us_us = _fetch_us_etf_holdings_yf(US_ETFS_US)

    # 가격+볼륨 90일 히스토리 다운로드
    us_syms = list(US_ETFS_US.keys()) + list(US_ETFS_KR.keys())
    kr_syms_yf = {meta['yf']: krx for krx, meta in {**KR_ETFS_KR, **KR_ETFS_US}.items()}
    kr_yf_list  = list(kr_syms_yf.keys())

    print('[백필] US ETF 히스토리...')
    try:
        us_hist   = yf.download(us_syms,  period='3mo', auto_adjust=True, progress=False)
        us_close  = us_hist.get('Close',  None)
        us_volume = us_hist.get('Volume', None)
    except Exception as e:
        print(f'[백필] US 히스토리 오류: {e}')
        us_close = us_volume = None

    print('[백필] KR ETF 히스토리...')
    try:
        kr_hist   = yf.download(kr_yf_list, period='3mo', auto_adjust=True, progress=False)
        kr_close  = kr_hist.get('Close',  None)
        kr_volume = kr_hist.get('Volume', None)
    except Exception as e:
        print(f'[백필] KR 히스토리 오류: {e}')
        kr_close = kr_volume = None

    if us_close is None or us_close.empty:
        print('[백필] US 데이터 없음'); return 0

    dates = us_close.index.strftime('%Y-%m-%d').tolist()

    # KR 날짜→인덱스 맵 (날짜 수 다를 수 있음)
    kr_date_map: dict[str, int] = {}
    if kr_close is not None and not kr_close.empty:
        for pos, ts in enumerate(kr_close.index):
            kr_date_map[ts.strftime('%Y-%m-%d')] = pos

    def _prf_us(sym, aum_or_tv, i):
        """US ETF: iloc 기반."""
        if not aum_or_tv or us_close is None or sym not in us_close.columns:
            return None
        try:
            p1 = float(us_close[sym].iloc[i])
            p0 = float(us_close[sym].iloc[i - 1])
            if p0 == 0 or np.isnan(p1) or np.isnan(p0): return None
            return round(aum_or_tv * (p1 / p0 - 1.0), 0)
        except Exception:
            return None

    def _prf_kr(yf_sym, aum_or_tv, date):
        """KR ETF: 날짜 기반 (.loc) — US와 날짜 수 달라도 정확."""
        if not aum_or_tv or kr_close is None or yf_sym not in kr_close.columns:
            return None
        pos = kr_date_map.get(date)
        if pos is None or pos == 0:
            return None
        try:
            p1 = float(kr_close[yf_sym].iloc[pos])
            p0 = float(kr_close[yf_sym].iloc[pos - 1])
            if p0 == 0 or np.isnan(p1) or np.isnan(p0): return None
            return round(aum_or_tv * (p1 / p0 - 1.0), 0)
        except Exception:
            return None

    saved = 0
    for i in range(1, len(dates)):
        date = dates[i]
        if date >= today_str:
            continue  # 오늘 이후 건너뜀

        # ── US→US 플로우 추정 ──────────────────────────────────────────
        flow_us_us_day: dict[str, float] = {}
        for sym in US_ETFS_US:
            aum = (snap_us_us.get(sym) or {}).get('aum', 0)
            v = _prf_us(sym, aum, i)
            if v is not None: flow_us_us_day[sym] = v

        # ── US→KR 플로우 추정 ─────────────────────────────────────────
        flow_us_kr_day: dict[str, float] = {}
        for sym in US_ETFS_KR:
            aum = (snap_us_kr.get(sym) or {}).get('aum', 0)
            v = _prf_us(sym, aum, i)
            if v is not None: flow_us_kr_day[sym] = v

        # ── KR→KR 플로우 추정 ─────────────────────────────────────────
        flow_kr_kr_day: dict[str, float] = {}
        for krx, meta in KR_ETFS_KR.items():
            tv = (snap_kr_kr.get(krx) or {}).get('trading_value', 0)
            if not tv: tv = meta.get('ref_tv', 0)   # 주말/장외 폴백
            v = _prf_kr(meta['yf'], tv, date)
            if v is not None: flow_kr_kr_day[krx] = v

        # ── KR→US 플로우 추정 ─────────────────────────────────────────
        flow_kr_us_day: dict[str, float] = {}
        for krx, meta in KR_ETFS_US.items():
            tv = (snap_kr_us.get(krx) or {}).get('trading_value', 0)
            if not tv: tv = meta.get('ref_tv', 0)   # 주말/장외 폴백
            v = _prf_kr(meta['yf'], tv, date)
            if v is not None: flow_kr_us_day[krx] = v

        # ── 종목별 유입액 집계 ─────────────────────────────────────────
        imp_us_us = _aggregate_impact(flow_us_us_day, hold_us_us, US_ETFS_US,
                                       'USD', min_etf_flow=1_000_000, min_stock_flow=10_000)
        imp_us_kr = _aggregate_impact(flow_us_kr_day, US_KR_HOLDINGS, US_ETFS_KR,
                                       'USD', min_etf_flow=100_000, min_stock_flow=1_000)
        imp_kr_kr = _aggregate_impact(flow_kr_kr_day, KR_ETF_KR_HOLDINGS, KR_ETFS_KR,
                                       'KRW', min_etf_flow=100_000_000, min_stock_flow=10_000_000)
        imp_kr_us = _aggregate_impact(flow_kr_us_day, KR_ETF_US_HOLDINGS, KR_ETFS_US,
                                       'KRW', min_etf_flow=10_000_000, min_stock_flow=1_000_000)

        day_result = {
            'date':       date,
            'snap_us_us': {}, 'snap_us_kr': {}, 'snap_kr_kr': {}, 'snap_kr_us': {},
            'flow_us_us': {k: round(v) for k,v in flow_us_us_day.items()},
            'flow_us_kr': {k: round(v) for k,v in flow_us_kr_day.items()},
            'flow_kr_kr': {k: round(v) for k,v in flow_kr_kr_day.items()},
            'flow_kr_us': {k: round(v) for k,v in flow_kr_us_day.items()},
            'imp_us_us':  _rank(imp_us_us),
            'imp_us_kr':  _rank(imp_us_kr),
            'imp_kr_kr':  _rank(imp_kr_kr),
            'imp_kr_us':  _rank(imp_kr_us),
            'bull_bear':  None,
            'is_backfill': True,
            'updated_at': datetime.now().isoformat(timespec='seconds'),
        }
        database.save_etf_flows(date, day_result)
        saved += 1

    print(f'[백필] 완료: {saved}일 저장')
    return saved


if __name__ == '__main__':
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
    import database
    database.init_db()

    prev_row  = database.get_etf_flows_latest()
    prev_data = prev_row.get('data', {}) if prev_row else {}
    if prev_row:
        print(f'전일 데이터: {prev_row.get("date")}')

    result = fetch_all_etf_flows(prev_data)
    today  = result['date']

    for label, key in [('🇺🇸→🇺🇸 US종목', 'imp_us_us'), ('🇰🇷→🇰🇷 KR종목', 'imp_kr_kr'),
                        ('🇺🇸→🇰🇷 KR종목(USD)', 'imp_us_kr'), ('🇰🇷→🇺🇸 US종목(KRW)', 'imp_kr_us')]:
        items = result[key]
        cur = 'USD' if 'USD' in label or key.endswith('us') else 'KRW'
        sym = '$' if cur == 'USD' else '₩'
        print(f'\n[{label}] {len(items)}개')
        for x in items[:5]:
            print(f'  {x["ticker"]:8s} {x["name"][:20]:20s} {sym}{x["total_flow"]:>+,.0f}')
    if result.get('bull_bear') is not None:
        print(f'\n레버리지 센티: {result["bull_bear"]:+.1f}%')

    database.save_etf_flows(today, result)
    print(f'\nDB 저장 완료 ({today})')

    # 히스토리가 7일 미만이면 백필 실행
    hist_count = len(database.get_etf_flows_history(90))
    if hist_count < 7:
        print(f'\n[백필] 히스토리 {hist_count}일 → 90일 백필 시작...')
        backfill_historical_flows(90)
