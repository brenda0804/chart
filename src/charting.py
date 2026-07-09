"""차트 생성 및 분석 (Plotly 인터랙티브).

- 일봉: 캔들 + 이평선(5,20) + 거래량 + 외인/기관/개인 누적 순매수
- 1분봉: 캔들 + 이평선 + 거래량 + '1천만원 최적 단타 구간' 하이라이트
- 분석 데이터는 CSV 로 저장(outputs/[종목명]/[날짜]_min.csv) → 역추적 시 인터랙티브 재생성
- hover 툴팁 / 확대·축소 / 구간 포커스는 Plotly 기본 제공
"""
import math

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

CAPITAL = 10_000_000  # 단타 분석 기준 투자금 (1,000만 원)

# 거래 비용 (기본값 — 증권사마다 다르므로 UI 에서 조정 가능)
COMMISSION_RATE = 0.00015  # 매매수수료 0.015% (편도, 매수·매도 각각)
TAX_RATE = 0.0015          # 증권거래세 0.15% (매도 시에만, 2025~ KOSPI·KOSDAQ)

_UP, _DOWN = "#e03131", "#1c7ed6"  # 한국식: 상승=빨강, 하락=파랑


# ---- 데이터 → DataFrame --------------------------------------------------
def daily_to_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows).rename(
        columns={
            "stck_bsop_date": "Date", "stck_oprc": "Open", "stck_hgpr": "High",
            "stck_lwpr": "Low", "stck_clpr": "Close", "acml_vol": "Volume",
        }
    )
    df["Date"] = pd.to_datetime(df["Date"], format="%Y%m%d")
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.set_index("Date").sort_index()[["Open", "High", "Low", "Close", "Volume"]]


def minute_to_df(rows: list[dict], date: str) -> pd.DataFrame:
    df = pd.DataFrame(rows).rename(
        columns={
            "stck_cntg_hour": "Time", "stck_oprc": "Open", "stck_hgpr": "High",
            "stck_lwpr": "Low", "stck_prpr": "Close", "cntg_vol": "Volume",
        }
    )
    df["Date"] = pd.to_datetime(date + df["Time"], format="%Y%m%d%H%M%S")
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.set_index("Date").sort_index()[["Open", "High", "Low", "Close", "Volume"]]


def investor_to_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).rename(
        columns={
            "stck_bsop_date": "Date", "frgn_ntby_qty": "외국인",
            "orgn_ntby_qty": "기관", "prsn_ntby_qty": "개인",
        }
    )
    if "Date" not in df:
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df["Date"], format="%Y%m%d")
    for c in ["외국인", "기관", "개인"]:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    return df.set_index("Date").sort_index()[["외국인", "기관", "개인"]]


# ---- 단타 분석 ------------------------------------------------------------
def optimal_daytrade(df_min: pd.DataFrame, capital: int = CAPITAL,
                     commission_rate: float = COMMISSION_RATE,
                     tax_rate: float = TAX_RATE) -> dict | None:
    """구간 내 '한 번 매수→한 번 매도' 최대수익 (사후 최적해, 복기용).

    수수료·세금을 반영한 순이익이 최대가 되는 구간을 찾는다.
    """
    closes = df_min["Close"].tolist()
    if len(closes) < 2:
        return None
    min_pos, best_net, best = 0, None, None
    for j in range(len(closes)):
        t = _trade(df_min, min_pos, j, capital, commission_rate, tax_rate)
        if best_net is None or t["profit_amt"] > best_net:
            best_net, best = t["profit_amt"], t
        if closes[j] < closes[min_pos]:
            min_pos = j
    if best is None or best["buy_pos"] == best["sell_pos"] or best["profit_amt"] <= 0:
        return None  # 수수료·세금까지 빼고 남는 순이익 구간이 없음
    return best


def simulate(df_min: pd.DataFrame, buy_pos: int, sell_pos: int, capital: int = CAPITAL,
             commission_rate: float = COMMISSION_RATE, tax_rate: float = TAX_RATE) -> dict:
    """사용자 지정 매수/매도 위치로 손익 계산 (수수료·세금 반영)."""
    return _trade(df_min, buy_pos, sell_pos, capital, commission_rate, tax_rate)


