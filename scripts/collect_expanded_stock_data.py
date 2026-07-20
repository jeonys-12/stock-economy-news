from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import collect_stock_data
from stock_universe import build_stock_universe

KST = timezone(timedelta(hours=9))
OUTPUT = Path("data/stock_data.json")


def main() -> None:
    universe, universe_status = build_stock_universe()
    if not universe:
        raise SystemExit("확장 모니터링 종목을 구성하지 못했습니다.")

    collect_stock_data.STOCKS = universe
    print(
        "Expanded universe loaded: "
        f"total={len(universe)}, "
        f"top50={universe_status.get('top_kospi_loaded', 0)}, "
        f"hanwha={universe_status.get('hanwha_group_count', 0)}, "
        f"lg={universe_status.get('lg_group_count', 0)}"
    )
    collect_stock_data.main()

    if not OUTPUT.exists():
        return
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    payload.setdefault("source_status", {})["stock_universe"] = universe_status
    payload["universe"] = {
        "policy": universe_status.get("policy"),
        "updated_at": datetime.now(KST).isoformat(),
        "stock_count": len(universe),
        "stocks": [
            {
                "name": name,
                "code": meta.get("code"),
                "sector": meta.get("sector"),
                "business_sector": meta.get("business_sector"),
                "group": meta.get("group"),
                "tags": meta.get("universe_tags", []),
                "market_cap_rank": meta.get("market_cap_rank"),
            }
            for name, meta in universe.items()
        ],
    }

    for name, meta in universe.items():
        row = payload.get("stocks", {}).get(name)
        if not isinstance(row, dict):
            continue
        row["universe_tags"] = meta.get("universe_tags", [])
        row["market_cap_rank"] = meta.get("market_cap_rank")
        row["group"] = meta.get("group")
        if meta.get("business_sector"):
            row["business_sector"] = meta["business_sector"]

    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Expanded universe metadata saved for {len(universe)} stocks")


if __name__ == "__main__":
    main()
