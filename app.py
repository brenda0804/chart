"""관심종목 관리 + 당일 단타 복기용 매매일지 대시보드 (Streamlit + Plotly).

실행:  streamlit run app.py
"""
from datetime import datetime, timedelta
from pathlib import Path

import streamlit as st

from src import kis_client, store, charting, symbols, signals

st.set_page_config(page_title="단타 매매일지 대시보드", page_icon="📈", layout="wide")


@st.cache_data(show_spinner=False)
def _search_symbols(query: str) -> list[dict]:
    """종목 검색 (세션 내 결과 캐시). 최초 1회 KRX 마스터를 내려받는다."""
    return symbols.search(query)


# ---- 사이드바: 관심종목 CRUD ---------------------------------------------
def sidebar_watchlist() -> dict | None:
    st.sidebar.header("⭐ 관심 종목")

    # --- 종목 검색으로 추가 (이름/코드 부분 입력) ---
    st.sidebar.caption("🔎 종목 검색으로 추가")
    query = st.sidebar.text_input(
        "종목명 또는 코드", placeholder="예: 삼성 / 005930", key="symbol_query"
    )
    if query:
        try:
            with st.spinner("종목 검색 중..."):
                results = _search_symbols(query)
        except Exception as e:  # noqa: BLE001 (종목 마스터 로드 실패)
            st.sidebar.error(f"종목 목록 로드 실패: {e}")
            results = []
        if results:
            opts = {f"{s['name']} ({s['code']}) · {s['market']}": s for s in results}
            picked = st.sidebar.selectbox("검색 결과", list(opts.keys()), key="symbol_pick")
            if st.sidebar.button("➕ 관심 종목에 추가", use_container_width=True):
                s = opts[picked]
                ok = store.add_watch(s["code"], s["name"])
                st.toast(f"{s['name']} 추가됨" if ok else "이미 등록된 종목")
                st.rerun()
        else:
            st.sidebar.info("검색 결과가 없습니다. 다른 키워드를 입력해 보세요.")

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

    _sidebar_fee_settings()
    return selected


def _sidebar_fee_settings() -> None:
    """수수료·세금율 설정 (증권사마다 다름). 세션에 저장해 단타 계산에 반영."""
    with st.sidebar.expander("⚙️ 수수료·세금 설정"):
        comm = st.number_input("매매수수료 %(편도)", min_value=0.0, max_value=1.0,
                               value=0.015, step=0.001, format="%.4f")
        tax = st.number_input("증권거래세 %(매도)", min_value=0.0, max_value=1.0,
                              value=0.15, step=0.01, format="%.4f")
        st.session_state["comm_rate"] = comm / 100
        st.session_state["tax_rate"] = tax / 100
        st.caption("기본: 수수료 0.015%, 거래세 0.15%(2025~)")


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
        c2.warning("⚠️ 과거 날짜는 KIS 무료 API로 1분봉을 새로 받을 수 없습니다. "
                   "그날 저장해 둔 데이터가 있으면 인터랙티브로 복기합니다.")

    if c1.button("🔍 분석 실행", type="primary"):
        _run_analysis(code, name, date_str, is_today)

    comm = st.session_state.get("comm_rate", charting.COMMISSION_RATE)
    tax = st.session_state.get("tax_rate", charting.TAX_RATE)

    # ---- 일봉 + 전일 대비 시가 갭 (세션에 있으면) ----
    ana = st.session_state.get("ana")
    key = f"{code}_{date_str}"
    if ana and ana["key"] == key and ana.get("df_daily") is not None:
        gi = charting.gap_info(ana["df_daily"], date_str, commission_rate=comm, tax_rate=tax)
        if gi:
            _render_gap(gi)
        st.plotly_chart(
            charting.build_daily_figure(name, code, ana["df_daily"], ana["investor"]),
            use_container_width=True,
        )

    # ---- 1분봉 (세션 우선, 없으면 DB 저장 데이터) ----
    df_min = None
    if ana and ana["key"] == key:
        df_min = ana.get("df_min")
    if df_min is None:
        df_min = store.load_minute_data(code, date_str)

    if df_min is not None and not df_min.empty:
        _render_minute_section(name, code, date_str, df_min)
    else:
        st.caption("1분봉 데이터가 없습니다. [분석 실행]을 눌러 생성하세요(당일만 가능).")

    _render_memo(code, name, date_str)


