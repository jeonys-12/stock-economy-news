from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

KST = timezone(timedelta(hours=9))
INPUT = Path("data/stock-input.json")
OUTPUT = Path("data/stock-analysis.json")


def safe_cagr(first: float, last: float, periods: int) -> float | None:
    if periods <= 0 or first <= 0 or last <= 0:
        return None
    return ((last / first) ** (1 / periods) - 1) * 100


def trend(values: list[float]) -> str:
    if len(values) < 2:
        return "데이터 부족"
    changes = [b - a for a, b in zip(values, values[1:])]
    positive = sum(change > 0 for change in changes)
    negative = sum(change < 0 for change in changes)
    if positive >= len(changes) * 0.75:
        return "개선"
    if negative >= len(changes) * 0.75:
        return "악화"
    return "혼조"


def clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def score_stock(stock: dict) -> dict:
    years = sorted(stock.get("years", []), key=lambda item: item["year"])[-5:]
    if len(years) < 3:
        raise ValueError(f"{stock.get('ticker')}: 최소 3개년 데이터가 필요합니다.")

    eps = [float(item["eps"]) for item in years]
    ocf = [float(item["operating_cash_flow"]) for item in years]
    roe = [float(item["roe"]) for item in years]
    debt = [float(item["debt_ratio"]) for item in years]
    coverage = [float(item["interest_coverage"]) for item in years]

    eps_cagr = safe_cagr(eps[0], eps[-1], len(eps) - 1)
    ocf_cagr = safe_cagr(ocf[0], ocf[-1], len(ocf) - 1)
    avg_roe = mean(roe)
    latest_roe = roe[-1]
    latest_debt = debt[-1]
    latest_coverage = coverage[-1]

    price = float(stock.get("price") or 0)
    forward_eps = float(stock.get("forward_eps") or 0)
    peer_forward_per = float(stock.get("peer_forward_per") or 0)
    forward_per = price / forward_eps if price > 0 and forward_eps > 0 else None
    per_discount = (
        (peer_forward_per - forward_per) / peer_forward_per * 100
        if forward_per and peer_forward_per > 0 else None
    )

    eps_score = 0 if eps_cagr is None else clamp(50 + eps_cagr * 2)
    if trend(eps) == "개선":
        eps_score += 10
    elif trend(eps) == "악화":
        eps_score -= 15

    cash_score = 0 if ocf_cagr is None else clamp(50 + ocf_cagr * 1.8)
    if all(value > 0 for value in ocf):
        cash_score += 10
    if trend(ocf) == "악화":
        cash_score -= 15

    roe_score = clamp(latest_roe * 4)
    if latest_roe > avg_roe:
        roe_score += 10
    if trend(roe) == "악화":
        roe_score -= 15

    debt_score = clamp(100 - max(0, latest_debt - 50) * 0.65)
    coverage_score = clamp(latest_coverage * 12.5)
    safety_score = debt_score * 0.45 + coverage_score * 0.55
    if trend(debt) == "악화":
        safety_score -= 10
    if trend(coverage) == "악화":
        safety_score -= 10

    valuation_score = 40
    if forward_per:
        valuation_score = clamp(100 - forward_per * 3)
    if per_discount is not None:
        valuation_score = clamp(valuation_score + per_discount * 0.8)

    component_scores = {
        "eps_growth": round(clamp(eps_score), 1),
        "cash_flow": round(clamp(cash_score), 1),
        "roe": round(clamp(roe_score), 1),
        "financial_safety": round(clamp(safety_score), 1),
        "valuation": round(clamp(valuation_score), 1),
    }
    total = (
        component_scores["eps_growth"] * 0.25
        + component_scores["cash_flow"] * 0.20
        + component_scores["roe"] * 0.20
        + component_scores["financial_safety"] * 0.20
        + component_scores["valuation"] * 0.15
    )

    warnings: list[str] = []
    strengths: list[str] = []
    if eps_cagr is not None and eps_cagr >= 10:
        strengths.append("EPS가 중장기적으로 성장")
    if ocf_cagr is not None and ocf_cagr >= 8 and all(value > 0 for value in ocf):
        strengths.append("영업현금흐름이 양수이며 증가")
    if latest_roe >= 12 and trend(roe) != "악화":
        strengths.append("ROE가 양호하거나 개선")
    if latest_debt <= 100 and latest_coverage >= 5:
        strengths.append("부채와 이자상환능력이 안정적")
    if per_discount is not None and per_discount >= 10:
        strengths.append("추정 PER이 경쟁사 평균보다 낮음")

    if eps_cagr is not None and eps_cagr < 0:
        warnings.append("EPS가 감소 추세")
    if trend(ocf) == "악화" or any(value <= 0 for value in ocf[-2:]):
        warnings.append("영업현금흐름 악화 여부 확인 필요")
    if latest_roe < 8 or trend(roe) == "악화":
        warnings.append("ROE 수준 또는 방향이 약함")
    if latest_debt > 150:
        warnings.append("부채비율이 높음")
    if latest_coverage < 2:
        warnings.append("이자보상여력이 낮음")
    if per_discount is not None and per_discount < -20:
        warnings.append("추정 PER이 경쟁사보다 높음")

    grade = "A" if total >= 80 else "B" if total >= 65 else "C" if total >= 50 else "D"
    return {
        "ticker": stock["ticker"],
        "name": stock["name"],
        "market": stock.get("market", ""),
        "sector": stock.get("sector", ""),
        "score": round(total, 1),
        "grade": grade,
        "metrics": {
            "eps_cagr": None if eps_cagr is None else round(eps_cagr, 1),
            "operating_cash_flow_cagr": None if ocf_cagr is None else round(ocf_cagr, 1),
            "latest_roe": round(latest_roe, 1),
            "average_roe": round(avg_roe, 1),
            "latest_debt_ratio": round(latest_debt, 1),
            "latest_interest_coverage": round(latest_coverage, 1),
            "forward_per": None if forward_per is None else round(forward_per, 2),
            "peer_forward_per": peer_forward_per or None,
            "peer_per_discount": None if per_discount is None else round(per_discount, 1),
        },
        "trends": {
            "eps": trend(eps),
            "operating_cash_flow": trend(ocf),
            "roe": trend(roe),
            "debt_ratio": trend([-value for value in debt]),
            "interest_coverage": trend(coverage),
        },
        "component_scores": component_scores,
        "strengths": strengths,
        "warnings": warnings,
        "history": years,
    }


def main() -> None:
    payload = json.loads(INPUT.read_text(encoding="utf-8"))
    results, errors = [], []
    for stock in payload.get("stocks", []):
        try:
            results.append(score_stock(stock))
        except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
            errors.append(str(exc))
    results.sort(key=lambda item: item["score"], reverse=True)
    output = {
        "updated_at": datetime.now(KST).isoformat(),
        "data_status": payload.get("data_status", "unknown"),
        "notice": payload.get("notice", ""),
        "methodology": {
            "years": "최근 3~5개년",
            "weights": {
                "EPS 성장률": 25,
                "영업현금흐름": 20,
                "ROE": 20,
                "재무안전성": 20,
                "추정 PER·경쟁사 비교": 15
            },
            "warning": "점수는 정량 선별 도구이며 매수·매도 판단을 대신하지 않습니다."
        },
        "count": len(results),
        "errors": errors,
        "stocks": results,
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(results)} stock analyses to {OUTPUT}")


if __name__ == "__main__":
    main()
