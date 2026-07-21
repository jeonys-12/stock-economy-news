from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

import collect_public_market_fallback as fallback
import repair_stock_factors

KST = timezone(timedelta(hours=9))
DATA_FILE = Path("data/stock_data.json")
PREVIOUS_FILE = Path(os.getenv("PREVIOUS_STOCK_DATA", "/tmp/stock_data_previous.json"))
MAX_WORKERS = max(2, min(6, int(os.getenv("PUBLIC_MARKET_WORKERS", "6"))))


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

    counters = {"valuation_fresh": 0, "valuation_cached": 0, "investor_flow_fresh": 0, "investor_flow_cached": 0}
    errors: list[dict[str, str]] = []
    print(f"Fast public market collection: stocks={len(stocks)}, workers={MAX_WORKERS}")

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

            financials = row.get("financials") if isinstance(row.get("financials"), dict) else {}
            for key in ("roe_pct", "roe_status", "roe_source", "roe_formula", "roe_reason", "roe_annualization_factor"):
                financials.pop(key, None)
            row["financials"] = financials
            row["quantitative"] = repair_stock_factors.score(financials, market, row.get("consensus", {}))

    status_ok = any(counters.values())
    source_status = payload.setdefault("source_status", {})
    source_status.pop("roe_analysis", None)
    source_status["public_market_fallback"] = {
        "status": "ok" if status_ok else "failed",
        "role": "PER·PBR·EPS·BPS 및 외국인·기관 수급 보완",
        "source": "NAVER Finance 공개 페이지",
        **counters,
        "requested_stocks": len(stocks),
        "parallel_workers": MAX_WORKERS,
        "errors": errors[:20],
        "updated_at": datetime.now(KST).isoformat(),
    }
    methodology = payload.setdefault("methodology", {})
    methodology.pop("roe_policy", None)
    methodology["fundamental_metric_policy"] = "PER·PBR은 공개 종목 메인 페이지에서 수집하고 누락 시 현재가/EPS·BPS로 파생 계산합니다."
    payload["updated_at"] = datetime.now(KST).isoformat()
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Fast public market collection complete: {counters}; errors={len(errors)}")


if __name__ == "__main__":
    main()