def _trade(df: pd.DataFrame, buy_pos: int, sell_pos: int, capital: int,
           commission_rate: float = COMMISSION_RATE, tax_rate: float = TAX_RATE) -> dict:
    buy_price = float(df["Close"].iloc[buy_pos])
    sell_price = float(df["Close"].iloc[sell_pos])
    shares = math.floor(capital / buy_price) if buy_price > 0 else 0
    buy_amount = shares * buy_price
    sell_amount = shares * sell_price

    gross = sell_amount - buy_amount                     # 세전 차익
    buy_fee = math.floor(buy_amount * commission_rate)   # 매수 수수료
    sell_fee = math.floor(sell_amount * commission_rate)  # 매도 수수료
    tax = math.floor(sell_amount * tax_rate)             # 증권거래세(매도)
    fees_total = buy_fee + sell_fee + tax
    net = gross - fees_total                              # 순이익
    ret = net / buy_amount * 100 if buy_amount else 0.0
    gross_ret = gross / buy_amount * 100 if buy_amount else 0.0
    return {
        "buy_pos": buy_pos, "sell_pos": sell_pos,
        "buy_time": df.index[buy_pos], "sell_time": df.index[sell_pos],
        "buy_price": buy_price, "sell_price": sell_price,
        "shares": shares, "cost": buy_amount,
        "gross_profit": gross, "buy_fee": buy_fee, "sell_fee": sell_fee,
        "tax": tax, "fees_total": fees_total,
        "profit_amt": net, "ret_pct": ret, "gross_ret_pct": gross_ret,
    }


def gap_info(df_daily: pd.DataFrame, date_str: str, capital: int = CAPITAL,
             commission_rate: float = COMMISSION_RATE, tax_rate: float = TAX_RATE) -> dict | None:
    """전일 종가 대비 당일 시가 갭(오버나이트) 정보.

    '전날 종가에 사서 당일 시가에 팔았다면'의 순이익(수수료·세금 반영)도 계산.
    """
    if df_daily is None or df_daily.empty:
        return None
    df = df_daily.sort_index()
    target = pd.to_datetime(date_str, format="%Y%m%d")
    if target not in df.index:
        return None
    pos = df.index.get_loc(target)
    if pos == 0:
        return None
    prev_close = float(df.iloc[pos - 1]["Close"])
    open_price = float(df.iloc[pos]["Open"])
    if prev_close <= 0 or open_price <= 0:
        return None
    gap_pct = (open_price - prev_close) / prev_close * 100

    shares = math.floor(capital / prev_close)
    buy_amt = shares * prev_close
    sell_amt = shares * open_price
    gross = sell_amt - buy_amt
    fees = (math.floor(buy_amt * commission_rate) + math.floor(sell_amt * commission_rate)
            + math.floor(sell_amt * tax_rate))
    net = gross - fees
    return {
        "prev_date": df.index[pos - 1], "prev_close": prev_close,
        "open_price": open_price, "gap_pct": gap_pct, "shares": shares,
        "gross": gross, "fees": fees, "net": net,
        "net_ret": net / buy_amt * 100 if buy_amt else 0.0,
    }


# ---- Plotly 차트 ----------------------------------------------------------
def _ma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()


