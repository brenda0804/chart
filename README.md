# chart — 관심종목 · 단타 복기 매매일지 대시보드

한국투자증권(KIS) Open API + Streamlit으로 만든 **국내주식 관심종목 관리 & 당일 단타 복기용 매매일지** 대시보드입니다.

## 기능

- **관심 종목 CRUD**: 사이드바에서 종목 추가/수정/삭제 (`data_store.json` 저장)
- **멀티 타임프레임 차트**
  - 일봉: 캔들 + 이평선(5·20) + 거래량 + **외인/기관/개인 누적 수급** 보조패널
  - 1분봉: 캔들 + 이평선 + 거래량
- **1,000만 원 최적 단타 하이라이트**: 당일 1분봉에서 수익이 최대인 매수→매도 구간을 찾아 음영 표시, 예상 수익률·수익금을 타이틀에 표기
- **분석 이미지 자동 저장**: `outputs/[종목명]/[날짜]_분석.png`
- **날짜별 메모(매매일지)**: 종목·날짜별 메모를 차트 이미지 경로와 함께 저장
- **종합 매매일지(모아보기 + 역추적)**: 모든 메모를 검색·필터, 행 클릭 시 당시 차트 이미지를 즉시 로드해 복기

## ⚠️ KIS 무료 API의 구조적 제약

- **과거 날짜 1분봉은 조회 불가** — KIS 분봉 API는 *당일* 장중만 제공. 그래서 매일 분석 시점에 PNG로 저장해 두고, 나중에 메모 모아보기에서 그 이미지를 불러 복기하는 구조입니다.
- **수급은 일(day) 단위** — 분 단위 실시간 외인/기관 수급은 무료 범위 밖이라, 일봉 차트에 일별 누적 수급으로 표시합니다.

## 요구사항

- Python 3.10+
- 한국투자증권 계좌 + [KIS Developers](https://apiportal.koreainvestment.com/intro) AppKey/AppSecret

## 설치

```bash
pip install -r requirements.txt
```

> 이 PC는 `python`이 Windows 스토어 스텁이므로 `py` 런처를 쓰세요: `py -m pip install -r requirements.txt`

## 설정

`.env.example`을 복사해 `.env`를 만들고 키를 채웁니다. `.env`는 git에 올라가지 않습니다.

```
KIS_APP_KEY=발급받은_앱키
KIS_APP_SECRET=발급받은_시크릿
KIS_ENV=vps        # vps=모의투자, prod=실전투자
```

## 실행

```bash
streamlit run app.py
```

브라우저(기본 http://localhost:8501)가 열립니다.
1. 사이드바에서 관심 종목 추가 → 2. [차트 분석 & 메모] 탭에서 [분석 실행] → 3. 메모 작성/저장 → 4. [종합 매매일지] 탭에서 모아보기·복기

### 참고: 단순 차트만 그리기 (CLI)

```bash
py main.py 005930     # 일봉 캔들 차트 PNG만 생성
```

## 구조

```
chart/
├── app.py                # Streamlit 대시보드 (메인)
├── main.py               # CLI 일봉 차트 (부가)
├── src/
│   ├── config.py         # .env 로드, 환경별 도메인
│   ├── kis_client.py     # KIS 토큰/시세/분봉/수급 + 쓰로틀·재시도
│   ├── store.py          # data_store.json (관심종목 + 메모)
│   └── charting.py       # 차트 렌더링 + 최적 단타 분석
├── outputs/              # 분석 이미지 (git 제외)
├── data_store.json       # 로컬 데이터 (git 제외)
├── .env.example
└── requirements.txt
```

## 데이터/보안

- `.env`(키), `token.json`(토큰), `data_store.json`(메모), `outputs/`(이미지)는 모두 `.gitignore`로 커밋되지 않습니다.
- KIS 초당 호출 제한은 `kis_client.py`의 쓰로틀(호출 간 0.3초)과 재시도로 회피합니다.

## 개발 현황

- [x] 관심종목 CRUD + 일봉/수급 차트
- [x] 1분봉 + 1천만원 최적 단타 하이라이트 + PNG 저장
- [x] 날짜별 메모 + 종합 매매일지 모아보기/역추적
- [ ] 실시간 WebSocket 체결가 스트리밍
- [ ] 미국장(Finnhub) 연동
