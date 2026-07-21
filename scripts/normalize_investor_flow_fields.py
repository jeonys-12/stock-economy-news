from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA_FILE = Path("data/stock_data.json")
RENAMES = {
    "foreign_net_buy_10d_krw": "foreign_net_buy_10d_shares",
    "institution_net_buy_10d_krw": "institution_net_buy_10d_shares",
    "individual_net_buy_10d_krw": "individual_net_buy_10d_shares",
}


def normalize_flow(flow: dict[str, Any]) -> bool:
    changed = False
    for old_key, new_key in RENAMES.items():
        if new_key not in flow and old_key in flow:
            flow[new_key] = flow[old_key]
            changed = True
        if old_key in flow:
            del flow[old_key]
            changed = True
    if any(key in flow for key in RENAMES.values()):
        if flow.get("unit") != "shares":
            flow["unit"] = "shares"
            changed = True
        if flow.get("period_description") != "최근 10거래일 순매매량 합계":
            flow["period_description"] = "최근 10거래일 순매매량 합계"
            changed = True
    return changed


def main() -> None:
    if not DATA_FILE.exists():
        raise SystemExit("data/stock_data.json not found")
    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    stocks = payload.get("stocks", {}) if isinstance(payload, dict) else {}
    changed_stocks = 0
    for row in stocks.values() if isinstance(stocks, dict) else []:
        if not isinstance(row, dict):
            continue
        market = row.get("market", {})
        flow = market.get("investor_flow", {}) if isinstance(market, dict) else {}
        if isinstance(flow, dict) and normalize_flow(flow):
            changed_stocks += 1
    payload.setdefault("methodology", {})["investor_flow_policy"] = (
        "외국인·기관 수급은 NAVER Finance 투자자별 매매 페이지의 최근 10거래일 순매매량 합계이며 단위는 주입니다."
    )
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Investor flow fields normalized for {changed_stocks} stocks")


if __name__ == "__main__":
    main()