def _render_gap(gi: dict) -> None:
    """전일 종가 대비 시가 갭 패널."""
    st.markdown("##### 🌙 전일 종가 대비 시가 (갭)")
    c = st.columns(4)
    c[0].metric("전일 종가", f"{gi['prev_close']:,.0f}원", gi["prev_date"].strftime("%m/%d"))
    c[1].metric("당일 시가(09:00)", f"{gi['open_price']:,.0f}원")
    c[2].metric("시가 등락률", f"{gi['gap_pct']:+.2f}%")
    c[3].metric("전날 보유 순이익", f"{gi['net']:,.0f}원", f"{gi['net_ret']:+.2f}%")
    verb = "올라" if gi["gap_pct"] >= 0 else "내려"
    st.caption(
        f"전일({gi['prev_date']:%m/%d}) 종가 {gi['prev_close']:,.0f}원 → 당일 시가 {gi['open_price']:,.0f}원 "
        f"({gi['gap_pct']:+.2f}% {verb}감). 전날 종가에 1천만원어치({gi['shares']:,}주) 사서 당일 시가에 "
        f"팔았다면 순이익 **{gi['net']:+,.0f}원** (수수료·세금 {gi['fees']:,.0f}원 차감)"
    )


def _render_minute_section(name: str, code: str, date_str: str, df_min) -> None:
    st.markdown("#### ⏱️ 1분봉 (마우스 오버로 시/고/저/종가 확인, 드래그로 확대)")

    # --- 구간 포커스 (요구 3) ---
    tmin = df_min.index[0].to_pydatetime()
    tmax = df_min.index[-1].to_pydatetime()
    if tmin < tmax:
        rng = st.slider("표시 구간", min_value=tmin, max_value=tmax,
                        value=(tmin, tmax), format="HH:mm", key="focus_range")
        dff = df_min[(df_min.index >= rng[0]) & (df_min.index <= rng[1])]
    else:
        dff = df_min
    if dff.empty or len(dff) < 2:
        st.info("선택 구간에 데이터가 부족합니다.")
        return

    comm = st.session_state.get("comm_rate", charting.COMMISSION_RATE)
    tax = st.session_state.get("tax_rate", charting.TAX_RATE)

    trade = charting.optimal_daytrade(dff, commission_rate=comm, tax_rate=tax)
    st.plotly_chart(
        charting.build_minute_figure(name, code, date_str, dff, trade),
        use_container_width=True,
    )

    # --- 최적 단타 상세 (요구 4, 수수료·세금 반영) ---
    st.markdown("##### 💰 이 구간 최적 단타 (사후 복기 · 실시간 신호 아님)")
    if trade:
        m = st.columns(4)
        m[0].metric("매수", f"{trade['buy_price']:,.0f}원", trade["buy_time"].strftime("%H:%M"))
        m[1].metric("매도", f"{trade['sell_price']:,.0f}원", trade["sell_time"].strftime("%H:%M"))
        m[2].metric("순수익률", f"{trade['ret_pct']:+.2f}%",
                    f"세전 {trade['gross_ret_pct']:+.2f}%")
        m[3].metric("순수익금", f"{trade['profit_amt']:,.0f}원", f"{trade['shares']:,}주 매수")
        st.caption(
            f"{trade['buy_time']:%H:%M} {trade['buy_price']:,.0f}원 매수({trade['shares']:,}주, "
            f"{trade['cost']:,.0f}원) → {trade['sell_time']:%H:%M} {trade['sell_price']:,.0f}원 매도  \n"
            f"세전차익 **{trade['gross_profit']:+,.0f}원** − 비용 {trade['fees_total']:,.0f}원"
            f"(매수수수료 {trade['buy_fee']:,.0f} + 매도수수료 {trade['sell_fee']:,.0f} + 거래세 {trade['tax']:,.0f}) "
            f"= **순이익 {trade['profit_amt']:+,.0f}원** ({trade['ret_pct']:+.2f}%)"
        )
    else:
        st.info("이 구간에는 수수료·세금까지 빼고 남는 수익 구간이 없습니다.")

    # --- 직접 시뮬레이션 (요구 4) ---
    with st.expander("🧮 직접 매수/매도 시점 골라 시뮬레이션"):
        times = list(dff.index)
        labels = [t.strftime("%H:%M") for t in times]
        s1, s2 = st.columns(2)
        bi = s1.select_slider("매수 시각", options=range(len(times)), value=0,
                              format_func=lambda i: labels[i], key="sim_buy")
        si = s2.select_slider("매도 시각", options=range(len(times)), value=len(times) - 1,
                              format_func=lambda i: labels[i], key="sim_sell")
        if si > bi:
            r = charting.simulate(dff, bi, si, commission_rate=comm, tax_rate=tax)
            st.success(
                f"{labels[bi]} {r['buy_price']:,.0f}원 매수({r['shares']:,}주) → "
                f"{labels[si]} {r['sell_price']:,.0f}원 매도  ⇒  "
                f"순이익 **{r['profit_amt']:+,.0f}원** ({r['ret_pct']:+.2f}%)  "
                f"·  비용 {r['fees_total']:,.0f}원(수수료+거래세)"
            )
        else:
            st.warning("매도 시각을 매수 시각보다 뒤로 선택하세요.")

    _render_realtime(name, code, df_min, comm, tax)


