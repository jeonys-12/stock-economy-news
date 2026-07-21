from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

import analyze_naver_financial_summary as base


def period_month(period: str) -> str | None:
    match = re.search(r"20\d{2}\.(\d{2})", period)
    return match.group(1) if match else None


def corrected_annual_column_count(table: Any, periods: list[str]) -> int:
    """네이버 표의 연간 구간을 실제 기간 패턴으로 판정한다.

    일부 표는 '최근 연간 실적' colspan에 좌측 항목명 열까지 포함해
    실제 연간 열보다 1 크게 표시한다. 첫 연간 기간과 동일한 결산월이
    연속되는 구간을 우선 사용해 첫 분기 열이 연간으로 섞이지 않게 한다.
    """
    if not periods:
        return 0

    first_month = period_month(periods[0])
    inferred = 0
    if first_month:
        for period in periods:
            if period_month(period) != first_month:
                break
            inferred += 1

    structural = 0
    for cell in table.select("th,td"):
        if "최근연간실적" not in base.normalize(cell.get_text(" ", strip=True)):
            continue
        try:
            structural = int(cell.get("colspan") or 0)
        except (TypeError, ValueError):
            structural = 0
        break

    if inferred >= 2:
        return min(inferred, len(periods))
    if 2 <= structural <= len(periods):
        return structural
    return min(4, len(periods))


def collect_one(name: str, row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    code = str(row.get("code", "")).strip()
    if len(code) != 6:
        return name, {"status": "failed", "reason": "유효한 6자리 종목코드가 없습니다.", "history": [], "metrics": {}}

    try:
        html, source_url = base.fetch_main_html(code)
        table = base.select_financial_table(BeautifulSoup(html, "html.parser"))
        periods = base.extract_periods(table)
        if not periods:
            raise RuntimeError("기업실적분석 기간 헤더를 찾지 못했습니다.")

        annual_count = corrected_annual_column_count(table, periods)
        annual_periods = periods[:annual_count]
        rows = {
            "revenue": base.find_row_values(table, ("매출액", "영업수익")),
            "operating_profit": base.find_row_values(table, ("영업이익",)),
            "net_income": base.find_row_values(table, ("당기순이익", "지배주주순이익")),
            "eps": base.find_row_values(table, ("EPS(원)", "EPS", "주당순이익")),
            "roe_pct": base.find_row_values(table, ("ROE(지배주주)", "ROE(%)", "ROE")),
            "debt_ratio_pct": base.find_row_values(table, ("부채비율(%)", "부채비율")),
            "per": base.find_row_values(table, ("PER(배)", "PER")),
        }
        annual_rows = {
            key: base.align(values, len(periods))[:annual_count]
            for key, values in rows.items()
        }

        history_by_year: dict[int, dict[str, Any]] = {}
        forward_candidates: list[tuple[int, float, str]] = []
        forward_per_candidates: list[tuple[int, float, str]] = []
        for key, values in annual_rows.items():
            for period, value in zip(annual_periods, values):
                year = base.parse_year(period)
                if year is None or value is None:
                    continue
                if base.is_estimate(period):
                    if key == "eps" and value > 0:
                        forward_candidates.append((year, value, period))
                    if key == "per" and value > 0:
                        forward_per_candidates.append((year, value, period))
                    continue
                history_by_year.setdefault(year, {"year": year})[key] = value

        history = sorted(history_by_year.values(), key=lambda item: item["year"])[-base.HISTORY_YEARS:]
        if len(history) < 2:
            raise RuntimeError(f"연간 확정 실적이 {len(history)}개년만 인식됐습니다: {annual_periods}")

        forward = max(forward_candidates, key=lambda item: item[0]) if forward_candidates else None
        forward_per_row = max(forward_per_candidates, key=lambda item: item[0]) if forward_per_candidates else None
        market = row.get("market", {}) if isinstance(row.get("market"), dict) else {}
        metrics = base.build_metrics(
            history,
            base.num(market.get("current_price")),
            forward[1] if forward else None,
            forward_per_row[1] if forward_per_row else None,
        )
        metrics["forward_eps_period"] = forward[2] if forward else None

        status = "ok" if len(history) >= 3 else "partial"
        result = {
            "status": status,
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
            "updated_at": datetime.now(base.KST).isoformat(),
        }
        if status == "partial":
            result["reason"] = (
                f"설립·분할 또는 상장 이력으로 확정 연간 실적이 {len(history)}개년만 제공됩니다. "
                "확보된 기간으로 성장률을 계산하며 3~5년 장기 추세 평가는 제한됩니다."
            )
        return name, result

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
            "updated_at": datetime.now(base.KST).isoformat(),
        }


def promote_partial_results() -> None:
    """2개년 데이터도 제한적이지만 유효한 분석 축으로 표시한다."""
    payload = json.loads(base.DATA_FILE.read_text(encoding="utf-8"))
    partial_count = 0
    for row in payload.get("stocks", {}).values():
        analysis = row.get("quality_value_analysis", {})
        metrics = analysis.get("metrics", {})
        if analysis.get("status") != "partial" or metrics.get("history_years", 0) < 2:
            continue
        partial_count += 1
        quantitative = row.setdefault("quantitative", {})
        quantitative["available_dimensions"] = 1
        quantitative["available_dimension_names"] = ["quality_value"]

    source = payload.setdefault("source_status", {}).setdefault("quality_value_analysis", {})
    source["partial_stocks"] = partial_count
    source["minimum_history_years"] = 2
    source["history_policy"] = "일반 종목은 3~5년, 설립·분할·신규상장 종목은 확보 가능한 2개년을 제한적으로 사용"
    payload.setdefault("methodology", {})["quality_value_policy"] = (
        "네이버 기업실적분석의 연간 열에서 EPS·영업이익·ROE·부채비율·예상 EPS를 추출합니다. "
        "일반 종목은 최근 3~5년을 비교하고, 설립·분할·신규상장 종목은 2개년 자료를 제한적으로 인정합니다."
    )
    base.DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    base.collect_one = collect_one
    base.main()
    promote_partial_results()
