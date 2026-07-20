from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

KST = timezone(timedelta(hours=9))
DATA_FILE = Path("data/stock_data.json")
BASE_URL = "https://data-dbg.krx.co.kr/svc/apis/sto"
MARKET_ENDPOINTS = {
    "KOSPI": "stk_bydd_trd",
    "KOSDAQ": "ksq_bydd_trd",
}
MAX_LOOKBACK_DAYS = 10
TIMEOUT = (10, 40)


def number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if text in {"", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def request_market(api_key: str, endpoint: str, base_date: str) -> list[dict[str, Any]]:
    response = requests.get(
        f"{BASE_URL}/{endpoint}",
        params={"basDd": base_date},
        headers={"AUTH_KEY": api_key, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("OutBlock_1", [])
    if not isinstance(rows, list):
        raise RuntimeError(f"Unexpected KRX response format for {endpoint}")
    return [row for row in rows if isinstance(row, dict)]


def load_latest_official_rows(api_key: str) -> tuple[str, dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    now = datetime.now(KST)

    for offset in range(MAX_LOOKBACK_DAYS + 1):
        base_date = (now - timedelta(days=offset)).strftime("%Y%m%d")
        combined: dict[str, dict[str, Any]] = {}
        succeeded = 0

        for market_name, endpoint in MARKET_ENDPOINTS.items():
            try:
                rows = request_market(api_key, endpoint, base_date)
                succeeded += 1
                for row in rows:
                    code = str(row.get("ISU_SRT_CD") or row.get("ISU_CD") or "").strip()
                    if code.startswith("A") and len(code) == 7:
                        code = code[1:]
                    if len(code) != 6 or not code.isdigit():
                        continue
                    row["_market"] = market_name
                    combined[code] = row
            except Exception as exc:
                errors.append(f"{market_name} {base_date}: {str(exc)[:180]}")

        if combined:
            return base_date, combined, errors
        if succeeded == 0 and offset == 0:
            continue

    raise RuntimeError("KRX OPEN API에서 최근 거래일 데이터를 찾지 못했습니다.")


def enrich_stock(row: dict[str, Any], official: dict[str, Any], base_date: str) -> None:
    market = row.setdefault("market", {})
    close = number(official.get("TDD_CLSPRC"))
    change = number(official.get("CMPPREVDD_PRC"))
    change_pct = number(official.get("FLUC_RT"))
    volume = number(official.get("ACC_TRDVOL"))
    value = number(official.get("ACC_TRDVAL"))
    market_cap = number(official.get("MKTCAP"))

    if close is not None:
        market["current_price"] = int(close)
    market["as_of"] = base_date
    market["official_krx"] = {
        "status": "ok",
        "market": official.get("_market"),
        "name": official.get("ISU_NM"),
        "close": int(close) if close is not None else None,
        "change": int(change) if change is not None else None,
        "change_pct": change_pct,
        "volume": int(volume) if volume is not None else None,
        "trading_value_krw": int(value) if value is not None else None,
        "market_cap_krw": int(market_cap) if market_cap is not None else None,
        "source": "KRX Data Marketplace OPEN API",
        "fetched_at": datetime.now(KST).isoformat(),
    }
    previous_source = str(market.get("source") or "").strip()
    market["source"] = "KRX OPEN API + pykrx" if previous_source else "KRX OPEN API"


def main() -> None:
    api_key = os.getenv("KRX_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("KRX_API_KEY is not configured")
    if not DATA_FILE.exists():
        raise SystemExit("data/stock_data.json not found")

    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    stocks = payload.get("stocks", {})
    if not isinstance(stocks, dict):
        raise SystemExit("Invalid stock_data.json: stocks must be an object")

    source_status = payload.setdefault("source_status", {})
    try:
        base_date, official_rows, errors = load_latest_official_rows(api_key)
        matched = 0
        for stock_row in stocks.values():
            if not isinstance(stock_row, dict):
                continue
            code = str(stock_row.get("code", "")).strip()
            official = official_rows.get(code)
            if not official:
                continue
            enrich_stock(stock_row, official, base_date)
            matched += 1

        source_status["krx_open_api"] = {
            "configured": True,
            "status": "ok" if matched else "partial",
            "as_of": base_date,
            "matched_stocks": matched,
            "requested_stocks": len(stocks),
            "endpoints": list(MARKET_ENDPOINTS.values()),
            "role": "공식 일별 시세·거래대금·시가총액 우선 수집",
            "fallback": "PER·PBR·외국인·기관 수급은 pykrx 보조 수집",
            "errors": errors[-10:],
            "updated_at": datetime.now(KST).isoformat(),
        }
        print(f"KRX OPEN API recognized: {matched}/{len(stocks)} stocks matched for {base_date}")
    except Exception as exc:
        source_status["krx_open_api"] = {
            "configured": True,
            "status": "failed",
            "reason": str(exc)[:300],
            "fallback": "기존 pykrx 데이터 유지",
            "updated_at": datetime.now(KST).isoformat(),
        }
        print(f"KRX OPEN API failed; pykrx data retained: {exc}")

    payload["updated_at"] = datetime.now(KST).isoformat()
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
