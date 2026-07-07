"""국내 상장 종목 마스터 (코드·이름 검색용).

FinanceDataReader 로 KRX 전체 종목 목록을 받아 symbols.json 으로 캐시한다.
- 이름 일부 또는 코드 앞자리로 검색 → [{code, name, market}] 반환
- 캐시는 7일마다 갱신(또는 refresh(force=True))
"""
import json
import time
from pathlib import Path

_CACHE = Path(__file__).resolve().parent.parent / "symbols.json"
_MAX_AGE = 7 * 24 * 3600  # 7일


def _fetch_from_krx() -> list[dict]:
    import FinanceDataReader as fdr

    df = fdr.StockListing("KRX")
    df = df[["Code", "Name", "Market"]].dropna(subset=["Code", "Name"])
    return [
        {"code": str(r["Code"]).zfill(6), "name": str(r["Name"]), "market": str(r.get("Market", ""))}
        for _, r in df.iterrows()
    ]


def refresh(force: bool = False) -> int:
    """캐시가 없거나 오래됐으면 갱신. 반환: 종목 수."""
    if not force and _CACHE.exists():
        age = time.time() - _CACHE.stat().st_mtime
        if age < _MAX_AGE:
            return len(_load_raw())
    data = _fetch_from_krx()
    _CACHE.write_text(
        json.dumps({"fetched_at": _now(), "items": data}, ensure_ascii=False),
        encoding="utf-8",
    )
    return len(data)


def _load_raw() -> list[dict]:
    if not _CACHE.exists():
        return []
    try:
        return json.loads(_CACHE.read_text(encoding="utf-8")).get("items", [])
    except (json.JSONDecodeError, OSError):
        return []


def load() -> list[dict]:
    """종목 목록 반환(없으면 자동 갱신)."""
    items = _load_raw()
    if not items:
        refresh(force=True)
        items = _load_raw()
    return items


def search(query: str, limit: int = 30) -> list[dict]:
    """이름 부분일치 또는 코드 앞자리 매칭. [{code, name, market}]."""
    q = (query or "").strip()
    if not q:
        return []
    items = load()
    if q.isdigit():
        res = [s for s in items if s["code"].startswith(q)]
    else:
        ql = q.lower()
        res = [s for s in items if ql in s["name"].lower()]
    # 이름 짧은(정확도 높은) 순 → 코드순
    res.sort(key=lambda s: (len(s["name"]), s["code"]))
    return res[:limit]


def name_of(code: str) -> str | None:
    code = str(code).zfill(6)
    for s in load():
        if s["code"] == code:
            return s["name"]
    return None


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
