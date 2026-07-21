from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

import repair_stock_factors

KST = timezone(timedelta(hours=9))
DATA_FILE = Path("data/stock_data.json")
PREVIOUS_FILE = Path(os.getenv("PREVIOUS_STOCK_DATA", "/tmp/stock_data_previous.json"))
CACHE_DAYS = 3
TIMEOUT = (10, 30)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Referer": "https://finance.naver.com/",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
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


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def fetch(session: requests.Session, url: str) -> str:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = session.get(url, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "euc-kr"
            return response.text
        except Exception as exc:
            last_error = exc
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(str(last_error)[:300])


def collect_valuation(session: requests.Session, code: str) -> dict[str, Any]:
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    html = fetch(session, url)
    soup = BeautifulSoup(html, "html.parser")
    selectors = {"per": "#_per", "pbr": "#_pbr", "eps": "#_eps", "bps": "#_bps"}
    result: dict[str, Any] = {}
    for key, selector in selectors.items():
        node = soup.select_one(selector)
        value = number(node.get_text(" ", strip=True)) if node else None
        if value is not None:
            result[key] = value
    if not result.get("per") or not result.get("pbr"):
        text = soup.get_text(" ", strip=True)
        patterns = {"per": r"PER(?:lEPS)?\s*([0-9,.\-]+)", "pbr": r"PBR(?:lBPS)?\s*([0-9,.\-]+)"}
        for key, pattern in patterns.items():
            if key in result:
                continue
            match = re.search(pattern, text, re.IGNORECASE)
            value = number(match.group(1)) if match else None
            if value is not None:
                result[key] = value
    if not any(result.get(key) is not None for key in ("per", "pbr", "eps", "bps")):
        raise RuntimeError("공개 시세 페이지에서 PER·PBR 값을 찾지 못했습니다.")
    result.update({"status": "ok", "source": "NAVER Finance 공개 시세 페이지", "fetched_at": datetime.now(KST).isoformat()})
    return result


def flatten_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [" ".join(str(part) for part in column if str(part) != "nan").strip() for column in frame.columns]
    else:
        frame.columns = [str(column).strip() for column in frame.columns]
    return frame


def find_column(columns: list[str], aliases: tuple[str, ...]) -> str | None:
    normalized_columns = [(column, re.sub(r"\s+", "", column)) for column in columns]
    for alias in aliases:
        for column, normalized in normalized_columns:
            if alias in normalized:
                return column
    return None


def collect_investor_flow(session: requests.Session, code: str) -> dict[str, Any]:
    url = f"https://finance.naver.com/item/frgn.naver?code={code}&page=1"
    html = fetch(session, url)
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError as exc:
        raise RuntimeError("투자자별 거래 표를 찾지 못했습니다.") from exc
    selected: pd.DataFrame | None = None
    institution_col: str | None = None
    foreign_col: str | None = None
    date_col: str | None = None
    for raw in tables:
        frame = flatten_columns(raw.copy())
        columns = list(frame.columns)
        inst = find_column(columns, ("기관순매매량", "기관"))
        foreign = find_column(columns, ("외국인순매매량", "외국인"))
        date = find_column(columns, ("날짜", "일자"))
        if inst and foreign and date:
            selected, institution_col, foreign_col, date_col = frame, inst, foreign, date
            break
    if selected is None or not institution_col or not foreign_col or not date_col:
        raise RuntimeError("공개 시세 페이지의 기관·외국인 컬럼 구조를 확인하지 못했습니다.")
    selected = selected[selected[date_col].astype(str).str.match(r"\d{4}\.\d{2}\.\d{2}", na=False)].head(10)
    if selected.empty:
        raise RuntimeError("최근 투자자별 거래 데이터가 없습니다.")
    institution_series = pd.to_numeric(selected[institution_col].astype(str).str.replace(",", "", regex=False), errors="coerce")
    foreign_series = pd.to_numeric(selected[foreign_col].astype(str).str.replace(",", "", regex=False), errors="coerce")
    if institution_series.notna().sum() == 0 and foreign_series.notna().sum() == 0:
        raise RuntimeError("기관·외국인 순매매 값을 숫자로 변환하지 못했습니다.")
    return {
        "institution_net_buy_10d_krw": int(institution_series.fillna(0).sum()),
        "foreign_net_buy_10d_krw": int(foreign_series.fillna(0).sum()),
        "unit": "shares",
        "trading_days": int(len(selected)),
        "status": "ok",
        "source": "NAVER Finance 공개 투자자별 매매 페이지",
        "fetched_at": datetime.now(KST).isoformat(),
    }


def parse_dt(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(KST)
    except (TypeError, ValueError):
        return None


def cached_value(previous: dict[str, Any], name: str, key: str) -> dict[str, Any] | None:
    market = previous.get("stocks", {}).get(name, {}).get("market", {})
    value = market.get(key, {}) if isinstance(market, dict) else {}
    if not isinstance(value, dict) or value.get("status") not in {"ok", "cached"}:
        return None
    fetched = parse_dt(value.get("fetched_at") or value.get("cached_from") or previous.get("updated_at"))
    if not fetched:
        return None
    age = datetime.now(KST) - fetched
    if age < timedelta(0) or age > timedelta(days=CACHE_DAYS):
        return None
    cached = dict(value)
    cached.update({
        "status": "cached", "cached_from": fetched.isoformat(),
        "cache_age_hours": round(age.total_seconds() / 3600, 1),
        "source": f"{value.get('source', '공개 시장 데이터')} ({CACHE_DAYS}일 캐시)",
    })
    return cached


def has_valuation(value: Any) -> bool:
    return isinstance(value, dict) and any(number(value.get(key)) is not None for key in ("per", "pbr", "eps", "bps"))


def has_flow(value: Any) -> bool:
    return isinstance(value, dict) and any(number(value.get(key)) is not None for key in ("foreign_net_buy_10d_krw", "institution_net_buy_10d_krw"))


def main() -> None:
    payload = load_json(DATA_FILE)
    if not payload:
        raise SystemExit("data/stock_data.json not found or invalid")
    previous = load_json(PREVIOUS_FILE)
    stocks = payload.get("stocks", {})
    if not isinstance(stocks, dict):
        raise SystemExit("stocks must be an object")
    success_valuation = success_flow = cache_valuation = cache_flow = 0
    errors: list[dict[str, str]] = []
    session = requests.Session()
    for name, row in stocks.items():
        if not isinstance(row, dict):
            continue
        code = str(row.get("code", "")).strip()
        market = row.setdefault("market", {})
        if len(code) != 6:
            errors.append({"stock": name, "reason": "유효한 6자리 종목코드가 없습니다."})
            continue
        if not has_valuation(market.get("valuation")):
            try:
                market["valuation"] = collect_valuation(session, code)
                success_valuation += 1
            except Exception as exc:
                cached = cached_value(previous, name, "valuation")
                if cached and has_valuation(cached):
                    market["valuation"] = cached
                    cache_valuation += 1
                else:
                    market["valuation"] = {"status": "unavailable", "reason": str(exc)[:300], "source": "NAVER Finance 공개 페이지"}
                    errors.append({"stock": name, "field": "valuation", "reason": str(exc)[:240]})
        if not has_flow(market.get("investor_flow")):
            try:
                market["investor_flow"] = collect_investor_flow(session, code)
                success_flow += 1
            except Exception as exc:
                cached = cached_value(previous, name, "investor_flow")
                if cached and has_flow(cached):
                    market["investor_flow"] = cached
                    cache_flow += 1
                else:
                    market["investor_flow"] = {"status": "unavailable", "reason": str(exc)[:300], "source": "NAVER Finance 공개 페이지"}
                    errors.append({"stock": name, "field": "investor_flow", "reason": str(exc)[:240]})
        row["quantitative"] = repair_stock_factors.score(row.get("financials", {}), market, row.get("consensus", {}))
        time.sleep(0.15)
    payload.setdefault("source_status", {})["public_market_fallback"] = {
        "status": "ok" if success_valuation or success_flow or cache_valuation or cache_flow else "failed",
        "role": "KRX 로그인 없이 PER·PBR 및 외국인·기관 수급 보완",
        "source": "NAVER Finance 공개 페이지",
        "valuation_fresh": success_valuation,
        "valuation_cached": cache_valuation,
        "investor_flow_fresh": success_flow,
        "investor_flow_cached": cache_flow,
        "requested_stocks": len(stocks),
        "errors": errors[:20],
        "updated_at": datetime.now(KST).isoformat(),
    }
    payload["updated_at"] = datetime.now(KST).isoformat()
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Public market fallback: valuation fresh={success_valuation}, cached={cache_valuation}; flow fresh={success_flow}, cached={cache_flow}; errors={len(errors)}")


if __name__ == "__main__":
    main()
