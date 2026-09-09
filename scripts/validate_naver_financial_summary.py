from __future__ import annotations

import json
from pathlib import Path

DATA_FILE = Path("data/stock_data.json")
REQUIRED_STOCKS = {
    "기아": {
        "code": "000270",
        "minimum_history_years": 3,
        "allowed_statuses": {"ok", "cached"},
        "metrics": (
            "eps_growth_cagr_pct",
            "operating_profit_growth_cagr_pct",
            "average_roe_pct",
            "latest_debt_ratio_pct",
            "forward_eps",
            "forward_per",
        ),
        "require_target_price": True,
    },
    "SK이터닉스": {
        "code": "475150",
        "minimum_history_years": 2,
        "allowed_statuses": {"ok", "partial", "cached"},
        "metrics": (
            "operating_profit_growth_cagr_pct",
            "average_roe_pct",
            "latest_debt_ratio_pct",
            "forward_eps",
            "forward_per",
        ),
        "require_target_price": False,
    },
}


def main() -> None:
    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    stocks = payload.get("stocks", {})
    errors: list[str] = []

    for name, rule in REQUIRED_STOCKS.items():
        row = stocks.get(name)
        if not isinstance(row, dict):
            errors.append(f"{name}: 종목 데이터 없음")
            continue
        if str(row.get("code", "")) != rule["code"]:
            errors.append(f"{name}: 종목코드 불일치")

        analysis = row.get("quality_value_analysis", {})
        if analysis.get("status") not in rule["allowed_statuses"]:
            errors.append(f"{name}: Financial Summary 상태={analysis.get('status')}")
            continue

        history = analysis.get("history", [])
        history_count = len(history) if isinstance(history, list) else 0
        minimum_history = int(rule["minimum_history_years"])
        if history_count < minimum_history:
            errors.append(f"{name}: 연간 실적 {history_count}개년, 최소 {minimum_history}개년 필요")
        elif name == "SK이터닉스":
            years = [int(item.get("year")) for item in history if isinstance(item, dict) and item.get("year")]
            if years and (min(years) < 2024 or max(years) > 2025):
                errors.append(f"{name}: 분기 또는 예상 열이 확정 연간 이력에 혼입됨={years}")

        metrics = analysis.get("metrics", {})
        for key in rule["metrics"]:
            if metrics.get(key) is None:
                errors.append(f"{name}: {key} 누락")

        if rule.get("require_target_price"):
            market = row.get("market", {})
            valuation = market.get("valuation", {}) if isinstance(market, dict) else {}
            if valuation.get("target_price") is None:
                errors.append(f"{name}: 목표주가 누락")

    status = payload.setdefault("source_status", {})
    status["naver_required_stock_validation"] = {
        "status": "failed" if errors else "ok",
        "checked_stocks": list(REQUIRED_STOCKS),
        "errors": errors,
    }
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if errors:
        raise SystemExit("NAVER Financial Summary validation failed: " + " | ".join(errors))
    print("NAVER Financial Summary validation passed: 기아 3개년 및 SK이터닉스 2개년 재무·예상지표")


if __name__ == "__main__":
    main()
