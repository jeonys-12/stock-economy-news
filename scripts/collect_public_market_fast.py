from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

import collect_public_market_fallback as fallback
import enrich_roe
import repair_stock_factors

KST = timezone(timedelta(hours=9))
DATA_FILE = Path("data/stock_data.json")
PREVIOUS_FILE = Path(os.getenv("PREVIOUS_STOCK_DATA", "/tmp/stock_data_previous.json"))
MAX_WORKERS = max(2, min(6, int(os.getenv("PUBLIC_MARKET_WORKERS", "6"))))


def apply_public_roe(row: dict[str, Any], valuation: dict[str, Any]) -> bool:
    """OpenDART ROE 계산이 불가능할 때 공개 페이지 ROE를 보조값으로 반영합니다."""
    roe_pct = fallback.number(valuation.get("roe_pct"))
    if roe_pct is None:
        return False

    financials = row.get("financials") if isinstance(row.get("financials"), dict) else {}
    financials.update({
        "roe_pct": round(roe_pct, 2),
        "roe_status": "fallback",
        "roe_source": valuation.get("source", "NAVER Finance 종목 메인 페이지"),
        "roe_formula": "공개 페이지 제공 ROE",
        "roe_reason": "OpenDART 계산값을 확보하지 못해 공개 ROE를 보조값으로 사용했습니다.",
    })

    quantitative = row.get("quantitative") if isinstance(row.get("quantitative"), dict) else {}
    components = quantitative.get("components") if isinstance(quantitative.get("components"), dict) else {}
    previous_points = fallback.number(quantitative.get("roe_score_applied")) or 0.0
    total_before = fallback.number(quantitative.get("score")) or 0.0
    points = enrich_roe.roe_score(roe_pct)
    components["financials"] = round((fallback.number(components.get("financials")) or 0.0) - previous_points + points, 1)
    total = round(total_before - previous_points + points, 1)
    quantitative.update({
        "score": total,
        "components": components,
        "roe_score_applied": points,
        "roe_pct": round(roe_pct, 2),
        "roe_source_type": "public_fallback",
        "signal": enrich_roe.signal(total),
    })
    row["financials"] = financials
    row["quantitative"] = quantitative
    return True


def collect_one(name: str, code: str, current_price: int | float | None, previous: dict[str, Any]) -> dict[str, Any]:
    session = requests.Session()
    result: dict[str, Any] = {"name": name, "valuation": None, "flow": None, "errors": []}

    try:
        result["valuation"] = fallback.collect_valuation(session, code, current_price)
        result["valuation_mode"] = "fresh"
    except Exception as exc:
        cached = fallback.cached_value(previous, name, "valuation")
        if cached and fallback.has_valuation(cached):
            result["valuation"] = cached
            result["valuation_mode"] = "cached"
        else:
            reason = str(exc)[:300]
            result["valuation"] = {"status": "unavailable", "reason": reason, "source": "NAVER Finance 공개 페이지"}
            result["valuation_mode"] = "failed"
            result["errors"].append({"stock": name, "field": "valuation", "reason": reason[:240]})

    try:
        result["flow"] = fallback.collect_investor_flow(session, code)
        result["flow_mode"] = "fresh"
    except Exception as exc:
        cached = fallback.cached_value(previous, name, "investor_flow")
        if cached and fallback.has_flow(cached):
            result["flow"] = cached
            result["flow_mode"] = "cached"
        else:
            reason = str(exc)[:300]
            result["flow"] = {"status": "unavailable", "reason": reason, "source": "NAVER Finance 공개 페이지"}
            result["flow_mode"] = "failed"
            result["errors"].append({"stock": name, "field": "investor_flow", "reason": reason[:240]})
    session.close()
    return result


