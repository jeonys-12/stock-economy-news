from __future__ import annotations

import io
import json
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import requests

KST = timezone(timedelta(hours=9))
DATA_FILE = Path("data/stock_data.json")
API_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
HEADERS = {"User-Agent": "Mozilla/5.0 StockEconomyNews/2.1"}
MAX_WORKERS = max(2, min(6, int(os.getenv("DART_LATEST_WORKERS", "5"))))
REPORTS = (("11014", "3분기"), ("11012", "반기"), ("11013", "1분기"), ("11011", "사업보고서"))
FLOW_KEYS = {"revenue", "operating_profit", "net_income"}
ALIASES = {
    "revenue": {"매출액", "영업수익", "수익(매출액)", "보험영업수익"},
    "operating_profit": {"영업이익", "영업이익(손실)"},
    "net_income": {"당기순이익", "당기순이익(손실)", "연결당기순이익"},
    "assets": {"자산총계"},
    "liabilities": {"부채총계"},
    "equity": {"자본총계"},
}


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


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / abs(previous) * 100, 2)


def get_corp_codes(api_key: str) -> dict[str, str]:
    response = requests.get(
        "https://opendart.fss.or.kr/api/corpCode.xml",
        params={"crtfc_key": api_key}, headers=HEADERS, timeout=(10, 45),
    )
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        root = ElementTree.fromstring(archive.read("CORPCODE.xml"))
    return {
        (node.findtext("stock_code") or "").strip(): (node.findtext("corp_code") or "").strip()
        for node in root.findall("list")
        if (node.findtext("stock_code") or "").strip() and (node.findtext("corp_code") or "").strip()
    }


def amount_pair(row: dict[str, Any], report_code: str, is_flow: bool) -> tuple[float | None, float | None, str]:
    if not is_flow:
        return number(row.get("thstrm_amount")), number(row.get("frmtrm_amount")), "기말 재무상태 비교"
    if report_code == "11013":
        current = number(row.get("thstrm_amount"))
        previous = number(row.get("frmtrm_q_amount"))
        if previous is None:
            previous = number(row.get("frmtrm_amount"))
        return current, previous, "1분기 전년 동기 단일기간 비교"
    if report_code in {"11012", "11014"}:
        current = number(row.get("thstrm_add_amount"))
        previous = number(row.get("frmtrm_add_amount"))
        if current is None:
            current = number(row.get("thstrm_amount"))
        if previous is None:
            previous = number(row.get("frmtrm_amount"))
        return current, previous, "전년 동기 누적기간 비교"
    return number(row.get("thstrm_amount")), number(row.get("frmtrm_amount")), "연간 전년 대비 비교"


def fetch_latest(api_key: str, corp_code: str) -> dict[str, Any]:
    now = datetime.now(KST)
    for year in (now.year, now.year - 1, now.year - 2):
        for report_code, report_name in REPORTS:
            response = requests.get(
                API_URL,
                params={
                    "crtfc_key": api_key, "corp_code": corp_code,
                    "bsns_year": str(year), "reprt_code": report_code, "fs_div": "CFS",
                },
                headers=HEADERS, timeout=(10, 40),
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") != "000":
                continue
            values: dict[str, dict[str, Any]] = {}
            for row in payload.get("list", []):
                account = str(row.get("account_nm", "")).strip()
                statement = str(row.get("sj_nm", ""))
                for key, names in ALIASES.items():
                    if account not in names:
                        continue
                    current, previous, basis = amount_pair(row, report_code, key in FLOW_KEYS)
                    quality = int(current is not None) + int(previous is not None) + int("연결" in statement)
                    old = values.get(key)
                    if old is None or quality > old["quality"]:
                        values[key] = {"current": current, "previous": previous, "basis": basis, "quality": quality}
            if not values:
                continue
            result: dict[str, Any] = {
                "status": "ok", "business_year": year, "report_code": report_code,
                "report_name": report_name, "source": "OpenDART",
                "fetched_at": datetime.now(KST).isoformat(),
            }
            for key, pair in values.items():
                result[key] = pair["current"]
                result[f"{key}_previous"] = pair["previous"]
                result[f"{key}_growth_pct"] = pct_change(pair["current"], pair["previous"])
                result[f"{key}_comparison_basis"] = pair["basis"]
            liabilities, equity = result.get("liabilities"), result.get("equity")
            if liabilities is not None and equity not in (None, 0):
                result["debt_ratio_pct"] = round(liabilities / equity * 100, 2)
            return result
    return {"status": "unavailable", "reason": "최근 연결재무제표를 찾지 못했습니다.", "source": "OpenDART"}


def main() -> None:
    api_key = os.getenv("OPENDART_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("OPENDART_API_KEY is not configured")
    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    stocks = payload.get("stocks", {})
    corp_codes = get_corp_codes(api_key)
    updated = 0
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="dart-latest") as executor:
        futures = {
            executor.submit(fetch_latest, api_key, corp_codes.get(str(row.get("code", "")), "")): name
            for name, row in stocks.items()
            if isinstance(row, dict) and corp_codes.get(str(row.get("code", "")))
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                financials = future.result()
                stocks[name]["financials"] = financials
                updated += int(financials.get("status") == "ok")
            except Exception as exc:
                errors.append(f"{name}: {str(exc)[:180]}")
    payload.setdefault("source_status", {})["opendart_latest_financials"] = {
        "status": "ok" if updated else "failed",
        "updated_stocks": updated,
        "requested_stocks": len(stocks),
        "comparison_policy": "1분기 단일기간, 반기·3분기 누적기간, 사업보고서 연간 전년 대비",
        "errors": errors[:20],
        "updated_at": datetime.now(KST).isoformat(),
    }
    payload["updated_at"] = datetime.now(KST).isoformat()
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Latest OpenDART financials refreshed: {updated}/{len(stocks)}; errors={len(errors)}")


if __name__ == "__main__":
    main()
