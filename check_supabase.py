"""Supabase 연결/스키마/저장 점검.

사용:  py check_supabase.py
- .env 의 SUPABASE_URL / SUPABASE_KEY 로 접속
- 테이블 접근, 관심종목·메모·1분봉(Parquet) 왕복 저장을 확인하고 정리한다.
"""
import sys

import pandas as pd

from src import config, store


def main() -> int:
    print("== 1. .env 설정 확인 ==")
    try:
        config.validate_supabase()
    except RuntimeError as e:
        print("❌", e)
        return 1
    print("URL:", config.SUPABASE_URL)
    print("KEY:", config.SUPABASE_KEY[:12] + "…(가림)")

    print("\n== 2. 테이블 접근 (ping) ==")
    try:
        store.ping()
        print("✅ watchlist 테이블 접근 OK")
    except store.StoreError as e:
        print("❌", e)
        print("→ database/supabase_schema.sql 을 Supabase SQL Editor 에서 실행했는지 확인하세요.")
        return 1

    print("\n== 3. 관심종목 CRUD ==")
    store.remove_watch("999999")
    assert store.add_watch("999999", "테스트종목") is True
    assert store.add_watch("999999", "테스트종목") is False  # 중복 방지
    names = [w["name"] for w in store.get_watchlist()]
    print("watchlist:", names)
    store.update_watch("999999", "테스트종목-수정")
    print("✅ add/중복차단/update OK")

    print("\n== 4. 메모 upsert/조회 ==")
    store.upsert_memo("999999", "테스트종목", "20260707", "첫 메모")
    store.upsert_memo("999999", "테스트종목", "20260707", "수정된 메모")  # upsert
    m = store.get_memo("999999", "20260707")
    print("메모:", m["memo"], "| updated:", m["updated"])
    assert m["memo"] == "수정된 메모"
    print("✅ upsert(중복 갱신) OK")

    print("\n== 5. 1분봉 Parquet 왕복 ==")
    idx = pd.date_range("2026-07-07 09:00", periods=5, freq="1min")
    df = pd.DataFrame(
        {"Open": [1, 2, 3, 4, 5], "High": [1, 2, 3, 4, 5], "Low": [1, 2, 3, 4, 5],
         "Close": [1, 2, 3, 4, 5], "Volume": [10, 20, 30, 40, 50]}, index=idx)
    store.save_minute_data("999999", "테스트종목", "20260707", df)
    loaded = store.load_minute_data("999999", "20260707")
    print("로드:", len(loaded), "행, 컬럼", list(loaded.columns))
    assert loaded is not None and len(loaded) == 5
    assert list(loaded["Close"]) == [1, 2, 3, 4, 5]
    print("✅ 저장/로드 일치 OK")

    print("\n== 6. 정리(테스트 데이터 삭제) ==")
    store.delete_memo("999999_20260707")
    store.delete_minute_data("999999", "20260707")
    store.remove_watch("999999")
    print("✅ 정리 완료")

    print("\n🎉 모든 점검 통과 — Supabase 저장소 정상 동작")
    return 0


if __name__ == "__main__":
    sys.exit(main())
