"""
analyze_algo.py
알고리즘별 실제 수익률 분석 리포트

사용법:
    python analyze_algo.py              # 전체 분석 (price_snapshots 현재가 사용)
    python analyze_algo.py --fetch      # KIS API로 현재가 직접 조회 후 분석
"""

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DB_FILE = Path(__file__).parent / "stocks.db"


def _conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def load_data(fetch_live: bool = False) -> list[dict]:
    """scan_results × price_snapshots JOIN → 분석용 레코드 리스트"""
    with _conn() as conn:
        # 종목별 최초 감지 기록만 (중복 제거: 같은 ticker의 가장 빠른 감지)
        rows = conn.execute("""
            SELECT
                sr.ticker,
                sr.name,
                sr.pattern,
                sr.conf,
                sr.current_price  AS first_price,
                sr.scanned_at     AS detected_at,
                ps.price          AS current_price,
                ps.fetched_at
            FROM scan_results sr
            JOIN (
                SELECT ticker, MIN(scanned_at) AS min_at
                FROM scan_results
                GROUP BY ticker
            ) first ON sr.ticker = first.ticker AND sr.scanned_at = first.min_at
            LEFT JOIN price_snapshots ps ON sr.ticker = ps.ticker
            ORDER BY sr.pattern, sr.ticker
        """).fetchall()

    records = [dict(r) for r in rows]

    if fetch_live:
        try:
            from data_fetcher import get_current_price
            print("📡 현재가 실시간 조회 중...")
            for r in records:
                try:
                    r["current_price"] = get_current_price(r["ticker"])
                    r["fetched_at"] = "live"
                except Exception:
                    pass
        except ImportError:
            print("⚠️  data_fetcher 임포트 실패 — DB 저장 현재가 사용")

    return records


def calc_return(first_price, current_price):
    if not first_price or not current_price or first_price <= 0:
        return None
    return (current_price - first_price) / first_price * 100


def fmt_pct(v, width=7):
    if v is None:
        return "   N/A "
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%".rjust(width)


def fmt_bar(v, width=20):
    """수익률을 ASCII 바 차트로"""
    if v is None:
        return " " * width
    ratio = max(-1.0, min(1.0, v / 30))   # ±30% 기준
    mid = width // 2
    if v >= 0:
        filled = int(ratio * mid)
        return " " * mid + "█" * filled + " " * (width - mid - filled)
    else:
        filled = int(-ratio * mid)
        return " " * (mid - filled) + "█" * filled + " " * (mid + (width - mid - filled) - filled - filled)


