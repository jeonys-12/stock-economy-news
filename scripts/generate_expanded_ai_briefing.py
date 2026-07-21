from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import generate_ai_briefing

STOCK_DATA_FILE = Path("data/stock_data.json")
BUY_THRESHOLD = 8
SELL_THRESHOLD = -8
MIN_DIMENSIONS = 2
MAX_CANDIDATES = 5

COMPONENT_LABELS = {
    "financials": "재무",
    "consensus": "컨센서스",
    "valuation": "밸류에이션",
    "flow": "수급",
    "momentum": "모멘텀",
}


def numeric(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def direct_evidence(name: str, items: list[dict[str, Any]]) -> list[dict[str, str]]:
    keyword = name.lower()
    result: list[dict[str, str]] = []
    for item in items:
        text = f"{item.get('title', '')} {item.get('description', '')}".lower()
        if keyword not in text:
            continue
        result.append({
            "id": str(item.get("id", "")),
            "title": str(item.get("title", "")),
            "url": str(item.get("url", "")),
            "source": str(item.get("source", "")),
        })
        if len(result) >= 3:
            break
    return result


def component_summary(quantitative: dict[str, Any], positive: bool) -> str:
    components = quantitative.get("components", {}) if isinstance(quantitative.get("components"), dict) else {}
    ranked = sorted(
        ((key, numeric(value)) for key, value in components.items()),
        key=lambda pair: pair[1],
        reverse=positive,
    )
    selected = [(key, value) for key, value in ranked if (value > 0 if positive else value < 0)][:2]
    if not selected:
        return "정량점수와 확보된 데이터 축을 기준으로 선별"
    return ", ".join(f"{COMPONENT_LABELS.get(key, key)} {value:+g}점" for key, value in selected)


def candidate_payload(name: str, row: dict[str, Any], items: list[dict[str, Any]], positive: bool) -> dict[str, Any]:
    quantitative = row.get("quantitative", {}) if isinstance(row.get("quantitative"), dict) else {}
    market = row.get("market", {}) if isinstance(row.get("market"), dict) else {}
    evidence = direct_evidence(name, items)
    score = numeric(quantitative.get("score"))
    dimensions = int(numeric(quantitative.get("available_dimensions")))
    basis = "뉴스+정량" if evidence else "정량 중심"
    direction = "긍정" if positive else "부정"
    reason = (
        f"{basis} 후보입니다. 종합점수 {score:+g}점, 유효 데이터 {dimensions}개 축이며 "
        f"주요 {direction} 근거는 {component_summary(quantitative, positive)}입니다."
    )
    risk = (
        "직접 연결 뉴스가 부족하므로 최신 공시와 업종 이슈를 추가 확인해야 합니다."
        if not evidence
        else "뉴스와 정량 신호가 단기간에 바뀔 수 있으므로 최신 수급과 공시를 재확인해야 합니다."
    )
    valuation = market.get("valuation", {}) if isinstance(market.get("valuation"), dict) else {}
    return {
        "name": name,
        "code": str(row.get("code", "")),
        "sector": str(row.get("sector", "")),
        "reason": reason,
        "risk": risk,
        "evidence": evidence,
        "recommendation_basis": "news_and_quantitative" if evidence else "quantitative_only",
        "quantitative_score": quantitative.get("score"),
        "score_components": quantitative.get("components", {}),
        "data_dimensions": quantitative.get("available_dimensions", 0),
        "metrics": {
            "current_price": market.get("current_price"),
            "return_20d_pct": market.get("return_20d_pct"),
            "per": valuation.get("per"),
            "pbr": valuation.get("pbr"),
        },
    }


def add_flexible_candidates(
    normalized: dict[str, Any],
    items: list[dict[str, Any]],
    stock_data: dict[str, Any],
) -> dict[str, Any]:
    stocks = stock_data.get("stocks", {}) if isinstance(stock_data.get("stocks"), dict) else {}

    buy_pool: list[tuple[float, int, str, dict[str, Any]]] = []
    sell_pool: list[tuple[float, int, str, dict[str, Any]]] = []
    for name, row in stocks.items():
        if not isinstance(row, dict) or name not in generate_ai_briefing.WATCHLIST:
            continue
        quantitative = row.get("quantitative", {}) if isinstance(row.get("quantitative"), dict) else {}
        score = numeric(quantitative.get("score"))
        dimensions = int(numeric(quantitative.get("available_dimensions")))
        if dimensions < MIN_DIMENSIONS:
            continue
        if score >= BUY_THRESHOLD:
            buy_pool.append((score, dimensions, name, row))
        if score <= SELL_THRESHOLD:
            sell_pool.append((score, dimensions, name, row))

    buy_pool.sort(key=lambda value: (-value[0], -value[1], value[2]))
    sell_pool.sort(key=lambda value: (value[0], -value[1], value[2]))

    for period_name in ("daily", "weekly"):
        period = normalized.get(period_name, {}) if isinstance(normalized.get(period_name), dict) else {}
        existing_buy = period.get("buy_candidates", []) if isinstance(period.get("buy_candidates"), list) else []
        existing_sell = period.get("sell_candidates", []) if isinstance(period.get("sell_candidates"), list) else []
        buy_names = {str(item.get("name", "")) for item in existing_buy if isinstance(item, dict)}
        sell_names = {str(item.get("name", "")) for item in existing_sell if isinstance(item, dict)}

        for _, _, name, row in buy_pool:
            if len(existing_buy) >= MAX_CANDIDATES:
                break
            if name not in buy_names:
                existing_buy.append(candidate_payload(name, row, items, True))
                buy_names.add(name)

        for _, _, name, row in sell_pool:
            if len(existing_sell) >= MAX_CANDIDATES:
                break
            if name not in sell_names:
                existing_sell.append(candidate_payload(name, row, items, False))
                sell_names.add(name)

        period["buy_candidates"] = existing_buy[:MAX_CANDIDATES]
        period["sell_candidates"] = existing_sell[:MAX_CANDIDATES]
        normalized[period_name] = period

    methodology = normalized.setdefault("methodology", {})
    methodology.update({
        "buy_review_threshold": BUY_THRESHOLD,
        "sell_review_threshold": SELL_THRESHOLD,
        "minimum_dimensions": MIN_DIMENSIONS,
        "news_requirement": "직접 뉴스 우선, 직접 뉴스가 없어도 2개 이상 데이터 축과 점수 기준 충족 시 정량 중심 후보 허용",
        "candidate_limit": MAX_CANDIDATES,
    })
    return normalized


def main() -> None:
    if STOCK_DATA_FILE.exists():
        payload = json.loads(STOCK_DATA_FILE.read_text(encoding="utf-8"))
        stocks = payload.get("stocks", {})
        if isinstance(stocks, dict) and stocks:
            generate_ai_briefing.WATCHLIST = list(stocks.keys())
            generate_ai_briefing.MAX_INPUT_ITEMS = 80
            print(f"Flexible AI watchlist loaded: {len(stocks)} stocks")

    original_normalize = generate_ai_briefing.normalize_briefing

    def flexible_normalize(raw: dict[str, Any], items: list[dict[str, Any]], stock_data: dict[str, Any]) -> dict[str, Any]:
        normalized = original_normalize(raw, items, stock_data)
        return add_flexible_candidates(normalized, items, stock_data)

    generate_ai_briefing.normalize_briefing = flexible_normalize
    generate_ai_briefing.main()


if __name__ == "__main__":
    main()
