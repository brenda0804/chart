"""국내주식 일봉 캔들 차트 그리기.

사용법:
    python main.py            # 기본: 삼성전자(005930), 최근 100일
    python main.py 000660     # 종목코드 지정 (SK하이닉스)
"""
import sys
from datetime import datetime, timedelta

import pandas as pd
import mplfinance as mpf

from src import kis_client

# Windows 콘솔 한글 출력 깨짐 방지
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def fetch_daily_df(stock_code: str, days: int = 100) -> pd.DataFrame:
    end = datetime.now()
    start = end - timedelta(days=days * 2)  # 주말/휴장 고려해 넉넉히
    rows = kis_client.get_daily_chart(
        stock_code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), period="D"
    )
    if not rows:
        raise RuntimeError(f"[{stock_code}] 시세 데이터가 비어있습니다. 종목코드를 확인하세요.")

    df = pd.DataFrame(rows)
    df = df.rename(
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
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col])
    df = df.set_index("Date").sort_index()
    return df[["Open", "High", "Low", "Close", "Volume"]]


def main() -> None:
    stock_code = sys.argv[1] if len(sys.argv) > 1 else "005930"
    df = fetch_daily_df(stock_code)
    print(f"[{stock_code}] {len(df)}개 일봉 조회 완료. 최근 종가: {df['Close'].iloc[-1]:,.0f}원")

    out = f"chart_{stock_code}.png"
    mpf.plot(
        df,
        type="candle",
        style="yahoo",
        volume=True,
        title=f"{stock_code} Daily Chart",
        mav=(5, 20),
        savefig=out,
    )
    print(f"차트 저장: {out}")


if __name__ == "__main__":
    main()
