from __future__ import annotations

import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
DATA_FILE = Path("data/stock_data.json")
MAIN_URL = "https://finance.naver.com/item/main.naver"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Referer": "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
}
MAX_WORKERS = max(2, min(6, int(os.getenv("QUALITY_ANALYSIS_WORKERS", "5"))))
HISTORY_YEARS = 5


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


def normalize(value: str) -> str:
    return re.sub(r"[\s·()\[\]]", "", value).upper()


def parse_year(period: str) -> int | None:
    match = re.search(r"20\d{2}", period)
    return int(match.group()) if match else None


def is_estimate(period: str) -> bool:
    upper = period.upper().replace(" ", "")
    return "(E)" in upper or upper.endswith("E") or "E)" in upper


def fetch_main_html(code: str) -> tuple[str, str]:
    last_error: Exception | None = None
    with requests.Session() as session:
        for attempt in range(2):
            try:
                response = session.get(
                    MAIN_URL,
                    params={"code": code},
                    headers=HEADERS,
                    timeout=(8, 25),
                )
                response.raise_for_status()
                response.encoding = response.apparent_encoding or "euc-kr"
                return response.text, response.url
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(0.8)
    raise RuntimeError(f"네이버 종목 메인 페이지 요청 실패: {last_error}")


def table_score(table: Any) -> int:
    text = normalize(table.get_text(" ", strip=True))
    score = sum(4 for token in ("매출액", "영업이익", "EPS", "ROE", "부채비율", "PER") if token in text)
    score += min(10, len(re.findall(r"20\d{2}\.\d{2}", text)))
    if "최근연간실적" in text:
        score += 8
    if "최근분기실적" in text:
        score += 4
    return score


def select_financial_table(soup: BeautifulSoup) -> Any:
    candidates = soup.select("table")
    if not candidates:
        raise RuntimeError("종목 메인 페이지에서 기업실적분석 표를 찾지 못했습니다.")
    table = max(candidates, key=table_score)
    if table_score(table) < 30:
        raise RuntimeError("매출액·영업이익·EPS·ROE가 포함된 기업실적분석 표를 확인하지 못했습니다.")
    return table


