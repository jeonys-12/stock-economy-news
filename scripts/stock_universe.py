from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

KST = timezone(timedelta(hours=9))

# 매수·매도 추천 모니터링은 아래 지정 종목만 대상으로 합니다.
# 사용자 입력의 중복 종목(현대건설)은 한 번만 포함하고, 종목명은 공식 표기로 정규화했습니다.
FIXED_RECOMMENDATION_STOCKS: dict[str, dict[str, str]] = {
    "삼성전자": {"code": "005930", "sector": "반도체"},
    "SK하이닉스": {"code": "000660", "sector": "반도체"},
    "한미반도체": {"code": "042700", "sector": "반도체장비"},
    "삼성전기": {"code": "009150", "sector": "전자부품"},
    "LS ELECTRIC": {"code": "010120", "sector": "전력기기"},
    "현대차": {"code": "005380", "sector": "자동차"},
    "기아": {"code": "000270", "sector": "자동차"},
    "현대모비스": {"code": "012330", "sector": "자동차부품"},
    "한화에어로스페이스": {"code": "012450", "sector": "방산"},
    "HD현대중공업": {"code": "329180", "sector": "조선"},
    "LG에너지솔루션": {"code": "373220", "sector": "이차전지"},
    "LG이노텍": {"code": "011070", "sector": "전자부품"},
    "LG디스플레이": {"code": "034220", "sector": "디스플레이"},
    "LG씨엔에스": {"code": "064400", "sector": "IT서비스"},
    "SK이노베이션": {"code": "096770", "sector": "에너지·이차전지"},
    "SK이터닉스": {"code": "475150", "sector": "신재생에너지"},
    "포스코퓨처엠": {"code": "003670", "sector": "이차전지소재"},
    "NAVER": {"code": "035420", "sector": "인터넷"},
    "카카오": {"code": "035720", "sector": "인터넷"},
    "현대건설": {"code": "000720", "sector": "건설"},
    "한화시스템": {"code": "272210", "sector": "방산·ICT"},
    "한화오션": {"code": "042660", "sector": "조선"},
    "한화엔진": {"code": "082740", "sector": "조선기자재"},
    "두산에너빌리티": {"code": "034020", "sector": "발전·원전"},
    "삼성바이오로직스": {"code": "207940", "sector": "바이오"},
    "셀트리온": {"code": "068270", "sector": "바이오"},
    "알테오젠": {"code": "196170", "sector": "바이오"},
    "에이비엘바이오": {"code": "298380", "sector": "바이오"},
    "하나금융지주": {"code": "086790", "sector": "금융"},
    "우리금융지주": {"code": "316140", "sector": "금융"},
    "GS건설": {"code": "006360", "sector": "건설"},
    "LX하우시스": {"code": "108670", "sector": "건축자재"},
}


def build_stock_universe() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    universe: dict[str, dict[str, Any]] = {}
    for order, (name, meta) in enumerate(FIXED_RECOMMENDATION_STOCKS.items(), start=1):
        universe[name] = {
            "code": meta["code"],
            "sector": meta["sector"],
            "business_sector": meta["sector"],
            "universe_tags": ["FIXED_RECOMMENDATION_WATCHLIST"],
            "watchlist_order": order,
            "market_cap_rank": None,
        }

    status: dict[str, Any] = {
        "policy": "사용자 지정 매수·매도 추천 모니터링 종목",
        "mode": "fixed_recommendation_watchlist",
        "updated_at": datetime.now(KST).isoformat(),
        "total_stocks": len(universe),
        "duplicate_removed": ["현대건설"],
        "name_normalization": {
            "Sk이노베이션": "SK이노베이션",
        },
        "dynamic_kospi_top50": False,
        "dynamic_group_scan": False,
    }
    return universe, status
