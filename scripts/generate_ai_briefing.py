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
STOCK_DATA_FILE = Path("data/stock_data.json")
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


def compact_stock_data(stock_payload: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for name, row in stock_payload.get("stocks", {}).items():
        if name not in WATCHLIST or not isinstance(row, dict):
            continue
        market = row.get("market", {}) if isinstance(row.get("market"), dict) else {}
        financials = row.get("financials", {}) if isinstance(row.get("financials"), dict) else {}
        consensus = row.get("consensus", {}) if isinstance(row.get("consensus"), dict) else {}
        quantitative = row.get("quantitative", {}) if isinstance(row.get("quantitative"), dict) else {}
        compact[name] = {
            "code": row.get("code"),
            "sector": row.get("sector"),
            "quantitative": quantitative,
            "market": {
                "status": market.get("status"),
                "as_of": market.get("as_of"),
                "current_price": market.get("current_price"),
                "return_5d_pct": market.get("return_5d_pct"),
                "return_20d_pct": market.get("return_20d_pct"),
                "return_60d_pct": market.get("return_60d_pct"),
                "valuation": market.get("valuation", {}),
                "investor_flow": market.get("investor_flow", {}),
            },
            "financials": {
                "status": financials.get("status"),
                "business_year": financials.get("business_year"),
                "report_name": financials.get("report_name"),
                "revenue": financials.get("revenue"),
                "revenue_growth_pct": financials.get("revenue_growth_pct"),
                "operating_profit": financials.get("operating_profit"),
                "operating_profit_growth_pct": financials.get("operating_profit_growth_pct"),
                "net_income": financials.get("net_income"),
                "net_income_growth_pct": financials.get("net_income_growth_pct"),
                "debt_ratio_pct": financials.get("debt_ratio_pct"),
            },
            "consensus": {
                "status": consensus.get("status"),
                "target_price": consensus.get("target_price"),
                "target_upside_pct": consensus.get("target_upside_pct"),
                "opinion": consensus.get("opinion"),
                "analyst_count": consensus.get("analyst_count"),
            },
        }
    return compact


def prompt_for(items: list[dict[str, Any]], stock_data: dict[str, Any]) -> str:
    now = datetime.now(KST).isoformat(timespec="minutes")
    return f"""
현재 시각은 {now}입니다. 당신은 한국 주식시장 리서치팀의 보조 분석가입니다.
아래 공개 뉴스와 정량 데이터를 함께 사용해 최근 24시간 및 최근 7일 브리핑을 각각 작성하십시오.

정량 데이터 구성:
- OpenDART: 최근 연결재무제표의 매출·영업이익·순이익·부채비율과 전년 동기 증감률
- FnGuide CompanyGuide 공개 화면: 투자의견·목표주가·상승여력·추정기관 수
- KRX/pykrx: 현재가, 5·20·60거래일 수익률, PER·PBR·배당수익률, 최근 약 10거래일 외국인·기관 순매수
- quantitative.score: 위 데이터로 계산한 보조 점수. 15 이상은 긍정, -15 이하는 부정, 그 사이는 중립

필수 원칙:
- 제공되지 않은 수치, 목표주가, 사건을 추정하거나 만들어내지 마십시오.
- 뉴스만 긍정적이거나 정량 데이터만 긍정적인 경우 추천하지 말고 상충 신호로 설명하십시오.
- 관심·분할매수 검토는 quantitative.score가 15 이상이고 available_dimensions가 2 이상이며, 해당 종목의 직접 뉴스 근거가 있을 때만 허용합니다.
- 비중 축소·매도 검토는 quantitative.score가 -15 이하이고 available_dimensions가 2 이상이며, 해당 종목의 직접 뉴스 근거가 있을 때만 허용합니다.
- 각 후보 reason에는 재무, 컨센서스, 밸류에이션, 수급, 모멘텀 중 확보된 근거를 최소 2개 명시하십시오.
- 단순 저PER·저PBR만으로 매수 후보를 만들지 말고 이익 추세 및 수급과 함께 판단하십시오.
- 목표주가 상승여력이 높아도 실적 악화나 외국인·기관 동반 순매도이면 위험을 명시하십시오.
- 급등 종목은 추격매수 위험을, 급락 종목은 가치함정 가능성을 반대 시나리오로 검토하십시오.
- 종목 의견은 WATCHLIST에 포함된 종목만 허용합니다.
- 매수·매도 확정 지시가 아니라 '관심·분할매수 검토'와 '비중 축소·매도 검토'로 표현하십시오.
- 각 핵심 근거와 종목 의견에는 반드시 실제 뉴스 ID를 evidence_ids에 넣으십시오.
- confidence는 자료 가용성, 출처 신뢰도, 뉴스와 정량 신호의 일관성을 반영하십시오.
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
    "buy_candidates": [{{"name":"종목명","code":"종목코드","sector":"업종","reason":"뉴스와 정량근거를 함께 반영한 이유","risk":"반대 시나리오","evidence_ids":["뉴스ID"]}}],
    "sell_candidates": [{{"name":"종목명","code":"종목코드","sector":"업종","reason":"뉴스와 정량근거를 함께 반영한 이유","risk":"반대 시나리오","evidence_ids":["뉴스ID"]}}],
    "risks": ["핵심 리스크"],
    "checks": ["투자 전 추가 확인사항"]
  }},
  "weekly": {{
    "signal": "긍정|중립|경계",
    "title": "한 문장 시장 전망",
    "summary": "3~5문장의 균형 잡힌 요약",
    "confidence": 0,
    "drivers": [{{"sentiment":"긍정|부정|중립","title":"핵심 근거","evidence_ids":["뉴스ID"]}}],
    "buy_candidates": [{{"name":"종목명","code":"종목코드","sector":"업종","reason":"뉴스와 정량근거를 함께 반영한 이유","risk":"반대 시나리오","evidence_ids":["뉴스ID"]}}],
    "sell_candidates": [{{"name":"종목명","code":"종목코드","sector":"업종","reason":"뉴스와 정량근거를 함께 반영한 이유","risk":"반대 시나리오","evidence_ids":["뉴스ID"]}}],
    "risks": ["핵심 리스크"],
    "checks": ["투자 전 추가 확인사항"]
  }}
}}

종목 정량 데이터:
{json.dumps(compact_stock_data(stock_data), ensure_ascii=False)}

뉴스 자료:
{build_news_text(items)}
""".strip()


def extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def normalize_briefing(raw: dict[str, Any], items: list[dict[str, Any]], stock_data: dict[str, Any]) -> dict[str, Any]:
    item_map = {str(item.get("id", "")): item for item in items if item.get("id")}
    stock_map = stock_data.get("stocks", {}) if isinstance(stock_data.get("stocks"), dict) else {}
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

    def candidate_allowed(name: str, key: str) -> tuple[bool, dict[str, Any]]:
        row = stock_map.get(name, {}) if isinstance(stock_map.get(name), dict) else {}
        quantitative = row.get("quantitative", {}) if isinstance(row.get("quantitative"), dict) else {}
        score = float(quantitative.get("score", 0) or 0)
        dimensions = int(quantitative.get("available_dimensions", 0) or 0)
        threshold_ok = score >= 15 if key == "buy_candidates" else score <= -15
        return dimensions >= 2 and threshold_ok, row

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
                allowed, row = candidate_allowed(name, key)
                if name not in allowed_names or not evidence or not allowed:
                    continue
                quantitative = row.get("quantitative", {})
                market = row.get("market", {})
                result.append({
                    "name": name,
                    "code": str(row.get("code") or candidate.get("code", ""))[:12],
                    "sector": str(row.get("sector") or candidate.get("sector", ""))[:30],
                    "reason": str(candidate.get("reason", ""))[:420],
                    "risk": str(candidate.get("risk", ""))[:300],
                    "evidence": evidence,
                    "quantitative_score": quantitative.get("score"),
                    "score_components": quantitative.get("components", {}),
                    "data_dimensions": quantitative.get("available_dimensions", 0),
                    "metrics": {
                        "current_price": market.get("current_price"),
                        "return_20d_pct": market.get("return_20d_pct"),
                        "per": market.get("valuation", {}).get("per") if isinstance(market.get("valuation"), dict) else None,
                        "pbr": market.get("valuation", {}).get("pbr") if isinstance(market.get("valuation"), dict) else None,
                    },
                })
            return result[:5]

        return {
            "signal": signal,
            "title": str(value.get("title", "뉴스와 정량지표를 종합한 시장 전망"))[:180],
            "summary": str(value.get("summary", "분석 결과가 충분하지 않습니다."))[:1000],
            "confidence": confidence,
            "drivers": drivers[:4],
            "buy_candidates": candidates("buy_candidates"),
            "sell_candidates": candidates("sell_candidates"),
            "risks": [str(x)[:100] for x in value.get("risks", []) if str(x).strip()][:6],
            "checks": [str(x)[:140] for x in value.get("checks", []) if str(x).strip()][:6],
        }

    return {
        "daily": normalize_period(raw.get("daily")),
        "weekly": normalize_period(raw.get("weekly")),
        "model": MODEL,
        "generated_at": datetime.now(KST).isoformat(),
        "analysis_type": "openai_multifactor",
        "methodology": stock_data.get("methodology", {}),
    }


def main() -> None:
    if not DATA_FILE.exists():
        raise SystemExit("data/news.json not found")

    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    stock_data = json.loads(STOCK_DATA_FILE.read_text(encoding="utf-8")) if STOCK_DATA_FILE.exists() else {"stocks": {}}
    items = select_items(payload.get("news", []))
    api_key = os.getenv("OPENAI_API_KEY", "").strip()

    payload["stock_data_status"] = {
        "status": "ok" if stock_data.get("stocks") else "unavailable",
        "updated_at": stock_data.get("updated_at"),
        "stock_count": len(stock_data.get("stocks", {})),
        "errors": stock_data.get("errors", [])[:10],
    }

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
        client = OpenAI(api_key=api_key, timeout=120.0, max_retries=2)
        response = client.responses.create(model=MODEL, input=prompt_for(items, stock_data), store=False)
        raw = extract_json(response.output_text)
        payload["ai_briefings"] = normalize_briefing(raw, items, stock_data)
        payload["ai_status"] = {
            "status": "ok",
            "model": MODEL,
            "input_items": len(items),
            "stock_factors": len(stock_data.get("stocks", {})),
            "analysis_type": "openai_multifactor",
            "generated_at": datetime.now(KST).isoformat(),
        }
        print(f"OpenAI multifactor briefing generated with {MODEL} from {len(items)} news and {len(stock_data.get('stocks', {}))} stocks")
    except Exception as exc:
        payload["ai_status"] = {"status": "failed", "reason": str(exc)[:300], "model": MODEL}
        print(f"OpenAI briefing failed: {exc}")

    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
