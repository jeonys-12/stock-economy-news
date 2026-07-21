from __future__ import annotations

import io
import json
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

import pandas as pd
import requests

KST = timezone(timedelta(hours=9))
DATA_FILE = Path("data/stock_data.json")
DART_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
HEADERS = {"User-Agent": "Mozilla/5.0 StockEconomyNews/2.0"}
MAX_WORKERS = max(2, min(6, int(os.getenv("QUALITY_ANALYSIS_WORKERS", "5"))))
HISTORY_YEARS = 5


def num(value: Any) -> float | None:
    if value is None:
        return None
    text = re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))
    if text in {"", "-", "."}:
        return None
    try:
        result = float(text)
        return result if math.isfinite(result) else None
    except ValueError:
        return None


def round_or_none(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def first_amount(row: dict[str, Any]) -> float | None:
    for key in ("thstrm_amount", "thstrm_add_amount", "thstrm_q_amount"):
        value = num(row.get(key))
        if value is not None:
            return value
    return None


def get_corp_codes(api_key: str) -> dict[str, str]:
    import zipfile
    from xml.etree import ElementTree

    response = requests.get(
        "https://opendart.fss.or.kr/api/corpCode.xml",
        params={"crtfc_key": api_key},
        headers=HEADERS,
        timeout=(10, 45),
    )
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        root = ElementTree.fromstring(archive.read("CORPCODE.xml"))
    return {
        (node.findtext("stock_code") or "").strip(): (node.findtext("corp_code") or "").strip()
        for node in root.findall("list")
        if (node.findtext("stock_code") or "").strip() and (node.findtext("corp_code") or "").strip()
    }


def fetch_annual(api_key: str, corp_code: str, year: int) -> dict[str, Any] | None:
    response = requests.get(
        DART_URL,
        params={
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": "11011",
            "fs_div": "CFS",
        },
        headers=HEADERS,
        timeout=(10, 40),
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "000":
        return None

    aliases = {
        "net_income": ("당기순이익", "당기순이익(손실)", "연결당기순이익"),
        "operating_cash_flow": (
            "영업활동으로 인한 현금흐름",
            "영업활동현금흐름",
            "영업활동으로부터의 현금흐름",
            "영업활동으로 인한 순현금흐름",
        ),
        "equity": ("자본총계",),
        "liabilities": ("부채총계",),
        "eps": ("기본주당이익", "기본주당순이익", "기본주당이익(손실)"),
    }
    selected: dict[str, float] = {}
    for row in payload.get("list", []):
        account = str(row.get("account_nm", "")).strip()
        statement = str(row.get("sj_nm", ""))
        for key, names in aliases.items():
            if account not in names:
                continue
            value = first_amount(row)
            if value is None:
                continue
            if key not in selected or "연결" in statement:
                selected[key] = value
    if not selected:
        return None
    equity = selected.get("equity")
    liabilities = selected.get("liabilities")
    net_income = selected.get("net_income")
    return {
        "year": year,
        **selected,
        "roe_pct": round_or_none(net_income / equity * 100 if net_income is not None and equity not in (None, 0) else None),
        "debt_ratio_pct": round_or_none(liabilities / equity * 100 if liabilities is not None and equity not in (None, 0) else None),
    }


def cagr(values: list[tuple[int, float | None]]) -> float | None:
    usable = [(year, value) for year, value in values if value is not None and value > 0]
    if len(usable) < 2:
        return None
    first_year, first = usable[0]
    last_year, last = usable[-1]
    periods = last_year - first_year
    if periods <= 0 or first <= 0 or last <= 0:
        return None
    return round(((last / first) ** (1 / periods) - 1) * 100, 2)


def fetch_forward_eps(code: str) -> dict[str, Any]:
    url = "https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp"
    params = {"pGB": "1", "gicode": f"A{code}", "cID": "", "MenuYn": "Y", "ReportGB": "", "NewMenuID": "101", "stkGb": "701"}
    response = requests.get(url, params=params, headers=HEADERS, timeout=(10, 35))
    response.raise_for_status()
    tables = pd.read_html(io.StringIO(response.text))
    candidates: list[tuple[int, float, str]] = []
    for frame in tables:
        if frame.empty:
            continue
        frame.columns = [" ".join(str(part) for part in col if str(part) != "nan") if isinstance(col, tuple) else str(col) for col in frame.columns]
        for _, row in frame.iterrows():
            label = str(row.iloc[0]).replace(" ", "")
            if "EPS" not in label.upper():
                continue
            for idx, value in enumerate(row.iloc[1:], start=1):
                parsed = num(value)
                if parsed is None or parsed <= 0:
                    continue
                column = str(frame.columns[idx])
                forecast_bonus = 2 if "(E)" in column.upper() or "E" in column.upper() else 0
                year_match = re.search(r"20\d{2}", column)
                year = int(year_match.group()) if year_match else 0
                candidates.append((forecast_bonus * 10000 + year, parsed, column))
    if not candidates:
        return {"status": "unavailable", "reason": "FnGuide 공개 표에서 예상 EPS를 찾지 못했습니다."}
    _, value, column = max(candidates, key=lambda item: item[0])
    return {"status": "ok", "forward_eps": round(value, 2), "period": column, "source": "FnGuide CompanyGuide"}


def build_metrics(history: list[dict[str, Any]], current_price: float | None, forecast: dict[str, Any]) -> dict[str, Any]:
    history = sorted(history, key=lambda item: item["year"])
    eps_cagr = cagr([(item["year"], num(item.get("eps"))) for item in history])
    if eps_cagr is None:
        eps_cagr = cagr([(item["year"], num(item.get("net_income"))) for item in history])
    ocf_values = [num(item.get("operating_cash_flow")) for item in history]
    ocf_positive = [value for value in ocf_values if value is not None]
    roe_values = [num(item.get("roe_pct")) for item in history if num(item.get("roe_pct")) is not None]
    debt_values = [num(item.get("debt_ratio_pct")) for item in history if num(item.get("debt_ratio_pct")) is not None]
    forward_eps = num(forecast.get("forward_eps"))
    forward_per = current_price / forward_eps if current_price and forward_eps and forward_eps > 0 else None
    return {
        "history_years": len(history),
        "eps_growth_cagr_pct": eps_cagr,
        "operating_cash_flow_positive_ratio_pct": round(sum(value > 0 for value in ocf_positive) / len(ocf_positive) * 100, 1) if ocf_positive else None,
        "operating_cash_flow_cagr_pct": cagr([(item["year"], num(item.get("operating_cash_flow"))) for item in history]),
        "average_roe_pct": round(sum(roe_values) / len(roe_values), 2) if roe_values else None,
        "latest_roe_pct": round_or_none(roe_values[-1] if roe_values else None),
        "latest_debt_ratio_pct": round_or_none(debt_values[-1] if debt_values else None),
        "debt_ratio_change_pp": round_or_none(debt_values[-1] - debt_values[0] if len(debt_values) >= 2 else None),
        "forward_eps": forward_eps,
        "forward_eps_period": forecast.get("period"),
        "forward_per": round_or_none(forward_per),
    }


def peer_medians(rows: dict[str, dict[str, Any]]) -> dict[str, dict[str, float]]:
    keys = ("eps_growth_cagr_pct", "operating_cash_flow_positive_ratio_pct", "average_roe_pct", "latest_debt_ratio_pct", "forward_per")
    sectors: dict[str, dict[str, list[float]]] = {}
    for row in rows.values():
        sector = str(row.get("business_sector") or row.get("sector") or "기타")
        bucket = sectors.setdefault(sector, {key: [] for key in keys})
        metrics = row.get("quality_value_analysis", {}).get("metrics", {})
        for key in keys:
            value = num(metrics.get(key))
            if value is not None:
                bucket[key].append(value)
    return {sector: {key: round(median(values), 2) for key, values in bucket.items() if values} for sector, bucket in sectors.items()}


def score_analysis(metrics: dict[str, Any], peers: dict[str, float]) -> tuple[int, dict[str, int], list[str]]:
    components = {"eps_growth": 0, "cash_flow": 0, "roe": 0, "financial_safety": 0, "valuation": 0}
    reasons: list[str] = []

    eps = num(metrics.get("eps_growth_cagr_pct"))
    peer_eps = num(peers.get("eps_growth_cagr_pct"))
    if eps is not None:
        components["eps_growth"] = 25 if eps >= 15 and (peer_eps is None or eps >= peer_eps) else 19 if eps > 5 else 11 if eps > 0 else 2
        reasons.append(f"EPS 3~5년 성장률 {eps:.1f}%")

    ocf_ratio = num(metrics.get("operating_cash_flow_positive_ratio_pct"))
    ocf_cagr = num(metrics.get("operating_cash_flow_cagr_pct"))
    if ocf_ratio is not None:
        components["cash_flow"] = 20 if ocf_ratio == 100 and (ocf_cagr is None or ocf_cagr >= 0) else 14 if ocf_ratio >= 75 else 7 if ocf_ratio >= 50 else 0
        reasons.append(f"영업현금흐름 양수 비율 {ocf_ratio:.0f}%")

    roe = num(metrics.get("average_roe_pct"))
    peer_roe = num(peers.get("average_roe_pct"))
    if roe is not None:
        components["roe"] = 20 if roe >= 15 and (peer_roe is None or roe >= peer_roe) else 15 if roe >= 10 else 9 if roe >= 5 else 0
        reasons.append(f"평균 ROE {roe:.1f}%")

    debt = num(metrics.get("latest_debt_ratio_pct"))
    debt_change = num(metrics.get("debt_ratio_change_pp"))
    peer_debt = num(peers.get("latest_debt_ratio_pct"))
    if debt is not None:
        base = 20 if debt < 100 else 14 if debt < 150 else 7 if debt < 250 else 0
        if peer_debt is not None and debt > peer_debt * 1.5:
            base = max(0, base - 4)
        if debt_change is not None and debt_change > 30:
            base = max(0, base - 4)
        components["financial_safety"] = base
        reasons.append(f"부채비율 {debt:.1f}%")

    fper = num(metrics.get("forward_per"))
    peer_per = num(peers.get("forward_per"))
    if fper is not None and fper > 0:
        components["valuation"] = 15 if peer_per and fper <= peer_per * 0.85 else 12 if fper <= 12 else 8 if fper <= 20 else 3 if fper <= 35 else 0
        reasons.append(f"추정 PER {fper:.1f}배")

    return sum(components.values()), components, reasons


def collect_one(name: str, row: dict[str, Any], api_key: str, corp_code: str | None) -> tuple[str, dict[str, Any]]:
    history: list[dict[str, Any]] = []
    errors: list[str] = []
    now = datetime.now(KST)
    if api_key and corp_code:
        for year in range(now.year - 1, now.year - HISTORY_YEARS - 1, -1):
            try:
                annual = fetch_annual(api_key, corp_code, year)
                if annual:
                    history.append(annual)
            except Exception as exc:
                errors.append(f"{year}: {str(exc)[:120]}")
    else:
        errors.append("OpenDART API 키 또는 고유번호 없음")

    try:
        forecast = fetch_forward_eps(str(row.get("code", "")))
    except Exception as exc:
        forecast = {"status": "failed", "reason": str(exc)[:160]}

    market = row.get("market", {}) if isinstance(row.get("market"), dict) else {}
    metrics = build_metrics(history, num(market.get("current_price")), forecast)
    return name, {
        "status": "ok" if history else "partial",
        "source": "OpenDART 연간 연결재무제표 + FnGuide 예상 EPS",
        "history": sorted(history, key=lambda item: item["year"]),
        "metrics": metrics,
        "forecast_status": forecast,
        "errors": errors[:8],
        "updated_at": datetime.now(KST).isoformat(),
    }


def main() -> None:
    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    stocks = payload.get("stocks", {}) if isinstance(payload.get("stocks"), dict) else {}
    if not stocks:
        raise SystemExit("stock_data.json에 종목 데이터가 없습니다.")

    api_key = os.getenv("OPENDART_API_KEY", "").strip()
    corp_codes = get_corp_codes(api_key) if api_key else {}
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="quality") as executor:
        futures = {
            executor.submit(collect_one, name, row, api_key, corp_codes.get(str(row.get("code", "")))): name
            for name, row in stocks.items()
            if isinstance(row, dict)
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                _, analysis = future.result()
            except Exception as exc:
                analysis = {"status": "failed", "reason": str(exc)[:180], "metrics": {}, "history": []}
            results[name] = analysis

    for name, analysis in results.items():
        stocks[name]["quality_value_analysis"] = analysis
    medians = peer_medians(stocks)

    completed = 0
    for name, row in stocks.items():
        analysis = row.get("quality_value_analysis", {})
        metrics = analysis.get("metrics", {})
        sector = str(row.get("business_sector") or row.get("sector") or "기타")
        peer = medians.get(sector, {})
        score, components, reasons = score_analysis(metrics, peer)
        analysis.update({
            "score": score,
            "grade": "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 50 else "D",
            "components": components,
            "peer_sector": sector,
            "peer_medians": peer,
            "reasons": reasons,
        })
        if analysis.get("status") == "ok":
            completed += 1

        quantitative = row.setdefault("quantitative", {})
        q_components = quantitative.setdefault("components", {})
        contribution = max(-15, min(15, round((score - 50) * 0.3, 1)))
        q_components["quality_value"] = contribution
        quantitative["quality_value_score"] = score
        quantitative["score"] = round(sum(num(value) or 0 for value in q_components.values()), 1)
        dimensions = set(quantitative.get("available_dimension_names", []))
        if metrics.get("history_years", 0) >= 3:
            dimensions.add("quality_value")
        quantitative["available_dimension_names"] = sorted(dimensions)
        quantitative["available_dimensions"] = max(int(quantitative.get("available_dimensions", 0)), len(dimensions))
        total = quantitative["score"]
        quantitative["signal"] = "긍정" if total >= 15 else "부정" if total <= -15 else "중립"

    payload.setdefault("source_status", {})["quality_value_analysis"] = {
        "status": "ok" if completed else "partial",
        "completed_stocks": completed,
        "requested_stocks": len(stocks),
        "history_years": HISTORY_YEARS,
        "parallel_workers": MAX_WORKERS,
        "source": "OpenDART + FnGuide",
        "updated_at": datetime.now(KST).isoformat(),
    }
    payload.setdefault("methodology", {})["quality_value_policy"] = (
        "EPS 성장률·영업현금흐름·ROE·부채비율·추정 PER을 최근 3~5년 추세와 동일 업종 중앙값으로 비교해 100점으로 평가합니다."
    )
    payload["updated_at"] = datetime.now(KST).isoformat()
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Quality/value analysis complete: {completed}/{len(stocks)} stocks")


if __name__ == "__main__":
    main()
