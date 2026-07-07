"""데이터 저장소 — Supabase(클라우드 Postgres) REST 백엔드.

테이블: watchlist / memos / minute_data  (database/supabase_schema.sql 로 사전 생성)
- 관심종목 CRUD
- 메모(매매일지) upsert/조회/삭제  (종목+날짜당 1건)
- 1분봉 데이터 저장/로드 (Parquet → base64 로 minute_data.parquet 컬럼)

supabase-py 대신 requests 로 PostgREST 를 직접 호출한다(의존성 최소화).
"""
import base64
import io
import time

import pandas as pd
import requests

from . import config


class StoreError(RuntimeError):
    """저장소 접근 오류."""


def _headers(extra: dict | None = None) -> dict:
    h = {
        "apikey": config.SUPABASE_KEY,
        "Authorization": f"Bearer {config.SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _url(table: str) -> str:
    return f"{config.SUPABASE_URL}/rest/v1/{table}"


def _request(method: str, table: str, *, params=None, json=None, prefer=None) -> list:
    config.validate_supabase()
    extra = {"Prefer": prefer} if prefer else None
    try:
        resp = requests.request(
            method, _url(table), headers=_headers(extra),
            params=params, json=json, timeout=15,
        )
    except requests.RequestException as e:
        raise StoreError(f"Supabase 연결 실패: {e}") from e
    if resp.status_code >= 400:
        raise StoreError(f"Supabase 오류 {resp.status_code}: {resp.text[:200]}")
    if resp.text:
        try:
            return resp.json()
        except ValueError:
            return []
    return []


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


# ---- 관심종목 CRUD -------------------------------------------------------
def get_watchlist() -> list[dict]:
    return _request("GET", "watchlist", params={"select": "code,name,added", "order": "added.asc"})


def add_watch(code: str, name: str) -> bool:
    """추가. 이미 있으면 False."""
    existing = _request("GET", "watchlist", params={"code": f"eq.{code}", "select": "code"})
    if existing:
        return False
    _request("POST", "watchlist", json={"code": code, "name": name, "added": _now()},
             prefer="return=minimal")
    return True


def update_watch(code: str, name: str) -> None:
    _request("PATCH", "watchlist", params={"code": f"eq.{code}"},
             json={"name": name}, prefer="return=minimal")


def remove_watch(code: str) -> None:
    _request("DELETE", "watchlist", params={"code": f"eq.{code}"}, prefer="return=minimal")


# ---- 메모 CRUD -----------------------------------------------------------
def _memo_id(code: str, date: str) -> str:
    return f"{code}_{date}"


def upsert_memo(code: str, name: str, date: str, memo: str) -> None:
    """(code, date) 기준 upsert."""
    rec = {
        "id": _memo_id(code, date), "code": code, "name": name,
        "date": date, "memo": memo, "updated": _now(),
    }
    _request("POST", "memos", params={"on_conflict": "id"}, json=rec,
             prefer="resolution=merge-duplicates,return=minimal")


def get_memos() -> list[dict]:
    return _request("GET", "memos", params={"select": "*", "order": "updated.desc"})


def get_memo(code: str, date: str) -> dict | None:
    rows = _request("GET", "memos", params={"id": f"eq.{_memo_id(code, date)}", "select": "*"})
    return rows[0] if rows else None


def delete_memo(memo_id: str) -> None:
    _request("DELETE", "memos", params={"id": f"eq.{memo_id}"}, prefer="return=minimal")


# ---- 1분봉 데이터 (Parquet base64) ---------------------------------------
def save_minute_data(code: str, name: str, date: str, df: pd.DataFrame) -> None:
    buf = io.BytesIO()
    df.to_parquet(buf)  # index(=시각) 포함 저장
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    rec = {
        "id": _memo_id(code, date), "code": code, "name": name, "date": date,
        "parquet": encoded, "rows": int(len(df)), "updated": _now(),
    }
    _request("POST", "minute_data", params={"on_conflict": "id"}, json=rec,
             prefer="resolution=merge-duplicates,return=minimal")


def load_minute_data(code: str, date: str) -> pd.DataFrame | None:
    rows = _request("GET", "minute_data",
                    params={"id": f"eq.{_memo_id(code, date)}", "select": "parquet"})
    if not rows:
        return None
    raw = base64.b64decode(rows[0]["parquet"])
    return pd.read_parquet(io.BytesIO(raw))


def delete_minute_data(code: str, date: str) -> None:
    _request("DELETE", "minute_data", params={"id": f"eq.{_memo_id(code, date)}"},
             prefer="return=minimal")


def ping() -> bool:
    """연결/스키마 확인용. 테이블 접근이 되면 True."""
    _request("GET", "watchlist", params={"select": "code", "limit": "1"})
    return True
