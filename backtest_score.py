"""
backtest_score.py
스코어 선행 검증 + 가중치 자동 최적화

실행:
    source venv/bin/activate
    python backtest_score.py

결과:
    - 현재 가중치 정확도(1주/2주/4주 선행)
    - 60% 미만이면 그리드서치로 최적화
    - 최적 가중치를 main.py WEIGHTS에 자동 반영
"""

import os
import sys
import time
import json
import itertools
from datetime import datetime, timedelta

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── 설정 ─────────────────────────────────────────────────────────────
TARGET_ACCURACY = 0.60  # 목표 방향 정확도

STATUS_SCORE = {"위험": -2, "경고": -1, "관망": 0, "긍정": 1, "최상": 2}

# 현재 가중치 (main.py와 동기화)
BASE_WEIGHTS = {
    "tariff":          12,
    "usd_krw":         15,
    "us10y":           10,
    "foreign_flow":    15,
    "commercial_law":   5,
    "fund_flow":        5,
    "semiconductor":    5,
    "ria":              5,
    "msci":             5,
    "wti":             12,
    "soxx":             8,
    "hy_spread":       20,
    "tga_mmf_status":   8,
    "mmf_total":        8,
    "rrp":              5,
    "tga":              8,
    "yield_curve":     10,
    "fear_greed":      10,
    # 신규
    "btc":              8,
    "vix":             12,
    "nasdaq":           8,
    "gold":             5,
}

# 백테스트 가능한 정량 지표만 (Claude 정성 지표 제외)
QUANT_KEYS = [
    "usd_krw", "us10y", "wti", "soxx", "hy_spread",
    "yield_curve", "mmf_total", "tga", "rrp",
    "fear_greed", "foreign_flow",
    "btc", "vix", "nasdaq", "gold",
]

# ── 데이터 수집 ───────────────────────────────────────────────────────

def fetch_yahoo_history(symbol: str, range_: str = '1y') -> list[tuple[str, float]]:
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
    params = {'interval': '1d', 'range': range_}
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; StockBot/1.0)'}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        res = r.json()['chart']['result'][0]
        timestamps = res['timestamp']
        closes = res['indicators']['quote'][0]['close']
        return [
            (datetime.fromtimestamp(ts).strftime('%Y-%m-%d'), round(cv, 4))
            for ts, cv in zip(timestamps, closes) if cv is not None
        ]
    except Exception as e:
        print(f'  [Yahoo] {symbol} 오류: {e}')
    return []


def fetch_fred_history(series_id: str, api_key: str, limit: int = 300) -> list[tuple[str, float]]:
    url = 'https://api.stlouisfed.org/fred/series/observations'
    params = {'series_id': series_id, 'api_key': api_key, 'file_type': 'json',
              'sort_order': 'desc', 'limit': limit}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        js = r.json()
        if 'error_code' in js:
            return []
        result = []
        for obs in js.get('observations', []):
            if obs['value'] not in ('.', ''):
                result.append((obs['date'], float(obs['value'])))
        return list(reversed(result))
    except Exception as e:
        print(f'  [FRED] {series_id} 오류: {e}')
    return []


def fetch_kospi_history() -> list[tuple[str, float]]:
    """Yahoo Finance로 KOSPI 1년 종가 수집 (^KS11)"""
    data = fetch_yahoo_history('^KS11', '1y')
    if data:
        print(f'  KOSPI(Yahoo): {len(data)}일 수집')
    return data


def fetch_fg_history() -> list[tuple[str, float]]:
    """CNN Fear & Greed 히스토리"""
    urls = [
        'https://production.dataviz.cnn.io/index/fearandgreed/graphdata/',
        'https://production.dataviz.cnn.io/index/fearandgreed/graphdata',
    ]
    headers = {'User-Agent': 'Mozilla/5.0 Chrome/120', 'Referer': 'https://edition.cnn.com/markets/fear-and-greed'}
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=15)
            if not r.ok:
                continue
            js = r.json()
            hist = js.get('fear_and_greed_historical', {}).get('data', [])
            result = []
            for pt in hist:
                d = datetime.fromtimestamp(pt['x'] / 1000).strftime('%Y-%m-%d')
                result.append((d, float(pt['y'])))
            return sorted(result)
        except Exception as e:
            print(f'  [F&G] {url} 오류: {e}')
    return []


# ── 상태 임계값 ───────────────────────────────────────────────────────

