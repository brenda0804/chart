# chart

한국투자증권(KIS) Open API로 국내주식 시세를 받아와 캔들 차트를 그리는 프로그램입니다.

## 기능

- KIS 접근토큰 발급 및 캐시 (24시간 유효)
- 국내주식 현재가 / 일봉(기간별 시세) 조회
- 캔들 차트 + 이동평균선(5·20) + 거래량 시각화 (PNG 저장)

## 요구사항

- Python 3.10+
- 한국투자증권 계좌 및 [KIS Developers](https://apiportal.koreainvestment.com/intro)에서 발급받은 AppKey / AppSecret

## 설치

```bash
pip install -r requirements.txt
```

## 설정

`.env.example`을 복사해 `.env`를 만들고 발급받은 키를 채웁니다.
`.env`는 `.gitignore`로 git에 올라가지 않습니다.

```bash
cp .env.example .env
```

```
KIS_APP_KEY=발급받은_앱키
KIS_APP_SECRET=발급받은_시크릿
KIS_ENV=vps        # vps=모의투자, prod=실전투자
```

## 실행

```bash
python main.py            # 삼성전자(005930) 최근 100일 일봉
python main.py 000660     # 종목코드 지정 (예: SK하이닉스)
```

실행하면 `chart_{종목코드}.png` 파일로 캔들 차트가 저장됩니다.

## 구조

```
chart/
├── main.py              # 진입점: 일봉 조회 → 캔들 차트 저장
├── src/
│   ├── config.py        # .env 로드, 환경별 도메인
│   └── kis_client.py    # KIS 토큰 발급 + 시세 조회
├── .env.example         # 키 템플릿 (실제 .env는 커밋 안 됨)
└── requirements.txt
```

## 개발 현황

- [x] KIS 인증 + 일봉 캔들 차트
- [ ] 실시간 WebSocket 체결가 연동
- [ ] 미국장(Finnhub 등) 연동
