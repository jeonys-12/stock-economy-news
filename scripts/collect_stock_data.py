from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup
from pykrx import stock

KST = timezone(timedelta(hours=9))
OUTPUT = Path("data/stock_data.json")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; StockEconomyNewsBot/1.0; +https://github.com/jeonys-12/stock-economy-news)"
}

STOCKS: dict[str, dict[str, str]] = {
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


def number(value: Any) -> float | None:
    if value is None:
        return None
    text = re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))
    if text in {"", "-", "."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / abs(previous) * 100, 2)


def safe_round(value: Any, digits: int = 2) -> float | None:
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def get_corp_codes(api_key: str) -> dict[str, str]:
    response = requests.get(
        "https://opendart.fss.or.kr/api/corpCode.xml",
        params={"crtfc_key": api_key},
        headers=HEADERS,
        timeout=45,
    )
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        xml_bytes = archive.read("CORPCODE.xml")
    root = ElementTree.fromstring(xml_bytes)
    result: dict[str, str] = {}
    for node in root.findall("list"):
        stock_code = (node.findtext("stock_code") or "").strip()
        corp_code = (node.findtext("corp_code") or "").strip()
        if stock_code and corp_code:
            result[stock_code] = corp_code
    return result


def latest_dart_financials(api_key: str, corp_code: str) -> dict[str, Any]:
    now = datetime.now(KST)
    candidates: list[tuple[int, str]] = []
    for year in (now.year, now.year - 1, now.year - 2):
        for report_code in ("11014", "11012", "11013", "11011"):
            candidates.append((year, report_code))

    aliases = {
        "revenue": {"매출액", "영업수익", "수익(매출액)", "보험영업수익"},
        "operating_profit": {"영업이익", "영업이익(손실)"},
        "net_income": {"당기순이익", "당기순이익(손실)", "연결당기순이익"},
        "assets": {"자산총계"},
        "liabilities": {"부채총계"},
        "equity": {"자본총계"},
    }

    for year, report_code in candidates:
        params = {
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": report_code,
            "fs_div": "CFS",
        }
        response = requests.get(
            "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
            params=params,
            headers=HEADERS,
            timeout=35,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "000":
            continue

        values: dict[str, dict[str, float | None]] = {}
        for row in payload.get("list", []):
            account_name = str(row.get("account_nm", "")).strip()
            for key, names in aliases.items():
                if account_name in names and key not in values:
                    values[key] = {
                        "current": number(row.get("thstrm_amount")),
                        "previous": number(row.get("frmtrm_amount")),
                    }
        if not values:
            continue

        result: dict[str, Any] = {
            "status": "ok",
            "business_year": year,
            "report_code": report_code,
            "report_name": {"11013": "1분기", "11012": "반기", "11014": "3분기", "11011": "사업보고서"}.get(report_code, report_code),
            "source": "OpenDART",
        }
        for key, pair in values.items():
            result[key] = pair["current"]
            result[f"{key}_growth_pct"] = pct_change(pair["current"], pair["previous"])
        liabilities = result.get("liabilities")
        equity = result.get("equity")
        if liabilities is not None and equity not in (None, 0):
            result["debt_ratio_pct"] = round(liabilities / equity * 100, 2)
        return result

    return {"status": "unavailable", "reason": "최근 연결재무제표를 찾지 못했습니다.", "source": "OpenDART"}


def latest_market_date(code: str) -> tuple[str, Any]:
    end = datetime.now(KST)
    start = end - timedelta(days=120)
    frame = stock.get_market_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), code)
    if frame is None or frame.empty:
        raise RuntimeError("KRX 가격 데이터 없음")
    return frame.index[-1].strftime("%Y%m%d"), frame


