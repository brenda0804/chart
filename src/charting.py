"""차트 생성 및 분석.

- 일봉 차트: 캔들 + 이평선(5,20) + 거래량 + 외인/기관/개인 누적 순매수(보조패널)
- 1분봉 차트: 캔들 + 이평선 + 거래량 + '1천만원 최적 단타 구간' 음영 하이라이트
- 결과 PNG: outputs/[종목명]/[날짜]_분석.png
"""
import math
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # Streamlit/headless 환경용 (GUI 없이 파일 저장)
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import pandas as pd
import mplfinance as mpf

# 한글 폰트 (윈도우: 맑은 고딕 / 맥: AppleGothic / 나눔). 설치된 것 중 첫 번째 사용.
_available = {f.name for f in fm.fontManager.ttflist}
_FONT = next(
    (f for f in ("Malgun Gothic", "AppleGothic", "NanumGothic", "MS Gothic") if f in _available),
    "DejaVu Sans",
)
matplotlib.rcParams["font.family"] = _FONT
matplotlib.rcParams["axes.unicode_minus"] = False

# mplfinance 스타일(yahoo)이 폰트를 덮어쓰므로, 한글 폰트를 rc 로 주입한 커스텀 스타일 사용
_STYLE = mpf.make_mpf_style(
    base_mpf_style="yahoo",
    rc={"font.family": _FONT, "axes.unicode_minus": False},
)

_OUTPUT_ROOT = Path(__file__).resolve().parent.parent / "outputs"
CAPITAL = 10_000_000  # 단타 분석 기준 투자금 (1,000만 원)


# ---- 공통 유틸 -----------------------------------------------------------
def _safe_dir(name: str) -> str:
    """폴더명으로 못 쓰는 문자 제거."""
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "unknown"


def output_path(name: str, date: str) -> Path:
    d = _OUTPUT_ROOT / _safe_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{date}_분석.png"


# ---- 데이터 → DataFrame --------------------------------------------------
def daily_to_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows).rename(
        columns={
            "stck_bsop_date": "Date",
            "stck_oprc": "Open",
            "stck_hgpr": "High",
            "stck_lwpr": "Low",
            "stck_clpr": "Close",
            "acml_vol": "Volume",
        }
    )
    df["Date"] = pd.to_datetime(df["Date"], format="%Y%m%d")
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.set_index("Date").sort_index()[["Open", "High", "Low", "Close", "Volume"]]


def minute_to_df(rows: list[dict], date: str) -> pd.DataFrame:
    """1분봉 rows → DataFrame. date='YYYYMMDD'."""
    df = pd.DataFrame(rows).rename(
        columns={
            "stck_cntg_hour": "Time",
            "stck_oprc": "Open",
            "stck_hgpr": "High",
            "stck_lwpr": "Low",
            "stck_prpr": "Close",
            "cntg_vol": "Volume",
        }
    )
    df["Date"] = pd.to_datetime(date + df["Time"], format="%Y%m%d%H%M%S")
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.set_index("Date").sort_index()[["Open", "High", "Low", "Close", "Volume"]]


