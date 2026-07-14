"""KIS 실시간 WebSocket 체결가 스트림 (Phase 2).

- 승인키(approval_key) 발급: REST 토큰과 별개
- 백그라운드 스레드에서 WS 연결 유지, 최신 체결가를 스레드-세이프하게 보관
- Streamlit 은 latest_tick(code) 로 최신값만 읽음 (rerun 과 분리)

⚠️ 실시간 체결은 **장중에만** 흐릅니다(장외/휴장이면 연결돼도 데이터 없음).
tr_id H0STCNT0 = 국내주식 실시간 체결가.
"""
import json
import threading
import time
from collections import deque

import requests

try:
    from websocket import WebSocketApp
except ImportError:  # websocket-client 미설치 시
    WebSocketApp = None

from . import config

_approval = {"key": None, "ts": 0.0}


def get_approval_key() -> str:
    """실시간 접속용 승인키 발급(캐시 12h). 필드명이 secretkey 임에 주의."""
    if _approval["key"] and time.time() - _approval["ts"] < 12 * 3600:
        return _approval["key"]
    config.validate()
    url = f"{config.BASE_URL}/oauth2/Approval"
    body = {
        "grant_type": "client_credentials",
        "appkey": config.KIS_APP_KEY,
        "secretkey": config.KIS_APP_SECRET,
    }
    resp = requests.post(url, json=body, timeout=10)
    resp.raise_for_status()
    key = resp.json()["approval_key"]
    _approval.update(key=key, ts=time.time())
    return key


class _Stream:
    def __init__(self) -> None:
        self.ticks: dict[str, dict] = {}          # 최신 틱
        self.buffers: dict[str, deque] = {}       # 틱 히스토리 (실시간 캔들 집계용)
        self.app = None
        self.thread: threading.Thread | None = None
        self.code: str | None = None
        self.connected = False
        self.error: str | None = None
        self.lock = threading.Lock()

    def _subscribe(self, ws) -> None:
        msg = {
            "header": {"approval_key": get_approval_key(), "custtype": "P",
                       "tr_type": "1", "content-type": "utf-8"},
            "body": {"input": {"tr_id": "H0STCNT0", "tr_key": self.code}},
        }
        ws.send(json.dumps(msg))

    def _on_open(self, ws) -> None:
        self.connected = True
        self.error = None
        self._subscribe(ws)

    def _on_message(self, ws, data: str) -> None:
        # 실시간 데이터: '0|H0STCNT0|001|<code>^<time>^<price>^...'
        if data and data[0] in ("0", "1"):
            parts = data.split("|")
            if len(parts) >= 4 and parts[1] == "H0STCNT0":
                f = parts[3].split("^")
                if len(f) > 12:
                    try:
                        t, price = f[1], float(f[2])
                        vol = float(f[12])  # 체결 거래량(CNTG_VOL)
                        with self.lock:
                            self.ticks[self.code] = {
                                "price": price, "time": t,
                                "chg_pct": float(f[5]), "at": time.time(),
                            }
                            buf = self.buffers.setdefault(self.code, deque(maxlen=8000))
                            buf.append((t, price, vol))
                    except (ValueError, IndexError):
                        pass
            return
        # 제어 메시지(구독 ack / PINGPONG)
        try:
            j = json.loads(data)
            if j.get("header", {}).get("tr_id") == "PINGPONG":
                ws.send(data)  # pong 되돌려 연결 유지
        except (json.JSONDecodeError, TypeError):
            pass

    def _on_error(self, ws, err) -> None:
        self.error = str(err)
        self.connected = False

    def _on_close(self, ws, *_a) -> None:
        self.connected = False

    def start(self, code: str) -> None:
        if WebSocketApp is None:
            self.error = "websocket-client 미설치"
            return
        if self.thread and self.thread.is_alive() and self.code == code:
            return  # 이미 같은 종목 스트리밍 중
        self.stop()
        self.code = code
        self.error = None
        self.app = WebSocketApp(
            config.WS_URL, on_open=self._on_open, on_message=self._on_message,
            on_error=self._on_error, on_close=self._on_close,
        )
        self.thread = threading.Thread(target=self.app.run_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.app:
            try:
                self.app.close()
            except Exception:  # noqa: BLE001
                pass
        self.app = None
        self.connected = False

    def latest(self, code: str) -> dict | None:
        with self.lock:
            return self.ticks.get(code)

    def tick_history(self, code: str) -> list:
        with self.lock:
            return list(self.buffers.get(code, ()))


_stream = _Stream()


def start_stream(code: str) -> None:
    _stream.start(code)


def stop_stream() -> None:
    _stream.stop()


def latest_tick(code: str) -> dict | None:
    return _stream.latest(code)


def tick_history(code: str) -> list:
    """실시간 캔들 집계용 틱 히스토리 [(HHMMSS, price, vol), ...]."""
    return _stream.tick_history(code)


def status() -> dict:
    return {"connected": _stream.connected, "code": _stream.code, "error": _stream.error}