def get_status(key: str, v: float, closes_list: list = None) -> str:
    def above(v, thr, default='최상'):
        for t, s in thr:
            if v >= t: return s
        return default
    def below(v, thr, default='최상'):
        for t, s in thr:
            if v <= t: return s
        return default

    if key == 'usd_krw':   return above(v, [(1520,'위험'),(1490,'경고'),(1455,'관망'),(1420,'긍정')])
    if key == 'us10y':     return above(v, [(4.5,'위험'),(4.2,'경고'),(3.8,'관망'),(3.5,'긍정')])
    if key == 'wti':       return above(v, [(90,'위험'),(80,'경고'),(70,'관망'),(60,'긍정')])
    if key == 'hy_spread': return above(v, [(5.0,'위험'),(4.0,'경고'),(3.5,'관망'),(3.0,'긍정')])
    if key == 'vix':       return above(v, [(30,'위험'),(25,'경고'),(20,'관망'),(15,'긍정')])
    if key == 'gold':      return above(v, [(2700,'위험'),(2550,'경고'),(2400,'관망'),(2200,'긍정')])
    if key == 'yield_curve':
        if v <= -0.5: return '위험'
        if v <= 0:    return '경고'
        if v >= 1.0:  return '최상'
        if v >= 0.3:  return '긍정'
        return '관망'
    if key == 'mmf_total':
        if v <= 7.5: return '위험'
        if v <= 7.8: return '경고'
        if v <= 8.0: return '관망'
        return '긍정' if v < 8.5 else '최상'
    if key == 'fear_greed':
        if v >= 75:   return '위험'
        if v >= 55:   return '경고'
        if v >= 45:   return '관망'
        if v >= 25:   return '긍정'
        return '최상'
    if key == 'foreign_flow':
        eok = v  # 이미 억원 단위
        if eok <= -5000: return '위험'
        if eok <= -1000: return '경고'
        if eok <= -1:    return '관망'
        if eok >= 5000:  return '최상'
        return '긍정'
    if key == 'btc':
        if v <= 55000: return '위험'
        if v <= 70000: return '경고'
        if v <= 85000: return '관망'
        if v < 95000:  return '긍정'
        return '최상'
    if key == 'nasdaq':
        # closes_list: [(date, val), ...] 정렬됨
        if not closes_list or len(closes_list) < 20:
            return '관망'
        idx = next((i for i, (d, _) in enumerate(closes_list) if abs(closes_list[i][1] - v) < 1), None)
        if idx is None or idx < 20:
            return '관망'
        prev = closes_list[idx - 20][1]
        pct = (v - prev) / prev * 100
        if pct >= 5:    return '최상'
        if pct >= 2:    return '긍정'
        if pct >= 0:    return '관망'
        if pct >= -3:   return '경고'
        return '위험'
    # soxx, tga, rrp → 기본 관망
    return '관망'


# ── 스코어 계산 ───────────────────────────────────────────────────────

def compute_score(day_data: dict, weights: dict) -> float:
    wsum, wmax = 0, 0
    for k, w in weights.items():
        if k not in QUANT_KEYS:
            continue
        st = day_data.get(k)
        if st and st in STATUS_SCORE:
            wsum += STATUS_SCORE[st] * w
        wmax += w * 2
    return round(50 - (wsum / wmax * 50) if wmax else 50, 2)


# ── 백테스트 정확도 ───────────────────────────────────────────────────

def backtest(daily_status: dict, kospi: list[tuple[str, float]],
             lag_weeks: int, weights: dict) -> float:
    """
    lag_weeks 주 선행 방향 정확도.
    score 상승 → KOSPI 하락 (음의 상관) 이 맞으면 correct.
    """
    kospi_dict = dict(kospi)
    dates_sorted = sorted(daily_status.keys())
    kospi_dates  = sorted(kospi_dict.keys())

    correct = total = 0
    lag_days = lag_weeks * 7

    for d in dates_sorted:
        future_d = (datetime.strptime(d, '%Y-%m-%d') + timedelta(days=lag_days)).strftime('%Y-%m-%d')
        # 가장 가까운 미래 KOSPI 날짜
        future_kospi = next((kospi_dict[fd] for fd in kospi_dates if fd >= future_d), None)
        cur_kospi    = next((kospi_dict[cd] for cd in kospi_dates if cd >= d), None)
        if future_kospi is None or cur_kospi is None:
            continue

        score = compute_score(daily_status[d], weights)
        kospi_ret = (future_kospi - cur_kospi) / cur_kospi

        # score > 50 → 위험 → KOSPI 하락 기대
        # score < 50 → 안전 → KOSPI 상승 기대
        predicted_down = score > 50
        actual_down    = kospi_ret < 0
        if predicted_down == actual_down:
            correct += 1
        total += 1

    return correct / total if total > 0 else 0