def investor_to_df(rows: list[dict]) -> pd.DataFrame:
    """투자자매매동향 → 날짜 인덱스, 외인/기관/개인 순매수 수량."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).rename(
        columns={
            "stck_bsop_date": "Date",
            "frgn_ntby_qty": "외국인",
            "orgn_ntby_qty": "기관",
            "prsn_ntby_qty": "개인",
        }
    )
    if "Date" not in df:
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df["Date"], format="%Y%m%d")
    for c in ["외국인", "기관", "개인"]:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    return df.set_index("Date").sort_index()[["외국인", "기관", "개인"]]


# ---- 최적 단타 구간 분석 --------------------------------------------------
def optimal_daytrade(df_min: pd.DataFrame, capital: int = CAPITAL) -> dict | None:
    """당일 1분봉에서 '한 번 매수→한 번 매도'로 수익이 최대가 되는 구간을 찾는다.

    (사후 최적해: 복기용. 실매매 신호가 아님)
    반환: buy/sell 위치·가격·수량·수익금·수익률, 없으면 None.
    """
    closes = df_min["Close"].tolist()
    if len(closes) < 2:
        return None

    min_pos = 0
    best = None  # (profit_per_share, buy_pos, sell_pos)
    for j in range(len(closes)):
        gain = closes[j] - closes[min_pos]
        if best is None or gain > best[0]:
            best = (gain, min_pos, j)
        if closes[j] < closes[min_pos]:
            min_pos = j

    profit_per_share, buy_pos, sell_pos = best
    if profit_per_share <= 0 or buy_pos == sell_pos:
        return None  # 당일 상승 구간 없음

    buy_price = closes[buy_pos]
    sell_price = closes[sell_pos]
    shares = math.floor(capital / buy_price)
    if shares <= 0:
        return None
    profit_amt = shares * (sell_price - buy_price)
    ret_pct = (sell_price - buy_price) / buy_price * 100
    return {
        "buy_pos": buy_pos,
        "sell_pos": sell_pos,
        "buy_time": df_min.index[buy_pos],
        "sell_time": df_min.index[sell_pos],
        "buy_price": buy_price,
        "sell_price": sell_price,
        "shares": shares,
        "profit_amt": profit_amt,
        "ret_pct": ret_pct,
    }


# ---- 차트 렌더링 ----------------------------------------------------------
def render_daily_chart(name: str, code: str, df: pd.DataFrame,
                       investor: pd.DataFrame, save_to: Path) -> Path:
    """일봉 캔들 + 이평 + 거래량 + 수급 보조패널."""
    apds = []
    if not investor.empty:
        inv = investor.reindex(df.index).fillna(0).cumsum()  # 누적 순매수 추이
        apds = [
            mpf.make_addplot(inv["외국인"], panel=2, color="red", ylabel="누적수급"),
            mpf.make_addplot(inv["기관"], panel=2, color="green"),
            mpf.make_addplot(inv["개인"], panel=2, color="blue"),
        ]
    fig, _ = mpf.plot(
        df, type="candle", style=_STYLE, mav=(5, 20), volume=True,
        addplot=apds, returnfig=True, figsize=(11, 7),
        title=f"\n{name}({code}) 일봉  |  외인(빨강)/기관(초록)/개인(파랑) 누적순매수",
    )
    fig.savefig(save_to, dpi=100, bbox_inches="tight")
    plt.close(fig)
    return save_to


def render_minute_chart(name: str, code: str, date: str, df_min: pd.DataFrame,
                        trade: dict | None, save_to: Path) -> Path:
    """1분봉 캔들 + 이평 + 거래량 + 최적 단타 구간 하이라이트. save_to 로 저장."""
    if trade:
        title = (
            f"\n{name}({code}) {date} 1분봉  |  "
            f"최적 단타 수익률 {trade['ret_pct']:+.2f}%  "
            f"수익금 {trade['profit_amt']:,.0f}원 (1천만원 기준)"
        )
    else:
        title = f"\n{name}({code}) {date} 1분봉  |  당일 수익 가능 구간 없음"

    fig, axes = mpf.plot(
        df_min, type="candle", style=_STYLE, mav=(5, 20), volume=True,
        returnfig=True, figsize=(11, 6), title=title,
    )
    if trade:
        # mplfinance x축은 정수 위치 인덱스 → buy_pos~sell_pos 음영
        axes[0].axvspan(trade["buy_pos"], trade["sell_pos"], color="gold", alpha=0.25)
        axes[0].annotate(
            f"매수 {trade['buy_price']:,.0f}", (trade["buy_pos"], trade["buy_price"]),
            color="blue", fontsize=8, ha="center", va="top",
        )
        axes[0].annotate(
            f"매도 {trade['sell_price']:,.0f}", (trade["sell_pos"], trade["sell_price"]),
            color="red", fontsize=8, ha="center", va="bottom",
        )
    fig.savefig(save_to, dpi=100, bbox_inches="tight")
    plt.close(fig)
    return save_to