def extract_periods(table: Any) -> list[str]:
    best: list[str] = []
    for row in table.select("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.select("th,td")]
        periods = [cell for cell in cells if re.search(r"20\d{2}\.\d{2}", cell)]
        if len(periods) > len(best):
            best = periods
    return best


def annual_column_count(table: Any, total_periods: int) -> int:
    for cell in table.select("th,td"):
        text = normalize(cell.get_text(" ", strip=True))
        if "최근연간실적" not in text:
            continue
        raw = cell.get("colspan")
        try:
            count = int(raw)
        except (TypeError, ValueError):
            count = 0
        if 2 <= count <= total_periods:
            return count
    # 네이버 표는 통상 연간 4열 + 분기 6열이다. 구조가 달라져도 첫 중복 연월 전까지를 연간으로 본다.
    return min(4, total_periods)


def find_row_values(table: Any, aliases: tuple[str, ...]) -> list[float | None]:
    normalized_aliases = tuple(normalize(alias) for alias in aliases)
    for row in table.select("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.select("th,td")]
        if len(cells) < 2:
            continue
        label = normalize(cells[0])
        if any(alias == label or alias in label for alias in normalized_aliases):
            return [num(cell) for cell in cells[1:]]
    return []


def align(values: list[float | None], total_periods: int) -> list[float | None]:
    if len(values) > total_periods:
        return values[-total_periods:]
    if len(values) < total_periods:
        return [None] * (total_periods - len(values)) + values
    return values


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


def pct_change(latest: float | None, previous: float | None) -> float | None:
    if latest is None or previous in (None, 0):
        return None
    return round((latest - previous) / abs(previous) * 100, 2)


def build_metrics(history: list[dict[str, Any]], current_price: float | None, forward_eps: float | None, displayed_forward_per: float | None) -> dict[str, Any]:
    history = sorted(history, key=lambda item: item["year"])
    eps_values = [(item["year"], num(item.get("eps"))) for item in history]
    op_values = [(item["year"], num(item.get("operating_profit"))) for item in history]
    roe_values = [num(item.get("roe_pct")) for item in history if num(item.get("roe_pct")) is not None]
    debt_values = [num(item.get("debt_ratio_pct")) for item in history if num(item.get("debt_ratio_pct")) is not None]
    latest_op = op_values[-1][1] if op_values else None
    previous_op = op_values[-2][1] if len(op_values) >= 2 else None
    calculated_forward_per = current_price / forward_eps if current_price and forward_eps and forward_eps > 0 else None
    forward_per = displayed_forward_per if displayed_forward_per and displayed_forward_per > 0 else calculated_forward_per
    return {
        "history_years": len(history),
        "eps_growth_cagr_pct": cagr(eps_values),
        "operating_profit_growth_cagr_pct": cagr(op_values),
        "latest_operating_profit_growth_pct": pct_change(latest_op, previous_op),
        "average_roe_pct": round(sum(roe_values) / len(roe_values), 2) if roe_values else None,
        "latest_roe_pct": round(roe_values[-1], 2) if roe_values else None,
        "latest_debt_ratio_pct": round(debt_values[-1], 2) if debt_values else None,
        "debt_ratio_change_pp": round(debt_values[-1] - debt_values[0], 2) if len(debt_values) >= 2 else None,
        "forward_eps": forward_eps,
        "forward_per": round(forward_per, 2) if forward_per is not None else None,
    }


def collect_one(name: str, row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    code = str(row.get("code", "")).strip()
    if len(code) != 6:
        return name, {"status": "failed", "reason": "유효한 6자리 종목코드가 없습니다.", "history": [], "metrics": {}}
    try:
        html, source_url = fetch_main_html(code)
        table = select_financial_table(BeautifulSoup(html, "html.parser"))
        periods = extract_periods(table)
        if not periods:
            raise RuntimeError("기업실적분석 기간 헤더를 찾지 못했습니다.")
        annual_count = annual_column_count(table, len(periods))
        annual_periods = periods[:annual_count]
        rows = {
            "revenue": find_row_values(table, ("매출액", "영업수익")),
            "operating_profit": find_row_values(table, ("영업이익",)),
            "net_income": find_row_values(table, ("당기순이익", "지배주주순이익")),
            "eps": find_row_values(table, ("EPS(원)", "EPS", "주당순이익")),
            "roe_pct": find_row_values(table, ("ROE(지배주주)", "ROE(%)", "ROE")),
            "debt_ratio_pct": find_row_values(table, ("부채비율(%)", "부채비율")),
            "per": find_row_values(table, ("PER(배)", "PER")),
        }
        annual_rows = {key: align(values, len(periods))[:annual_count] for key, values in rows.items()}
        history_by_year: dict[int, dict[str, Any]] = {}
        forward_candidates: list[tuple[int, float, str]] = []
        forward_per_candidates: list[tuple[int, float, str]] = []
        for key, values in annual_rows.items():
            for period, value in zip(annual_periods, values):
                year = parse_year(period)
                if year is None or value is None:
                    continue
                if is_estimate(period):
                    if key == "eps" and value > 0:
                        forward_candidates.append((year, value, period))
                    if key == "per" and value > 0:
                        forward_per_candidates.append((year, value, period))
                    continue
                history_by_year.setdefault(year, {"year": year})[key] = value
        history = sorted(history_by_year.values(), key=lambda item: item["year"])[-HISTORY_YEARS:]
        forward = max(forward_candidates, key=lambda item: item[0]) if forward_candidates else None
        forward_per_row = max(forward_per_candidates, key=lambda item: item[0]) if forward_per_candidates else None
        market = row.get("market", {}) if isinstance(row.get("market"), dict) else {}
        metrics = build_metrics(
            history,
            num(market.get("current_price")),
            forward[1] if forward else None,
            forward_per_row[1] if forward_per_row else None,
        )
        metrics["forward_eps_period"] = forward[2] if forward else None
        if len(history) < 3:
            raise RuntimeError(f"연간 확정 실적이 {len(history)}개년만 인식됐습니다: {annual_periods}")
        return name, {
            "status": "ok",
            "source": "NAVER Finance 종목 메인 기업실적분석",
            "source_url": source_url,
            "collection_mode": "main_page_financial_summary",
            "annual_column_count": annual_count,
            "annual_periods": annual_periods,
            "history": history,
            "metrics": metrics,
            "forecast_status": {
                "status": "ok" if forward else "unavailable",
                "forward_eps": forward[1] if forward else None,
                "period": forward[2] if forward else None,
                "source": "NAVER Finance 기업실적분석 컨센서스(E)",
            },
            "updated_at": datetime.now(KST).isoformat(),
        }
    except Exception as exc:
        previous = row.get("quality_value_analysis", {}) if isinstance(row.get("quality_value_analysis"), dict) else {}
        if previous.get("status") in {"ok", "partial", "cached"}:
            cached = dict(previous)
            cached.update({
                "status": "cached",
                "live_collection_status": "failed",
                "live_collection_reason": str(exc)[:300],
                "source": f"{previous.get('source', 'NAVER Finance 기업실적분석')} (이전 정상 캐시)",
            })
            return name, cached
        return name, {
            "status": "failed",
            "reason": str(exc)[:300],
            "history": [],
            "metrics": {},
            "source": "NAVER Finance 종목 메인 기업실적분석",
            "updated_at": datetime.now(KST).isoformat(),
        }


def peer_medians(stocks: dict[str, dict[str, Any]]) -> dict[str, dict[str, float]]:
    keys = ("eps_growth_cagr_pct", "operating_profit_growth_cagr_pct", "average_roe_pct", "latest_debt_ratio_pct", "forward_per")
    sectors: dict[str, dict[str, list[float]]] = {}
    for row in stocks.values():
        sector = str(row.get("business_sector") or row.get("sector") or "기타")
        bucket = sectors.setdefault(sector, {key: [] for key in keys})
        metrics = row.get("quality_value_analysis", {}).get("metrics", {})
        for key in keys:
            value = num(metrics.get(key))
            if value is not None:
                bucket[key].append(value)
    return {sector: {key: round(median(values), 2) for key, values in bucket.items() if values} for sector, bucket in sectors.items()}


def score_analysis(metrics: dict[str, Any], peers: dict[str, float]) -> tuple[int, dict[str, int], list[str]]:
    components = {"eps_growth": 0, "operating_profit": 0, "roe": 0, "financial_safety": 0, "valuation": 0}
    reasons: list[str] = []
    eps = num(metrics.get("eps_growth_cagr_pct")); peer_eps = num(peers.get("eps_growth_cagr_pct"))
    if eps is not None:
        components["eps_growth"] = 25 if eps >= 15 and (peer_eps is None or eps >= peer_eps) else 19 if eps > 5 else 11 if eps > 0 else 2
        reasons.append(f"EPS 3~5년 성장률 {eps:.1f}%")
    op = num(metrics.get("operating_profit_growth_cagr_pct")); peer_op = num(peers.get("operating_profit_growth_cagr_pct"))
    if op is not None:
        components["operating_profit"] = 20 if op >= 12 and (peer_op is None or op >= peer_op) else 15 if op > 5 else 8 if op > 0 else 1
        reasons.append(f"영업이익 3~5년 성장률 {op:.1f}%")
    roe = num(metrics.get("average_roe_pct")); peer_roe = num(peers.get("average_roe_pct"))
    if roe is not None:
        components["roe"] = 20 if roe >= 15 and (peer_roe is None or roe >= peer_roe) else 15 if roe >= 10 else 9 if roe >= 5 else 0
        reasons.append(f"평균 ROE {roe:.1f}%")
    debt = num(metrics.get("latest_debt_ratio_pct")); change = num(metrics.get("debt_ratio_change_pp")); peer_debt = num(peers.get("latest_debt_ratio_pct"))
    if debt is not None:
        base = 20 if debt < 100 else 14 if debt < 150 else 7 if debt < 250 else 0
        if peer_debt is not None and debt > peer_debt * 1.5:
            base = max(0, base - 4)
        if change is not None and change > 30:
            base = max(0, base - 4)
        components["financial_safety"] = base
        reasons.append(f"부채비율 {debt:.1f}%")
    fper = num(metrics.get("forward_per")); peer_per = num(peers.get("forward_per"))
    if fper is not None and fper > 0:
        components["valuation"] = 15 if peer_per and fper <= peer_per * 0.85 else 12 if fper <= 12 else 8 if fper <= 20 else 3 if fper <= 35 else 0
        reasons.append(f"추정 PER {fper:.1f}배")
    return sum(components.values()), components, reasons


def main() -> None:
    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    stocks = payload.get("stocks", {})
    if not isinstance(stocks, dict) or not stocks:
        raise SystemExit("stock_data.json에 종목 데이터가 없습니다.")
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="naver-main") as executor:
        futures = {executor.submit(collect_one, name, row): name for name, row in stocks.items() if isinstance(row, dict)}
        for future in as_completed(futures):
            name = futures[future]
            try:
                _, analysis = future.result()
            except Exception as exc:
                analysis = {"status": "failed", "reason": str(exc)[:300], "history": [], "metrics": {}}
            results[name] = analysis
    for name, analysis in results.items():
        stocks[name]["quality_value_analysis"] = analysis
    medians = peer_medians(stocks)
    completed = cached = failed = 0
    failure_samples: list[str] = []
    for name, row in stocks.items():
        analysis = row.get("quality_value_analysis", {})
        metrics = analysis.get("metrics", {})
        sector = str(row.get("business_sector") or row.get("sector") or "기타")
        score, components, reasons = score_analysis(metrics, medians.get(sector, {}))
        analysis.update({
            "score": score,
            "grade": "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 50 else "D",
            "components": components,
            "peer_sector": sector,
            "peer_medians": medians.get(sector, {}),
            "reasons": reasons,
        })
        completed += int(analysis.get("status") == "ok")
        cached += int(analysis.get("status") == "cached")
        failed += int(analysis.get("status") == "failed")
        if analysis.get("status") == "failed" and len(failure_samples) < 10:
            failure_samples.append(f"{name}: {analysis.get('reason', '원인 없음')}")
        row["quantitative"] = {
            "score": round((score - 50) * 0.6, 1),
            "components": {"quality_value": round((score - 50) * 0.6, 1)},
            "quality_value_score": score,
            "available_dimensions": 1 if metrics.get("history_years", 0) >= 3 else 0,
            "available_dimension_names": ["quality_value"] if metrics.get("history_years", 0) >= 3 else [],
            "signal": "긍정" if score >= 65 else "부정" if score < 40 else "중립",
        }
    payload.setdefault("source_status", {})["quality_value_analysis"] = {
        "status": "ok" if completed else "partial",
        "completed_stocks": completed,
        "cached_stocks": cached,
        "failed_stocks": failed,
        "requested_stocks": len(stocks),
        "source": "NAVER Finance 종목 메인 기업실적분석",
        "failure_samples": failure_samples,
        "excluded_sources": ["OpenDART", "KRX OPEN API", "FnGuide 별도 호출"],
        "updated_at": datetime.now(KST).isoformat(),
    }
    payload.setdefault("methodology", {})["quality_value_policy"] = (
        "네이버 종목 메인 페이지 기업실적분석의 연간 열에서 EPS·영업이익·ROE·부채비율·예상 EPS를 추출해 최근 3~5년 추세와 업종 중앙값을 비교합니다."
    )
    payload["updated_at"] = datetime.now(KST).isoformat()
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"NAVER main financial analysis complete: live={completed}, cached={cached}, failed={failed}, total={len(stocks)}")


if __name__ == "__main__":
    main()
