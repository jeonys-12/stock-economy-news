from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pykrx import stock

KST = timezone(timedelta(hours=9))
TOP_KOSPI_COUNT = 50
LOOKBACK_DAYS = 10

# 기존 핵심 종목은 시가총액 순위 변동과 관계없이 유지합니다.
CORE_STOCKS: dict[str, dict[str, str]] = {
    "삼성전자": {"code": "005930", "sector": "반도체"},
    "SK하이닉스": {"code": "000660", "sector": "반도체"},
    "현대차": {"code": "005380", "sector": "자동차"},
    "기아": {"code": "000270", "sector": "자동차"},
    "한화에어로스페이스": {"code": "012450", "sector": "방산"},
    "HD현대중공업": {"code": "329180", "sector": "조선"},
    "삼성중공업": {"code": "010140", "sector": "조선"},
    "POSCO홀딩스": {"code": "005490", "sector": "소재"},
    "LG에너지솔루션": {"code": "373220", "sector": "이차전지"},
    "삼성SDI": {"code": "006400", "sector": "이차전지"},
    "NAVER": {"code": "035420", "sector": "인터넷"},
    "카카오": {"code": "035720", "sector": "인터넷"},
    "KB금융": {"code": "105560", "sector": "금융"},
    "신한지주": {"code": "055550", "sector": "금융"},
    "현대건설": {"code": "000720", "sector": "건설"},
    "대우건설": {"code": "047040", "sector": "건설"},
}

# 사명 변경이나 데이터 제공 지연에 대비한 그룹 핵심 종목 보완 목록입니다.
GROUP_FALLBACKS: dict[str, dict[str, str]] = {
    "한화": {"code": "000880", "sector": "한화그룹"},
    "한화손해보험": {"code": "000370", "sector": "한화그룹"},
    "한화투자증권": {"code": "003530", "sector": "한화그룹"},
    "한화솔루션": {"code": "009830", "sector": "한화그룹"},
    "한화에어로스페이스": {"code": "012450", "sector": "한화그룹"},
    "한화오션": {"code": "042660", "sector": "한화그룹"},
    "한화생명": {"code": "088350", "sector": "한화그룹"},
    "한화시스템": {"code": "272210", "sector": "한화그룹"},
    "한화갤러리아": {"code": "452260", "sector": "한화그룹"},
    "LG": {"code": "003550", "sector": "LG그룹"},
    "LG이노텍": {"code": "011070", "sector": "LG그룹"},
    "LG디스플레이": {"code": "034220", "sector": "LG그룹"},
    "LG유플러스": {"code": "032640", "sector": "LG그룹"},
    "LG생활건강": {"code": "051900", "sector": "LG그룹"},
    "LG화학": {"code": "051910", "sector": "LG그룹"},
    "LG전자": {"code": "066570", "sector": "LG그룹"},
    "LG헬로비전": {"code": "037560", "sector": "LG그룹"},
    "LG에너지솔루션": {"code": "373220", "sector": "LG그룹"},
    "LG씨엔에스": {"code": "064400", "sector": "LG그룹"},
}


def _is_common_stock(name: str) -> bool:
    normalized = name.replace(" ", "")
    excluded_terms = ("스팩", "리츠", "인버스", "레버리지", "ETF", "ETN")
    if any(term.lower() in normalized.lower() for term in excluded_terms):
        return False
    # 우선주 표기는 대부분 '우', '우B', '우C'로 끝납니다.
    if normalized.endswith(("우", "우B", "우C", "1우", "2우B", "3우B")):
        return False
    return True


def _latest_kospi_market_cap() -> tuple[str, Any]:
    now = datetime.now(KST)
    for offset in range(LOOKBACK_DAYS + 1):
        date = (now - timedelta(days=offset)).strftime("%Y%m%d")
        try:
            frame = stock.get_market_cap_by_ticker(date, market="KOSPI")
        except Exception:
            continue
        if frame is not None and not frame.empty:
            return date, frame
    raise RuntimeError("최근 KOSPI 시가총액 데이터를 찾지 못했습니다.")


