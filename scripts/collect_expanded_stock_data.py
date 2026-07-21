from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import collect_stock_data
from stock_universe import build_stock_universe

KST = timezone(timedelta(hours=9))
OUTPUT = Path("data/stock_data.json")


def disabled_login_market_data(code: str) -> dict[str, Any]:
    """KRX 홈페이지 로그인 또는 pykrx 경로를 실행하지 않습니다.

    가격·시가총액 등 공식 시장 데이터는 후속 collect_krx_official.py 단계에서
    KRX_API_KEY 기반 Open API로만 보완합니다.
    """
    return {
        "status": "unavailable",
        "reason": "로그인 기반 KRX/pykrx 수집을 비활성화했습니다. KRX Open API 승인 후 공식 API 단계에서 보완합니다.",
        "valuation": {},
        "investor_flow": {},
        "source": "KRX Open API 대기",
    }


def main() -> None:
    universe, universe_status = build_stock_universe()
    if not universe:
        raise SystemExit("추천 모니터링 종목을 구성하지 못했습니다.")

    collect_stock_data.STOCKS = universe
    collect_stock_data.collect_market_data = disabled_login_market_data
    print(f"Fixed recommendation watchlist loaded: total={len(universe)}")
    print("KRX website login/pykrx collection disabled; Open API enrichment only")
    collect_stock_data.main()

    if not OUTPUT.exists():
        return

    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    payload.setdefault("source_status", {})["stock_universe"] = universe_status
    payload.setdefault("source_status", {})["krx_login_collection"] = {
        "status": "disabled",
        "reason": "KRX_ID·KRX_PW를 사용하는 로그인 기반 수집을 사용하지 않습니다.",
        "replacement": "KRX_API_KEY 기반 collect_krx_official.py",
        "updated_at": datetime.now(KST).isoformat(),
    }
    payload["universe"] = {
        "policy": universe_status.get("policy"),
        "mode": universe_status.get("mode"),
        "updated_at": datetime.now(KST).isoformat(),
        "stock_count": len(universe),
        "stocks": [
            {
                "name": name,
                "code": meta.get("code"),
                "sector": meta.get("sector"),
                "business_sector": meta.get("business_sector"),
                "tags": meta.get("universe_tags", []),
                "watchlist_order": meta.get("watchlist_order"),
            }
            for name, meta in universe.items()
        ],
    }

    for name, meta in universe.items():
        row = payload.get("stocks", {}).get(name)
        if not isinstance(row, dict):
            continue
        row["universe_tags"] = meta.get("universe_tags", [])
        row["watchlist_order"] = meta.get("watchlist_order")
        if meta.get("business_sector"):
            row["business_sector"] = meta["business_sector"]

    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Fixed recommendation watchlist metadata saved for {len(universe)} stocks")


if __name__ == "__main__":
    main()