def analyze(records: list[dict]) -> None:
    # 현재가 있는 것만 수익률 계산
    valid = [r for r in records if r.get("current_price")]
    no_price = [r for r in records if not r.get("current_price")]

    print("\n" + "=" * 70)
    print("  📊 알고리즘별 실제 수익률 분석 리포트")
    print("=" * 70)

    if not valid:
        print("\n⚠️  현재가 데이터 없음. --fetch 옵션을 사용하거나 대시보드에서 '현재가 불러오기'를 먼저 실행하세요.")
        return

    if no_price:
        tickers = ", ".join(r["ticker"] for r in no_price)
        print(f"\n⚠️  현재가 없어 제외된 종목 ({len(no_price)}개): {tickers}")

    # 수익률 계산
    for r in valid:
        r["ret"] = calc_return(r["first_price"], r["current_price"])

    # ── 1. 알고리즘별 집계 ─────────────────────────────────────────────
    by_algo: dict[str, list] = defaultdict(list)
    for r in valid:
        if r["ret"] is not None:
            by_algo[r["pattern"]].append(r)

    print("\n┌─────────────────────────────────────────────────────────────────┐")
    print("│  알고리즘별 성과 요약                                           │")
    print("├──────────────┬──────┬────────┬────────┬────────┬────────────────┤")
    print("│ 알고리즘     │ 종목 │ 평균수익│  승률  │ 최대↑  │ 최대↓          │")
    print("├──────────────┼──────┼────────┼────────┼────────┼────────────────┤")

    algo_order = ["골삼이(상승초입)", "골삼이", "골든샘플", "레드삼각", "MA압축지지"]
    all_rets = []

    for algo in algo_order + [k for k in by_algo if k not in algo_order]:
        items = by_algo.get(algo, [])
        if not items:
            continue
        rets = [r["ret"] for r in items]
        all_rets.extend(rets)
        avg  = sum(rets) / len(rets)
        wins = sum(1 for r in rets if r > 0)
        win_rate = wins / len(rets) * 100
        best = max(rets)
        worst = min(rets)
        sign = "+" if avg >= 0 else ""
        print(f"│ {algo:<12} │ {len(items):4} │ {sign}{avg:6.2f}% │ {win_rate:5.1f}%  │ {best:+6.2f}% │ {worst:+6.2f}%        │")

    print("├──────────────┼──────┼────────┼────────┼────────┼────────────────┤")
    if all_rets:
        total_avg  = sum(all_rets) / len(all_rets)
        total_wins = sum(1 for r in all_rets if r > 0)
        sign = "+" if total_avg >= 0 else ""
        print(f"│ 전체         │ {len(all_rets):4} │ {sign}{total_avg:6.2f}% │ {total_wins/len(all_rets)*100:5.1f}%  │ {max(all_rets):+6.2f}% │ {min(all_rets):+6.2f}%        │")
    print("└──────────────┴──────┴────────┴────────┴────────┴────────────────┘")

    # ── 2. 알고리즘별 종목 상세 ───────────────────────────────────────
    for algo in algo_order + [k for k in by_algo if k not in algo_order]:
        items = by_algo.get(algo, [])
        if not items:
            continue

        items_sorted = sorted(items, key=lambda r: r["ret"] or 0, reverse=True)
        print(f"\n  {'─'*50}")
        print(f"  {algo}  ({len(items)}종목)")
        print(f"  {'─'*50}")
        print(f"  {'종목명':<12} {'코드':>7}  {'감지가':>9}  {'현재가':>9}  {'수익률':>8}  {'신뢰도':>5}  {'감지일'}")
        print(f"  {'─'*50}")

        for r in items_sorted:
            ret_str = fmt_pct(r["ret"], 8)
            flag = "✅" if r["ret"] and r["ret"] > 0 else ("❌" if r["ret"] and r["ret"] < 0 else "➖")
            detected = (r["detected_at"] or "")[:10]
            print(f"  {flag} {r['name']:<11} {r['ticker']:>7}  "
                  f"{r['first_price']:>9,}  {r['current_price']:>9,}  "
                  f"{ret_str}  {r['conf']:>4}%  {detected}")

    # ── 3. 신뢰도 vs 수익률 분석 ──────────────────────────────────────
    print(f"\n  {'─'*50}")
    print("  신뢰도 구간별 평균 수익률")
    print(f"  {'─'*50}")

    brackets = [(90, 100), (80, 90), (70, 80), (60, 70)]
    for lo, hi in brackets:
        items = [r for r in valid if r.get("ret") is not None and lo <= r["conf"] < hi]
        if not items:
            continue
        avg = sum(r["ret"] for r in items) / len(items)
        wins = sum(1 for r in items if r["ret"] > 0)
        bar = fmt_bar(avg)
        sign = "+" if avg >= 0 else ""
        print(f"  {lo}~{hi}%  [{bar}]  {sign}{avg:6.2f}%  ({len(items)}종목, 승률 {wins/len(items)*100:.0f}%)")

    # ── 4. 개선 시사점 ────────────────────────────────────────────────
    print(f"\n  {'─'*50}")
    print("  💡 개선 시사점")
    print(f"  {'─'*50}")

    suggestions = []
    for algo in algo_order + [k for k in by_algo if k not in algo_order]:
        items = by_algo.get(algo, [])
        if not items:
            continue
        rets = [r["ret"] for r in items if r["ret"] is not None]
        if not rets:
            continue
        avg  = sum(rets) / len(rets)
        wins = sum(1 for r in rets if r > 0)
        win_rate = wins / len(rets) * 100

        if win_rate < 40:
            suggestions.append(f"⚠️  [{algo}] 승률 {win_rate:.0f}% — 진입 조건 강화 또는 과열 필터 추가 검토")
        elif win_rate < 50:
            suggestions.append(f"🔶 [{algo}] 승률 {win_rate:.0f}% — 신뢰도 임계값 상향(현재 conf_base) 검토")

        if avg < -5:
            suggestions.append(f"⚠️  [{algo}] 평균 수익률 {avg:.1f}% — 패턴 조건 재검토 필요")
        elif avg < 0:
            suggestions.append(f"🔶 [{algo}] 평균 수익률 {avg:.1f}% — 손절 기준 또는 진입가 조건 검토")

        # 고신뢰도 저수익 패턴
        high_conf = [r for r in items if r["conf"] >= 85 and r["ret"] is not None and r["ret"] < 0]
        if len(high_conf) >= 2:
            suggestions.append(f"🔶 [{algo}] 고신뢰도(85%+)에서도 손실 {len(high_conf)}건 — 신뢰도 보너스 기준 재검토")

    if suggestions:
        for s in suggestions:
            print(f"  {s}")
    else:
        print("  ✅ 특별한 문제 없음 — 데이터가 더 쌓이면 재분석 권장")

    print(f"\n  현재가 기준: {valid[0].get('fetched_at', '?')[:16] if valid else '?'}")
    print("=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(description="알고리즘 성과 분석")
    parser.add_argument("--fetch", action="store_true", help="현재가 실시간 조회")
    args = parser.parse_args()

    if not DB_FILE.exists():
        print(f"❌ DB 파일 없음: {DB_FILE}")
        sys.exit(1)

    records = load_data(fetch_live=args.fetch)
    if not records:
        print("⚠️  스캔 결과 없음. 먼저 스캔을 실행하세요.")
        sys.exit(0)

    analyze(records)


if __name__ == "__main__":
    main()
