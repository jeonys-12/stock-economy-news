from __future__ import annotations

import json
from pathlib import Path

import generate_ai_briefing

STOCK_DATA_FILE = Path("data/stock_data.json")


def main() -> None:
    if STOCK_DATA_FILE.exists():
        payload = json.loads(STOCK_DATA_FILE.read_text(encoding="utf-8"))
        stocks = payload.get("stocks", {})
        if isinstance(stocks, dict) and stocks:
            generate_ai_briefing.WATCHLIST = list(stocks.keys())
            # 확대된 종목군에서 직접 관련 뉴스가 포함될 가능성을 높입니다.
            generate_ai_briefing.MAX_INPUT_ITEMS = 60
            print(f"Expanded AI watchlist loaded: {len(stocks)} stocks")
    generate_ai_briefing.main()


if __name__ == "__main__":
    main()
