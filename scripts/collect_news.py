from __future__ import annotations

import hashlib
import html
import json
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
OUTPUT = Path("data/news.json")
MAX_AGE_DAYS = 30
MAX_ITEMS = 300

SEARCHES = [
    {"country": "대한민국", "category": "경제정책", "query": "한국 경제정책 정부 기획재정부 한국은행 산업통상자원부"},
    {"country": "대한민국", "category": "금융시장", "query": "한국 주식 증시 코스피 코스닥 환율 금리 채권 금융시장"},
    {"country": "대한민국", "category": "건설·부동산", "query": "한국 건설 부동산 주택 재건축 재개발 국토교통부"},
    {"country": "미국", "category": "경제정책", "query": "미국 경제정책 연준 FOMC 재무부 관세 고용 물가"},
    {"country": "미국", "category": "금융시장", "query": "미국 증시 뉴욕증시 나스닥 S&P500 다우 연준 금리"},
    {"country": "미국", "category": "건설·부동산", "query": "미국 건설 부동산 주택시장 모기지 상업용 부동산"},
]

IMPORTANT_KEYWORDS = {
    "기준금리": 20, "금리 인상": 20, "금리 인하": 20, "FOMC": 20, "연준": 18,
    "한국은행": 18, "관세": 18, "환율": 15, "인플레이션": 15, "물가": 14,
    "고용": 12, "GDP": 15, "경기침체": 20, "부도": 18, "파산": 20,
    "규제": 12, "공급대책": 15, "재건축": 10, "분양": 8, "수주": 8,
    "실적": 10, "상장폐지": 20, "급락": 15, "급등": 12,
}

USER_AGENT = "Mozilla/5.0 (compatible; StockEconomyNewsDashboard/1.0)"


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(html.unescape(value), "html.parser")
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def parse_date(entry: dict) -> datetime:
    for key in ("published", "updated"):
        value = entry.get(key)
        if value:
            try:
                dt = parsedate_to_datetime(value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except (TypeError, ValueError, OverflowError):
                pass
    return datetime.now(timezone.utc)


def extract_source(entry: dict, title: str) -> str:
    source = entry.get("source") or {}
    if isinstance(source, dict) and source.get("title"):
        return clean_text(source["title"])
    if " - " in title:
        return title.rsplit(" - ", 1)[-1].strip()
    return "Google News"


def normalize_title(title: str, source: str) -> str:
    suffix = f" - {source}"
    return title[:-len(suffix)].strip() if source and title.endswith(suffix) else title


def importance_score(title: str, description: str, published_at: datetime) -> int:
    text = f"{title} {description}".lower()
    score = 35
    for keyword, weight in IMPORTANT_KEYWORDS.items():
        if keyword.lower() in text:
            score += weight
    age_hours = max(0, (datetime.now(timezone.utc) - published_at).total_seconds() / 3600)
    if age_hours <= 12:
        score += 10
    elif age_hours <= 24:
        score += 6
    elif age_hours <= 72:
        score += 3
    return min(score, 100)


def rss_url(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=ko&gl=KR&ceid=KR:ko"


def collect_feed(search: dict) -> list[dict]:
    response = requests.get(rss_url(search["query"]), headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    items: list[dict] = []

    for entry in feed.entries:
        raw_title = clean_text(entry.get("title"))
        url = entry.get("link", "").strip()
        if not raw_title or not url:
            continue

        source = extract_source(entry, raw_title)
        title = normalize_title(raw_title, source)
        description = clean_text(entry.get("summary") or entry.get("description"))
        published_at = parse_date(entry)
        if published_at < datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS):
            continue

        unique = hashlib.sha1(f"{title}|{url}".encode("utf-8")).hexdigest()[:16]
        items.append({
            "id": unique,
            "title": title,
            "description": description[:360],
            "url": url,
            "source": source,
            "country": search["country"],
            "category": search["category"],
            "published_at": published_at.isoformat(),
            "importance_score": importance_score(title, description, published_at),
        })
    return items


def deduplicate(items: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for item in sorted(items, key=lambda x: x["published_at"], reverse=True):
        normalized = re.sub(r"[^가-힣a-z0-9]", "", item["title"].lower())[:90]
        if item["url"] in seen_urls or normalized in seen_titles:
            continue
        seen_urls.add(item["url"])
        seen_titles.add(normalized)
        result.append(item)
    return result


def main() -> None:
    all_items: list[dict] = []
    errors: list[str] = []
    for search in SEARCHES:
        try:
            all_items.extend(collect_feed(search))
        except Exception as exc:  # Keep other feeds running when one source fails.
            errors.append(f"{search['country']} / {search['category']}: {exc}")
        time.sleep(1)

    items = deduplicate(all_items)
    items.sort(key=lambda x: (x["importance_score"], x["published_at"]), reverse=True)
    payload = {
        "updated_at": datetime.now(KST).isoformat(),
        "count": min(len(items), MAX_ITEMS),
        "errors": errors,
        "news": items[:MAX_ITEMS],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {payload['count']} articles to {OUTPUT}")
    if errors:
        print("Feed warnings:")
        for error in errors:
            print(f"- {error}")


if __name__ == "__main__":
    main()
