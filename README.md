# 주식 / 경제 뉴스 몰아보기

GitHub Actions가 한국·미국의 경제정책, 금융시장, 건설·부동산 뉴스를 RSS로 자동 수집하고 GitHub Pages에서 보여주는 정적 웹 대시보드입니다.

## 주요 기능

- 한국 / 미국 국가 필터
- 경제정책 / 금융시장 / 건설·부동산 카테고리 필터
- 24시간 / 7일 / 30일 기간 필터
- 키워드 검색
- 중요 뉴스 우선 정렬
- 매일 오전 6시 30분(KST) 자동 업데이트
- GitHub Actions 수동 실행 지원

## 최초 설정

1. 저장소의 **Settings → Pages**로 이동합니다.
2. **Build and deployment → Source**를 `GitHub Actions`로 선택합니다.
3. 저장소의 **Actions → Collect and Deploy News → Run workflow**를 한 번 실행합니다.
4. 배포가 끝나면 Pages 주소에서 대시보드를 확인합니다.

## 데이터 수집 방식

Google News RSS 검색 결과를 이용하며, 공개 RSS 제목·링크·게시일·출처만 저장합니다. 기사 전문은 저장하지 않습니다.

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

본 대시보드는 투자 참고용 정보 수집 도구이며 투자 권유 또는 수익을 보장하지 않습니다.