def _render_realtime(name: str, code: str, df_min, comm: float, tax: float) -> None:
    """🔴 실시간 단타 모니터 (Phase 1: 폴링). 현재가·신호·판단·시나리오 투영."""
    st.divider()
    st.markdown("#### 🔴 실시간 단타 모니터  ·  참고용(투자자문·예측 아님)")
    live = st.toggle("자동 새로고침 (30초)", key="live_toggle",
                     help="켜면 30초마다 현재가를 다시 불러 신호·시나리오를 갱신합니다.")
    run_every = "30s" if live else None

    @st.fragment(run_every=run_every)
    def panel() -> None:
        cols = st.columns([1, 1, 2])
        tp_in = cols[0].number_input("목표 수익률 %(0=자동)", min_value=0.0, value=0.0,
                                     step=0.1, key="rt_tp")
        sp_in = cols[1].number_input("손절 %(0=자동)", min_value=0.0, value=0.0,
                                     step=0.1, key="rt_sp")
        tp = tp_in if tp_in > 0 else None
        sp = sp_in if sp_in > 0 else None

        try:
            q = kis_client.get_current_price(code)
            cur = float(q.get("stck_prpr", 0))
            chg = float(q.get("prdy_ctrt", 0))
        except Exception as e:  # noqa: BLE001
            st.error(f"현재가 조회 실패: {e}")
            return
        if cur <= 0:
            st.warning("현재가를 가져오지 못했습니다(장외/휴장일 수 있음).")
            return

        sigs = signals.technical_signals(df_min, current_price=cur)
        verdict = signals.timing_verdict(sigs)
        sc = signals.scenario(df_min, cur, commission_rate=comm, tax_rate=tax,
                              target_pct=tp, stop_pct=sp)

        m = st.columns(3)
        m[0].metric("현재가", f"{cur:,.0f}원", f"{chg:+.2f}% (전일대비)")
        m[1].metric(f"목표가 (+{sc['target_pct']:.2f}%)", f"{sc['target']:,.0f}원",
                    f"순이익 {sc['target_net']:+,.0f}원")
        m[2].metric(f"손절가 (-{sc['stop_pct']:.2f}%)", f"{sc['stop']:,.0f}원",
                    f"{sc['stop_net']:+,.0f}원", delta_color="inverse")

        (st.success if verdict["go"] else st.info)(
            f"**{verdict['title']}** — 판단 근거: {verdict['reason']}  \n"
            f"손익비(목표/손절) {sc['rr']:.1f} · {sc['shares']:,}주 · 변동성 {sc['vol_pct']:.2f}%/분"
        )
        if sigs:
            st.write("  ".join(f"`{s['label']}`" for s in sigs))
            for s in sigs:
                st.caption(f"- **{s['label']}**: {s['detail']}")
        else:
            st.caption("현재 특이 신호 없음 (20분 이상 데이터 필요)")

        recent = df_min.tail(60)  # 최근 60분 + 목표/손절 투영
        st.plotly_chart(
            charting.build_minute_figure(name, code, recent.index[-1].strftime("%Y%m%d"),
                                         recent, None, scenario=sc),
            use_container_width=True,
        )
        st.caption(
            f"⏱️ 갱신 {datetime.now():%H:%M:%S} · 목표/손절선은 변동성 기반 **시나리오**이며 "
            "미래 가격 예측이 아닙니다. 실제 매매 판단·책임은 본인에게 있습니다."
        )

    panel()