# ── 메인 ─────────────────────────────────────────────────────────────

def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
    fred_key = os.getenv('FRED_API_KEY', '')

    print('=' * 55)
    print('  시장 지표 스코어 백테스트 & 가중치 최적화')
    print('=' * 55)

    # ── 1. 데이터 수집 ──────────────────────────────────────────
    print('\n[1/4] 데이터 수집 중 (약 1~2분)...')

    symbols = {
        'usd_krw': 'KRW=X', 'us10y': '^TNX', 'wti': 'CL=F',
        'soxx': 'SOXX', 'btc': 'BTC-USD', 'vix': '^VIX',
        'nasdaq': '^IXIC', 'gold': 'GC=F',
    }
    fred_map = {
        'hy_spread': 'BAMLH0A0HYM2',
        'yield_curve': 'T10Y2Y',
        'rrp': 'RRPONTSYD',
    }

    raw: dict[str, list[tuple[str, float]]] = {}
    for key, sym in symbols.items():
        print(f'  Yahoo: {key}...')
        raw[key] = fetch_yahoo_history(sym, '1y')
        time.sleep(0.4)

    fg = fetch_fg_history()
    if fg:
        raw['fear_greed'] = fg
        print(f'  F&G: {len(fg)}일')

    if fred_key:
        for key, series in fred_map.items():
            print(f'  FRED: {key}...')
            raw[key] = fetch_fred_history(series, fred_key)
            time.sleep(0.4)

    print('  pykrx: KOSPI...')
    kospi = fetch_kospi_history()
    print(f'  KOSPI: {len(kospi)}일 수집')

    if not kospi:
        print('❌ KOSPI 데이터 없음 — 백테스트 불가')
        return

    # 외국인 수급 (pykrx) — 실패해도 계속
    try:
        from pykrx import stock as _stock
        ff_raw = []
        for d, _ in kospi[-60:]:  # 최근 60일만 (속도)
            d_krx = d.replace('-', '')
            try:
                df = _stock.get_market_trading_value_by_investor(d_krx, d_krx, 'KOSPI')
                for label in ['외국인합계', '외국인']:
                    if label in df.index:
                        net = int(df.loc[label, '순매수']) / 1e8
                        ff_raw.append((d, net))
                        break
            except Exception:
                pass
            time.sleep(0.05)
        if ff_raw:
            raw['foreign_flow'] = ff_raw
            print(f'  외국인수급: {len(ff_raw)}일')
    except Exception as e:
        print(f'  외국인수급 스킵: {e}')

    # ── 2. 날짜별 상태 계산 ─────────────────────────────────────
    print('\n[2/4] 날짜별 상태 계산 중...')

    # 날짜 → 값 딕셔너리
    val_dicts: dict[str, dict[str, float]] = {}
    for key, pairs in raw.items():
        for d, v in pairs:
            val_dicts.setdefault(d, {})[key] = v

    nasdaq_list = sorted(raw.get('nasdaq', []))

    daily_status: dict[str, dict[str, str]] = {}
    for d in sorted(val_dicts.keys()):
        row = {}
        for key, v in val_dicts[d].items():
            if key == 'nasdaq':
                row[key] = get_status(key, v, nasdaq_list)
            elif key == 'mmf_total':
                row[key] = get_status(key, v / 1000)  # B$ → T$
            else:
                row[key] = get_status(key, v)
        daily_status[d] = row

    print(f'  {len(daily_status)}일치 상태 계산 완료')

    # ── 3. 현재 가중치 정확도 ────────────────────────────────────
    print('\n[3/4] 현재 가중치 성능 평가...')
    quant_weights = {k: v for k, v in BASE_WEIGHTS.items() if k in QUANT_KEYS}

    results = {}
    for lag in [1, 2, 4]:
        acc = backtest(daily_status, kospi, lag, quant_weights)
        results[lag] = acc
        print(f'  {lag}주 선행 방향 정확도: {acc:.1%}')

    best_lag = max(results, key=results.get)
    best_acc = results[best_lag]

    if best_acc >= TARGET_ACCURACY:
        print(f'\n✅ 현재 가중치 성능 충분 ({best_acc:.1%} @ {best_lag}주)')
        _update_main_weights(quant_weights)
        _print_summary(quant_weights, results, best_acc, optimized=False)
        return

    # ── 4. 가중치 그리드서치 ────────────────────────────────────
    print(f'\n[4/4] 정확도 부족 ({best_acc:.1%}) → 가중치 최적화 시작...')

    # 핵심 지표 가중치만 탐색 (조합 폭발 방지)
    search_space = {
        'usd_krw':     [10, 15, 20],
        'hy_spread':   [15, 20, 25],
        'fear_greed':  [8, 12, 15],
        'vix':         [8, 12, 15],
        'foreign_flow':[10, 15, 20],
        'btc':         [5, 8, 12],
        'us10y':       [8, 12, 15],
        'nasdaq':      [5, 8, 12],
    }
    fixed = {k: v for k, v in quant_weights.items() if k not in search_space}

    best_weights = dict(quant_weights)
    best_acc_opt = best_acc

    keys = list(search_space.keys())
    vals = list(search_space.values())
    total_combos = 1
    for v in vals:
        total_combos *= len(v)
    print(f'  탐색 조합 수: {total_combos:,}')

    for combo in itertools.product(*vals):
        w = dict(zip(keys, combo))
        w.update(fixed)
        acc = backtest(daily_status, kospi, best_lag, w)
        if acc > best_acc_opt:
            best_acc_opt = acc
            best_weights = dict(w)

    print(f'\n최적화 결과: {best_acc_opt:.1%} (기존 {best_acc:.1%})')
    print('최적 가중치 (변경된 항목):')
    for k in search_space:
        old = quant_weights.get(k, 0)
        new = best_weights.get(k, 0)
        if old != new:
            print(f'  {k}: {old} → {new}')

    # 최종 정확도 검증
    final_results = {}
    for lag in [1, 2, 4]:
        acc = backtest(daily_status, kospi, lag, best_weights)
        final_results[lag] = acc
        print(f'  최적 {lag}주 선행: {acc:.1%}')

    _update_main_weights(best_weights)
    _print_summary(best_weights, final_results, best_acc_opt, optimized=True)