def collect_market_data(code: str) -> dict[str, Any]:
    latest_date, ohlcv = latest_market_date(code)
    closes = ohlcv["종가"].astype(float)
    current_price = float(closes.iloc[-1])
    return_5d = pct_change(current_price, float(closes.iloc[-6])) if len(closes) >= 6 else None
    return_20d = pct_change(current_price, float(closes.iloc[-21])) if len(closes) >= 21 else None
    return_60d = pct_change(current_price, float(closes.iloc[-61])) if len(closes) >= 61 else None

    fundamentals = stock.get_market_fundamental_by_date(latest_date, latest_date, code)
    valuation: dict[str, Any] = {}
    if fundamentals is not None and not fundamentals.empty:
        row = fundamentals.iloc[-1]
        valuation = {
            "per": safe_round(row.get("PER")),
            "pbr": safe_round(row.get("PBR")),
            "eps": safe_round(row.get("EPS"), 0),
            "bps": safe_round(row.get("BPS"), 0),
            "dividend_yield_pct": safe_round(row.get("DIV")),
            "dps": safe_round(row.get("DPS"), 0),
        }

    start_flow = (datetime.strptime(latest_date, "%Y%m%d") - timedelta(days=14)).strftime("%Y%m%d")
    flows = stock.get_market_trading_value_by_date(start_flow, latest_date, code)
    investor_flow: dict[str, Any] = {}
    if flows is not None and not flows.empty:
        sums = flows.sum(numeric_only=True)
        investor_flow = {
            "foreign_net_buy_10d_krw": int(sums.get("외국인합계", 0)),
            "institution_net_buy_10d_krw": int(sums.get("기관합계", 0)),
            "individual_net_buy_10d_krw": int(sums.get("개인", 0)),
        }

    return {
        "status": "ok",
        "as_of": latest_date,
        "current_price": int(current_price),
        "return_5d_pct": return_5d,
        "return_20d_pct": return_20d,
        "return_60d_pct": return_60d,
        "valuation": valuation,
        "investor_flow": investor_flow,
        "source": "KRX/pykrx",
    }


