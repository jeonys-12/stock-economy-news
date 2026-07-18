from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus, urljoin

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
USER_AGENT = "Mozilla/5.0 (compatible; StockEconomyNewsDashboard/1.5)"
HEADERS = {"User-Agent": USER_AGENT}

# method_order는 공식 API → 공식 RSS → 공개 웹페이지 → Google News RSS 대체 순서입니다.
# verified=False인 URL은 사이트 개편 시 변경 가능성이 있어 자동 실패 후 다음 방식으로 전환됩니다.
SOURCES = [
    {
        "name": "금융감독원 DART", "domain": "dart.fss.or.kr", "category": "기업공시", "priority": 3,
        "methods": [
            {"type": "opendart_api", "secret": "OPENDART_API_KEY", "requires_auth": True,
             "note": "OpenDART 인증키 필요"},
            {"type": "google_news", "query": "site:dart.fss.or.kr (주요사항보고서 OR 사업보고서 OR 분기보고서 OR 수주 OR 유상증자 OR 전환사채 OR 최대주주)"},
        ],
        "alternative": "GitHub Secrets에 OPENDART_API_KEY를 등록하면 공식 API를 사용합니다.",
    },
    {
        "name": "한국거래소 KIND", "domain": "kind.krx.co.kr", "category": "기업공시", "priority": 3,
        "methods": [{"type": "google_news", "query": "site:kind.krx.co.kr (공시 OR 거래정지 OR 관리종목 OR 상장폐지 OR 조회공시 OR 불성실공시)"}],
        "restricted": True,
        "alternative": "KIND 공개 페이지는 자동수집 구조가 자주 변경될 수 있어 Google News RSS로 보완합니다. 정밀 공시는 DART API를 우선 사용하십시오.",
    },
    {
        "name": "기획재정부", "domain": "moef.go.kr", "category": "경제정책", "priority": 3,
        "methods": [{"type": "google_news", "query": "site:moef.go.kr (경제정책 OR 세제 OR 재정 OR 물가 OR 대외경제 OR 보도자료)"}],
        "alternative": "공식 RSS 주소가 안정적으로 확인되면 source 설정에 rss 메서드를 추가할 수 있습니다.",
    },
    {
        "name": "한국은행", "domain": "bok.or.kr", "category": "금융시장", "priority": 3,
        "methods": [{"type": "google_news", "query": "site:bok.or.kr (기준금리 OR 통화정책 OR 경제전망 OR 금융시장 OR 환율 OR 보도자료)"}],
        "alternative": "ECOS 통계는 별도 API 키를 사용해 지표 수집 모듈로 분리하는 것이 안정적입니다.",
    },
    {
        "name": "금융위원회", "domain": "fsc.go.kr", "category": "금융시장", "priority": 3,
        "methods": [{"type": "google_news", "query": "site:fsc.go.kr (자본시장 OR 공매도 OR 금융정책 OR 은행 OR 대출 OR 증권 OR 보도자료)"}],
        "alternative": "공식 보도자료 공개 페이지를 원문 확인용으로 병행합니다.",
    },
    {
        "name": "산업통상자원부", "domain": "motie.go.kr", "category": "경제정책", "priority": 2,
        "methods": [{"type": "google_news", "query": "site:motie.go.kr (수출 OR 산업정책 OR 반도체 OR 에너지 OR 통상 OR 공급망 OR 보도자료)"}],
        "alternative": "공공데이터포털 OpenAPI가 제공되는 개별 데이터는 별도 API 연동을 권장합니다.",
    },
    {
        "name": "국토교통부", "domain": "molit.go.kr", "category": "건설·부동산", "priority": 3,
        "methods": [{"type": "google_news", "query": "site:molit.go.kr (주택정책 OR 부동산 OR 건설 OR 재건축 OR 재개발 OR 공급대책 OR 보도자료)"}],
        "alternative": "실거래가는 국토교통부 실거래가 OpenAPI를 별도 키로 연동하는 방식이 안정적입니다.",
    },
    {
        "name": "매일경제", "domain": "mk.co.kr", "category": "금융시장", "priority": 1,
        "methods": [
            {"type": "rss", "url": "https://www.mk.co.kr/rss/50200011/", "label": "공식 증권 RSS"},
            {"type": "rss", "url": "https://www.mk.co.kr/rss/30100041/", "label": "공식 경제 RSS"},
            {"type": "google_news", "query": "site:mk.co.kr (증시 OR 코스피 OR 기업 OR 실적 OR 경제정책 OR 부동산 OR 건설)"},
        ],
        "alternative": "공식 RSS 실패 시 Google News RSS로 자동 전환합니다.",
    },
    {
        "name": "한국경제", "domain": "hankyung.com", "category": "금융시장", "priority": 1,
        "methods": [{"type": "google_news", "query": "site:hankyung.com (증시 OR 기업 OR 실적 OR 경제정책 OR 산업 OR 부동산 OR 건설)"}],
        "restricted": True,
        "alternative": "공개 기사 페이지는 접근정책과 유료기사 여부가 달라 Google News RSS 제목·링크 중심으로 수집합니다.",
    },
    {
        "name": "연합뉴스", "domain": "yna.co.kr", "category": "경제정책", "priority": 1,
        "methods": [
            {"type": "rss", "url": "https://www.yna.co.kr/rss/economy.xml", "label": "경제 RSS", "verified": False},
            {"type": "google_news", "query": "site:yna.co.kr (경제 OR 금융 OR 증권 OR 산업 OR 건설 OR 부동산) -스포츠 -연예"},
        ],
        "alternative": "RSS 주소 변경 시 Google News RSS로 자동 전환합니다.",
    },
    {
        "name": "대한경제", "domain": "dnews.co.kr", "category": "건설·부동산", "priority": 2,
        "methods": [{"type": "google_news", "query": "site:dnews.co.kr (건설 OR 수주 OR SOC OR 부동산 OR 주택 OR 재개발 OR 재건축)"}],
        "restricted": True,
        "alternative": "공식 RSS가 확인되지 않아 Google News RSS를 사용하며 원문은 브라우저에서 확인합니다.",
    },
    {
        "name": "한국부동산원", "domain": "reb.or.kr", "category": "건설·부동산", "priority": 2,
        "methods": [{"type": "google_news", "query": "site:reb.or.kr (주택가격 OR 지가 OR 부동산시장 OR 거래량 OR 실거래 OR 통계 OR 보도자료)"}],
        "alternative": "R-ONE 통계는 파일 다운로드 또는 제공 API가 확인되는 지표별로 별도 연동하는 것이 안정적입니다.",
    },
]

