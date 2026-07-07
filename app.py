"""관심종목 관리 + 당일 단타 복기용 매매일지 대시보드 (Streamlit).

실행:  streamlit run app.py
"""
from datetime import datetime, timedelta
from pathlib import Path

import streamlit as st

from src import kis_client, store, charting

st.set_page_config(page_title="단타 매매일지 대시보드", page_icon="📈", layout="wide")


# ---- 사이드바: 관심종목 CRUD ---------------------------------------------
def sidebar_watchlist() -> dict | None:
    st.sidebar.header("⭐ 관심 종목")

    with st.sidebar.form("add_watch", clear_on_submit=True):
        st.caption("새 종목 추가")
        c1, c2 = st.columns(2)
        code = c1.text_input("종목코드", placeholder="005930")
        name = c2.text_input("종목명", placeholder="삼성전자")
        if st.form_submit_button("추가", use_container_width=True):
            if code and name:
                ok = store.add_watch(code.strip(), name.strip())
                st.toast("추가됨" if ok else "이미 등록된 종목")
                st.rerun()
            else:
                st.warning("종목코드와 종목명을 모두 입력하세요.")

    watchlist = store.get_watchlist()
    if not watchlist:
        st.sidebar.info("관심 종목을 추가해 주세요.")
        return None

    labels = [f"{w['name']} ({w['code']})" for w in watchlist]
    idx = st.sidebar.radio("분석할 종목 선택", range(len(labels)),
                           format_func=lambda i: labels[i])
    selected = watchlist[idx]

    with st.sidebar.expander("✏️ 수정 / 삭제"):
        new_name = st.text_input("종목명 수정", value=selected["name"], key="edit_name")
        c1, c2 = st.columns(2)
        if c1.button("수정", use_container_width=True):
            store.update_watch(selected["code"], new_name.strip())
            st.toast("수정됨")
            st.rerun()
        if c2.button("삭제", use_container_width=True, type="primary"):
            store.remove_watch(selected["code"])
            st.toast("삭제됨")
            st.rerun()

    return selected


# ---- 탭 1: 차트 분석 & 메모 ----------------------------------------------
def tab_analysis(selected: dict) -> None:
    if not selected:
        st.info("← 사이드바에서 관심 종목을 먼저 추가/선택하세요.")
        return

    code, name = selected["code"], selected["name"]
    st.subheader(f"📊 {name} ({code}) 분석")

    c1, c2 = st.columns([1, 3])
    pick = c1.date_input("분석 날짜", value=datetime.now())
    date_str = pick.strftime("%Y%m%d")
    is_today = date_str == datetime.now().strftime("%Y%m%d")
    if not is_today:
        c2.warning("⚠️ 과거 날짜는 KIS 무료 API로 1분봉을 받을 수 없어 **일봉**만 새로 생성됩니다. "
                   "1분봉 복기는 그날 저장해 둔 이미지로만 가능합니다.")

    if st.button("🔍 분석 실행 (차트 생성)", type="primary"):
        _run_analysis(code, name, date_str, is_today)

    # 생성된(또는 저장된) 이미지 표시
    img = charting.output_path(name, date_str)
    if img.exists():
        st.image(str(img), caption=f"{name} {date_str} 분석 차트", use_container_width=True)
    else:
        st.caption("아직 이 날짜의 분석 이미지가 없습니다. [분석 실행]을 눌러 생성하세요.")

    # ---- 메모 ----
    st.divider()
    st.markdown("#### 📝 이 날의 메모 기록")
    existing = store.get_memo(code, date_str)
    memo_text = st.text_area(
        "매매 복기 메모",
        value=existing["memo"] if existing else "",
        height=140,
        placeholder="진입 근거, 실수, 다음 매매 시 개선점 등을 기록하세요.",
    )
    c1, c2 = st.columns([1, 5])
    if c1.button("💾 저장"):
        image_path = str(img) if img.exists() else ""
        store.upsert_memo(code, name, date_str, memo_text.strip(), image_path)
        st.success("메모가 저장되었습니다.")
    if existing and c2.button("🗑️ 메모 삭제"):
        store.delete_memo(existing["id"])
        st.rerun()


