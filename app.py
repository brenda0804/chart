"""관심종목 관리 + 당일 단타 복기용 매매일지 대시보드 (Streamlit + Plotly).

실행:  streamlit run app.py
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_calendar import calendar

from src import kis_client, store, charting, symbols, signals, realtime_ws, plan

st.set_page_config(page_title="단타 매매일지 대시보드", page_icon="📈", layout="wide")


@st.cache_data(show_spinner=False)
def _search_symbols(query: str) -> list[dict]:
    """종목 검색 (세션 내 결과 캐시). 최초 1회 KRX 마스터를 내려받는다."""
    return symbols.search(query)


# ---- 사이드바: 관심종목 CRUD ---------------------------------------------
def sidebar_watchlist() -> None:
    st.sidebar.header("⭐ 관심 종목")

    # --- 종목 검색으로 추가 (이름/코드 부분 입력) ---
    query = st.sidebar.text_input(
        "종목 검색 (이름/코드)", placeholder="예: 삼성 / 005930", key="symbol_query"
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
        _sidebar_fee_settings()
        return

    st.sidebar.markdown("**목록**")
    for w in watchlist:
        row = st.sidebar.columns([5, 1, 1], vertical_alignment="center")
        row[0].write(f"{w['name']} ({w['code']})")
        if row[1].button("✏️", key=f"edit_{w['code']}", help="이름 수정"):
            cur = st.session_state.get("editing")
            st.session_state["editing"] = None if cur == w["code"] else w["code"]
            st.rerun()
        if row[2].button("🗑️", key=f"del_{w['code']}", help="삭제"):
            store.remove_watch(w["code"])
            st.toast(f"{w['name']} 삭제됨")
            st.rerun()
        if st.session_state.get("editing") == w["code"]:
            newn = st.sidebar.text_input("새 이름", value=w["name"], key=f"ename_{w['code']}")
            ec = st.sidebar.columns(2)
            if ec[0].button("저장", key=f"esave_{w['code']}", use_container_width=True):
                store.update_watch(w["code"], newn.strip())
                st.session_state["editing"] = None
                st.toast("수정됨")
                st.rerun()
            if ec[1].button("취소", key=f"ecancel_{w['code']}", use_container_width=True):
                st.session_state["editing"] = None
                st.rerun()

    _sidebar_fee_settings()


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
# (종목,날짜) 조회 결과 캐시 — 스레드에서 호출 가능(병렬 프리페치용). st.cache_data 대신 모듈 캐시.
_ANALYSIS_CACHE: dict = {}
_ANALYSIS_LOCK = threading.Lock()
_ANALYSIS_TTL = 60  # 초


def _fetch_raw(code: str, name: str, date_str: str, is_today: bool) -> dict:
    res = {"df_daily": None, "investor": None, "df_min": None, "clip_note": "", "error": ""}
    try:
        end = datetime.now()
        start = end - timedelta(days=200)
        daily_rows = kis_client.get_daily_chart(
            code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
        res["df_daily"] = charting.daily_to_df(daily_rows) if daily_rows else None
        res["investor"] = charting.investor_to_df(kis_client.get_investor_trend(code))
        if is_today:
            now = datetime.now()
            # 오늘은 현재 시각까지만 페이지네이션 → 콜 수 대폭 감소
            min_rows = kis_client.get_minute_chart(code, to_hour=now.strftime("%H%M%S"))
            if min_rows:
                dfm = charting.minute_to_df(min_rows, date_str)
                dfm = dfm[dfm.index <= now]  # 미래 캔들 제거
                res["clip_note"] = f"현재({now:%H:%M})까지 {len(dfm)}개 캔들 (장 마감 후 전체 복기)."
                if not dfm.empty:
                    store.save_minute_data(code, name, date_str, dfm)
                res["df_min"] = dfm
        else:
            res["df_min"] = store.load_minute_data(code, date_str)
    except kis_client.KISError as e:
        res["error"] = f"KIS API 오류: {e}"
    except Exception as e:  # noqa: BLE001
        res["error"] = f"조회 오류: {e}"
    return res


def fetch_analysis(code: str, name: str, date_str: str, is_today: bool) -> dict:
    """(종목,날짜) 조회 (TTL 캐시). 스레드에서 호출 가능."""
    key = (code, date_str, is_today)
    now = time.time()
    with _ANALYSIS_LOCK:
        hit = _ANALYSIS_CACHE.get(key)
    if hit and now - hit[0] < _ANALYSIS_TTL:
        return hit[1]
    res = _fetch_raw(code, name, date_str, is_today)
    with _ANALYSIS_LOCK:
        _ANALYSIS_CACHE[key] = (now, res)
    return res


def fetch_analysis_clear() -> None:
    with _ANALYSIS_LOCK:
        _ANALYSIS_CACHE.clear()


def _prefetch(stocks: list, date_str: str, is_today: bool) -> None:
    """여러 종목을 병렬로 미리 조회해 캐시를 워밍(탭 렌더 전)."""
    uniq = {s["code"]: s for s in stocks}.values()
    if len(uniq) <= 1:
        return
    with st.spinner(f"{len(uniq)}개 종목 병렬 조회 중..."):
        with ThreadPoolExecutor(max_workers=min(4, len(uniq))) as ex:
            futs = [ex.submit(fetch_analysis, s["code"], s["name"], date_str, is_today)
                    for s in uniq]
            for f in futs:
                try:
                    f.result()
                except Exception:  # noqa: BLE001
                    pass


def _calendar_clicked_date(state) -> str | None:
    """streamlit-calendar 반환에서 클릭된 날짜(YYYY-MM-DD) 추출.

    날짜 셀 클릭(dateClick), 기록칩 클릭(eventClick), 범위 선택(select) 모두 대응.
    """
    if not state:
        return None
    cb = state.get("callback")
    s = None
    if cb == "dateClick":
        d = state.get("dateClick") or {}
        s = d.get("date") or d.get("dateStr")
    elif cb == "eventClick":
        ev = (state.get("eventClick") or {}).get("event") or {}
        s = ev.get("start")
    elif cb == "select":
        d = state.get("select") or {}
        s = d.get("start") or d.get("startStr")
    return s[:10] if s else None


def tab_analysis() -> None:
    """달력(월 뷰)에서 날짜 클릭 → 그날 확인한 종목을 탭으로. ➕탭에서 새 종목 분석 추가."""
    comm = st.session_state.get("comm_rate", charting.COMMISSION_RATE)
    tax = st.session_state.get("tax_rate", charting.TAX_RATE)
    today = datetime.now().date()
    if "cal_date" not in st.session_state:
        st.session_state["cal_date"] = today
    cal_date = st.session_state["cal_date"]

    cleft, cright = st.columns([1.4, 2.6])

    with cleft:
        if st.button("📅 오늘로", use_container_width=True):
            if cal_date != today:
                st.session_state["cal_date"] = today
                st.rerun()

        # 기록 있는 날을 달력에 표시(파란 칩)
        rdates = store.record_dates()
        events = [{"start": f"{d[:4]}-{d[4:6]}-{d[6:]}", "title": "📊 기록",
                   "allDay": True, "color": "#3182ce"} for d in rdates]
        options = {
            "initialView": "dayGridMonth",
            "initialDate": cal_date.isoformat(),
            "locale": "ko",
            "headerToolbar": {"left": "prev,next", "center": "title", "right": ""},
            "height": 470,
            "fixedWeekCount": False,
            "selectable": True,
        }
        state = calendar(events=events, options=options, key="cal_widget")
        clicked = _calendar_clicked_date(state)
        if clicked:
            try:
                nd = datetime.strptime(clicked, "%Y-%m-%d").date()
                if nd != cal_date and nd <= today:
                    st.session_state["cal_date"] = nd
                    st.rerun()
            except ValueError:
                pass
        st.caption(f"선택: **{cal_date:%Y-%m-%d (%a)}**  ·  📊 = 기록 있는 날")

    with cright:
        date_str = cal_date.strftime("%Y%m%d")
        recs = store.stocks_on_date(date_str)
        extras = st.session_state.get("extra_stocks", {}).get(date_str, [])
        rec_codes = {r["code"] for r in recs}
        stocks = recs + [e for e in extras if e["code"] not in rec_codes]

        st.markdown(f"**📈 {cal_date:%m/%d (%a)} 확인한 종목**")
        if stocks:
            _prefetch(stocks, date_str, date_str == today.strftime("%Y%m%d"))
            labels = [f"{s['name']}{' 📊' if s.get('has_data') else ''}" for s in stocks] + ["➕ 새 분석"]
            tabs = st.tabs(labels)
            for t, s in zip(tabs[:-1], stocks):
                with t:
                    _render_date(s["code"], s["name"], cal_date, comm, tax)
            with tabs[-1]:
                _render_add_stock(date_str, [s["code"] for s in stocks])
        else:
            st.info("이 날 확인한 종목이 없습니다. 아래에서 종목을 골라 분석하세요.")
            _render_add_stock(date_str, [])


def _render_add_stock(date_str: str, existing: list) -> None:
    """관심종목을 눌러 이 날짜로 분석/추가 (원클릭)."""
    wl = [w for w in store.get_watchlist() if w["code"] not in existing]
    if not wl:
        st.caption("추가할 관심종목이 없습니다. (사이드바에서 관심 종목을 추가하세요)")
        return
    st.caption("관심종목을 눌러 이 날짜로 분석 (분석 시 자동 저장):")
    cols = st.columns(2)
    for i, w in enumerate(wl):
        if cols[i % 2].button(f"▶ {w['name']} ({w['code']})",
                              key=f"add_{w['code']}_{date_str}", use_container_width=True):
            extras = st.session_state.setdefault("extra_stocks", {})
            extras.setdefault(date_str, []).append({"code": w["code"], "name": w["name"]})
            st.rerun()


def _render_date(code: str, name: str, d, comm: float, tax: float) -> None:
    """종목 탭 내용: 자동 조회 → 일봉/갭/1분봉/메모."""
    date_str = d.strftime("%Y%m%d")
    is_today = date_str == datetime.now().strftime("%Y%m%d")

    h = st.columns([4, 1])
    h[0].markdown(f"##### {name} ({code}) · {d:%Y-%m-%d}")
    if h[1].button("🔄 새로고침", key=f"refresh_{code}_{date_str}", use_container_width=True):
        fetch_analysis_clear()
        st.rerun()

    with st.spinner("조회 중..."):
        data = fetch_analysis(code, name, date_str, is_today)
    if data["error"]:
        st.error(data["error"])
        return
    if data["clip_note"]:
        st.caption(f"⏱️ {data['clip_note']}")

    df_daily = data["df_daily"]
    if df_daily is not None and not df_daily.empty:
        gi = charting.gap_info(df_daily, date_str, commission_rate=comm, tax_rate=tax)
        if gi:
            _render_gap(gi)
        st.plotly_chart(charting.build_daily_figure(name, code, df_daily, data["investor"]),
                        use_container_width=True, key=f"daily_{code}_{date_str}")

    df_min = data["df_min"]
    if df_min is not None and not df_min.empty:
        _render_minute_section(name, code, date_str, df_min, comm, tax, is_today)
    elif not is_today:
        st.caption("이 날짜의 저장된 1분봉이 없습니다(그날 조회한 적이 없으면 복기 불가).")
    else:
        st.caption("1분봉 데이터가 없습니다(장 시작 전/장외).")

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


def _render_minute_section(name: str, code: str, date_str: str, df_min,
                           comm: float, tax: float, is_today: bool) -> None:
    k = f"{code}_{date_str}"  # 탭별 위젯 key 고유화
    st.markdown("#### ⏱️ 1분봉 (마우스 오버로 시/고/저/종가 확인, 드래그로 확대)")

    # --- 구간 포커스 ---
    tmin = df_min.index[0].to_pydatetime()
    tmax = df_min.index[-1].to_pydatetime()
    if tmin < tmax:
        rng = st.slider("표시 구간", min_value=tmin, max_value=tmax,
                        value=(tmin, tmax), format="HH:mm", key=f"focus_{k}")
        dff = df_min[(df_min.index >= rng[0]) & (df_min.index <= rng[1])]
    else:
        dff = df_min
    if dff.empty or len(dff) < 2:
        st.info("선택 구간에 데이터가 부족합니다.")
        return

    trade = charting.optimal_daytrade(dff, commission_rate=comm, tax_rate=tax)
    st.plotly_chart(
        charting.build_minute_figure(name, code, date_str, dff, trade),
        use_container_width=True, key=f"minchart_{k}",
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
                              format_func=lambda i: labels[i], key=f"simbuy_{k}")
        si = s2.select_slider("매도 시각", options=range(len(times)), value=len(times) - 1,
                              format_func=lambda i: labels[i], key=f"simsell_{k}")
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

    # 실시간 모니터는 '오늘' 탭에서만 (장중 실시간 대상)
    if is_today:
        _render_realtime(name, code, date_str, df_min, comm, tax)

    _render_plan(name, code, df_min, comm, tax, is_today)


def _render_plan(name: str, code: str, df_min, comm: float, tax: float, is_today: bool) -> None:
    """📋 매매 플랜(규칙 기반) + 과거 근거. 모바일 우선(세로 카드/표)."""
    if df_min is None or df_min.empty:
        return
    ref = float(df_min["Close"].iloc[-1])
    vol = signals.volatility_pct(df_min)
    p = plan.trading_plan(ref, commission_rate=comm, tax_rate=tax, vol_pct=vol)
    lv, pr = p["levels"], p["prices"]

    st.divider()
    st.markdown("#### 📋 매매 플랜 · 참고용(예측·투자자문 아님)")

    tbl = pd.DataFrame([
        {"단계": "🔴 손절", "가격": f"{pr['stop']:,.0f}", "기준대비": f"-{lv['stop']:.1f}%", "액션": "전량 매도"},
        {"단계": "🟠 추매", "가격": f"{pr['add']:,.0f}", "기준대비": f"-{lv['add']:.1f}%", "액션": "매수(평단↓)"},
        {"단계": "⚪ 진입", "가격": f"{pr['entry']:,.0f}", "기준대비": "0%", "액션": "매수"},
        {"단계": "🟢 익절1", "가격": f"{pr['tp1']:,.0f}", "기준대비": f"+{lv['tp1']:.1f}%", "액션": "절반 매도"},
        {"단계": "🟢 익절2", "가격": f"{pr['tp2']:,.0f}", "기준대비": f"+{lv['tp2']:.1f}%", "액션": "나머지 매도"},
    ])
    st.dataframe(tbl, hide_index=True, use_container_width=True)

    c = st.columns(2)
    c[0].metric("계획 성공 시(익절1·2)", f"{p['net_win']:+,.0f}원", f"{p['ret_win']:+.2f}%")
    c[1].metric("손절 시", f"{p['net_loss']:+,.0f}원", f"{p['ret_loss']:+.2f}%",
                delta_color="inverse")
    st.caption(
        f"기준가 {ref:,.0f}원 · 1천만원({p['shares']:,}주) 기준 · 레벨은 변동성"
        f"({vol:.2f}%/분)으로 자동 산정 · 손익비 {p['rr']:.1f}"
    )

    # 과거 근거 (오늘, 같은 시각 이후 30분 실제 수익률 분포)
    if is_today:
        st.markdown("##### 📚 과거 근거 · 같은 시각 이후 30분 (실제 데이터)")
        stats = plan.historical_forward_stats(
            code, datetime.now().time(), today_str=datetime.now().strftime("%Y%m%d"))
        if stats:
            s = st.columns(3)
            s[0].metric("표본", f"{stats['n']}일")
            s[1].metric("중앙 수익률", f"{stats['median']:+.2f}%")
            s[2].metric("상승 비율", f"{stats['win_rate']:.0f}%")
            st.caption(
                f"과거 {stats['n']}일 중 현재 시각 이후 30분 수익률 범위 "
                f"[{stats['min']:+.2f}% ~ {stats['max']:+.2f}%]. 표본이 적으면 참고만 하세요(예측 아님)."
            )
        else:
            st.caption("과거 데이터가 부족합니다. 며칠 더 기록이 쌓이면 통계가 표시됩니다.")


def _render_realtime(name: str, code: str, date_str: str, df_min,
                     comm: float, tax: float) -> None:
    """🔴 실시간 단타 모니터. 현재가·신호·판단·시나리오 투영."""
    k = f"{code}_{date_str}"
    st.divider()
    st.markdown("#### 🔴 실시간 단타 모니터  ·  참고용(투자자문·예측 아님)")
    t1, t2 = st.columns(2)
    ws_on = t1.toggle("⚡ 실시간 WebSocket (초단위)", key=f"ws_{k}",
                      help="장중 KIS 체결가를 초단위로 스트리밍(Phase 2).")
    auto = t2.toggle("🔄 자동 새로고침 (30초)", key=f"live_{k}",
                     help="REST 폴링으로 주기 갱신(Phase 1).")

    # WebSocket 스트림 시작/중지 (토글 변경 시 전체 rerun 에서 처리)
    if ws_on:
        realtime_ws.start_stream(code)
        run_every = "2s"      # 공유 틱을 자주 읽어 화면 갱신 (API 호출 아님)
    else:
        realtime_ws.stop_stream()
        run_every = "30s" if auto else None

    @st.fragment(run_every=run_every)
    def panel() -> None:
        cols = st.columns([1, 1, 2])
        tp_in = cols[0].number_input("목표 수익률 %(0=자동)", min_value=0.0, value=0.0,
                                     step=0.1, key=f"rt_tp_{k}")
        sp_in = cols[1].number_input("손절 %(0=자동)", min_value=0.0, value=0.0,
                                     step=0.1, key=f"rt_sp_{k}")
        tp = tp_in if tp_in > 0 else None
        sp = sp_in if sp_in > 0 else None

        cur, chg, src = 0.0, 0.0, ""
        if ws_on:  # WebSocket 우선
            tk = realtime_ws.latest_tick(code)
            stt = realtime_ws.status()
            if tk:
                cur, chg = tk["price"], tk["chg_pct"]
                src = f"⚡ WS 실시간 {tk['time'][:2]}:{tk['time'][2:4]}:{tk['time'][4:6]}"
            else:
                st.caption(f"⚡ WS {'연결됨' if stt['connected'] else '연결 대기'} · "
                           f"체결 대기 중(장외이면 안 옴){' · ' + stt['error'] if stt['error'] else ''}. REST로 대체.")
        if cur <= 0:  # WS 없거나 대기 → REST 조회
            try:
                q = kis_client.get_current_price(code)
                cur = float(q.get("stck_prpr", 0))
                chg = float(q.get("prdy_ctrt", 0))
                src = "REST 조회"
            except Exception as e:  # noqa: BLE001
                st.error(f"현재가 조회 실패: {e}")
                return
        if cur <= 0:
            st.warning("현재가를 가져오지 못했습니다(장외/휴장일 수 있음).")
            return

        # 실시간 캔들: WS 틱 → 1분봉 집계 → 과거+실시간 병합 (매 갱신마다 성장)
        df_view = df_min
        live_note = ""
        if ws_on:
            th = realtime_ws.tick_history(code)
            if th:
                lc = charting.live_candles(th, datetime.now().strftime("%Y%m%d"))
                df_view = charting.merge_live(df_min, lc)
                live_note = f" · 실시간 캔들 {len(lc)}분"

        sigs = signals.technical_signals(df_view, current_price=cur)
        verdict = signals.timing_verdict(sigs)
        sc = signals.scenario(df_view, cur, commission_rate=comm, tax_rate=tax,
                              target_pct=tp, stop_pct=sp)

        m = st.columns(3)
        m[0].metric(f"현재가 · {src}", f"{cur:,.0f}원", f"{chg:+.2f}% (전일대비)")
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

        recent = df_view.tail(60)  # 최근 60분(실시간 병합) + 목표/손절 투영
        st.plotly_chart(
            charting.build_minute_figure(name, code, recent.index[-1].strftime("%Y%m%d"),
                                         recent, None, scenario=sc),
            use_container_width=True, key=f"rtchart_{k}",
        )
        st.caption(
            f"⏱️ 갱신 {datetime.now():%H:%M:%S}{live_note} · 목표/손절선은 변동성 기반 **시나리오**이며 "
            "미래 가격 예측이 아닙니다. 실제 매매 판단·책임은 본인에게 있습니다."
        )

    panel()


def _render_memo(code: str, name: str, date_str: str) -> None:
    k = f"{code}_{date_str}"
    st.divider()
    st.markdown("#### 📝 이 날의 메모 기록")
    existing = store.get_memo(code, date_str)
    memo_text = st.text_area(
        "매매 복기 메모", value=existing["memo"] if existing else "", height=140,
        placeholder="진입 근거, 실수, 다음 매매 시 개선점 등을 기록하세요.", key=f"memo_{k}",
    )
    c1, c2 = st.columns([1, 5])
    if c1.button("💾 저장", key=f"save_{k}"):
        store.upsert_memo(code, name, date_str, memo_text.strip())
        st.success("메모가 저장되었습니다.")
    if existing and c2.button("🗑️ 메모 삭제", key=f"del_{k}"):
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
                use_container_width=True, key=f"journal_{sel['id']}",
            )
        else:
            st.warning("저장된 차트 데이터를 찾을 수 없습니다. (그날 분석을 실행하지 않았거나 파일이 이동/삭제됨)")
        st.markdown("**메모**")
        st.write(sel["memo"] or "_(메모 없음)_")


# ---- 메인 ----------------------------------------------------------------
_RESPONSIVE_CSS = """
<style>
.block-container { padding-top: 1.2rem; }
/* 모바일: 여백 축소, 글씨 가독성, 탭 가로 스크롤 */
@media (max-width: 640px) {
  .block-container { padding: 0.6rem 0.6rem 3rem !important; }
  h1,h2,h3,h4 { line-height: 1.25 !important; }
  [data-testid="stMetricValue"] { font-size: 1.05rem !important; }
  [data-testid="stMetricLabel"] p { font-size: 0.72rem !important; }
  [data-testid="stMetricDelta"] { font-size: 0.72rem !important; }
  [data-baseweb="tab-list"] { overflow-x: auto !important; flex-wrap: nowrap !important; }
  [data-testid="stCaptionContainer"] { font-size: 0.8rem !important; }
}
</style>
"""


def main() -> None:
    st.markdown(_RESPONSIVE_CSS, unsafe_allow_html=True)
    sidebar_watchlist()
    tab1, tab2 = st.tabs(["차트 분석 & 메모", "종합 매매일지"])
    with tab1:
        tab_analysis()
    with tab2:
        tab_journal()


if __name__ == "__main__":
    main()
