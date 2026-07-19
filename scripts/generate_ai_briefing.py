from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI

KST = timezone(timedelta(hours=9))
DATA_FILE = Path("data/news.json")
MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"
MAX_INPUT_ITEMS = 35
WATCHLIST = [
    "삼성전자", "SK하이닉스", "현대차", "기아", "한화에어로스페이스",
    "HD현대중공업", "삼성중공업", "POSCO홀딩스", "LG에너지솔루션",
    "삼성SDI", "NAVER", "카카오", "KB금융", "신한지주", "현대건설", "대우건설",
]


def parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def select_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent = [item for item in items if parse_datetime(str(item.get("published_at", ""))) >= cutoff]
    recent.sort(
        key=lambda item: (
            int(item.get("source_priority", 0)),
            int(item.get("importance_score", 0)),
            parse_datetime(str(item.get("published_at", ""))),
        ),
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    for item in recent:
        source = str(item.get("source", "출처 미상"))
        if source_counts.get(source, 0) >= 4:
            continue
        selected.append(item)
        source_counts[source] = source_counts.get(source, 0) + 1
        if len(selected) >= MAX_INPUT_ITEMS:
            break
    return selected


def build_news_text(items: list[dict[str, Any]]) -> str:
    rows = []
    for index, item in enumerate(items, start=1):
        rows.append(
            "\n".join(
                [
                    f"[뉴스 {index}]",
                    f"ID: {item.get('id', '')}",
                    f"게시시각: {item.get('published_at', '')}",
                    f"분야: {item.get('category', '')}",
                    f"출처: {item.get('source', '')}",
                    f"제목: {item.get('title', '')}",
                    f"요약: {str(item.get('description', ''))[:600]}",
                ]
            )
        )
    return "\n\n".join(rows)


def prompt_for(items: list[dict[str, Any]]) -> str:
    now = datetime.now(KST).isoformat(timespec="minutes")
    return f"""
현재 시각은 {now}입니다. 당신은 한국 주식시장 리서치팀의 보조 분석가입니다.
아래에 제공된 공개 뉴스 제목과 요약만을 근거로, 최근 24시간과 최근 7일 브리핑을 각각 작성하십시오.

필수 원칙:
- 제공되지 않은 주가, 재무수치, 목표주가, 밸류에이션, 사건을 추정하거나 만들어내지 마십시오.
- 사실과 해석을 구분하고, 상반된 신호와 반대 시나리오를 함께 반영하십시오.
- 종목 의견은 아래 WATCHLIST에 포함된 종목만 허용합니다.
- 종목과 직접 연결되는 근거가 부족하면 추천 목록에 넣지 마십시오.
- 매수·매도 확정 지시가 아니라 '관심·분할매수 검토'와 '비중 축소·매도 검토'로 표현하십시오.
- 각 핵심 근거와 종목 의견에는 반드시 실제 뉴스 ID를 evidence_ids에 넣으십시오.
- 동일 이슈를 여러 매체가 반복 보도한 경우 하나의 근거로 묶으십시오.
- confidence는 0~100 정수이며 자료의 양, 출처 신뢰도, 신호 일관성을 반영하십시오.
- 출력은 설명이나 마크다운 없이 유효한 JSON 객체 하나만 반환하십시오.

WATCHLIST:
{json.dumps(WATCHLIST, ensure_ascii=False)}

JSON 형식:
{{
  "daily": {{
    "signal": "긍정|중립|경계",
    "title": "한 문장 시장 전망",
    "summary": "3~5문장의 균형 잡힌 요약",
    "confidence": 0,
    "drivers": [{{"sentiment":"긍정|부정|중립","title":"핵심 근거","evidence_ids":["뉴스ID"]}}],
    "buy_candidates": [{{"name":"종목명","code":"종목코드 또는 빈 문자열","sector":"업종","reason":"검토 이유","risk":"반대 시나리오 또는 위험","evidence_ids":["뉴스ID"]}}],
    "sell_candidates": [{{"name":"종목명","code":"종목코드 또는 빈 문자열","sector":"업종","reason":"축소 검토 이유","risk":"반대 시나리오 또는 확인사항","evidence_ids":["뉴스ID"]}}],
    "risks": ["핵심 리스크"],
    "checks": ["투자 전 추가 확인사항"]
  }},
  "weekly": {{
    "signal": "긍정|중립|경계",
    "title": "한 문장 시장 전망",
    "summary": "3~5문장의 균형 잡힌 요약",
    "confidence": 0,
    "drivers": [{{"sentiment":"긍정|부정|중립","title":"핵심 근거","evidence_ids":["뉴스ID"]}}],
    "buy_candidates": [{{"name":"종목명","code":"종목코드 또는 빈 문자열","sector":"업종","reason":"검토 이유","risk":"반대 시나리오 또는 위험","evidence_ids":["뉴스ID"]}}],
    "sell_candidates": [{{"name":"종목명","code":"종목코드 또는 빈 문자열","sector":"업종","reason":"축소 검토 이유","risk":"반대 시나리오 또는 확인사항","evidence_ids":["뉴스ID"]}}],
    "risks": ["핵심 리스크"],
    "checks": ["투자 전 추가 확인사항"]
  }}
}}

뉴스 자료:
{build_news_text(items)}
""".strip()


def extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def normalize_briefing(raw: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    item_map = {str(item.get("id", "")): item for item in items if item.get("id")}
    allowed_names = set(WATCHLIST)

    def evidence_links(ids: Any) -> list[dict[str, str]]:
        result = []
        for evidence_id in ids if isinstance(ids, list) else []:
            item = item_map.get(str(evidence_id))
            if item:
                result.append({
                    "id": str(item.get("id", "")),
                    "title": str(item.get("title", "")),
                    "url": str(item.get("url", "")),
                    "source": str(item.get("source", "")),
                })
        return result[:3]

    def normalize_period(value: Any) -> dict[str, Any]:
        value = value if isinstance(value, dict) else {}
        signal = str(value.get("signal", "중립"))
        if signal not in {"긍정", "중립", "경계"}:
            signal = "중립"
        try:
            confidence = max(0, min(100, int(value.get("confidence", 0))))
        except Exception:
            confidence = 0

        drivers = []
        for driver in value.get("drivers", []) if isinstance(value.get("drivers"), list) else []:
            if not isinstance(driver, dict):
                continue
            evidence = evidence_links(driver.get("evidence_ids"))
            if not evidence:
                continue
            sentiment = str(driver.get("sentiment", "중립"))
            if sentiment not in {"긍정", "부정", "중립"}:
                sentiment = "중립"
            drivers.append({"sentiment": sentiment, "title": str(driver.get("title", ""))[:180], "evidence": evidence})

        def candidates(key: str) -> list[dict[str, Any]]:
            result = []
            values = value.get(key, []) if isinstance(value.get(key), list) else []
            for candidate in values:
                if not isinstance(candidate, dict):
                    continue
                name = str(candidate.get("name", "")).strip()
                evidence = evidence_links(candidate.get("evidence_ids"))
                if name not in allowed_names or not evidence:
                    continue
                result.append({
                    "name": name,
                    "code": str(candidate.get("code", ""))[:12],
                    "sector": str(candidate.get("sector", ""))[:30],
                    "reason": str(candidate.get("reason", ""))[:300],
                    "risk": str(candidate.get("risk", ""))[:240],
                    "evidence": evidence,
                })
            return result[:5]

        return {
            "signal": signal,
            "title": str(value.get("title", "뉴스 흐름을 종합한 시장 전망"))[:180],
            "summary": str(value.get("summary", "분석 결과가 충분하지 않습니다."))[:900],
            "confidence": confidence,
            "drivers": drivers[:4],
            "buy_candidates": candidates("buy_candidates"),
            "sell_candidates": candidates("sell_candidates"),
            "risks": [str(x)[:80] for x in value.get("risks", []) if str(x).strip()][:6],
            "checks": [str(x)[:120] for x in value.get("checks", []) if str(x).strip()][:5],
        }

    return {
        "daily": normalize_period(raw.get("daily")),
        "weekly": normalize_period(raw.get("weekly")),
        "model": MODEL,
        "generated_at": datetime.now(KST).isoformat(),
        "analysis_type": "openai",
    }


def main() -> None:
    if not DATA_FILE.exists():
        raise SystemExit("data/news.json not found")

    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    items = select_items(payload.get("news", []))
    api_key = os.getenv("OPENAI_API_KEY", "").strip()

    if not api_key:
        payload["ai_status"] = {"status": "skipped", "reason": "OPENAI_API_KEY is not configured"}
        DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("OPENAI_API_KEY missing: rule-based briefing remains active")
        return

    if not items:
        payload["ai_status"] = {"status": "skipped", "reason": "No recent news items"}
        DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("No recent items: rule-based briefing remains active")
        return

    try:
        client = OpenAI(api_key=api_key, timeout=90.0, max_retries=2)
        response = client.responses.create(
            model=MODEL,
            input=prompt_for(items),
            store=False,
        )
        raw = extract_json(response.output_text)
        payload["ai_briefings"] = normalize_briefing(raw, items)
        payload["ai_status"] = {
            "status": "ok",
            "model": MODEL,
            "input_items": len(items),
            "generated_at": datetime.now(KST).isoformat(),
        }
        print(f"OpenAI briefing generated with {MODEL} from {len(items)} items")
    except Exception as exc:
        payload["ai_status"] = {"status": "failed", "reason": str(exc)[:300], "model": MODEL}
        print(f"OpenAI briefing failed: {exc}")

    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
