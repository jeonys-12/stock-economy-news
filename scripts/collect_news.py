from __future__ import annotations

import hashlib
import html
import json
import re
import time
from collections import Counter
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
MAX_ITEMS = 500
MAX_YOUTUBE_PER_CHANNEL = 5

# 공식기관 발표를 Google News RSS에서 기관명·공식 도메인 중심으로 검색합니다.
# 기관 웹사이트가 RSS를 제공하지 않거나 차단하는 경우에도 관련 공식 발표와 인용 기사를 함께 포착할 수 있습니다.
SEARCHES = [
    # 대한민국
    {"country": "대한민국", "category": "통화정책·금리", "institution": "한국은행", "query": "한국은행 기준금리 금융통화위원회 경제전망 site:bok.or.kr OR 한국은행"},
    {"country": "대한민국", "category": "금융정책·규제", "institution": "금융위원회", "query": "금융위원회 공매도 밸류업 자본시장 금융규제 site:fsc.go.kr OR 금융위원회"},
    {"country": "대한민국", "category": "기업공시", "institution": "금융감독원 DART", "query": "DART 공시 실적 유상증자 전환사채 최대주주 변경 감사의견 site:dart.fss.or.kr OR 금융감독원 전자공시"},
    {"country": "대한민국", "category": "증권시장", "institution": "한국거래소 KRX·KIND", "query": "한국거래소 KIND 거래정지 관리종목 상장폐지 시장경보 site:krx.co.kr OR site:kind.krx.co.kr"},
    {"country": "대한민국", "category": "정부 재정·세제", "institution": "기획재정부", "query": "기획재정부 경제정책 세제 재정 외환시장 site:moef.go.kr OR 기획재정부"},
    {"country": "대한민국", "category": "산업·수출", "institution": "산업통상자원부", "query": "산업통상자원부 수출 반도체 자동차 배터리 원전 에너지 site:motie.go.kr OR 산업통상자원부"},
    {"country": "대한민국", "category": "건설·부동산", "institution": "국토교통부", "query": "국토교통부 주택공급 부동산 건설 SOC 재건축 site:molit.go.kr OR 국토교통부"},
    {"country": "대한민국", "category": "물가·고용·경기", "institution": "통계청", "query": "통계청 소비자물가 산업활동 고용 소매판매 경기지수 site:kostat.go.kr OR 통계청"},
    {"country": "대한민국", "category": "건설·부동산", "institution": "한국부동산원", "query": "한국부동산원 주택가격 전세가격 청약 거래동향 site:reb.or.kr OR 한국부동산원"},
    {"country": "대한민국", "category": "산업·수출", "institution": "관세청", "query": "관세청 수출입 실적 무역수지 site:customs.go.kr OR 관세청"},

    # 미국
    {"country": "미국", "category": "통화정책·금리", "institution": "Federal Reserve", "query": "Federal Reserve FOMC interest rates dot plot minutes site:federalreserve.gov"},
    {"country": "미국", "category": "기업공시", "institution": "SEC EDGAR", "query": "SEC EDGAR 10-K 10-Q 8-K Form 4 13D 13F site:sec.gov"},
    {"country": "미국", "category": "물가·고용·경기", "institution": "U.S. Bureau of Labor Statistics", "query": "BLS CPI PPI nonfarm payroll unemployment JOLTS site:bls.gov"},
    {"country": "미국", "category": "물가·고용·경기", "institution": "U.S. Bureau of Economic Analysis", "query": "BEA GDP PCE personal income corporate profits site:bea.gov"},
    {"country": "미국", "category": "정부 재정·세제", "institution": "U.S. Treasury", "query": "U.S. Treasury bonds fiscal policy sanctions foreign exchange site:home.treasury.gov"},
    {"country": "미국", "category": "에너지·원자재", "institution": "U.S. EIA", "query": "EIA crude oil inventories natural gas energy outlook site:eia.gov"},

    # 글로벌
    {"country": "글로벌", "category": "글로벌 경제", "institution": "IMF", "query": "IMF World Economic Outlook global growth inflation site:imf.org"},
    {"country": "글로벌", "category": "글로벌 경제", "institution": "OECD", "query": "OECD economic outlook leading indicators inflation site:oecd.org"},
    {"country": "글로벌", "category": "글로벌 경제", "institution": "World Bank", "query": "World Bank global economic prospects commodity markets site:worldbank.org"},
]

