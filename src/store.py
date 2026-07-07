"""로컬 데이터 저장소 (data_store.json 단일 파일).

- watchlist: 관심 종목 목록 [{code, name, added}]
- memos: 날짜별 매매일지 메모 [{id, code, name, date, memo, image, updated}]

메모는 (종목코드, 날짜) 조합당 1건으로 upsert 한다.
"""
import json
import time
from pathlib import Path

_STORE_PATH = Path(__file__).resolve().parent.parent / "data_store.json"
_DEFAULT = {"watchlist": [], "memos": []}


def _load() -> dict:
    if not _STORE_PATH.exists():
        return {"watchlist": [], "memos": []}
    try:
        data = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"watchlist": [], "memos": []}
    data.setdefault("watchlist", [])
    data.setdefault("memos", [])
    return data


def _save(data: dict) -> None:
    _STORE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---- 관심종목 CRUD -------------------------------------------------------
def get_watchlist() -> list[dict]:
    return _load()["watchlist"]


def add_watch(code: str, name: str) -> bool:
    """추가. 이미 있으면 False."""
    data = _load()
    if any(w["code"] == code for w in data["watchlist"]):
        return False
    data["watchlist"].append(
        {"code": code, "name": name, "added": _now()}
    )
    _save(data)
    return True


def update_watch(code: str, name: str) -> None:
    data = _load()
    for w in data["watchlist"]:
        if w["code"] == code:
            w["name"] = name
    _save(data)


def remove_watch(code: str) -> None:
    data = _load()
    data["watchlist"] = [w for w in data["watchlist"] if w["code"] != code]
    _save(data)


# ---- 메모 CRUD -----------------------------------------------------------
def _memo_id(code: str, date: str) -> str:
    return f"{code}_{date}"


def upsert_memo(code: str, name: str, date: str, memo: str, image: str) -> None:
    """(code, date) 기준 upsert. 있으면 갱신, 없으면 추가."""
    data = _load()
    mid = _memo_id(code, date)
    rec = {
        "id": mid,
        "code": code,
        "name": name,
        "date": date,
        "memo": memo,
        "image": image,
        "updated": _now(),
    }
    for i, m in enumerate(data["memos"]):
        if m["id"] == mid:
            data["memos"][i] = rec
            _save(data)
            return
    data["memos"].append(rec)
    _save(data)


def get_memos() -> list[dict]:
    """최신 갱신순 정렬."""
    return sorted(_load()["memos"], key=lambda m: m.get("updated", ""), reverse=True)


def get_memo(code: str, date: str) -> dict | None:
    mid = _memo_id(code, date)
    for m in _load()["memos"]:
        if m["id"] == mid:
            return m
    return None


def delete_memo(memo_id: str) -> None:
    data = _load()
    data["memos"] = [m for m in data["memos"] if m["id"] != memo_id]
    _save(data)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
