"""
watchlist.py
패턴 감지 종목 영구 저장 + 수동 추가/제거
"""

import json
import os
from datetime import datetime

DIR = os.path.dirname(os.path.abspath(__file__))
WL_FILE = os.path.join(DIR, "watchlist.json")


def _load() -> dict:
    if os.path.exists(WL_FILE):
        with open(WL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"stocks": [], "last_scan": None}


def _save(data: dict) -> None:
    with open(WL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def update_from_scan(results: list[dict]) -> None:
    """스캔 결과로 워치리스트 갱신 (중복 제거, 패턴 업데이트)"""
    data = _load()
    existing = {s["ticker"]: i for i, s in enumerate(data["stocks"])}

    for r in results:
        ticker = r["ticker"]
        entry = {
            "ticker": ticker,
            "name": r["name"],
            "pattern": r["pattern"],
            "added_date": datetime.now().strftime("%Y-%m-%d"),
        }
        if ticker in existing:
            # 패턴 업데이트
            data["stocks"][existing[ticker]]["pattern"] = r["pattern"]
        else:
            data["stocks"].append(entry)

    data["last_scan"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    _save(data)


def add_stock(ticker: str, name: str, pattern: str = "수동추가") -> bool:
    """수동으로 종목 추가 (이미 있으면 False)"""
    data = _load()
    if any(s["ticker"] == ticker for s in data["stocks"]):
        return False
    data["stocks"].append({
        "ticker": ticker,
        "name": name,
        "pattern": pattern,
        "added_date": datetime.now().strftime("%Y-%m-%d"),
    })
    _save(data)
    return True


def remove_stock(ticker: str) -> bool:
    """종목 제거 (없으면 False)"""
    data = _load()
    before = len(data["stocks"])
    data["stocks"] = [s for s in data["stocks"] if s["ticker"] != ticker]
    if len(data["stocks"]) == before:
        return False
    _save(data)
    return True


def get_all() -> dict:
    return _load()


def get_tickers() -> list[str]:
    return [s["ticker"] for s in _load()["stocks"]]