def _update_main_weights(weights: dict):
    """main.py WEIGHTS 딕셔너리 자동 업데이트"""
    main_path = os.path.join(os.path.dirname(__file__), 'main.py')
    try:
        with open(main_path, 'r', encoding='utf-8') as f:
            content = f.read()

        import re
        # WEIGHTS 블록 찾아서 교체
        new_weights_str = '    WEIGHTS = {\n'
        for k, v in weights.items():
            new_weights_str += f'        "{k}":{" " * max(1, 20 - len(k))}{v},\n'
        new_weights_str += '    }'

        content_new = re.sub(
            r'    WEIGHTS = \{[^}]+\}',
            new_weights_str,
            content,
            flags=re.DOTALL
        )
        if content_new == content:
            print('⚠️  main.py WEIGHTS 패턴 미매칭 — 수동 확인 필요')
            return
        with open(main_path, 'w', encoding='utf-8') as f:
            f.write(content_new)
        print('✅ main.py WEIGHTS 업데이트 완료')
    except Exception as e:
        print(f'⚠️  main.py 업데이트 실패: {e}')


def _print_summary(weights: dict, results: dict, best_acc: float, optimized: bool):
    """결과 요약 출력 + JSON 저장"""
    summary = {
        'optimized': optimized,
        'accuracy_by_lag': {f'{k}w': round(v, 4) for k, v in results.items()},
        'best_accuracy': round(best_acc, 4),
        'weights': weights,
        'updated_at': datetime.now().isoformat(),
    }
    out_path = os.path.join(os.path.dirname(__file__), 'backtest_result.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f'\n📄 결과 저장: {out_path}')
    print('\n─── 최종 요약 ───')
    print(f'  최적화 여부: {"예" if optimized else "아니오 (기존 충분)"}')
    for lag, acc in results.items():
        bar = '█' * int(acc * 20) + '░' * (20 - int(acc * 20))
        print(f'  {lag}주 선행: [{bar}] {acc:.1%}')
    print(f'  목표 정확도: {TARGET_ACCURACY:.0%}')
    print(f'  달성 여부:   {"✅" if best_acc >= TARGET_ACCURACY else "❌ (데이터 부족 또는 구조적 한계)"}')


if __name__ == '__main__':
    main()