YOUTUBE_CHANNELS = [
    {"name": "손에잡히는경제", "channel_id": "UCiYbaVEODktcsh09454Grow"},
    {"name": "언더스탠딩 : 세상의 모든 지식", "channel_id": "UCIUni4ScRp4mqPXsxy62L5w"},
    {"name": "슈카월드", "channel_id": "UCsJ6RuBiTVWRX156FVbeaGg"},
]

IMPORTANT_KEYWORDS = {
    "기준금리": 20, "금리 인상": 20, "금리 인하": 20, "한국은행": 18, "환율": 15,
    "물가": 14, "GDP": 15, "경기침체": 20, "부도": 18, "파산": 20, "규제": 12,
    "공급대책": 15, "재건축": 10, "분양": 8, "수주": 8, "실적": 10, "상장폐지": 20,
    "거래정지": 20, "유상증자": 15, "전환사채": 15, "최대주주": 15, "급락": 15, "급등": 12,
}
STOPWORDS = {"그런데", "그리고", "그래서", "하지만", "이제", "지금", "정말", "사실", "때문", "관련", "있는", "없는", "하는", "되는", "같은", "있습니다", "합니다", "입니다", "영상", "오늘", "이번"}


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = BeautifulSoup(html.unescape(value), "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def parse_date(entry: dict) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc)
    for key in ("published", "updated"):
        value = entry.get(key)
        if value:
            try:
                dt = parsedate_to_datetime(value)
                return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
            except Exception:
                try:
                    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
                except Exception:
                    pass
    return datetime.now(timezone.utc)


def importance_score(title: str, description: str, published_at: datetime, priority: int = 0) -> int:
    score = 35 + priority * 5
    text = f"{title} {description}".lower()
    score += sum(weight for keyword, weight in IMPORTANT_KEYWORDS.items() if keyword.lower() in text)
    age = (datetime.now(timezone.utc) - published_at).total_seconds() / 3600
    score += 10 if age <= 12 else 6 if age <= 24 else 3 if age <= 72 else 0
    return min(score, 100)


def google_news_url(query: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=ko&gl=KR&ceid=KR:ko"


def feed_items(source: dict, url: str, method: str, label: str = "") -> list[dict]:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    if getattr(feed, "bozo", False) and not feed.entries:
        raise RuntimeError(f"RSS parse failed: {getattr(feed, 'bozo_exception', 'unknown')}")
    items = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    for entry in feed.entries:
        raw_title = clean_text(entry.get("title"))
        link = (entry.get("link") or "").strip()
        if not raw_title or not link:
            continue
        published = parse_date(entry)
        if published < cutoff:
            continue
        feed_source = entry.get("source") or {}
        feed_source_name = clean_text(feed_source.get("title")) if isinstance(feed_source, dict) else ""
        title = raw_title
        if method == "google_news" and " - " in raw_title:
            possible_source = raw_title.rsplit(" - ", 1)[-1].strip()
            title = raw_title[: -(len(possible_source) + 3)].strip()
        description = clean_text(entry.get("summary") or entry.get("description"))
        unique = hashlib.sha1(f"{source['name']}|{title}|{link}".encode()).hexdigest()[:16]
        items.append({
            "id": unique, "type": "news", "title": title, "description": description[:360], "url": link,
            "source": source["name"], "source_domain": source["domain"], "source_priority": source["priority"],
            "collection_method": method, "collection_label": label or method, "country": "대한민국",
            "category": source["category"], "published_at": published.isoformat(),
            "importance_score": importance_score(title, description, published, source["priority"]),
        })
    if not items:
        raise RuntimeError("no recent items")
    return items


def collect_opendart(source: dict, method: dict) -> list[dict]:
    key = os.getenv(method["secret"], "").strip()
    if not key:
        raise RuntimeError(f"missing GitHub Secret: {method['secret']}")
    end = datetime.now(KST)
    start = end - timedelta(days=MAX_AGE_DAYS)
    params = {"crtfc_key": key, "bgn_de": start.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"), "page_count": 100}
    response = requests.get("https://opendart.fss.or.kr/api/list.json", params=params, headers=HEADERS, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") not in ("000", "013"):
        raise RuntimeError(payload.get("message", "OpenDART API error"))
    items = []
    for row in payload.get("list", []):
        receipt = row.get("rcept_no", "")
        title = clean_text(f"{row.get('corp_name', '')} · {row.get('report_nm', '')}")
        if not receipt or not title:
            continue
        published = datetime.strptime(row["rcept_dt"], "%Y%m%d").replace(tzinfo=KST).astimezone(timezone.utc)
        link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}"
        desc = clean_text(f"제출인: {row.get('flr_nm', '')} / 시장: {row.get('corp_cls', '')}")
        items.append({
            "id": hashlib.sha1(f"dart|{receipt}".encode()).hexdigest()[:16], "type": "news", "title": title,
            "description": desc, "url": link, "source": source["name"], "source_domain": source["domain"],
            "source_priority": source["priority"], "collection_method": "official_api", "collection_label": "OpenDART 공식 API",
            "country": "대한민국", "category": source["category"], "published_at": published.isoformat(),
            "importance_score": importance_score(title, desc, published, source["priority"]),
        })
    return items


def collect_source(source: dict) -> tuple[list[dict], dict]:
    attempts = []
    for method in source["methods"]:
        try:
            if method["type"] == "opendart_api":
                items = collect_opendart(source, method)
                used = "official_api"
                label = "OpenDART 공식 API"
            elif method["type"] == "rss":
                items = feed_items(source, method["url"], "official_rss", method.get("label", "공식 RSS"))
                used = "official_rss"
                label = method.get("label", "공식 RSS")
            elif method["type"] == "webpage":
                raise RuntimeError("webpage collector not configured")
            else:
                items = feed_items(source, google_news_url(method["query"]), "google_news_rss", "Google News RSS 대체")
                used = "google_news_rss"
                label = "Google News RSS 대체"
            return items, {"status": "ok", "method": used, "label": label, "attempts": attempts}
        except Exception as exc:
            attempts.append({"method": method["type"], "error": str(exc)[:180]})
    return [], {"status": "failed", "method": "none", "label": "수집 실패", "attempts": attempts}


def transcript_chunks(video_id: str) -> list[str]:
    transcript = YouTubeTranscriptApi().fetch(video_id, languages=["ko", "en"])
    chunks, buffer = [], ""
    for snippet in transcript:
        text = clean_text(snippet.text)
        buffer = f"{buffer} {text}".strip()
        if len(buffer) >= 100:
            chunks.append(buffer)
            buffer = ""
    if buffer:
        chunks.append(buffer)
    return chunks


def summarize(chunks: list[str], fallback: str) -> tuple[str, str]:
    if not chunks:
        return (fallback[:420] or "영상 설명과 자막을 확인할 수 없습니다."), "description"
    words = [w for c in chunks for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", c.lower()) if w not in STOPWORDS]
    freq = Counter(words)
    scored = []
    for idx, chunk in enumerate(chunks):
        tokens = [w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", chunk.lower()) if w not in STOPWORDS]
        if tokens:
            scored.append((sum(freq[w] for w in tokens) / len(tokens), idx, chunk))
    selected = sorted(scored, reverse=True)[:3]
    selected.sort(key=lambda x: x[1])
    return " · ".join(x[2] for x in selected)[:520], "transcript"


def collect_youtube(channel: dict) -> list[dict]:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel['channel_id']}"
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    items = []
    for entry in feed.entries[:MAX_YOUTUBE_PER_CHANNEL]:
        title, link, published = clean_text(entry.get("title")), (entry.get("link") or "").strip(), parse_date(entry)
        if not title or not link or published < cutoff:
            continue
        video_id = entry.get("yt_videoid") or link.split("v=")[-1].split("&")[0]
        fallback = clean_text(entry.get("media_description") or entry.get("summary") or "")
        try:
            description, summary_source = summarize(transcript_chunks(video_id), fallback)
        except Exception:
            description, summary_source = summarize([], fallback)
        items.append({
            "id": hashlib.sha1(f"youtube|{channel['channel_id']}|{video_id}".encode()).hexdigest()[:16],
            "type": "youtube", "title": title, "description": description, "summary_source": summary_source,
            "url": link, "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg", "source": channel["name"],
            "source_priority": 1, "collection_method": "official_rss", "collection_label": "YouTube 공식 채널 RSS",
            "country": "대한민국", "category": "경제 유튜브", "published_at": published.isoformat(),
            "importance_score": min(100, importance_score(title, description, published, 1) + 5),
        })
    return items


def deduplicate(items: list[dict]) -> list[dict]:
    result, urls, titles = [], set(), set()
    for item in sorted(items, key=lambda x: x["published_at"], reverse=True):
        normalized = re.sub(r"[^가-힣a-z0-9]", "", item["title"].lower())[:90]
        if item["url"] in urls or normalized in titles:
            continue
        urls.add(item["url"]); titles.add(normalized); result.append(item)
    return result


def limit_per_source(items: list[dict]) -> list[dict]:
    counts, result = defaultdict(int), []
    for item in sorted(items, key=lambda x: (x.get("importance_score", 0), x["published_at"]), reverse=True):
        limit = MAX_YOUTUBE_PER_CHANNEL if item.get("type") == "youtube" else MAX_ITEMS_PER_SOURCE
        if counts[item["source"]] < limit:
            counts[item["source"]] += 1
            result.append(item)
    return result


def main() -> None:
    all_items, errors, statuses = [], [], []
    for source in SOURCES:
        items, runtime = collect_source(source)
        all_items.extend(items)
        status = {
            "name": source["name"], "domain": source["domain"], "category": source["category"],
            "priority": source["priority"], "restricted": source.get("restricted", False),
            "requires_auth": any(m.get("requires_auth") for m in source["methods"]),
            "alternative": source.get("alternative", ""), **runtime,
        }
        statuses.append(status)
        if runtime["status"] != "ok":
            errors.append(f"{source['name']}: all methods failed")
        print(f"{source['name']}: {len(items)} items via {runtime['label']}")
        time.sleep(0.5)
    for channel in YOUTUBE_CHANNELS:
        try:
            all_items.extend(collect_youtube(channel))
        except Exception as exc:
            errors.append(f"YouTube / {channel['name']}: {exc}")
    items = limit_per_source(deduplicate(all_items))
    items.sort(key=lambda x: (x["importance_score"], x["published_at"]), reverse=True)
    payload = {
        "updated_at": datetime.now(KST).isoformat(), "count": min(len(items), MAX_ITEMS),
        "source_status": statuses, "errors": errors, "news": items[:MAX_ITEMS],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {payload['count']} items to {OUTPUT}")


if __name__ == "__main__":
    main()
