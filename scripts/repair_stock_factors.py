from __future__ import annotations

import io
import json
import os
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import requests
from pykrx import stock

KST = timezone(timedelta(hours=9))
DATA_FILE = Path("data/stock_data.json")
PREVIOUS_FILE = Path(os.getenv("PREVIOUS_STOCK_DATA", "/tmp/stock_data_previous.json"))
CACHE_DAYS = 7
HEADERS = {"User-Agent": "Mozilla/5.0 StockEconomyNews/1.0"}
VALID_OPINIONS = ("buy", "매수", "hold", "보유", "중립", "neutral", "sell", "매도")
INVALID_LABELS = {"목표주가", "투자의견", "컨센서스", "추정기관수", "기관수", "n/a", "-", ""}


def num(value: Any) -> float | None:
    if value is None:
        return None
    text = re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))
    if text in {"", "-", "."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / abs(previous) * 100, 2)


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


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def first_amount(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = num(row.get(key))
        if value is not None:
            return value
    return None


def get_corp_codes(api_key: str) -> dict[str, str]:
    response = requests.get(
        "https://opendart.fss.or.kr/api/corpCode.xml",
        params={"crtfc_key": api_key}, headers=HEADERS, timeout=(10, 45),
    )
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        xml_bytes = archive.read("CORPCODE.xml")
    root = ElementTree.fromstring(xml_bytes)
    return {
        (node.findtext("stock_code") or "").strip(): (node.findtext("corp_code") or "").strip()
        for node in root.findall("list")
        if (node.findtext("stock_code") or "").strip() and (node.findtext("corp_code") or "").strip()
    }


def refresh_dart(api_key: str, corp_code: str) -> dict[str, Any]:
    now = datetime.now(KST)
    aliases = {
        "revenue": {"매출액", "영업수익", "수익(매출액)", "보험영업수익"},
        "operating_profit": {"영업이익", "영업이익(손실)"},
        "net_income": {"당기순이익", "당기순이익(손실)", "연결당기순이익"},
        "assets": {"자산총계"}, "liabilities": {"부채총계"}, "equity": {"자본총계"},
    }
    current_keys = ("thstrm_q_amount", "thstrm_add_amount", "thstrm_amount")
    previous_keys = ("frmtrm_q_amount", "frmtrm_add_amount", "frmtrm_amount")
    for year in (now.year, now.year - 1, now.year - 2):
        for report_code in ("11014", "11012", "11013", "11011"):
            response = requests.get(
                "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                params={"crtfc_key": api_key, "corp_code": corp_code, "bsns_year": str(year),
                        "reprt_code": report_code, "fs_div": "CFS"},
                headers=HEADERS, timeout=(10, 40),
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") != "000":
                continue
            selected: dict[str, tuple[float | None, float | None]] = {}
            for row in payload.get("list", []):
                name = str(row.get("account_nm", "")).strip()
                statement = str(row.get("sj_nm", ""))
                for key, names in aliases.items():
                    if name not in names:
                        continue
                    current = first_amount(row, current_keys)
                    previous = first_amount(row, previous_keys)
                    quality = int(current is not None) + int(previous is not None) + int("연결" in statement)
                    old = selected.get(key)
                    old_quality = -1 if old is None else int(old[0] is not None) + int(old[1] is not None)
                    if quality > old_quality:
                        selected[key] = (current, previous)
            if not selected:
                continue
            result: dict[str, Any] = {
                "status": "ok", "business_year": year, "report_code": report_code,
                "report_name": {"11013": "1분기", "11012": "반기", "11014": "3분기", "11011": "사업보고서"}[report_code],
                "source": "OpenDART", "fetched_at": datetime.now(KST).isoformat(),
            }
            for key, pair in selected.items():
                result[key] = pair[0]
                result[f"{key}_growth_pct"] = pct(pair[0], pair[1])
            if result.get("liabilities") is not None and result.get("equity") not in (None, 0):
                result["debt_ratio_pct"] = round(result["liabilities"] / result["equity"] * 100, 2)
            return result
    return {"status": "unavailable", "reason": "최근 연결재무제표를 찾지 못했습니다.", "source": "OpenDART"}


def valid_consensus(value: dict[str, Any]) -> bool:
    if value.get("status") not in {"ok", "cached"}:
        return False
    target = num(value.get("target_price"))
    analysts = num(value.get("analyst_count"))
    opinion = str(value.get("opinion") or "").strip()
    opinion_ok = opinion.lower() not in INVALID_LABELS and any(term in opinion.lower() for term in VALID_OPINIONS)
    return bool((target and target > 0) or (analysts and analysts > 0) or opinion_ok)


def cached_consensus(previous: dict[str, Any], name: str) -> dict[str, Any] | None:
    value = previous.get("stocks", {}).get(name, {}).get("consensus", {})
    if not isinstance(value, dict) or not valid_consensus(value):
        return None
    fetched = parse_dt(value.get("fetched_at") or value.get("cached_from") or previous.get("updated_at"))
    if not fetched:
        return None
    age = datetime.now(KST) - fetched
    if age < timedelta(0) or age > timedelta(days=CACHE_DAYS):
        return None
    result = dict(value)
    result.update({
        "status": "cached", "cached_from": fetched.isoformat(),
        "cache_age_hours": round(age.total_seconds() / 3600, 1),
        "cache_expires_at": (fetched + timedelta(days=CACHE_DAYS)).isoformat(),
        "source": "FnGuide CompanyGuide (검증된 7일 캐시)",
    })
    return result


def latest_market_date(code: str) -> tuple[str, Any]:
    end = datetime.now(KST)
    start = end - timedelta(days=140)
    frame = stock.get_market_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), code)
    if frame is None or frame.empty:
        raise RuntimeError("가격 데이터 없음")
    return frame.index[-1].strftime("%Y%m%d"), frame


