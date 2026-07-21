from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import collect_stock_data
from stock_universe import build_stock_universe

KST = timezone(timedelta(hours=9))
OUTPUT = Path("data/stock_data.json")
MAX_WORKERS = max(2, min(6, int(os.getenv("STOCK_COLLECT_WORKERS", "6"))))


def disabled_login_market_data() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "reason": "KRX 로그인 기반 수집은 사용하지 않으며 후속 Open API 단계에서 보완합니다.",
        "valuation": {},
        "investor_flow": {},
        "source": "KRX Open API 대기",
    }


def collect_one(
    name: str,
    meta: dict[str, Any],
    dart_key: str,
    corp_codes: dict[str, str],
    previous_payload: dict[str, Any],
) -> tuple[str, dict[str, Any], list[str], bool]:
    code = str(meta.get("code", ""))
    errors: list[str] = []
    row: dict[str, Any] = {
        "name": name,
        "code": code,
        "sector": meta.get("sector", ""),
        "market": disabled_login_market_data(),
    }

    corp_code = corp_codes.get(code)
    if dart_key and corp_code:
        try:
            row["financials"] = collect_stock_data.latest_dart_financials(dart_key, corp_code)
        except Exception as exc:
            row["financials"] = {"status": "failed", "reason": str(exc)[:180], "source": "OpenDART"}
            errors.append(f"{name} DART: {exc}")
    else:
        row["financials"] = {
            "status": "unavailable",
            "reason": "DART 고유번호 또는 API 키 없음",
            "source": "OpenDART",
        }

    live_consensus: dict[str, Any]
    try:
        live_consensus = collect_stock_data.collect_fnguide_consensus(code, None)
    except Exception as exc:
        live_consensus = {"status": "failed", "reason": str(exc)[:180], "source": "FnGuide CompanyGuide"}
        errors.append(f"{name} FnGuide: {exc}")

    cache_reused = False
    if live_consensus.get("status") == "ok":
        row["consensus"] = live_consensus
    else:
        cached = collect_stock_data.cached_fnguide_consensus(previous_payload, name)
        if cached:
            cached["live_collection_status"] = live_consensus.get("status", "failed")
            cached["live_collection_reason"] = str(live_consensus.get("reason", ""))[:180]
            row["consensus"] = cached
            cache_reused = True
        else:
            row["consensus"] = live_consensus

    row["quantitative"] = collect_stock_data.score_stock(
        row.get("financials", {}), row.get("market", {}), row.get("consensus", {})
    )
    return name, row, errors, cache_reused


def main() -> None:
    universe, universe_status = build_stock_universe()
    if not universe:
        raise SystemExit("추천 모니터링 종목을 구성하지 못했습니다.")

    previous_payload = collect_stock_data.load_previous_payload()
    dart_key = os.getenv("OPENDART_API_KEY", "").strip()
    corp_codes: dict[str, str] = {}
    errors: list[str] = []
    dart_status: dict[str, Any] = {
        "configured": bool(dart_key),
        "status": "pending" if dart_key else "missing",
        "corp_code_count": 0,
    }
    if dart_key:
        try:
            corp_codes = collect_stock_data.get_corp_codes(dart_key)
            dart_status.update({"status": "ok", "corp_code_count": len(corp_codes)})
        except Exception as exc:
            dart_status.update({"status": "failed", "reason": str(exc)[:180]})
            errors.append(f"OpenDART corpCode: {exc}")

    result: dict[str, Any] = {}
    cache_reused = 0
    print(f"Fast fixed watchlist collection: stocks={len(universe)}, workers={MAX_WORKERS}")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="stock") as executor:
        futures = {
            executor.submit(collect_one, name, meta, dart_key, corp_codes, previous_payload): name
            for name, meta in universe.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                _, row, row_errors, reused = future.result()
            except Exception as exc:
                meta = universe[name]
                row = {
                    "name": name,
                    "code": meta.get("code"),
                    "sector": meta.get("sector"),
                    "market": disabled_login_market_data(),
                    "financials": {"status": "failed", "reason": str(exc)[:180], "source": "OpenDART"},
                    "consensus": {"status": "failed", "reason": str(exc)[:180], "source": "FnGuide CompanyGuide"},
                    "quantitative": {"score": 0, "components": {}, "available_dimensions": 0, "signal": "중립"},
                }
                row_errors = [f"{name} collection: {exc}"]
                reused = False
            result[name] = row
            errors.extend(row_errors)
            cache_reused += int(reused)

    # 사용자가 지정한 순서를 JSON에서도 유지합니다.
    ordered_result: dict[str, Any] = {}
    for name, meta in universe.items():
        row = result[name]
        row["universe_tags"] = meta.get("universe_tags", [])
        row["watchlist_order"] = meta.get("watchlist_order")
        row["business_sector"] = meta.get("business_sector") or meta.get("sector")
        ordered_result[name] = row

    universe_status.update({
        "collection_mode": "bounded_parallel",
        "parallel_workers": MAX_WORKERS,
    })
    payload = {
        "updated_at": datetime.now(KST).isoformat(),
        "source_status": {
            "opendart": dart_status,
            "fnguide": {
                "mode": "optional_html_with_cache",
                "cache_days": collect_stock_data.FNGUIDE_CACHE_DAYS,
                "cached_stocks_reused": cache_reused,
                "parallel_workers": MAX_WORKERS,
            },
            "stock_universe": universe_status,
            "krx_login_collection": {
                "status": "disabled",
                "reason": "KRX_ID·KRX_PW 로그인 수집을 사용하지 않습니다.",
                "replacement": "KRX_API_KEY 기반 collect_krx_official.py",
            },
        },
        "methodology": {
            "description": "OpenDART 재무, FnGuide 컨센서스, KRX 시장정보를 결합한 보조 점수",
            "buy_review_threshold": 8,
            "sell_review_threshold": -8,
            "minimum_dimensions": 2,
            "fnguide_policy": "실패 시 이전 정상 데이터를 최대 7일간 재사용",
            "collection_efficiency": f"종목별 네트워크 요청을 최대 {MAX_WORKERS}개로 제한 병렬 처리",
        },
        "universe": {
            "policy": universe_status.get("policy"),
            "mode": universe_status.get("mode"),
            "stock_count": len(universe),
            "stocks": [
                {
                    "name": name,
                    "code": meta.get("code"),
                    "sector": meta.get("sector"),
                    "tags": meta.get("universe_tags", []),
                    "watchlist_order": meta.get("watchlist_order"),
                }
                for name, meta in universe.items()
            ],
        },
        "stocks": ordered_result,
        "errors": errors[:80],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(ordered_result)} stocks; FnGuide cache reused={cache_reused}; errors={len(errors)}")


if __name__ == "__main__":
    main()
