from __future__ import annotations

import io
import json
import math
import os
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
DATA_FILE = Path("data/stock_data.json")
DART_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; StockEconomyNews/2.1; +https://github.com/jeonys-12/stock-economy-news)"
}
MAX_WORKERS = max(2, min(6, int(os.getenv("QUALITY_ANALYSIS_WORKERS", "5"))))
HISTORY_YEARS = 5
MODE = os.getenv("QUALITY_ANALYSIS_MODE", "daily").strip().lower()
NAVER_MAIN_URL = "https://navercomp2.wisereport.co.kr/v2/company/c1010001.aspx"
NAVER_FINANCE_URL = "https://navercomp2.wisereport.co.kr/v2/company/c1030001.aspx"


def num(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").replace("−", "-").strip()
    if text in {"", "-", "N/A", "nan"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        result = float(match.group())
        return result if math.isfinite(result) else None
    except ValueError:
        return None


def round_or_none(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=KST)
        return result.astimezone(KST)
    except (TypeError, ValueError):
        return None


def cagr(values: list[tuple[int, float | None]]) -> float | None:
    usable = [(year, value) for year, value in values if value is not None and value > 0]
    if len(usable) < 2:
        return None
    first_year, first = usable[0]
    last_year, last = usable[-1]
    periods = last_year - first_year
    if periods <= 0:
        return None
    return round(((last / first) ** (1 / periods) - 1) * 100, 2)


def normalize_label(value: str) -> str:
    return re.sub(r"[\s·]", "", value).upper()


def extract_periods(soup: BeautifulSoup) -> list[str]:
    best: list[str] = []
    for row in soup.select("table tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.select("th,td")]
        periods = [cell for cell in cells if re.search(r"20\d{2}", cell)]
        if len(periods) > len(best):
            best = periods
    return best


def find_metric_values(soup: BeautifulSoup, aliases: tuple[str, ...]) -> tuple[list[str], list[float | None]]:
    periods = extract_periods(soup)
    normalized_aliases = tuple(normalize_label(alias) for alias in aliases)
    for row in soup.select("table tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.select("th,td")]
        if len(cells) < 2:
            continue
        label = normalize_label(cells[0])
        if not any(alias in label for alias in normalized_aliases):
            continue
        values = [num(cell) for cell in cells[1:]]
        if periods and len(values) > len(periods):
            values = values[-len(periods):]
        if periods and len(values) < len(periods):
            periods = periods[-len(values):]
        return periods, values
    return [], []


def merge_metric(history: dict[int, dict[str, Any]], periods: list[str], values: list[float | None], key: str) -> None:
    for period, value in zip(periods, values):
        match = re.search(r"20\d{2}", period)
        if not match or value is None or "(E)" in period.upper():
            continue
        year = int(match.group())
        history.setdefault(year, {"year": year})[key] = value


def forecast_from_row(periods: list[str], values: list[float | None]) -> dict[str, Any]:
    candidates: list[tuple[int, float, str]] = []
    for period, value in zip(periods, values):
        if value is None or value <= 0:
            continue
        year_match = re.search(r"20\d{2}", period)
        year = int(year_match.group()) if year_match else 0
        estimated = "(E)" in period.upper() or "E" in period.upper()
        candidates.append(((10000 if estimated else 0) + year, value, period))
    if not candidates:
        return {"status": "unavailable", "reason": "예상 EPS를 찾지 못했습니다."}
    _, value, period = max(candidates, key=lambda item: item[0])
    return {
        "status": "ok",
        "forward_eps": round(value, 2),
        "period": period,
        "source": "NAVER Finance / WISEreport",
    }


def fetch_naver_history(code: str) -> dict[str, Any]:
    session = requests.Session()
    params = {"cmp_cd": code}
    responses = []
    for url in (NAVER_MAIN_URL, NAVER_FINANCE_URL):
        response = session.get(url, params=params, headers=HEADERS, timeout=(8, 25))
        response.raise_for_status()
        responses.append(response.text)
    session.close()

    soups = [BeautifulSoup(text, "lxml") for text in responses]
    history: dict[int, dict[str, Any]] = {}
    metric_aliases = {
        "eps": ("EPS(원)", "EPS", "주당순이익"),
        "roe_pct": ("ROE(%)", "ROE"),
        "debt_ratio_pct": ("부채비율", "부채비율(%)"),
        "operating_cash_flow": ("영업활동현금흐름", "영업활동으로인한현금흐름"),
        "net_income": ("당기순이익", "지배주주순이익"),
        "equity": ("자본총계", "지배주주지분"),
        "liabilities": ("부채총계",),
    }
    eps_periods: list[str] = []
    eps_values: list[float | None] = []
    for key, aliases in metric_aliases.items():
        periods: list[str] = []
        values: list[float | None] = []
        for soup in soups:
            periods, values = find_metric_values(soup, aliases)
            if values:
                break
        merge_metric(history, periods, values, key)
        if key == "eps":
            eps_periods, eps_values = periods, values

    rows = sorted(history.values(), key=lambda item: item["year"])[-HISTORY_YEARS:]
    for row in rows:
        if row.get("roe_pct") is None and row.get("net_income") is not None and row.get("equity") not in (None, 0):
            row["roe_pct"] = round_or_none(row["net_income"] / row["equity"] * 100)
        if row.get("debt_ratio_pct") is None and row.get("liabilities") is not None and row.get("equity") not in (None, 0):
            row["debt_ratio_pct"] = round_or_none(row["liabilities"] / row["equity"] * 100)

    return {
        "history": rows,
        "forecast": forecast_from_row(eps_periods, eps_values),
        "source": "NAVER Finance / WISEreport 공개 기업정보",
    }


def first_amount(row: dict[str, Any]) -> float | None:
    for key in ("thstrm_amount", "thstrm_add_amount", "thstrm_q_amount"):
        value = num(row.get(key))
        if value is not None:
            return value
    return None


def get_corp_codes(api_key: str) -> dict[str, str]:
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


def fetch_dart_annual(api_key: str, corp_code: str, year: int) -> dict[str, Any] | None:
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
    for item in payload.get("list", []):
        account = str(item.get("account_nm", "")).strip()
        statement = str(item.get("sj_nm", ""))
        for key, names in aliases.items():
            if account not in names:
                continue
            value = first_amount(item)
            if value is not None and (key not in selected or "연결" in statement):
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


def fetch_dart_history(api_key: str, corp_code: str) -> list[dict[str, Any]]:
    now = datetime.now(KST)
    history: list[dict[str, Any]] = []
    for year in range(now.year - 1, now.year - HISTORY_YEARS - 1, -1):
        annual = fetch_dart_annual(api_key, corp_code, year)
        if annual:
            history.append(annual)
    return sorted(history, key=lambda item: item["year"])


def build_metrics(history: list[dict[str, Any]], current_price: float | None, forecast: dict[str, Any]) -> dict[str, Any]:
    history = sorted(history, key=lambda item: item["year"])
    eps_cagr = cagr([(item["year"], num(item.get("eps"))) for item in history])
    if eps_cagr is None:
        eps_cagr = cagr([(item["year"], num(item.get("net_income"))) for item in history])
    ocf_values = [num(item.get("operating_cash_flow")) for item in history]
    ocf_present = [value for value in ocf_values if value is not None]
    roe_values = [num(item.get("roe_pct")) for item in history if num(item.get("roe_pct")) is not None]
    debt_values = [num(item.get("debt_ratio_pct")) for item in history if num(item.get("debt_ratio_pct")) is not None]
    forward_eps = num(forecast.get("forward_eps"))
    forward_per = current_price / forward_eps if current_price and forward_eps and forward_eps > 0 else None
    return {
        "history_years": len(history),
        "eps_growth_cagr_pct": eps_cagr,
        "operating_cash_flow_positive_ratio_pct": round(sum(value > 0 for value in ocf_present) / len(ocf_present) * 100, 1) if ocf_present else None,
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


def valid_cached_analysis(analysis: dict[str, Any], max_days: int = 8) -> bool:
    if not isinstance(analysis, dict) or not analysis.get("history"):
        return False
    updated = parse_dt(analysis.get("updated_at"))
    return bool(updated and datetime.now(KST) - updated <= timedelta(days=max_days))


def compare_histories(naver: list[dict[str, Any]], dart: list[dict[str, Any]]) -> dict[str, Any]:
    naver_by_year = {int(row["year"]): row for row in naver if row.get("year")}
    checked = 0
    differences: list[str] = []
    for dart_row in dart:
        year = int(dart_row["year"])
        naver_row = naver_by_year.get(year)
        if not naver_row:
            continue
        for key in ("eps", "operating_cash_flow", "roe_pct", "debt_ratio_pct"):
            left, right = num(naver_row.get(key)), num(dart_row.get(key))
            if left is None or right is None:
                continue
            checked += 1
            denominator = max(abs(right), 1)
            if abs(left - right) / denominator > 0.1:
                differences.append(f"{year} {key}: 네이버 {left:g} / DART {right:g}")
    return {
        "status": "matched" if checked and not differences else "difference" if differences else "insufficient",
        "checked_values": checked,
        "differences": differences[:12],
        "verified_at": datetime.now(KST).isoformat(),
    }


def collect_daily(name: str, row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    previous = row.get("quality_value_analysis", {}) if isinstance(row.get("quality_value_analysis"), dict) else {}
    try:
        naver = fetch_naver_history(str(row.get("code", "")))
        history = naver["history"]
        forecast = naver["forecast"]
        if not history and valid_cached_analysis(previous):
            cached = dict(previous)
            cached.update({"status": "cached", "daily_source_status": "naver_empty", "updated_at": datetime.now(KST).isoformat()})
            return name, cached
        market = row.get("market", {}) if isinstance(row.get("market"), dict) else {}
        return name, {
            "status": "ok" if history else "partial",
            "mode": "daily_screening",
            "source": naver["source"],
            "history": history,
            "metrics": build_metrics(history, num(market.get("current_price")), forecast),
            "forecast_status": forecast,
            "official_verification": previous.get("official_verification", {}),
            "updated_at": datetime.now(KST).isoformat(),
        }
    except Exception as exc:
        if valid_cached_analysis(previous):
            cached = dict(previous)
            cached.update({"status": "cached", "daily_source_status": f"naver_failed: {str(exc)[:140]}", "updated_at": datetime.now(KST).isoformat()})
            return name, cached
        return name, {"status": "failed", "reason": str(exc)[:180], "history": [], "metrics": {}, "mode": "daily_screening"}


def collect_weekly(name: str, row: dict[str, Any], api_key: str, corp_code: str | None) -> tuple[str, dict[str, Any]]:
    previous = row.get("quality_value_analysis", {}) if isinstance(row.get("quality_value_analysis"), dict) else {}
    naver_history = previous.get("history", []) if isinstance(previous.get("history"), list) else []
    forecast = previous.get("forecast_status", {}) if isinstance(previous.get("forecast_status"), dict) else {}
    if not naver_history:
        try:
            naver = fetch_naver_history(str(row.get("code", "")))
            naver_history, forecast = naver["history"], naver["forecast"]
        except Exception:
            pass
    if not api_key or not corp_code:
        return name, {**previous, "status": "partial", "mode": "weekly_verification", "official_verification": {"status": "unavailable", "reason": "OpenDART 키 또는 고유번호 없음"}}
    dart_history = fetch_dart_history(api_key, corp_code)
    verification = compare_histories(naver_history, dart_history)
    selected_history = dart_history if len(dart_history) >= 3 else naver_history
    market = row.get("market", {}) if isinstance(row.get("market"), dict) else {}
    return name, {
        "status": "ok" if selected_history else "partial",
        "mode": "weekly_verification",
        "source": "OpenDART 공식 연간 연결재무제표" if len(dart_history) >= 3 else "NAVER Finance / WISEreport 캐시",
        "history": selected_history,
        "naver_history_snapshot": naver_history,
        "metrics": build_metrics(selected_history, num(market.get("current_price")), forecast),
        "forecast_status": forecast,
        "official_verification": verification,
        "updated_at": datetime.now(KST).isoformat(),
    }


def main() -> None:
    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    stocks = payload.get("stocks", {}) if isinstance(payload.get("stocks"), dict) else {}
    if not stocks:
        raise SystemExit("stock_data.json에 종목 데이터가 없습니다.")

    api_key = os.getenv("OPENDART_API_KEY", "").strip() if MODE == "weekly" else ""
    corp_codes = get_corp_codes(api_key) if api_key else {}
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="quality") as executor:
        if MODE == "weekly":
            futures = {
                executor.submit(collect_weekly, name, row, api_key, corp_codes.get(str(row.get("code", "")))): name
                for name, row in stocks.items() if isinstance(row, dict)
            }
        else:
            futures = {
                executor.submit(collect_daily, name, row): name
                for name, row in stocks.items() if isinstance(row, dict)
            }
        for future in as_completed(futures):
            name = futures[future]
            try:
                _, analysis = future.result()
            except Exception as exc:
                analysis = {"status": "failed", "reason": str(exc)[:180], "metrics": {}, "history": [], "mode": MODE}
            results[name] = analysis

    for name, analysis in results.items():
        stocks[name]["quality_value_analysis"] = analysis
    medians = peer_medians(stocks)
    completed = 0
    verified = 0
    for row in stocks.values():
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
        if analysis.get("status") in {"ok", "cached"}:
            completed += 1
        if analysis.get("official_verification", {}).get("status") in {"matched", "difference"}:
            verified += 1
        quantitative = row.setdefault("quantitative", {})
        q_components = quantitative.setdefault("components", {})
        q_components["quality_value"] = max(-15, min(15, round((score - 50) * 0.3, 1)))
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
        "mode": "네이버 일일 선별" if MODE != "weekly" else "OpenDART 주간 검증",
        "completed_stocks": completed,
        "verified_stocks": verified,
        "requested_stocks": len(stocks),
        "history_years": HISTORY_YEARS,
        "parallel_workers": MAX_WORKERS,
        "source": "NAVER Finance / WISEreport" if MODE != "weekly" else "OpenDART + NAVER 검증",
        "updated_at": datetime.now(KST).isoformat(),
    }
    payload.setdefault("methodology", {})["quality_value_policy"] = (
        "매일 네이버·WISEreport 자료로 EPS 성장률·영업현금흐름·ROE·부채비율·추정 PER을 선별하고, 매주 OpenDART 5개년 공식 재무로 검증합니다. 동일 업종 중앙값과 비교합니다."
    )
    payload["updated_at"] = datetime.now(KST).isoformat()
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Quality/value {MODE} complete: {completed}/{len(stocks)} stocks; verified={verified}")


if __name__ == "__main__":
    main()