def valuation_for(date: str, code: str) -> dict[str, Any]:
    frame = stock.get_market_fundamental_by_ticker(date)
    if frame is None or frame.empty or code not in frame.index:
        frame = stock.get_market_fundamental_by_date(date, date, code)
        if frame is None or frame.empty:
            return {}
        row = frame.iloc[-1]
    else:
        row = frame.loc[code]
    values = {
        "per": num(row.get("PER")), "pbr": num(row.get("PBR")), "eps": num(row.get("EPS")),
        "bps": num(row.get("BPS")), "dividend_yield_pct": num(row.get("DIV")), "dps": num(row.get("DPS")),
    }
    return {key: value for key, value in values.items() if value is not None}


def investor_flow_for(date: str, code: str) -> dict[str, Any]:
    start = (datetime.strptime(date, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
    result: dict[str, Any] = {}
    try:
        frame = stock.get_market_trading_value_by_date(start, date, code)
        if frame is not None and not frame.empty:
            sums = frame.sum(numeric_only=True)
            aliases = {
                "foreign_net_buy_10d_krw": ("외국인합계", "외국인"),
                "institution_net_buy_10d_krw": ("기관합계", "기관"),
                "individual_net_buy_10d_krw": ("개인",),
            }
            for output, names in aliases.items():
                for name in names:
                    if name in sums.index:
                        result[output] = int(sums[name])
                        break
    except Exception:
        pass
    if result:
        return result
    try:
        frame = stock.get_market_trading_value_by_investor(start, date, code)
        if frame is not None and not frame.empty:
            for index, row in frame.iterrows():
                label = str(index)
                net = num(row.get("순매수"))
                if net is None:
                    continue
                if "외국인" in label:
                    result["foreign_net_buy_10d_krw"] = int(net)
                elif "기관" in label:
                    result["institution_net_buy_10d_krw"] = int(net)
                elif "개인" in label:
                    result["individual_net_buy_10d_krw"] = int(net)
    except Exception:
        pass
    return result


def refresh_market(existing: dict[str, Any], code: str) -> dict[str, Any]:
    date, ohlcv = latest_market_date(code)
    closes = ohlcv["종가"].astype(float)
    price = float(closes.iloc[-1])
    existing = dict(existing) if isinstance(existing, dict) else {}
    existing.update({
        "status": "ok", "as_of": date, "current_price": int(price),
        "return_5d_pct": pct(price, float(closes.iloc[-6])) if len(closes) >= 6 else None,
        "return_20d_pct": pct(price, float(closes.iloc[-21])) if len(closes) >= 21 else None,
        "return_60d_pct": pct(price, float(closes.iloc[-61])) if len(closes) >= 61 else None,
        "valuation": valuation_for(date, code), "investor_flow": investor_flow_for(date, code),
        "source": "KRX/pykrx", "fetched_at": datetime.now(KST).isoformat(),
    })
    return existing


def score(financials: dict[str, Any], market: dict[str, Any], consensus: dict[str, Any]) -> dict[str, Any]:
    c = {"financials": 0.0, "consensus": 0.0, "valuation": 0.0, "flow": 0.0, "momentum": 0.0}
    dimensions: list[str] = []
    if financials.get("status") == "ok":
        available = [financials.get("revenue_growth_pct"), financials.get("operating_profit_growth_pct"), financials.get("debt_ratio_pct")]
        if sum(value is not None for value in available) >= 2:
            dimensions.append("financials")
        op = financials.get("operating_profit_growth_pct")
        rev = financials.get("revenue_growth_pct")
        debt = financials.get("debt_ratio_pct")
        if op is not None: c["financials"] += 12 if op >= 15 else 6 if op > 0 else -12 if op <= -15 else -6
        if rev is not None: c["financials"] += 6 if rev >= 5 else 3 if rev > 0 else -6 if rev <= -5 else -3
        if debt is not None: c["financials"] += 3 if debt < 100 else -3 if debt > 200 else 0
    if valid_consensus(consensus):
        dimensions.append("consensus")
        upside = consensus.get("target_upside_pct")
        opinion = str(consensus.get("opinion") or "").lower()
        if upside is not None: c["consensus"] += 15 if upside >= 20 else 8 if upside >= 10 else -10 if upside <= -10 else -4 if upside < 0 else 2
        if "buy" in opinion or "매수" in opinion: c["consensus"] += 5
        elif "sell" in opinion or "매도" in opinion: c["consensus"] -= 8
    valuation = market.get("valuation", {}) if isinstance(market.get("valuation"), dict) else {}
    if any(valuation.get(key) is not None for key in ("per", "pbr")):
        dimensions.append("valuation")
        per, pbr = valuation.get("per"), valuation.get("pbr")
        if per is not None and per > 0: c["valuation"] += 5 if per <= 10 else 2 if per <= 20 else -4 if per >= 40 else 0
        if pbr is not None and pbr > 0: c["valuation"] += 4 if pbr <= 1 else 1 if pbr <= 2 else -3 if pbr >= 5 else 0
    flow = market.get("investor_flow", {}) if isinstance(market.get("investor_flow"), dict) else {}
    if any(flow.get(key) is not None for key in ("foreign_net_buy_10d_krw", "institution_net_buy_10d_krw")):
        dimensions.append("flow")
        for key in ("foreign_net_buy_10d_krw", "institution_net_buy_10d_krw"):
            value = flow.get(key)
            if value is not None: c["flow"] += 6 if value > 0 else -6 if value < 0 else 0
    if market.get("return_20d_pct") is not None or market.get("return_60d_pct") is not None:
        dimensions.append("momentum")
        r20, r60 = market.get("return_20d_pct"), market.get("return_60d_pct")
        if r20 is not None: c["momentum"] += 5 if 0 < r20 <= 15 else -4 if r20 < -10 else -3 if r20 > 25 else 0
        if r60 is not None: c["momentum"] += 3 if 0 < r60 <= 25 else -3 if r60 < -15 else -2 if r60 > 40 else 0
    total = round(sum(c.values()), 1)
    return {"score": total, "components": c, "available_dimensions": len(dimensions), "dimension_names": dimensions,
            "signal": "긍정" if total >= 15 else "부정" if total <= -15 else "중립"}


def main() -> None:
    payload = load_json(DATA_FILE)
    previous = load_json(PREVIOUS_FILE)
    stocks = payload.get("stocks", {})
    if not isinstance(stocks, dict):
        raise SystemExit("Invalid stock_data.json")
    errors: list[str] = []
    api_key = os.getenv("OPENDART_API_KEY", "").strip()
    corp_codes: dict[str, str] = {}
    if api_key:
        try:
            corp_codes = get_corp_codes(api_key)
        except Exception as exc:
            errors.append(f"OpenDART corp codes: {exc}")
    cached_count = 0
    for name, row in stocks.items():
        if not isinstance(row, dict):
            continue
        code = str(row.get("code", ""))
        try:
            row["market"] = refresh_market(row.get("market", {}), code)
        except Exception as exc:
            errors.append(f"{name} market repair: {exc}")
        if api_key and corp_codes.get(code):
            try:
                row["financials"] = refresh_dart(api_key, corp_codes[code])
            except Exception as exc:
                errors.append(f"{name} DART repair: {exc}")
        consensus = row.get("consensus", {}) if isinstance(row.get("consensus"), dict) else {}
        if not valid_consensus(consensus):
            cached = cached_consensus(previous, name)
            if cached:
                cached["live_collection_status"] = consensus.get("status", "invalid")
                cached["live_collection_reason"] = "FnGuide 응답값 유효성 검증 실패"
                row["consensus"] = cached
                cached_count += 1
            else:
                row["consensus"] = {"status": "unavailable", "reason": "FnGuide 유효 데이터 없음", "source": "FnGuide CompanyGuide"}
        row["quantitative"] = score(row.get("financials", {}), row.get("market", {}), row.get("consensus", {}))
    status = payload.setdefault("source_status", {})
    status["data_quality_repair"] = {"status": "ok", "fnguide_valid_cache_reused": cached_count,
                                     "errors": errors[-30:], "updated_at": datetime.now(KST).isoformat()}
    payload.setdefault("methodology", {})["dimension_policy"] = "재무·컨센서스·밸류에이션·수급·모멘텀을 실제 값 존재 여부로 각각 계산"
    payload["errors"] = list(dict.fromkeys([*(payload.get("errors", []) if isinstance(payload.get("errors"), list) else []), *errors]))[-80:]
    payload["updated_at"] = datetime.now(KST).isoformat()
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Stock factor repair complete: {len(stocks)} stocks, valid FnGuide cache reused={cached_count}, errors={len(errors)}")


if __name__ == "__main__":
    main()
