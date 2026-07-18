from __future__ import annotations

import hashlib
import html
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi

KST = timezone(timedelta(hours=9))
OUTPUT = Path("data/news.json")
MAX_AGE_DAYS = 7
MAX_ITEMS = 300
MAX_ITEMS_PER_SOURCE = 25
MAX_YOUTUBE_PER_CHANNEL = 5

# 권장 최소 모니터링 조합 12개
# priority: 3=공식 공시·핵심 정책, 2=주요 정책·통계·전문기관, 1=언론사
SOURCES = [
    {
        "name": "금융감독원 DART",
        "domain": "dart.fss.or.kr",
        "category": "기업공시",
        "priority": 3,
        "query": "site:dart.fss.or.kr (주요사항보고서 OR 사업보고서 OR 분기보고서 OR 수주 OR 유상증자 OR 전환사채 OR 최대주주)",
    },
    {
        "name": "한국거래소 KIND",
        "domain": "kind.krx.co.kr",
        "category": "기업공시",
        "priority": 3,
        "query": "site:kind.krx.co.kr (공시 OR 거래정지 OR 관리종목 OR 상장폐지 OR 조회공시 OR 불성실공시)",
    },
    {
        "name": "기획재정부",
        "domain": "moef.go.kr",
        "category": "경제정책",
        "priority": 3,
        "query": "site:moef.go.kr (경제정책 OR 세제 OR 재정 OR 물가 OR 대외경제 OR 보도자료)",
    },
    {
        "name": "한국은행",
        "domain": "bok.or.kr",
        "category": "금융시장",
        "priority": 3,
        "query": "site:bok.or.kr (기준금리 OR 통화정책 OR 경제전망 OR 금융시장 OR 환율 OR 보도자료)",
    },
    {
        "name": "금융위원회",
        "domain": "fsc.go.kr",
        "category": "금융시장",
        "priority": 3,
        "query": "site:fsc.go.kr (자본시장 OR 공매도 OR 금융정책 OR 은행 OR 대출 OR 증권 OR 보도자료)",
    },
    {
        "name": "산업통상자원부",
        "domain": "motie.go.kr",
        "category": "경제정책",
        "priority": 2,
        "query": "site:motie.go.kr (수출 OR 산업정책 OR 반도체 OR 에너지 OR 통상 OR 공급망 OR 보도자료)",
    },
    {
        "name": "국토교통부",
        "domain": "molit.go.kr",
        "category": "건설·부동산",
        "priority": 3,
        "query": "site:molit.go.kr (주택정책 OR 부동산 OR 건설 OR 재건축 OR 재개발 OR 공급대책 OR 보도자료)",
    },
    {
        "name": "매일경제",
        "domain": "mk.co.kr",
        "category": "금융시장",
        "priority": 1,
        "query": "site:mk.co.kr (증시 OR 코스피 OR 기업 OR 실적 OR 경제정책 OR 부동산 OR 건설)",
    },
    {
        "name": "한국경제",
        "domain": "hankyung.com",
        "category": "금융시장",
        "priority": 1,
        "query": "site:hankyung.com (증시 OR 기업 OR 실적 OR 경제정책 OR 산업 OR 부동산 OR 건설)",
    },
    {
        "name": "연합뉴스",
        "domain": "yna.co.kr",
        "category": "경제정책",
        "priority": 1,
        "query": "site:yna.co.kr (경제 OR 금융 OR 증권 OR 산업 OR 건설 OR 부동산) -스포츠 -연예",
    },
    {
        "name": "대한경제",
        "domain": "dnews.co.kr",
        "category": "건설·부동산",
        "priority": 2,
        "query": "site:dnews.co.kr (건설 OR 수주 OR SOC OR 부동산 OR 주택 OR 재개발 OR 재건축)",
    },
    {
        "name": "한국부동산원",
        "domain": "reb.or.kr",
        "category": "건설·부동산",
        "priority": 2,
        "query": "site:reb.or.kr (주택가격 OR 지가 OR 부동산시장 OR 거래량 OR 실거래 OR 통계 OR 보도자료)",
    },
]

YOUTUBE_CHANNELS = [
    {"name": "손에잡히는경제", "channel_id": "UCiYbaVEODktcsh09454Grow"},
    {"name": "언더스탠딩 : 세상의 모든 지식", "channel_id": "UCIUni4ScRp4mqPXsxy62L5w"},
    {"name": "슈카월드", "channel_id": "UCsJ6RuBiTVWRX156FVbeaGg"},
]

