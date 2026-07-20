from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DATA_FILE = Path("data/news.json")

# 한국부동산원 사이트의 조직·메뉴·검색용 페이지는 뉴스가 아니므로 제외합니다.
REB_EXCLUDED_TITLE_PATTERNS = (
    r"^부서소개(?:\s*-\s*reb\.or\.kr)?$",
    r"^부서소개$",
)


def normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def is_excluded(item: dict[str, Any]) -> tuple[bool, str]:
    source = normalized_text(item.get("source"))
    domain = normalized_text(item.get("source_domain")).lower()
    title = normalized_text(item.get("title"))
    description = normalized_text(item.get("description"))

    is_reb = source == "한국부동산원" or domain == "reb.or.kr"
    if not is_reb:
        return False, ""

    for pattern in REB_EXCLUDED_TITLE_PATTERNS:
        if re.fullmatch(pattern, title, flags=re.IGNORECASE):
            return True, "한국부동산원 부서소개 페이지"

    # Google News가 제목의 출처 문자열을 제거하지 못한 경우까지 방어합니다.
    combined = f"{title} {description}".lower()
    if "부서소개" in combined and "reb.or.kr" in combined:
        return True, "한국부동산원 부서소개 페이지"

    return False, ""


def main() -> None:
    if not DATA_FILE.exists():
        raise SystemExit("data/news.json not found")

    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    news = payload.get("news", [])
    if not isinstance(news, list):
        raise SystemExit("data/news.json news field is not a list")

    kept: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for item in news:
        if not isinstance(item, dict):
            continue
        blocked, reason = is_excluded(item)
        if blocked:
            excluded.append({
                "id": str(item.get("id", "")),
                "title": normalized_text(item.get("title")),
                "source": normalized_text(item.get("source")),
                "reason": reason,
            })
            continue
        kept.append(item)

    payload["news"] = kept
    payload["count"] = len(kept)
    payload["exclusion_status"] = {
        "excluded_count": len(excluded),
        "rules": ["한국부동산원 부서소개 페이지 제외"],
        "excluded_items": excluded[:20],
    }
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"News exclusion filter: removed={len(excluded)}, kept={len(kept)}")


if __name__ == "__main__":
    main()
