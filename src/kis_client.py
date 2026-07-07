"""한국투자증권(KIS) Open API 클라이언트.

- 접근토큰(access token) 발급 및 파일 캐시 (KIS 는 하루 1회 발급 권장, 24시간 유효)
- 국내주식 현재가 조회
- 국내주식 일봉(기간별 시세) 조회
"""
import json
import time
from pathlib import Path

import requests

from . import config

# 토큰 캐시 파일 (.gitignore 에 의해 커밋 안 됨)
_TOKEN_CACHE = Path(__file__).resolve().parent.parent / "token.json"


def _load_cached_token() -> str | None:
    if not _TOKEN_CACHE.exists():
        return None
    try:
        data = json.loads(_TOKEN_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    # 만료 60초 전이면 무효 처리
    if data.get("expires_at", 0) - 60 < time.time():
        return None
    return data.get("access_token")


def _save_cached_token(token: str, expires_in: int) -> None:
    _TOKEN_CACHE.write_text(
        json.dumps({"access_token": token, "expires_at": time.time() + expires_in}),
        encoding="utf-8",
    )


def get_access_token() -> str:
    """접근토큰 발급 (캐시가 유효하면 재사용)."""
    cached = _load_cached_token()
    if cached:
        return cached

    config.validate()
    url = f"{config.BASE_URL}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": config.KIS_APP_KEY,
        "appsecret": config.KIS_APP_SECRET,
    }
    resp = requests.post(url, json=body, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    token = data["access_token"]
    _save_cached_token(token, int(data.get("expires_in", 86400)))
    return token


def _headers(tr_id: str) -> dict:
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {get_access_token()}",
        "appkey": config.KIS_APP_KEY,
        "appsecret": config.KIS_APP_SECRET,
        "tr_id": tr_id,
    }


def get_current_price(stock_code: str) -> dict:
    """국내주식 현재가 조회. stock_code 예: '005930'(삼성전자)."""
    url = f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code}
    resp = requests.get(url, headers=_headers("FHKST01010100"), params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()["output"]


def get_daily_chart(stock_code: str, start: str, end: str, period: str = "D") -> list[dict]:
    """국내주식 기간별 시세(일/주/월봉).

    start, end: 'YYYYMMDD' 형식.  period: D=일봉, W=주봉, M=월봉.
    반환: 최신→과거 순의 OHLC 리스트.
    """
    url = f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": stock_code,
        "FID_INPUT_DATE_1": start,
        "FID_INPUT_DATE_2": end,
        "FID_PERIOD_DIV_CODE": period,
        "FID_ORG_ADJ_PRC": "0",  # 0=수정주가 반영
    }
    resp = requests.get(url, headers=_headers("FHKST03010100"), params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()["output2"]