IMPORTANT_KEYWORDS = {
    "기준금리": 20, "금리 인상": 20, "금리 인하": 20, "한국은행": 18,
    "환율": 15, "인플레이션": 15, "물가": 14, "고용": 12, "GDP": 15,
    "경기침체": 20, "부도": 18, "파산": 20, "규제": 12, "공급대책": 15,
    "재건축": 10, "분양": 8, "수주": 8, "실적": 10, "상장폐지": 20,
    "거래정지": 20, "유상증자": 15, "전환사채": 15, "최대주주": 15,
    "급락": 15, "급등": 12,
}

STOPWORDS = {
    "그런데", "그리고", "그래서", "하지만", "이제", "지금", "정말", "사실", "때문", "대해서",
    "이렇게", "저렇게", "이것", "저것", "우리", "여러분", "오늘", "이번", "영상", "얘기",
    "말씀", "정도", "관련", "있는", "없는", "하는", "되는", "같은", "있습니다", "합니다",
    "입니다", "있어요", "하는데", "됩니다", "가지고", "그리고요", "그러니까", "아니면",
}

USER_AGENT = "Mozilla/5.0 (compatible; StockEconomyNewsDashboard/1.4)"


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(html.unescape(value), "html.parser")
    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\[(?:음악|박수|웃음|Music|Applause)[^\]]*\]", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def parse_date(entry: dict) -> datetime:
    for parsed_key in ("published_parsed", "updated_parsed"):
        value = entry.get(parsed_key)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc)
    for key in ("published", "updated"):
        value = entry.get(key)
        if value:
            try:
                dt = parsedate_to_datetime(value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except (TypeError, ValueError, OverflowError):
                try:
                    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
                except ValueError:
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


def importance_score(title: str, description: str, published_at: datetime, source_priority: int = 0) -> int:
    text = f"{title} {description}".lower()
    score = 35 + (source_priority * 5)
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


def collect_source(source_config: dict) -> list[dict]:
    response = requests.get(rss_url(source_config["query"]), headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    items: list[dict] = []

    for entry in feed.entries:
        raw_title = clean_text(entry.get("title"))
        url = entry.get("link", "").strip()
        if not raw_title or not url:
            continue

        google_source = extract_source(entry, raw_title)
        title = normalize_title(raw_title, google_source)
        description = clean_text(entry.get("summary") or entry.get("description"))
        published_at = parse_date(entry)
        if published_at < datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS):
            continue

        unique = hashlib.sha1(f"{source_config['name']}|{title}|{url}".encode("utf-8")).hexdigest()[:16]
        items.append({
            "id": unique,
            "type": "news",
            "title": title,
            "description": description[:360],
            "url": url,
            "source": source_config["name"],
            "source_domain": source_config["domain"],
            "source_priority": source_config["priority"],
            "country": "대한민국",
            "category": source_config["category"],
            "published_at": published_at.isoformat(),
            "importance_score": importance_score(
                title, description, published_at, source_config["priority"]
            ),
        })
    return items


def transcript_chunks(video_id: str) -> list[str]:
    transcript = YouTubeTranscriptApi().fetch(video_id, languages=["ko", "en"])
    chunks: list[str] = []
    buffer = ""
    for snippet in transcript:
        text = clean_text(snippet.text)
        if not text:
            continue
        buffer = f"{buffer} {text}".strip()
        if len(buffer) >= 90 or re.search(r"[.!?。！？]$", text):
            chunks.append(buffer)
            buffer = ""
    if buffer:
        chunks.append(buffer)
    return [chunk for chunk in chunks if 35 <= len(chunk) <= 320]


def summarize_chunks(chunks: list[str], fallback: str) -> tuple[str, str]:
    if not chunks:
        return (fallback[:420] or "영상 설명과 자막을 확인할 수 없습니다."), "description"

    words: list[str] = []
    for chunk in chunks:
        words.extend(
            word for word in re.findall(r"[가-힣A-Za-z0-9]{2,}", chunk.lower())
            if word not in STOPWORDS and not word.isdigit()
        )
    frequency = Counter(words)
    if not frequency:
        return " ".join(chunks[:3])[:420], "transcript"

    scored: list[tuple[float, int, str]] = []
    for index, chunk in enumerate(chunks):
        tokens = re.findall(r"[가-힣A-Za-z0-9]{2,}", chunk.lower())
        meaningful = [token for token in tokens if token not in STOPWORDS]
        if not meaningful:
            continue
        score = sum(frequency[token] for token in meaningful) / max(len(meaningful), 1)
        if any(keyword.lower() in chunk.lower() for keyword in IMPORTANT_KEYWORDS):
            score += 2.5
        scored.append((score, index, chunk))

    selected = sorted(scored, reverse=True)[:3]
    selected.sort(key=lambda item: item[1])
    summary = " · ".join(item[2] for item in selected)
    return summary[:520], "transcript"


def collect_youtube(channel: dict) -> list[dict]:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel['channel_id']}"
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    items: list[dict] = []

    for entry in feed.entries[:MAX_YOUTUBE_PER_CHANNEL]:
        title = clean_text(entry.get("title"))
        video_url = entry.get("link", "").strip()
        published_at = parse_date(entry)
        if not title or not video_url or published_at < datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS):
            continue

        video_id = entry.get("yt_videoid") or video_url.split("v=")[-1].split("&")[0]
        original_description = clean_text(entry.get("media_description") or entry.get("summary") or "")
        try:
            summary, summary_source = summarize_chunks(transcript_chunks(video_id), original_description)
        except Exception as exc:
            print(f"Transcript unavailable for {video_id}: {exc}")
            summary, summary_source = summarize_chunks([], original_description)

        unique = hashlib.sha1(f"youtube|{channel['channel_id']}|{video_id}".encode("utf-8")).hexdigest()[:16]
        items.append({
            "id": unique,
            "type": "youtube",
            "title": title,
            "description": summary,
            "summary_source": summary_source,
            "url": video_url,
            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            "source": channel["name"],
            "source_priority": 1,
            "country": "대한민국",
            "category": "경제 유튜브",
            "published_at": published_at.isoformat(),
            "importance_score": min(100, importance_score(title, summary, published_at, 1) + 5),
        })
        time.sleep(0.5)
    return items


