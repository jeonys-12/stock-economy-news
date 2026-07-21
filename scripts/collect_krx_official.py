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
MARKET_ENDPOINTS = {"KOSPI": "stk_bydd_trd", "KOSDAQ": "ksq_bydd_trd"}
MAX_LOOKBACK_DAYS = 14
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


def request_market(api_key: str, endpoint: str, base_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = f"{BASE_URL}/{endpoint}"
    response = requests.get(
        url,
        params={"basDd": base_date},
        headers={"AUTH_KEY": api_key, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    diagnostic: dict[str, Any] = {
        "endpoint": endpoint,
        "base_date": base_date,
        "http_status": response.status_code,
        "url": url,
    }
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        diagnostic["response_preview"] = response.text[:300]
        raise RuntimeError(f"KRX JSON 파싱 실패: {exc}") from exc

    diagnostic["result_code"] = payload.get("resultCode") or payload.get("RESULT_CODE")
    diagnostic["result_message"] = payload.get("resultMsg") or payload.get("RESULT_MSG") or payload.get("message")
    rows = payload.get("OutBlock_1", [])
    if not isinstance(rows, list):
        diagnostic["response_keys"] = list(payload.keys())[:20]
        raise RuntimeError(f"Unexpected KRX response format for {endpoint}: {diagnostic}")
    diagnostic["row_count"] = len(rows)
    return [row for row in rows if isinstance(row, dict)], diagnostic


def load_latest_official_rows(api_key: str) -> tuple[str, dict[str, dict[str, Any]], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    now = datetime.now(KST)
    authorization_error: str | None = None

    for offset in range(MAX_LOOKBACK_DAYS + 1):
        base_date = (now - timedelta(days=offset)).strftime("%Y%m%d")
        combined: dict[str, dict[str, Any]] = {}

        for market_name, endpoint in MARKET_ENDPOINTS.items():
            try:
                rows, diagnostic = request_market(api_key, endpoint, base_date)
                diagnostic["market"] = market_name
                diagnostics.append(diagnostic)
                for row in rows:
                    code = str(row.get("ISU_SRT_CD") or row.get("ISU_CD") or "").strip()
                    if code.startswith("A") and len(code) == 7:
                        code = code[1:]
                    if len(code) != 6 or not code.isdigit():
                        continue
                    row["_market"] = market_name
                    combined[code] = row
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                preview = exc.response.text[:300] if exc.response is not None else str(exc)
                diagnostics.append({"market": market_name, "endpoint": endpoint, "base_date": base_date,
                                    "http_status": status, "error": preview})
                if status in {401, 403}:
                    authorization_error = f"KRX API 인증 또는 서비스 활용승인 오류(HTTP {status})"
            except Exception as exc:
                diagnostics.append({"market": market_name, "endpoint": endpoint, "base_date": base_date,
                                    "error": str(exc)[:300]})

        if combined:
            return base_date, combined, diagnostics
        if authorization_error:
            raise RuntimeError(authorization_error)

    recent = diagnostics[-6:]
    raise RuntimeError(f"KRX OPEN API 최근 거래일 데이터 없음. 최근 응답: {json.dumps(recent, ensure_ascii=False)[:900]}")


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
    market["status"] = "ok"
    market.pop("reason", None)
    market["as_of"] = base_date
    market["official_krx"] = {
        "status": "ok", "market": official.get("_market"), "name": official.get("ISU_NM"),
        "close": int(close) if close is not None else None,
        "change": int(change) if change is not None else None,
        "change_pct": change_pct,
        "volume": int(volume) if volume is not None else None,
        "trading_value_krw": int(value) if value is not None else None,
        "market_cap_krw": int(market_cap) if market_cap is not None else None,
        "source": "KRX Data Marketplace OPEN API", "fetched_at": datetime.now(KST).isoformat(),
    }
    market["source"] = "KRX OPEN API"


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
        base_date, official_rows, diagnostics = load_latest_official_rows(api_key)
        matched = 0
        for stock_row in stocks.values():
            if not isinstance(stock_row, dict):
                continue
            official = official_rows.get(str(stock_row.get("code", "")).strip())
            if not official:
                continue
            enrich_stock(stock_row, official, base_date)
            matched += 1
        source_status["krx_open_api"] = {
            "configured": True, "status": "ok" if matched else "partial", "as_of": base_date,
            "matched_stocks": matched, "requested_stocks": len(stocks),
            "endpoints": list(MARKET_ENDPOINTS.values()),
            "role": "공식 일별 시세·거래대금·시가총액 우선 수집",
            "fallback": "PER·PBR·외국인·기관 수급은 로그인 없는 공개 시장 페이지로 보완",
            "diagnostics": diagnostics[-8:], "updated_at": datetime.now(KST).isoformat(),
        }
        print(f"KRX OPEN API recognized: {matched}/{len(stocks)} stocks matched for {base_date}")
    except Exception as exc:
        source_status["krx_open_api"] = {
            "configured": True, "status": "failed", "reason": str(exc)[:1200],
            "fallback": "가격은 기존 값 유지, PER·PBR·수급은 공개 시장 페이지로 보완",
            "updated_at": datetime.now(KST).isoformat(),
        }
        print(f"KRX OPEN API failed; existing price data retained: {exc}")

    payload["updated_at"] = datetime.now(KST).isoformat()
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
