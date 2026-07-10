"""단타 타이밍 참고 신호 + 목표/손절 시나리오.

⚠️ 규칙 기반 참고지표일 뿐, 매수 신호·투자자문이 아닙니다. 미래 예측 아님.
- technical_signals: 이평 크로스, 거래량 급증, 단기 급등/락, MA20 위치
- volatility_pct: 최근 변동성(%)
- scenario: 목표가/손절가(자동 or 수동) + 예상 순이익/손실 (수수료·세금 반영)
- timing_verdict: 신호 종합 판단(진입 관심/관망)
"""
import pandas as pd

from . import charting


def _sig(type_: str, label: str, level: str, detail: str) -> dict:
    """level: good(호재) / warn(주의) / info(중립)."""
    return {"type": type_, "label": label, "level": level, "detail": detail}


def technical_signals(df: pd.DataFrame, current_price: float | None = None) -> list[dict]:
    """1분봉 기반 기술적 신호 목록."""
    out: list[dict] = []
    if df is None or len(df) < 21:
        return out
    close = df["Close"]
    cur = float(current_price) if current_price else float(close.iloc[-1])

    ma5, ma20 = close.rolling(5).mean(), close.rolling(20).mean()
    if ma5.iloc[-2] <= ma20.iloc[-2] and ma5.iloc[-1] > ma20.iloc[-1]:
        out.append(_sig("golden", "골든크로스", "good", "MA5가 MA20을 상향 돌파"))
    elif ma5.iloc[-2] >= ma20.iloc[-2] and ma5.iloc[-1] < ma20.iloc[-1]:
        out.append(_sig("dead", "데드크로스", "warn", "MA5가 MA20을 하향 이탈"))

    vol = df["Volume"]
    avg_vol = vol.iloc[-21:-1].mean()
    if avg_vol > 0 and vol.iloc[-1] > 2 * avg_vol:
        out.append(_sig("vol", "거래량 급증", "good", f"직전 평균 대비 {vol.iloc[-1] / avg_vol:.1f}배"))

    if len(close) >= 6:
        base = float(close.iloc[-6])
        chg = (cur - base) / base * 100 if base else 0
        if chg >= 1.0:
            out.append(_sig("surge", "단기 급등", "good", f"최근 5분 {chg:+.2f}%"))
        elif chg <= -1.0:
            out.append(_sig("drop", "단기 급락", "warn", f"최근 5분 {chg:+.2f}%"))

    if not pd.isna(ma20.iloc[-1]):
        if cur > ma20.iloc[-1]:
            out.append(_sig("above", "MA20 위", "good", "20분 이평 위 (단기 상승추세)"))
        else:
            out.append(_sig("below", "MA20 아래", "warn", "20분 이평 아래 (단기 약세)"))
    return out


def volatility_pct(df: pd.DataFrame, n: int = 20) -> float:
    """최근 n개 캔들의 평균 (고가-저가)/종가 (%)."""
    if df is None or len(df) < 2:
        return 0.5
    rng = (df["High"] - df["Low"]).abs() / df["Close"].replace(0, pd.NA)
    return float(rng.tail(n).mean() * 100)


def scenario(df: pd.DataFrame, current_price: float, capital: int = charting.CAPITAL,
             commission_rate: float = charting.COMMISSION_RATE,
             tax_rate: float = charting.TAX_RATE,
             target_pct: float | None = None, stop_pct: float | None = None,
             k: float = 2.0) -> dict:
    """목표가/손절가 시나리오 + 예상 순이익/손실.

    target_pct/stop_pct 를 주면 그 값 사용(수동), 안 주면 변동성×k 로 자동 산정.
    미래 예측이 아니라 '이 가격에 사서 목표가/손절가에 팔면' 계산.
    """
    volp = volatility_pct(df)
    tp = target_pct if target_pct else max(round(volp * k, 2), 0.3)
    sp = stop_pct if stop_pct else max(round(volp * k, 2), 0.3)
    target = current_price * (1 + tp / 100)
    stop = current_price * (1 - sp / 100)
    tn = charting.net_pnl(current_price, target, capital, commission_rate, tax_rate)
    sn = charting.net_pnl(current_price, stop, capital, commission_rate, tax_rate)
    return {
        "vol_pct": volp, "target_pct": tp, "stop_pct": sp,
        "target": target, "stop": stop, "shares": tn["shares"],
        "target_net": tn["net"], "target_ret": tn["ret"],
        "stop_net": sn["net"], "stop_ret": sn["ret"],
        "rr": tp / sp if sp else 0.0,
    }


def timing_verdict(sigs: list[dict]) -> dict:
    """신호 종합 판단."""
    types = {s["type"] for s in sigs}
    goods = [s for s in sigs if s["level"] == "good"]
    warns = [s for s in sigs if s["level"] == "warn"]
    if "golden" in types and "vol" in types:
        return {"go": True, "title": "🟢 단타 관심 구간 (강)",
                "reason": "골든크로스 + 거래량 급증 동반"}
    if len(goods) >= 2 and not any(s["type"] in ("dead", "drop") for s in sigs):
        return {"go": True, "title": "🟢 단타 관심 구간",
                "reason": ", ".join(s["label"] for s in goods)}
    if warns:
        return {"go": False, "title": "🔴 관망 / 주의",
                "reason": ", ".join(s["label"] for s in warns)}
    return {"go": False, "title": "⚪ 특이 신호 없음", "reason": "뚜렷한 진입 근거 없음"}