def _run_analysis(code: str, name: str, date_str: str, is_today: bool) -> None:
    try:
        with st.spinner("일봉/수급 조회 중..."):
            end = datetime.now()
            start = end - timedelta(days=200)
            daily_rows = kis_client.get_daily_chart(
                code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
            )
            df_daily = charting.daily_to_df(daily_rows) if daily_rows else None
            investor = charting.investor_to_df(kis_client.get_investor_trend(code))

        df_min = None
        if is_today:
            with st.spinner("1분봉 조회 중... (초당 제한 회피로 다소 걸립니다)"):
                min_rows = kis_client.get_minute_chart(code)
            if min_rows:
                df_min = charting.minute_to_df(min_rows, date_str)
                now = datetime.now()
                before = len(df_min)
                df_min = df_min[df_min.index <= now]  # 미래 캔들 제거 (요구 5 수정)
                if len(df_min) < before:
                    st.info(
                        f"⏱️ 장중이라 현재 시각({now:%H:%M})까지 {len(df_min)}개 캔들만 분석합니다. "
                        "전체 하루 복기는 장 마감 후에 하세요. "
                        f"(서버가 마감 세션 데이터를 함께 줘서 {before}개가 왔으나, 미래 시각은 잘라냈습니다)"
                    )
                if not df_min.empty:
                    store.save_minute_data(code, name, date_str, df_min)
        else:
            df_min = store.load_minute_data(code, date_str)

        st.session_state["ana"] = {
            "key": f"{code}_{date_str}", "df_min": df_min,
            "df_daily": df_daily, "investor": investor,
        }
        st.toast("분석 완료")
    except kis_client.KISError as e:
        st.error(f"KIS API 오류: {e}")
    except Exception as e:  # noqa: BLE001
        st.error(f"분석 중 오류: {e}")


def _render_memo(code: str, name: str, date_str: str) -> None:
    st.divider()
    st.markdown("#### 📝 이 날의 메모 기록")
    existing = store.get_memo(code, date_str)
    memo_text = st.text_area(
        "매매 복기 메모", value=existing["memo"] if existing else "", height=140,
        placeholder="진입 근거, 실수, 다음 매매 시 개선점 등을 기록하세요.",
    )
    c1, c2 = st.columns([1, 5])
    if c1.button("💾 저장"):
        store.upsert_memo(code, name, date_str, memo_text.strip())
        st.success("메모가 저장되었습니다.")
    if existing and c2.button("🗑️ 메모 삭제"):
        store.delete_memo(existing["id"])
        st.rerun()


# ---- 탭 2: 종합 매매일지 (모아보기 + 인터랙티브 역추적) --------------------
def tab_journal() -> None:
    st.subheader("📒 종합 매매일지 (메모 모아보기)")
    memos = store.get_memos()
    if not memos:
        st.info("아직 저장된 메모가 없습니다. [차트 분석 & 메모] 탭에서 기록해 보세요.")
        return

    names = sorted({m["name"] for m in memos})
    c1, c2 = st.columns([1, 2])
    fname = c1.selectbox("종목 필터", ["(전체)"] + names)
    q = c2.text_input("메모 검색", placeholder="키워드")
    rows = [
        m for m in memos
        if (fname == "(전체)" or m["name"] == fname)
        and (not q or q.lower() in m["memo"].lower())
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
        sel = next((m for m in rows if m["id"] == st.session_state.get("journal_selected")), None)
        if not sel:
            st.info("← 왼쪽에서 [차트 보기]를 누르면 당시 차트(인터랙티브)와 메모가 표시됩니다.")
            return
        st.markdown(f"### {sel['name']} ({sel['code']}) — {sel['date']}")
        df = store.load_minute_data(sel["code"], sel["date"])
        if df is not None and not df.empty:
            trade = charting.optimal_daytrade(df)
            st.plotly_chart(
                charting.build_minute_figure(sel["name"], sel["code"], sel["date"], df, trade),
                use_container_width=True,
            )
        else:
            st.warning("저장된 차트 데이터를 찾을 수 없습니다. (그날 분석을 실행하지 않았거나 파일이 이동/삭제됨)")
        st.markdown("**메모**")
        st.write(sel["memo"] or "_(메모 없음)_")


# ---- 메인 ----------------------------------------------------------------
def main() -> None:
    st.title("📈")
    selected = sidebar_watchlist()
    tab1, tab2 = st.tabs(["차트 분석 & 메모", "종합 매매일지"])
    with tab1:
        tab_analysis(selected)
    with tab2:
        tab_journal()


if __name__ == "__main__":
    main()
