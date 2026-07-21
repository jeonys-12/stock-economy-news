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

KST = timezone(timedelta(hours=9))
DATA_FILE = Path("data/stock_data.json")
PREVIOUS_FILE = Path(os.getenv("PREVIOUS_STOCK_DATA", "/tmp/stock_data_previous.json"))
CACHE_DAYS = 3
TIMEOUT = (8, 20)
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
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def fetch(session: requests.Session, url: str) -> str:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = session.get(url, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "euc-kr"
            return response.text
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.8)
    raise RuntimeError(str(last_error)[:300])


def text_metric(text: str, labels: tuple[str, ...]) -> float | None:
    compact = re.sub(r"\s+", " ", text)
    for label in labels:
        match = re.search(rf"{re.escape(label)}[^0-9\-]{{0,30}}([0-9,.-]+)", compact, re.IGNORECASE)
        value = number(match.group(1)) if match else None
        if value is not None:
            return value
    return None


def collect_valuation(session: requests.Session, code: str, current_price: int | float | None = None) -> dict[str, Any]:
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    html = fetch(session, url)
    soup = BeautifulSoup(html, "html.parser")
    selectors = {
        "current_price": "#_nowVal",
        "per": "#_per",
        "pbr": "#_pbr",
        "eps": "#_eps",
        "bps": "#_bps",
    }
    result: dict[str, Any] = {}
    for key, selector in selectors.items():
        node = soup.select_one(selector)
        value = number(node.get_text(" ", strip=True)) if node else None
        if value is not None:
            result[key] = int(value) if key == "current_price" else value

    text = soup.get_text(" ", strip=True)
    fallback_labels = {
        "current_price": ("현재가",),
        "per": ("PER",),
        "pbr": ("PBR",),
        "eps": ("EPS",),
        "bps": ("BPS",),
        "target_price": ("목표주가",),
    }
    for key, labels in fallback_labels.items():
        if result.get(key) is None:
            value = text_metric(text, labels)
            if value is not None:
                result[key] = int(value) if key in {"current_price", "target_price"} else value

    price = number(result.get("current_price")) or number(current_price)
    eps = number(result.get("eps"))
    bps = number(result.get("bps"))
    target = number(result.get("target_price"))
    if result.get("per") is None and price is not None and eps not in (None, 0):
        result["per"] = round(price / eps, 2)
    if result.get("pbr") is None and price is not None and bps not in (None, 0):
        result["pbr"] = round(price / bps, 2)
    if target and price:
        result["target_upside_pct"] = round((target - price) / price * 100, 2)

    if not any(result.get(key) is not None for key in ("current_price", "per", "pbr", "eps", "bps")):
        raise RuntimeError("네이버 종목 메인 페이지에서 유효한 시장지표를 찾지 못했습니다.")

    result.update({
        "status": "ok",
        "source": "NAVER Finance 종목 메인 페이지",
        "source_url": url,
        "fetched_at": datetime.now(KST).isoformat(),
    })
    return result


def flatten_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [" ".join(str(part) for part in col if str(part) != "nan").strip() for col in frame.columns]
    else:
        frame.columns = [str(col).strip() for col in frame.columns]
    return frame


def find_column(columns: list[str], aliases: tuple[str, ...]) -> str | None:
    normalized = [(col, re.sub(r"\s+", "", col)) for col in columns]
    for alias in aliases:
        for col, value in normalized:
            if alias in value:
                return col
    return None


def collect_investor_flow(session: requests.Session, code: str) -> dict[str, Any]:
    url = f"https://finance.naver.com/item/frgn.naver?code={code}&page=1"
    html = fetch(session, url)
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError as exc:
        raise RuntimeError("투자자별 거래 표를 찾지 못했습니다.") from exc

    selected = None
    institution_col = foreign_col = date_col = None
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
        raise RuntimeError("기관·외국인 순매매 표 구조를 확인하지 못했습니다.")
    selected = selected[selected[date_col].astype(str).str.match(r"\d{4}\.\d{2}\.\d{2}", na=False)].head(10)
    if selected.empty:
        raise RuntimeError("최근 투자자별 거래 데이터가 없습니다.")

    institution = pd.to_numeric(selected[institution_col].astype(str).str.replace(",", "", regex=False), errors="coerce")
    foreign = pd.to_numeric(selected[foreign_col].astype(str).str.replace(",", "", regex=False), errors="coerce")
    return {
        "institution_net_buy_10d_shares": int(institution.fillna(0).sum()),
        "foreign_net_buy_10d_shares": int(foreign.fillna(0).sum()),
        "unit": "shares",
        "trading_days": int(len(selected)),
        "definition": "최근 10거래일 순매매량 합계",
        "status": "ok",
        "source": "NAVER Finance 투자자별 매매 페이지",
        "source_url": url,
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
        "status": "cached",
        "cached_from": fetched.isoformat(),
        "cache_age_hours": round(age.total_seconds() / 3600, 1),
        "source": f"{value.get('source', 'NAVER Finance')} ({CACHE_DAYS}일 캐시)",
    })
    return cached


def has_valuation(value: Any) -> bool:
    return isinstance(value, dict) and any(number(value.get(key)) is not None for key in ("current_price", "per", "pbr", "eps", "bps"))


def has_flow(value: Any) -> bool:
    return isinstance(value, dict) and any(number(value.get(key)) is not None for key in ("foreign_net_buy_10d_shares", "institution_net_buy_10d_shares"))