def find_row_value(soup: BeautifulSoup, labels: tuple[str, ...]) -> str | None:
    for row in soup.select("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.select("th,td")]
        if not cells:
            continue
        joined = " ".join(cells)
        if any(label in joined for label in labels):
            for cell in cells[1:]:
                if cell and cell not in {"-", "N/A"}:
                    return cell
    return None


def collect_fnguide_consensus(code: str, current_price: int | None) -> dict[str, Any]:
    url = "https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp"
    params = {"pGB": "1", "gicode": f"A{code}", "cID": "", "MenuYn": "Y", "ReportGB": "", "NewMenuID": "101", "stkGb": "701"}
    response = requests.get(url, params=params, headers=HEADERS, timeout=35)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    target_text = find_row_value(soup, ("목표주가",))
    opinion = find_row_value(soup, ("투자의견", "컨센서스"))
    analyst_text = find_row_value(soup, ("추정기관수", "기관수"))
    target_price = number(target_text)
    upside = pct_change(target_price, float(current_price)) if target_price and current_price else None

    if target_price is None and not opinion:
        return {"status": "unavailable", "reason": "FnGuide 공개 화면에서 컨센서스를 확인하지 못했습니다.", "source": "FnGuide CompanyGuide"}
    return {
        "status": "ok",
        "target_price": int(target_price) if target_price is not None else None,
        "target_upside_pct": upside,
        "opinion": opinion,
        "analyst_count": int(number(analyst_text) or 0) or None,
        "source": "FnGuide CompanyGuide",
        "source_url": response.url,
    }


def score_stock(financials: dict[str, Any], market: dict[str, Any], consensus: dict[str, Any]) -> dict[str, Any]:
    score = 0.0
    components: dict[str, float] = {"financials": 0, "consensus": 0, "valuation": 0, "flow": 0, "momentum": 0}
    available = 0

    if financials.get("status") == "ok":
        available += 1
        op_growth = financials.get("operating_profit_growth_pct")
        revenue_growth = financials.get("revenue_growth_pct")
        debt_ratio = financials.get("debt_ratio_pct")
        if op_growth is not None:
            components["financials"] += 12 if op_growth >= 15 else 6 if op_growth > 0 else -12 if op_growth <= -15 else -6
        if revenue_growth is not None:
            components["financials"] += 6 if revenue_growth >= 5 else 3 if revenue_growth > 0 else -6 if revenue_growth <= -5 else -3
        if debt_ratio is not None:
            components["financials"] += 3 if debt_ratio < 100 else -3 if debt_ratio > 200 else 0

    if consensus.get("status") == "ok":
        available += 1
        upside = consensus.get("target_upside_pct")
        if upside is not None:
            components["consensus"] += 15 if upside >= 20 else 8 if upside >= 10 else -10 if upside <= -10 else -4 if upside < 0 else 2
        opinion = str(consensus.get("opinion") or "").lower()
        if any(term in opinion for term in ("buy", "매수")):
            components["consensus"] += 5
        elif any(term in opinion for term in ("sell", "매도")):
            components["consensus"] -= 8

    if market.get("status") == "ok":
        available += 1
        valuation = market.get("valuation", {})
        per = valuation.get("per")
        pbr = valuation.get("pbr")
        if per is not None and per > 0:
            components["valuation"] += 5 if per <= 10 else 2 if per <= 20 else -4 if per >= 40 else 0
        if pbr is not None and pbr > 0:
            components["valuation"] += 4 if pbr <= 1 else 1 if pbr <= 2 else -3 if pbr >= 5 else 0

        flow = market.get("investor_flow", {})
        foreign = flow.get("foreign_net_buy_10d_krw")
        institution = flow.get("institution_net_buy_10d_krw")
        if foreign is not None:
            components["flow"] += 6 if foreign > 0 else -6 if foreign < 0 else 0
        if institution is not None:
            components["flow"] += 6 if institution > 0 else -6 if institution < 0 else 0

        r20 = market.get("return_20d_pct")
        r60 = market.get("return_60d_pct")
        if r20 is not None:
            components["momentum"] += 5 if 0 < r20 <= 15 else -4 if r20 < -10 else -3 if r20 > 25 else 0
        if r60 is not None:
            components["momentum"] += 3 if 0 < r60 <= 25 else -3 if r60 < -15 else -2 if r60 > 40 else 0

    score = round(sum(components.values()), 1)
    return {
        "score": score,
        "components": components,
        "available_dimensions": available,
        "signal": "긍정" if score >= 15 else "부정" if score <= -15 else "중립",
    }


def main() -> None:
    dart_key = os.getenv("OPENDART_API_KEY", "").strip()
    corp_codes: dict[str, str] = {}
    errors: list[str] = []
    if dart_key:
        try:
            corp_codes = get_corp_codes(dart_key)
        except Exception as exc:
            errors.append(f"OpenDART corpCode: {exc}")
    else:
        errors.append("OPENDART_API_KEY 미설정")

    result: dict[str, Any] = {}
    for name, meta in STOCKS.items():
        code = meta["code"]
        row: dict[str, Any] = {"name": name, "code": code, "sector": meta["sector"]}

        try:
            row["market"] = collect_market_data(code)
        except Exception as exc:
            row["market"] = {"status": "failed", "reason": str(exc)[:180], "source": "KRX/pykrx"}
            errors.append(f"{name} market: {exc}")

        try:
            corp_code = corp_codes.get(code)
            row["financials"] = latest_dart_financials(dart_key, corp_code) if dart_key and corp_code else {
                "status": "unavailable", "reason": "DART 고유번호 또는 API 키 없음", "source": "OpenDART"
            }
        except Exception as exc:
            row["financials"] = {"status": "failed", "reason": str(exc)[:180], "source": "OpenDART"}
            errors.append(f"{name} DART: {exc}")

        try:
            current_price = row.get("market", {}).get("current_price")
            row["consensus"] = collect_fnguide_consensus(code, current_price)
        except Exception as exc:
            row["consensus"] = {"status": "failed", "reason": str(exc)[:180], "source": "FnGuide CompanyGuide"}
            errors.append(f"{name} FnGuide: {exc}")

        row["quantitative"] = score_stock(row["financials"], row["market"], row["consensus"])
        result[name] = row
        print(f"{name}: score={row['quantitative']['score']} dimensions={row['quantitative']['available_dimensions']}")
        time.sleep(0.25)

    payload = {
        "updated_at": datetime.now(KST).isoformat(),
        "methodology": {
            "description": "OpenDART 재무, FnGuide 공개 컨센서스, KRX 가격·밸류에이션·외국인/기관 수급을 결합한 보조 점수",
            "buy_review_threshold": 15,
            "sell_review_threshold": -15,
            "minimum_dimensions": 2,
            "notice": "업종별 적정 밸류에이션 차이를 완전히 반영하지 못하므로 최종 매매 판단이 아닙니다.",
        },
        "stocks": result,
        "errors": errors[:80],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved stock factors for {len(result)} stocks to {OUTPUT}")


if __name__ == "__main__":
    main()