YOUTUBE_CHANNELS = [
    {"name": "손에잡히는경제", "channel_id": "UCiYbaVEODktcsh09454Grow"},
    {"name": "언더스탠딩 : 세상의 모든 지식", "channel_id": "UCIUni4ScRp4mqPXsxy62L5w"},
    {"name": "슈카월드", "channel_id": "UCsJ6RuBiTVWRX156FVbeaGg"},
]

IMPORTANT_KEYWORDS = {
    "기준금리": 20, "금리 인상": 20, "금리 인하": 20, "fomc": 20, "한국은행": 18,
    "환율": 15, "인플레이션": 15, "물가": 14, "cpi": 16, "ppi": 14, "고용": 12,
    "gdp": 15, "pce": 15, "비농업고용": 16, "경기침체": 20, "부도": 18, "파산": 20,
    "규제": 12, "공매도": 15, "공급대책": 15, "재건축": 10, "분양": 8, "수주": 8,
    "실적": 10, "유상증자": 16, "전환사채": 16, "감사의견": 18, "상장폐지": 20,
    "거래정지": 18, "관리종목": 18, "급락": 15, "급등": 12,
}

STOPWORDS = {
    "그런데", "그리고", "그래서", "하지만", "이제", "지금", "정말", "사실", "때문", "대해서",
    "이렇게", "저렇게", "이것", "저것", "우리", "여러분", "오늘", "이번", "영상", "얘기",
    "말씀", "정도", "관련", "있는", "없는", "하는", "되는", "같은", "있습니다", "합니다",
    "입니다", "있어요", "하는데", "됩니다", "가지고", "그리고요", "그러니까", "아니면",
}

OFFICIAL_DOMAINS = (
    "bok.or.kr", "fsc.go.kr", "dart.fss.or.kr", "krx.co.kr", "kind.krx.co.kr", "moef.go.kr",
    "motie.go.kr", "molit.go.kr", "kostat.go.kr", "reb.or.kr", "customs.go.kr",
    "federalreserve.gov", "sec.gov", "bls.gov", "bea.gov", "home.treasury.gov", "eia.gov",
    "imf.org", "oecd.org", "worldbank.org",
)

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


def is_official_item(url: str, source: str, institution: str) -> bool:
    haystack = f"{url} {source}".lower()
    return any(domain in haystack for domain in OFFICIAL_DOMAINS) or institution.lower() in source.lower()


def importance_score(title: str, description: str, published_at: datetime, official: bool = False) -> int:
    text = f"{title} {description}".lower()
    score = 35 + (12 if official else 0)
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
        official = is_official_item(url, source, search["institution"])
        unique = hashlib.sha1(f"{title}|{url}".encode("utf-8")).hexdigest()[:16]
        items.append({
            "id": unique,
            "type": "news",
            "title": title,
            "description": description[:360],
            "url": url,
            "source": source,
            "institution": search["institution"],
            "official": official,
            "country": search["country"],
            "category": search["category"],
            "published_at": published_at.isoformat(),
            "importance_score": importance_score(title, description, published_at, official),
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
            "institution": "경제 유튜브",
            "official": False,
            "country": "대한민국",
            "category": "경제 유튜브",
            "published_at": published_at.isoformat(),
            "importance_score": min(100, importance_score(title, summary, published_at) + 5),
        })
        time.sleep(0.5)
    return items


def deduplicate(items: list[dict]) -> list[dict]:
    result, seen_urls, seen_titles = [], set(), set()
    for item in sorted(items, key=lambda x: (x.get("official", False), x["published_at"]), reverse=True):
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
            collected = collect_feed(search)
            all_items.extend(collected)
            print(f"Collected {len(collected):>3} items: {search['country']} / {search['institution']}")
        except Exception as exc:
            errors.append(f"{search['country']} / {search['institution']}: {exc}")
        time.sleep(1)
    for channel in YOUTUBE_CHANNELS:
        try:
            all_items.extend(collect_youtube(channel))
        except Exception as exc:
            errors.append(f"YouTube / {channel['name']}: {exc}")
        time.sleep(1)
    items = deduplicate(all_items)
    items.sort(
        key=lambda x: (x.get("official", False), x["importance_score"], x["published_at"]),
        reverse=True,
    )
    payload = {
        "updated_at": datetime.now(KST).isoformat(),
        "count": min(len(items), MAX_ITEMS),
        "errors": errors,
        "monitored_institutions": [search["institution"] for search in SEARCHES],
        "news": items[:MAX_ITEMS],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {payload['count']} items to {OUTPUT}")
    if errors:
        print("Feed warnings:")
        for error in errors:
            print(f"- {error}")


if __name__ == "__main__":
    main()