def build_minute_figure(name: str, code: str, date: str,
                        df: pd.DataFrame, trade: dict | None) -> go.Figure:
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        vertical_spacing=0.04, row_heights=[0.75, 0.25])
    fig.add_trace(
        go.Candlestick(
            x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
            name="가격", increasing_line_color=_UP, decreasing_line_color=_DOWN,
        ),
        row=1, col=1,
    )
    for n, color in [(5, "orange"), (20, "purple")]:
        fig.add_trace(
            go.Scatter(x=df.index, y=_ma(df["Close"], n), name=f"MA{n}",
                       line=dict(width=1, color=color)),
            row=1, col=1,
        )
    vol_colors = [_UP if c >= o else _DOWN for o, c in zip(df["Open"], df["Close"])]
    fig.add_trace(
        go.Bar(x=df.index, y=df["Volume"], marker_color=vol_colors, name="거래량",
               showlegend=False),
        row=2, col=1,
    )

    if trade:
        fig.add_vrect(x0=trade["buy_time"], x1=trade["sell_time"],
                      fillcolor="gold", opacity=0.18, line_width=0, row=1, col=1)
        fig.add_trace(
            go.Scatter(x=[trade["buy_time"]], y=[trade["buy_price"]], mode="markers+text",
                       marker=dict(color=_DOWN, size=13, symbol="triangle-up"),
                       text=[f" 매수 {trade['buy_price']:,.0f}"], textposition="bottom right",
                       name="매수"),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(x=[trade["sell_time"]], y=[trade["sell_price"]], mode="markers+text",
                       marker=dict(color=_UP, size=13, symbol="triangle-down"),
                       text=[f" 매도 {trade['sell_price']:,.0f}"], textposition="top right",
                       name="매도"),
            row=1, col=1,
        )
        title = (f"{name} ({code}) · {date} 1분봉  |  "
                 f"최적 단타 순이익 {trade['ret_pct']:+.2f}%  ·  {trade['profit_amt']:,.0f}원"
                 f"  (수수료·세금 반영)")
    else:
        title = f"{name} ({code}) · {date} 1분봉  |  수익 가능 구간 없음"

    fig.update_layout(
        title=title, height=620, hovermode="x unified", dragmode="zoom",
        margin=dict(t=60, b=20, l=10, r=10),
        legend=dict(orientation="h", y=1.02, x=1, xanchor="right", yanchor="bottom"),
        xaxis_rangeslider_visible=False,
    )
    fig.update_yaxes(title_text="가격", row=1, col=1)
    fig.update_yaxes(title_text="거래량", row=2, col=1)
    return fig


def build_daily_figure(name: str, code: str, df: pd.DataFrame,
                       investor: pd.DataFrame) -> go.Figure:
    has_inv = not investor.empty
    rows = 3 if has_inv else 2
    heights = [0.6, 0.2, 0.2] if has_inv else [0.75, 0.25]
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True,
                        vertical_spacing=0.03, row_heights=heights)
    fig.add_trace(
        go.Candlestick(x=df.index, open=df["Open"], high=df["High"], low=df["Low"],
                       close=df["Close"], name="일봉",
                       increasing_line_color=_UP, decreasing_line_color=_DOWN),
        row=1, col=1,
    )
    for n, color in [(5, "orange"), (20, "purple")]:
        fig.add_trace(
            go.Scatter(x=df.index, y=_ma(df["Close"], n), name=f"MA{n}",
                       line=dict(width=1, color=color)),
            row=1, col=1,
        )
    vol_colors = [_UP if c >= o else _DOWN for o, c in zip(df["Open"], df["Close"])]
    fig.add_trace(
        go.Bar(x=df.index, y=df["Volume"], marker_color=vol_colors, name="거래량",
               showlegend=False),
        row=2, col=1,
    )
    if has_inv:
        inv = investor.reindex(df.index).fillna(0).cumsum()
        for col, color in [("외국인", _UP), ("기관", "#2f9e44"), ("개인", _DOWN)]:
            fig.add_trace(
                go.Scatter(x=inv.index, y=inv[col], name=col, line=dict(color=color, width=1.2)),
                row=3, col=1,
            )
        fig.update_yaxes(title_text="누적수급", row=3, col=1)

    fig.update_layout(
        title=f"{name} ({code}) 일봉  |  외인/기관/개인 누적 순매수",
        height=720, hovermode="x unified", margin=dict(t=60, b=20, l=10, r=10),
        legend=dict(orientation="h", y=1.02, x=1, xanchor="right", yanchor="bottom"),
        xaxis_rangeslider_visible=False,
    )
    fig.update_yaxes(title_text="가격", row=1, col=1)
    fig.update_yaxes(title_text="거래량", row=2, col=1)
    return fig
