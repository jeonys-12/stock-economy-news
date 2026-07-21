from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

import collect_public_market_fallback as naver

KST = timezone(timedelta(hours=9))
DATA_FILE = Path("data/stock_data.json")
PREVIOUS_FILE = Path(os.getenv("PREVIOUS_STOCK_DATA", "/tmp/stock_data_previous.json"))
MAX_WORKERS = max(2, min(6, int(os.getenv("PUBLIC_MARKET_WORKERS", "6"))))


def collect_one(name: str, code: str, previous: dict[str, Any]) -> dict[str, Any]:
    session = requests.Session()
    result: dict[str, Any] = {"name": name, "errors": []}
    try:
        result["valuation"] = naver.collect_valuation(session, code)
        result["valuation_mode"] = "fresh"
    except Exception as exc:
        cached = naver.cached_value(previous, name, "valuation")
        result["valuation"] = cached if cached and naver.has_valuation(cached) else {
            "status": "unavailable", "reason": str(exc)[:300], "source": "NAVER Finance"
        }
        result["valuation_mode"] = "cached" if cached else "failed"
        if not cached:
            result["errors"].append({"stock": name, "field": "valuation", "reason": str(exc)[:240]})
    try:
        result["flow"] = naver.collect_investor_flow(session, code)
        result["flow_mode"] = "fresh"
    except Exception as exc:
        cached = naver.cached_value(previous, name, "investor_flow")
        result["flow"] = cached if cached and naver.has_flow(cached) else {
            "status": "unavailable", "reason": str(exc)[:300], "source": "NAVER Finance"
        }
        result["flow_mode"] = "cached" if cached else "failed"
        if not cached:
            result["errors"].append({"stock": name, "field": "investor_flow", "reason": str(exc)[:240]})
    session.close()
    return result


def main() -> None:
    payload = naver.load_json(DATA_FILE)
    previous = naver.load_json(PREVIOUS_FILE)
    stocks = payload.get("stocks", {}) if isinstance(payload.get("stocks"), dict) else {}
    if not stocks:
        raise SystemExit("stock_data.json에 종목 데이터가 없습니다.")

    counters = {"valuation_fresh": 0, "valuation_cached": 0, "investor_flow_fresh": 0, "investor_flow_cached": 0}
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="naver") as executor:
        futures = {
            executor.submit(collect_one, name, str(row.get("code", "")).strip(), previous): name
            for name, row in stocks.items()
            if isinstance(row, dict) and len(str(row.get("code", "")).strip()) == 6
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                errors.append({"stock": name, "field": "naver_market", "reason": str(exc)[:240]})
                continue
            row = stocks[name]
            market = row.setdefault("market", {})
            market["valuation"] = result["valuation"]
            market["investor_flow"] = result["flow"]
            valuation = result["valuation"]
            if isinstance(valuation, dict) and valuation.get("current_price") is not None:
                market["current_price"] = valuation["current_price"]
                market["status"] = valuation.get("status", "ok")
                market["as_of"] = valuation.get("fetched_at")
                market["source"] = "NAVER Finance"
            counters[f"valuation_{result['valuation_mode']}"] = counters.get(f"valuation_{result['valuation_mode']}", 0) + 1
            counters[f"investor_flow_{result['flow_mode']}"] = counters.get(f"investor_flow_{result['flow_mode']}", 0) + 1
            errors.extend(result.get("errors", []))

    payload.setdefault("source_status", {})["naver_finance"] = {
        "status": "ok" if counters["valuation_fresh"] or counters["valuation_cached"] else "failed",
        "role": "현재가·PER·PBR·EPS·BPS·목표주가·최근 10거래일 수급 수집",
        "source": "NAVER Finance",
        **counters,
        "requested_stocks": len(stocks),
        "parallel_workers": MAX_WORKERS,
        "errors": errors[:20],
        "updated_at": datetime.now(KST).isoformat(),
    }
    payload["errors"] = errors[:80]
    payload["updated_at"] = datetime.now(KST).isoformat()
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"NAVER market collection complete: {counters}; errors={len(errors)}")


if __name__ == "__main__":
    main()