def build_stock_universe() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    universe: dict[str, dict[str, Any]] = {}
    status: dict[str, Any] = {
        "policy": "KOSPI 시가총액 상위 50개 보통주 + 한화그룹 + LG그룹 + 기존 핵심 종목",
        "top_kospi_target": TOP_KOSPI_COUNT,
        "market_cap_status": "pending",
    }

    try:
        as_of, market_cap = _latest_kospi_market_cap()
        cap_column = "시가총액" if "시가총액" in market_cap.columns else market_cap.columns[0]
        ranked = market_cap.sort_values(cap_column, ascending=False)
        rank = 0
        for code, row in ranked.iterrows():
            code = str(code).zfill(6)
            name = stock.get_market_ticker_name(code)
            if not name or not _is_common_stock(name):
                continue
            rank += 1
            universe[name] = {
                "code": code,
                "sector": "KOSPI 시총상위",
                "universe_tags": ["KOSPI_TOP50"],
                "market_cap_rank": rank,
                "market_cap_krw": int(row.get(cap_column, 0) or 0),
            }
            if rank >= TOP_KOSPI_COUNT:
                break
        status.update({
            "market_cap_status": "ok",
            "market_cap_as_of": as_of,
            "top_kospi_loaded": rank,
        })
    except Exception as exc:
        status.update({"market_cap_status": "failed", "reason": str(exc)[:240], "top_kospi_loaded": 0})

    # KOSPI 전체 종목명에서 한화·LG 계열 사명을 자동 탐지합니다.
    try:
        all_tickers = stock.get_market_ticker_list(market="KOSPI")
        for code in all_tickers:
            code = str(code).zfill(6)
            name = stock.get_market_ticker_name(code)
            if not name or not _is_common_stock(name):
                continue
            group = "한화그룹" if name.startswith("한화") else "LG그룹" if name == "LG" or name.startswith("LG") else None
            if not group:
                continue
            entry = universe.setdefault(name, {"code": code, "sector": group, "universe_tags": []})
            tags = entry.setdefault("universe_tags", [])
            tag = "HANWHA_GROUP" if group == "한화그룹" else "LG_GROUP"
            if tag not in tags:
                tags.append(tag)
            if entry.get("sector") == "KOSPI 시총상위":
                entry["group"] = group
            else:
                entry["sector"] = group
    except Exception as exc:
        status["group_scan_warning"] = str(exc)[:240]

    for name, meta in GROUP_FALLBACKS.items():
        entry = universe.setdefault(name, {"code": meta["code"], "sector": meta["sector"], "universe_tags": []})
        tags = entry.setdefault("universe_tags", [])
        tag = "HANWHA_GROUP" if meta["sector"] == "한화그룹" else "LG_GROUP"
        if tag not in tags:
            tags.append(tag)
        entry.setdefault("group", meta["sector"])

    for name, meta in CORE_STOCKS.items():
        entry = universe.setdefault(name, {"code": meta["code"], "sector": meta["sector"], "universe_tags": []})
        tags = entry.setdefault("universe_tags", [])
        if "CORE" not in tags:
            tags.append("CORE")
        if entry.get("sector") in {"KOSPI 시총상위", "한화그룹", "LG그룹"}:
            entry.setdefault("business_sector", meta["sector"])
        else:
            entry["sector"] = meta["sector"]

    status.update({
        "total_stocks": len(universe),
        "hanwha_group_count": sum("HANWHA_GROUP" in x.get("universe_tags", []) for x in universe.values()),
        "lg_group_count": sum("LG_GROUP" in x.get("universe_tags", []) for x in universe.values()),
        "core_count": sum("CORE" in x.get("universe_tags", []) for x in universe.values()),
    })
    return universe, status
