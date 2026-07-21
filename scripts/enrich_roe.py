from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
DATA_FILE = Path("data/stock_data.json")

ANNUALIZATION_FACTORS = {
    "11013": 4.0,       # 1분기
    "11012": 2.0,       # 반기
    "11014": 4.0 / 3.0, # 3분기
    "11011": 1.0,       # 사업보고서
}


def number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def roe_score(roe_pct: float) -> float:
    if roe_pct >= 20:
        return 6.0
    if roe_pct >= 12:
        return 4.0
    if roe_pct >= 8:
        return 2.0
    if roe_pct < 0:
        return -6.0
    return 0.0


def signal(score: float) -> str:
    return "긍정" if score >= 15 else "부정" if score <= -15 else "중립"


def enrich_stock(row: dict[str, Any]) -> tuple[bool, str | None]:
    financials = row.get("financials") if isinstance(row.get("financials"), dict) else {}
    if financials.get("status") != "ok":
        return False, "OpenDART 재무자료가 정상 상태가 아닙니다."

    net_income = number(financials.get("net_income"))
    equity = number(financials.get("equity"))
    if net_income is None or equity in (None, 0):
        financials["roe_status"] = "unavailable"
        financials["roe_reason"] = "당기순이익 또는 자본총계가 없어 ROE를 계산하지 못했습니다."
        return False, financials["roe_reason"]

    report_code = str(financials.get("report_code") or "")
    annualization_factor = ANNUALIZATION_FACTORS.get(report_code, 1.0)
    roe_pct = round(net_income * annualization_factor / equity * 100, 2)
    points = roe_score(roe_pct)

    financials.update({
        "roe_pct": roe_pct,
        "roe_status": "ok",
        "roe_source": "OpenDART 당기순이익·자본총계 계산",
        "roe_formula": "연환산 당기순이익 / 자본총계 × 100",
        "roe_annualization_factor": round(annualization_factor, 4),
    })

    quantitative = row.get("quantitative") if isinstance(row.get("quantitative"), dict) else {}
    components = quantitative.get("components") if isinstance(quantitative.get("components"), dict) else {}

    previous_roe_points = number(quantitative.get("roe_score_applied")) or 0.0
    previous_total = number(quantitative.get("score")) or 0.0
    components["financials"] = round((number(components.get("financials")) or 0.0) - previous_roe_points + points, 1)
    total = round(previous_total - previous_roe_points + points, 1)

    quantitative.update({
        "score": total,
        "components": components,
        "roe_score_applied": points,
        "roe_pct": roe_pct,
        "signal": signal(total),
    })
    row["financials"] = financials
    row["quantitative"] = quantitative
    return True, None


def main() -> None:
    if not DATA_FILE.exists():
        raise SystemExit("data/stock_data.json not found")

    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    stocks = payload.get("stocks", {})
    if not isinstance(stocks, dict):
        raise SystemExit("stocks must be an object")

    success = 0
    errors: list[dict[str, str]] = []
    for name, row in stocks.items():
        if not isinstance(row, dict):
            continue
        ok, reason = enrich_stock(row)
        if ok:
            success += 1
        elif reason:
            errors.append({"stock": name, "reason": reason})

    payload.setdefault("source_status", {})["roe_analysis"] = {
        "status": "ok" if success else "failed",
        "calculated_stocks": success,
        "requested_stocks": len(stocks),
        "method": "OpenDART 당기순이익과 자본총계로 연환산 ROE 계산",
        "scoring": "ROE 20% 이상 +6, 12% 이상 +4, 8% 이상 +2, 음수 -6",
        "errors": errors[:20],
        "updated_at": datetime.now(KST).isoformat(),
    }
    methodology = payload.setdefault("methodology", {})
    methodology["roe_policy"] = (
        "최근 OpenDART 보고서의 당기순이익을 보고기간에 따라 연환산하고 자본총계로 나눈 ROE를 재무점수에 반영합니다."
    )
    payload["updated_at"] = datetime.now(KST).isoformat()
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"ROE analysis completed: {success}/{len(stocks)} stocks")


if __name__ == "__main__":
    main()
