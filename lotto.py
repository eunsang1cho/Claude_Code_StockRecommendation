"""
lotto.py
로또 6/45 당첨번호 수집 및 추천 알고리즘 (5게임: 통계×2, ML×2, 랜덤×1)
"""

import json
import random
import sqlite3
import urllib.request
from collections import Counter, defaultdict

DB_PATH = 'stocks.db'
ALL_JSON_URL = 'https://smok95.github.io/lotto/results/all.json'
LATEST_JSON_URL = 'https://smok95.github.io/lotto/results/latest.json'

# ── DB ────────────────────────────────────────────────────────────

def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_table():
    with _conn() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS lotto_draws (
            draw_no INTEGER PRIMARY KEY,
            date    TEXT,
            n1 INTEGER, n2 INTEGER, n3 INTEGER,
            n4 INTEGER, n5 INTEGER, n6 INTEGER,
            bonus   INTEGER
        )''')
        conn.commit()

def fetch_and_store() -> dict:
    """all.json 으로 DB 업데이트. 반환: {saved, total, latest_draw}"""
    ensure_table()
    req = urllib.request.Request(ALL_JSON_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        draws = json.loads(r.read())

    with _conn() as conn:
        existing = {row[0] for row in conn.execute('SELECT draw_no FROM lotto_draws')}
        saved = 0
        for d in draws:
            if d['draw_no'] in existing:
                continue
            nums = d['numbers']
            conn.execute(
                'INSERT INTO lotto_draws VALUES (?,?,?,?,?,?,?,?,?)',
                (d['draw_no'], d['date'][:10], nums[0], nums[1], nums[2],
                 nums[3], nums[4], nums[5], d['bonus_no'])
            )
            saved += 1
        conn.commit()

    return {'saved': saved, 'total': len(draws), 'latest_draw': draws[-1]['draw_no']}

def get_all_draws() -> list:
    ensure_table()
    with _conn() as conn:
        rows = conn.execute('SELECT * FROM lotto_draws ORDER BY draw_no').fetchall()
    return [
        {'draw_no': r['draw_no'], 'date': r['date'],
         'numbers': [r['n1'], r['n2'], r['n3'], r['n4'], r['n5'], r['n6']],
         'bonus': r['bonus']}
        for r in rows
    ]

def get_draw_count() -> int:
    ensure_table()
    with _conn() as conn:
        return conn.execute('SELECT COUNT(*) FROM lotto_draws').fetchone()[0]

# ── 공통 유틸 ─────────────────────────────────────────────────────

def _weighted_sample(weights_dict: dict, k: int) -> list:
    """가중치 딕셔너리에서 중복 없이 k개 샘플링."""
    pool = list(weights_dict.keys())
    w = [weights_dict[n] for n in pool]
    chosen = []
    remaining_pool = pool[:]
    remaining_w = w[:]
    for _ in range(k):
        if not remaining_pool:
            break
        total = sum(remaining_w)
        if total == 0:
            chosen.append(random.choice(remaining_pool))
            idx = remaining_pool.index(chosen[-1])
        else:
            r = random.uniform(0, total)
            cumulative = 0
            idx = 0
            for i, wt in enumerate(remaining_w):
                cumulative += wt
                if r <= cumulative:
                    idx = i
                    break
        chosen.append(remaining_pool[idx])
        remaining_pool.pop(idx)
        remaining_w.pop(idx)
    return sorted(chosen)

# ── 추천 알고리즘 ─────────────────────────────────────────────────

def recommend_hot(draws: list) -> list:
    """통계①: 최근 52회(1년) 출현 빈도 상위 번호 가중 샘플링"""
    recent = draws[-52:]
    freq = Counter()
    for d in recent:
        for n in d['numbers']:
            freq[n] += 1
    weights = {i: freq.get(i, 0.3) for i in range(1, 46)}
    return _weighted_sample(weights, 6)

def recommend_cold(draws: list) -> list:
    """통계②: 소외 기간(미출현 회차 수) 비례 가중 샘플링"""
    latest_no = draws[-1]['draw_no']
    last_seen = {}
    for d in draws:
        for n in d['numbers']:
            last_seen[n] = d['draw_no']
    weights = {i: (latest_no - last_seen.get(i, 0)) for i in range(1, 46)}
    return _weighted_sample(weights, 6)

def recommend_cooccurrence(draws: list) -> list:
    """ML①: 공출현 행렬 — 자주 함께 나온 번호 그룹에서 선택"""
    matrix = defaultdict(int)
    for d in draws:
        nums = d['numbers']
        for i in range(len(nums)):
            for j in range(i + 1, len(nums)):
                a, b = min(nums[i], nums[j]), max(nums[i], nums[j])
                matrix[(a, b)] += 1

    # 번호별 "사교성 점수" (다른 번호와 공출현 합계)
    score = Counter()
    for (a, b), cnt in matrix.items():
        score[a] += cnt
        score[b] += cnt

    # 상위 20개에서 가중 샘플링 (결정적이지 않게)
    top20 = sorted(range(1, 46), key=lambda x: score.get(x, 0), reverse=True)[:20]
    weights = {n: score.get(n, 1) for n in top20}
    return _weighted_sample(weights, 6)

def recommend_cycle(draws: list) -> list:
    """ML②: 출현 주기 예측 — 평균 주기 대비 현재 미출현 기간 비율이 1에 가까운 번호 우선"""
    appearances = defaultdict(list)
    for d in draws:
        for n in d['numbers']:
            appearances[n].append(d['draw_no'])

    latest_no = draws[-1]['draw_no']
    weights = {}
    for num in range(1, 46):
        app = appearances.get(num, [])
        if len(app) < 2:
            weights[num] = 1.0
            continue
        gaps = [app[i + 1] - app[i] for i in range(len(app) - 1)]
        avg_gap = sum(gaps) / len(gaps)
        current_gap = latest_no - app[-1]
        ratio = current_gap / avg_gap if avg_gap > 0 else 1.0
        # ratio ≈ 1.0~1.5 구간에서 가중치 최대 (정규 분포 형태)
        weights[num] = max(0.1, 1.5 - abs(ratio - 1.2))
    return _weighted_sample(weights, 6)

def recommend_random() -> list:
    """완전 랜덤"""
    return sorted(random.sample(range(1, 46), 6))

# ── 추천 묶음 ─────────────────────────────────────────────────────

def get_recommendations(draws: list) -> list:
    """5게임 추천 반환."""
    return [
        {
            'label': '통계①',
            'name': '핫번호 조합',
            'color': '#f97316',
            'numbers': recommend_hot(draws),
            'desc': '최근 1년(52회) 출현 빈도 상위 번호 가중 샘플링',
        },
        {
            'label': '통계②',
            'name': '소외번호 조합',
            'color': '#3b82f6',
            'numbers': recommend_cold(draws),
            'desc': '오랫동안 나오지 않은 번호에 높은 가중치를 부여해 샘플링',
        },
        {
            'label': 'ML①',
            'name': '공출현 패턴',
            'color': '#a855f7',
            'numbers': recommend_cooccurrence(draws),
            'desc': f'전체 {len(draws)}회 공출현 행렬 분석 — 함께 자주 나온 번호 조합',
        },
        {
            'label': 'ML②',
            'name': '주기 예측',
            'color': '#10b981',
            'numbers': recommend_cycle(draws),
            'desc': '번호별 평균 출현 주기 계산 — 출현 차례가 된 번호 우선',
        },
        {
            'label': '랜덤',
            'name': '완전 랜덤',
            'color': '#6b7280',
            'numbers': recommend_random(),
            'desc': '1~45 중 6개 완전 무작위 추출 (로또와 동일 확률)',
        },
    ]

# ── 통계 ──────────────────────────────────────────────────────────

def get_stats(draws: list) -> dict:
    """번호별 출현 통계 + 요약."""
    total = len(draws)
    counter = Counter()
    last_seen = {}
    for d in draws:
        for n in d['numbers']:
            counter[n] += 1
            last_seen[n] = d['draw_no']

    latest_no = draws[-1]['draw_no'] if draws else 0
    numbers = [
        {
            'num': n,
            'count': counter.get(n, 0),
            'pct': round(counter.get(n, 0) / total * 100, 1) if total else 0,
            'last': last_seen.get(n, 0),
            'gap': latest_no - last_seen.get(n, 0),
        }
        for n in range(1, 46)
    ]
    top5 = sorted(numbers, key=lambda x: x['count'], reverse=True)[:5]
    cold5 = sorted(numbers, key=lambda x: x['gap'], reverse=True)[:5]
    return {
        'total_draws': total,
        'latest_draw': latest_no,
        'latest_date': draws[-1]['date'] if draws else '',
        'numbers': numbers,
        'top5': top5,
        'cold5': cold5,
    }