def main() -> None:
    payload = fallback.load_json(DATA_FILE)
    previous = fallback.load_json(PREVIOUS_FILE)
    stocks = payload.get("stocks", {}) if isinstance(payload.get("stocks"), dict) else {}
    if not stocks:
        raise SystemExit("stock_data.json에 종목 데이터가 없습니다.")

    counters = {
        "valuation_fresh": 0,
        "valuation_cached": 0,
        "investor_flow_fresh": 0,
        "investor_flow_cached": 0,
        "roe_dart_calculated": 0,
        "roe_public_fallback": 0,
        "roe_unavailable": 0,
    }
    errors: list[dict[str, str]] = []
    print(f"Unified public fundamentals: stocks={len(stocks)}, workers={MAX_WORKERS}")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="fundamental") as executor:
        futures = {
            executor.submit(
                collect_one,
                name,
                str(row.get("code", "")).strip(),
                (row.get("market", {}) if isinstance(row.get("market"), dict) else {}).get("current_price"),
                previous,
            ): name
            for name, row in stocks.items()
            if isinstance(row, dict) and len(str(row.get("code", "")).strip()) == 6
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                errors.append({"stock": name, "field": "public_market", "reason": str(exc)[:240]})
                continue

            row = stocks[name]
            market = row.setdefault("market", {})
            market["valuation"] = result["valuation"]
            market["investor_flow"] = result["flow"]
            if result.get("valuation_mode") == "fresh": counters["valuation_fresh"] += 1
            if result.get("valuation_mode") == "cached": counters["valuation_cached"] += 1
            if result.get("flow_mode") == "fresh": counters["investor_flow_fresh"] += 1
            if result.get("flow_mode") == "cached": counters["investor_flow_cached"] += 1
            errors.extend(result.get("errors", []))

            row["quantitative"] = repair_stock_factors.score(
                row.get("financials", {}), market, row.get("consensus", {})
            )
            dart_ok, dart_reason = enrich_roe.enrich_stock(row)
            if dart_ok:
                counters["roe_dart_calculated"] += 1
            elif apply_public_roe(row, result.get("valuation") or {}):
                counters["roe_public_fallback"] += 1
            else:
                counters["roe_unavailable"] += 1
                if dart_reason:
                    errors.append({"stock": name, "field": "roe", "reason": dart_reason[:240]})

    status_ok = any(counters[key] for key in ("valuation_fresh", "valuation_cached", "investor_flow_fresh", "investor_flow_cached"))
    payload.setdefault("source_status", {})["public_market_fallback"] = {
        "status": "ok" if status_ok else "failed",
        "role": "종목 메인 페이지 1회 요청으로 PER·PBR·EPS·BPS·ROE를 수집하고 수급을 별도 보완",
        "source": "NAVER Finance 공개 페이지 + OpenDART 계산",
        **counters,
        "requested_stocks": len(stocks),
        "parallel_workers": MAX_WORKERS,
        "errors": errors[:20],
        "updated_at": datetime.now(KST).isoformat(),
    }
    payload.setdefault("source_status", {})["roe_analysis"] = {
        "status": "ok" if counters["roe_dart_calculated"] or counters["roe_public_fallback"] else "failed",
        "calculated_stocks": counters["roe_dart_calculated"],
        "fallback_stocks": counters["roe_public_fallback"],
        "unavailable_stocks": counters["roe_unavailable"],
        "requested_stocks": len(stocks),
        "method": "OpenDART 연환산 ROE 우선, 공개 종목 페이지 ROE 보조",
        "scoring": "ROE 20% 이상 +6, 12% 이상 +4, 8% 이상 +2, 음수 -6",
        "updated_at": datetime.now(KST).isoformat(),
    }
    methodology = payload.setdefault("methodology", {})
    methodology["fundamental_metric_policy"] = (
        "PER·PBR은 공개 종목 메인 페이지에서 직접 수집하고 누락 시 현재가/EPS·BPS로 파생 계산합니다. "
        "ROE는 OpenDART 당기순이익·자본총계 계산값을 우선하며 실패 시 공개 ROE를 보조값으로 사용합니다."
    )
    methodology["roe_policy"] = "OpenDART 계산 우선, 공개 ROE 보조, 별도 중복 실행 단계 없음"
    payload["updated_at"] = datetime.now(KST).isoformat()
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Unified fundamentals complete: {counters}; errors={len(errors)}")


if __name__ == "__main__":
    main()