def _run_analysis(code: str, name: str, date_str: str, is_today: bool) -> None:
    save_to = charting.output_path(name, date_str)
    try:
        # 1) 일봉 + 수급
        with st.spinner("일봉/수급 조회 중..."):
            end = datetime.now()
            start = end - timedelta(days=200)
            daily_rows = kis_client.get_daily_chart(
                code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
            )
            investor_rows = kis_client.get_investor_trend(code)
        if daily_rows:
            df_d = charting.daily_to_df(daily_rows)
            df_inv = charting.investor_to_df(investor_rows)
            daily_img = save_to.with_name(f"{date_str}_일봉.png")
            charting.render_daily_chart(name, code, df_d, df_inv, daily_img)
            st.image(str(daily_img), caption="일봉 + 누적 수급", use_container_width=True)

        # 2) 1분봉 + 최적 단타 (당일만)
        if is_today:
            with st.spinner("1분봉 조회 중... (초당 제한 회피로 다소 걸립니다)"):
                min_rows = kis_client.get_minute_chart(code)
            if min_rows:
                df_m = charting.minute_to_df(min_rows, date_str)
                trade = charting.optimal_daytrade(df_m)
                charting.render_minute_chart(name, code, date_str, df_m, trade, save_to)
                if trade:
                    st.success(
                        f"최적 단타: {trade['buy_time']:%H:%M} 매수 → "
                        f"{trade['sell_time']:%H:%M} 매도 | "
                        f"수익률 {trade['ret_pct']:+.2f}% | "
                        f"수익금 {trade['profit_amt']:,.0f}원 ({trade['shares']:,}주)"
                    )
                else:
                    st.info("당일 수익 가능한 단타 구간이 없었습니다.")
            else:
                st.warning("1분봉 데이터가 없습니다(장 시작 전/휴장/장외 시간). 일봉을 분석 이미지로 저장합니다.")
                if daily_rows:
                    charting.render_daily_chart(name, code, df_d, df_inv, save_to)
        else:
            # 과거 날짜: 일봉을 분석 이미지로 저장
            if daily_rows:
                charting.render_daily_chart(name, code, df_d, df_inv, save_to)

        st.toast(f"저장 완료: {save_to}")
    except kis_client.KISError as e:
        st.error(f"KIS API 오류: {e}")
    except Exception as e:  # noqa: BLE001
        st.error(f"분석 중 오류: {e}")


# ---- 탭 2: 종합 매매일지 (메모 모아보기 + 역추적) --------------------------
def tab_journal() -> None:
    st.subheader("📒 종합 매매일지 (메모 모아보기)")
    memos = store.get_memos()
    if not memos:
        st.info("아직 저장된 메모가 없습니다. [차트 분석 & 메모] 탭에서 기록해 보세요.")
        return

    # 필터
    names = sorted({m["name"] for m in memos})
    c1, c2 = st.columns([1, 2])
    fname = c1.selectbox("종목 필터", ["(전체)"] + names)
    query = c2.text_input("메모 검색", placeholder="키워드")

    rows = [
        m for m in memos
        if (fname == "(전체)" or m["name"] == fname)
        and (not query or query.lower() in m["memo"].lower())
    ]
    st.caption(f"{len(rows)}건")

    left, right = st.columns([1, 1])
    with left:
        for m in rows:
            with st.container(border=True):
                st.markdown(f"**{m['name']} ({m['code']})** · `{m['date']}`")
                preview = (m["memo"][:60] + "…") if len(m["memo"]) > 60 else m["memo"]
                st.write(preview or "_(메모 없음)_")
                st.caption(f"수정: {m.get('updated', '-')}")
                if st.button("📈 차트 보기 / 복기", key=f"view_{m['id']}"):
                    st.session_state["journal_selected"] = m["id"]

    with right:
        sel_id = st.session_state.get("journal_selected")
        sel = next((m for m in rows if m["id"] == sel_id), None)
        if not sel:
            st.info("← 왼쪽에서 [차트 보기]를 누르면 당시 차트와 메모가 여기에 표시됩니다.")
            return
        st.markdown(f"### {sel['name']} ({sel['code']}) — {sel['date']}")
        if sel.get("image") and Path(sel["image"]).exists():
            st.image(sel["image"], use_container_width=True)
        else:
            st.warning("저장된 차트 이미지를 찾을 수 없습니다. (파일이 이동/삭제됨)")
        st.markdown("**메모**")
        st.write(sel["memo"] or "_(메모 없음)_")


# ---- 메인 ----------------------------------------------------------------
def main() -> None:
    st.title("📈 관심종목 · 단타 복기 매매일지")
    selected = sidebar_watchlist()
    tab1, tab2 = st.tabs(["차트 분석 & 메모", "종합 매매일지"])
    with tab1:
        tab_analysis(selected)
    with tab2:
        tab_journal()


if __name__ == "__main__":
    main()
