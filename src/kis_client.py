"""한국투자증권(KIS) Open API 클라이언트.

- 접근토큰(access token) 발급 및 파일 캐시 (24시간 유효)
- 국내주식 현재가 / 일봉 / 당일 1분봉 / 투자자별 매매동향 조회
- 초당 호출제한(EGW00201) 회피용 쓰로틀 + 재시도 내장
"""
import json
import time
from pathlib import Path

import requests

from . import config

# 토큰 캐시 파일 (.gitignore 에 의해 커밋 안 됨)
_TOKEN_CACHE = Path(__file__).resolve().parent.parent / "token.json"

# ---- 트래픽 제어 (초당 호출제한 회피 + 제한적 병렬 허용) ------------------
import threading

_MIN_INTERVAL = 0.15          # 호출 시작 간 최소 간격(초)
_MAX_CONCURRENT = 3           # 동시 in-flight 요청 상한
_sema = threading.Semaphore(_MAX_CONCURRENT)
_interval_lock = threading.Lock()
_last_call_at = [0.0]


def _throttle() -> None:
    """호출 시작 시각을 _MIN_INTERVAL 간격으로 스케줄링(스레드 세이프).

    lock 안에서 다음 슬롯만 예약하고 sleep은 밖에서 → 스레드들이 계단식으로 진행.
    """
    with _interval_lock:
        nxt = max(time.time(), _last_call_at[0] + _MIN_INTERVAL)
        _last_call_at[0] = nxt
    delay = nxt - time.time()
    if delay > 0:
        time.sleep(delay)


# ---- 토큰 ----------------------------------------------------------------
def _load_cached_token() -> str | None:
    if not _TOKEN_CACHE.exists():
        return None
    try:
        data = json.loads(_TOKEN_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("expires_at", 0) - 60 < time.time():  # 만료 60초 전이면 무효
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
        "custtype": "P",
    }


class KISError(RuntimeError):
    """KIS API 오류 (rt_cd != '0')."""


def _get(url: str, tr_id: str, params: dict, retries: int = 3) -> dict:
    """GET 요청 + 쓰로틀 + 초당제한/일시오류 재시도."""
    last = {}
    for attempt in range(retries):
        _throttle()
        try:
            with _sema:  # 동시 요청 수 제한
                resp = requests.get(url, headers=_headers(tr_id), params=params, timeout=10)
        except requests.RequestException:
            time.sleep(0.5)
            continue

        if resp.status_code == 200:
            data = resp.json()
            if data.get("rt_cd") == "0":
                return data
            # 초당 거래건수 초과 → 잠시 쉬고 재시도
            if data.get("msg_cd") == "EGW00201" or "초당" in data.get("msg1", ""):
                time.sleep(0.6)
                last = data
                continue
            raise KISError(f"[{tr_id}] {data.get('msg_cd')} {data.get('msg1')}")

        if resp.status_code in (429, 500, 502, 503):
            time.sleep(0.6)
            continue
        resp.raise_for_status()

    raise KISError(f"[{tr_id}] 재시도 초과: {last.get('msg1', '알 수 없는 오류')}")


# ---- 시세 조회 -----------------------------------------------------------
def get_current_price(stock_code: str) -> dict:
    """국내주식 현재가. stock_code 예: '005930'."""
    url = f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code}
    return _get(url, "FHKST01010100", params)["output"]


def get_daily_chart(stock_code: str, start: str, end: str, period: str = "D") -> list[dict]:
    """일/주/월봉. start,end='YYYYMMDD'. period: D/W/M. 반환: 과거 항목 포함 리스트."""
    url = f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": stock_code,
        "FID_INPUT_DATE_1": start,
        "FID_INPUT_DATE_2": end,
        "FID_PERIOD_DIV_CODE": period,
        "FID_ORG_ADJ_PRC": "0",
    }
    return _get(url, "FHKST03010100", params).get("output2", [])


def _minus_one_minute(hhmmss: str) -> str:
    """'093000' → '092900'. 페이지네이션용."""
    total = int(hhmmss[:2]) * 3600 + int(hhmmss[2:4]) * 60 + int(hhmmss[4:6]) - 60
    total = max(total, 0)
    return f"{total // 3600:02d}{(total % 3600) // 60:02d}{total % 60:02d}"


def get_minute_chart(stock_code: str, to_hour: str = "153000", max_calls: int = 14) -> list[dict]:
    """당일 1분봉. 한 번에 30건씩 반환되므로 시간을 되짚어가며 하루치를 모은다.

    ⚠️ KIS 무료 API 제약: '오늘' 장중 데이터만 조회 가능(과거 날짜 1분봉 불가).
    반환: 시간 오름차순 dict 리스트(중복 제거).
    """
    url = f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
    collected: dict[str, dict] = {}
    hour = to_hour
    for _ in range(max_calls):
        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_HOUR_1": hour,
            "FID_PW_DATA_INCU_YN": "Y",
        }
        rows = _get(url, "FHKST03010200", params).get("output2", [])
        if not rows:
            break
        for r in rows:
            t = r.get("stck_cntg_hour")
            if t:
                collected[t] = r
        earliest = min(r["stck_cntg_hour"] for r in rows if r.get("stck_cntg_hour"))
        if earliest <= "090000":
            break
        hour = _minus_one_minute(earliest)
    return [collected[t] for t in sorted(collected)]


def get_investor_trend(stock_code: str) -> list[dict]:
    """종목별 투자자매매동향(일 단위). 외인/기관/개인 순매수 수량 등.

    ⚠️ 일(day) 단위만 제공(분 단위 실시간 수급 아님).
    """
    url = f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor"
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code}
    try:
        return _get(url, "FHKST01010900", params).get("output", [])
    except KISError:
        return []  # 모의투자 등에서 미지원이면 조용히 건너뜀