def deduplicate(items: list[dict]) -> list[dict]:
    result, seen_urls, seen_titles = [], set(), set()
    for item in sorted(items, key=lambda x: x["published_at"], reverse=True):
        normalized = re.sub(r"[^가-힣a-z0-9]", "", item["title"].lower())[:90]
        if item["url"] in seen_urls or normalized in seen_titles:
            continue
        seen_urls.add(item["url"])
        seen_titles.add(normalized)
        result.append(item)
    return result


def limit_per_source(items: list[dict]) -> list[dict]:
    counts: defaultdict[str, int] = defaultdict(int)
    limited: list[dict] = []
    for item in sorted(
        items,
        key=lambda x: (x.get("importance_score", 0), x["published_at"]),
        reverse=True,
    ):
        source = item.get("source", "기타")
        limit = MAX_YOUTUBE_PER_CHANNEL if item.get("type") == "youtube" else MAX_ITEMS_PER_SOURCE
        if counts[source] >= limit:
            continue
        counts[source] += 1
        limited.append(item)
    return limited


def main() -> None:
    all_items: list[dict] = []
    errors: list[str] = []

    for source_config in SOURCES:
        try:
            collected = collect_source(source_config)
            all_items.extend(collected)
            print(f"{source_config['name']}: {len(collected)} items")
        except Exception as exc:
            errors.append(f"{source_config['name']}: {exc}")
        time.sleep(1)

    for channel in YOUTUBE_CHANNELS:
        try:
            all_items.extend(collect_youtube(channel))
        except Exception as exc:
            errors.append(f"YouTube / {channel['name']}: {exc}")
        time.sleep(1)

    items = [item for item in deduplicate(all_items) if item.get("country") == "대한민국"]
    items = limit_per_source(items)
    items.sort(key=lambda x: (x["importance_score"], x["published_at"]), reverse=True)

    payload = {
        "updated_at": datetime.now(KST).isoformat(),
        "count": min(len(items), MAX_ITEMS),
        "sources": [
            {
                "name": source["name"],
                "domain": source["domain"],
                "category": source["category"],
                "priority": source["priority"],
            }
            for source in SOURCES
        ],
        "errors": errors,
        "news": items[:MAX_ITEMS],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {payload['count']} Korean items from {len(SOURCES)} recommended sources to {OUTPUT}")
    if errors:
        print("Feed warnings:")
        for error in errors:
            print(f"- {error}")


if __name__ == "__main__":
    main()
