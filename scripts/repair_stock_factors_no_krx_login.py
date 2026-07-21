from __future__ import annotations

from typing import Any

import repair_stock_factors


def keep_existing_market(existing: dict[str, Any], code: str) -> dict[str, Any]:
    """pykrx 및 KRX 홈페이지 로그인 경로를 호출하지 않고 기존 시장값을 유지합니다."""
    market = dict(existing) if isinstance(existing, dict) else {}
    market.setdefault("status", "unavailable")
    market.setdefault(
        "reason",
        "로그인 기반 KRX/pykrx 보정을 비활성화했습니다. KRX Open API 승인 후 공식 API 단계에서 보완합니다.",
    )
    market.setdefault("valuation", {})
    market.setdefault("investor_flow", {})
    market["login_based_collection_disabled"] = True
    return market


def main() -> None:
    repair_stock_factors.refresh_market = keep_existing_market
    print("KRX website login/pykrx repair disabled; running DART and cache repair only")
    repair_stock_factors.main()


if __name__ == "__main__":
    main()
