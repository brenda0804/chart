"""매매 플랜(규칙 기반) + 과거 통계.

⚠️ 미래 예측이 아닙니다.
- trading_plan: 추매/진입/분할매도/손절 레벨과 각 시나리오의 순손익(수수료·세금 반영)
- historical_forward_stats: 축적된 과거 1분봉에서 '같은 시각 이후 N분' 실제 수익률 분포(경험적 근거)
"""
import math
from datetime import time as dtime
from statistics import median

import pandas as pd

from . import store, charting


def _auto_levels(vol_pct: float | None) -> dict:
    """1분 변동성으로 레벨(%) 자동 산정. 과도하게 작지/크지 않게 클램프."""
    u = min(max(vol_pct or 0.5, 0.5), 2.0)
    return {
        "add": round(u * 1.5, 1),   # 추매 -X%
        "tp1": round(u * 1.5, 1),   # 1차 익절 +X%
        "tp2": round(u * 3.0, 1),   # 2차 익절 +X%
        "stop": round(u * 2.0, 1),  # 손절 -X%
    }


def trading_plan(price: float, capital: int = charting.CAPITAL,
                 commission_rate: float = charting.COMMISSION_RATE,
                 tax_rate: float = charting.TAX_RATE, vol_pct: float | None = None,
                 levels: dict | None = None) -> dict:
    """규칙 기반 매매 플랜 + 익절/손절 시나리오 순손익."""
    lv = levels or _auto_levels(vol_pct)
    shares = math.floor(capital / price) if price > 0 else 0
    h1 = shares // 2
    h2 = shares - h1
    buy_amt = shares * price
    buy_fee = math.floor(buy_amt * commission_rate)

    prices = {
        "add": price * (1 - lv["add"] / 100),
        "entry": price,
        "tp1": price * (1 + lv["tp1"] / 100),
        "tp2": price * (1 + lv["tp2"] / 100),
        "stop": price * (1 - lv["stop"] / 100),
    }

    # 익절 시나리오: 절반 tp1, 절반 tp2
    s1, s2 = h1 * prices["tp1"], h2 * prices["tp2"]
    fee_win = math.floor(s1 * (commission_rate + tax_rate)) + math.floor(s2 * (commission_rate + tax_rate))
    net_win = (s1 + s2 - buy_amt) - buy_fee - fee_win

    # 손절 시나리오: 전량 stop
    s_all = shares * prices["stop"]
    net_loss = (s_all - buy_amt) - buy_fee - math.floor(s_all * (commission_rate + tax_rate))

    return {
        "levels": lv, "prices": prices, "shares": shares, "buy_amt": buy_amt,
        "net_win": net_win, "ret_win": net_win / buy_amt * 100 if buy_amt else 0.0,
        "net_loss": net_loss, "ret_loss": net_loss / buy_amt * 100 if buy_amt else 0.0,
        "rr": lv["tp1"] / lv["stop"] if lv["stop"] else 0.0,
    }


def historical_forward_stats(code: str, cur_time: dtime, forward_min: int = 30,
                             today_str: str = "") -> dict | None:
    """과거 저장분에서 '현재 시각 이후 forward_min분' 실제 수익률 분포.

    표본이 적을 수 있으니 참고용. 예측 아님.
    """
    dates = [d for d in store.record_dates() if d != today_str]
    rets: list[float] = []
    for d in dates:
        df = store.load_minute_data(code, d)
        if df is None or df.empty:
            continue
        base = df[df.index.time <= cur_time]
        if base.empty:
            continue
        base_close = float(base["Close"].iloc[-1])
        base_ts = base.index[-1]
        fwd = df[df.index <= base_ts + pd.Timedelta(minutes=forward_min)]
        if fwd.empty or base_close <= 0:
            continue
        fwd_close = float(fwd["Close"].iloc[-1])
        rets.append((fwd_close - base_close) / base_close * 100)

    if not rets:
        return None
    return {
        "n": len(rets), "median": median(rets), "min": min(rets), "max": max(rets),
        "win_rate": sum(1 for r in rets if r > 0) / len(rets) * 100,
        "forward_min": forward_min,
    }
