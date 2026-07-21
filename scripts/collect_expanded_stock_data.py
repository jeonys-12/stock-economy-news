from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from stock_universe import build_stock_universe

KST = timezone(timedelta(hours=9))
OUTPUT = Path("data/stock_data.json")


def load_previous() -> dict[str, Any]:
    if not OUTPUT.exists():
        return {}
    try:
        payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> None:
    universe, universe_status = build_stock_universe()
    if not universe:
        raise SystemExit("추천 모니터링 종목을 구성하지 못했습니다.")

    previous = load_previous()
    previous_stocks = previous.get("stocks", {}) if isinstance(previous.get("stocks"), dict) else {}
    stocks: dict[str, dict[str, Any]] = {}

    for name, meta in universe.items():
        old = previous_stocks.get(name, {}) if isinstance(previous_stocks.get(name), dict) else {}
        row: dict[str, Any] = {
            "name": name,
            "code": str(meta.get("code", "")),
            "sector": meta.get("sector", ""),
            "business_sector": meta.get("business_sector") or meta.get("sector", ""),
            "universe_tags": meta.get("universe_tags", []),
            "watchlist_order": meta.get("watchlist_order"),
            "market": old.get("market", {}) if isinstance(old.get("market"), dict) else {},
        }
        if isinstance(old.get("quality_value_analysis"), dict):
            row["quality_value_analysis"] = old["quality_value_analysis"]
        stocks[name] = row

    universe_status.update({"collection_mode": "naver_only", "external_calls": 0})
    payload = {
        "updated_at": datetime.now(KST).isoformat(),
        "source_status": {
            "stock_universe": universe_status,
            "naver_finance": {
                "status": "pending",
                "role": "Financial Summary·현재가·밸류에이션·투자자 수급 통합 수집",
            },
        },
        "methodology": {
            "description": "네이버페이 증권 Financial Summary를 최우선으로 사용하는 경량 종목 분석",
            "primary_source": "NAVER Finance Financial Summary",
            "excluded_sources": ["OpenDART", "KRX OPEN API", "FnGuide"],
            "minimum_dimensions": 3,
        },
        "universe": {
            "policy": universe_status.get("policy"),
            "mode": universe_status.get("mode"),
            "stock_count": len(universe),
            "stocks": [
                {
                    "name": name,
                    "code": meta.get("code"),
                    "sector": meta.get("sector"),
                    "tags": meta.get("universe_tags", []),
                    "watchlist_order": meta.get("watchlist_order"),
                }
                for name, meta in universe.items()
            ],
        },
        "stocks": stocks,
        "errors": [],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Prepared NAVER-only stock universe: {len(stocks)} stocks")


if __name__ == "__main__":
    main()
