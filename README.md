# 주식 / 경제 뉴스 몰아보기

GitHub Actions가 대한민국·미국·글로벌 공식기관과 주요 경제 콘텐츠를 RSS로 자동 수집하고 GitHub Pages에서 보여주는 정적 웹 대시보드입니다.

## 주요 기능

- 대한민국 / 미국 / 글로벌 국가·지역 필터
- 공식기관 발표, 기업공시, 통화정책, 경제지표, 증권시장, 건설·부동산 뉴스 통합 모니터링
- 공식기관 자료 우선 정렬 및 중요도 평가
- 24시간 / 7일 기간 필터
- 기관명·기업명·정책·종목·공시 검색
- 주요 경제 YouTube 채널 모니터링
- GitHub Actions 자동 업데이트 및 수동 실행 지원

## 모니터링 공식기관

### 대한민국

- 한국은행
- 금융위원회
- 금융감독원 DART
- 한국거래소 KRX·KIND
- 기획재정부
- 산업통상자원부
- 국토교통부
- 통계청
- 한국부동산원
- 관세청

### 미국

- Federal Reserve
- SEC EDGAR
- U.S. Bureau of Labor Statistics
- U.S. Bureau of Economic Analysis
- U.S. Treasury
- U.S. Energy Information Administration

### 글로벌

- IMF
- OECD
- World Bank

## 최초 설정

1. 저장소의 **Settings → Pages**로 이동합니다.
2. **Build and deployment → Source**를 `GitHub Actions`로 선택합니다.
3. 저장소의 **Actions → Collect and Deploy News → Run workflow**를 한 번 실행합니다.
4. 배포가 끝나면 Pages 주소에서 대시보드를 확인합니다.

## 데이터 수집 방식

- 공식기관명과 공식 도메인을 조합한 Google News RSS 검색 결과를 사용합니다.
- 제목, 링크, 게시일, 출처, 대상 기관, 국가, 카테고리와 중요도만 저장합니다.
- 기사 전문은 저장하지 않습니다.
- 공식 도메인 또는 기관명이 확인되는 항목에는 `official: true`가 부여되어 우선 표시됩니다.
- 기관 웹사이트가 RSS를 제공하지 않거나 접근을 제한하는 경우에는 해당 기관 발표를 인용한 언론기사가 함께 수집될 수 있습니다. 투자 판단 전 원문을 확인해야 합니다.

## 카테고리

- 통화정책·금리
- 기업공시
- 증권시장
- 금융정책·규제
- 물가·고용·경기
- 정부 재정·세제
- 산업·수출
- 건설·부동산
- 에너지·원자재
- 글로벌 경제
- 경제 유튜브

## 구조

```text
.
├─ index.html
├─ style.css
├─ app.js
├─ data/news.json
├─ scripts/collect_news.py
├─ requirements.txt
└─ .github/workflows/update-news.yml
```

## 참고

본 대시보드는 투자 참고용 정보 수집 도구이며 투자 권유 또는 수익을 보장하지 않습니다. 자동 분류와 중요도 점수는 오류가 있을 수 있으므로 반드시 공식 원문을 확인하십시오.
