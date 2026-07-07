"""환경설정 로드 (.env 에서 KIS 인증정보를 읽어온다)."""
import os
from dotenv import load_dotenv

load_dotenv()

KIS_APP_KEY = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")
KIS_ENV = os.getenv("KIS_ENV", "vps").lower()  # prod=실전, vps=모의

# Supabase (클라우드 DB — 관심종목/메모/1분봉 저장)
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")


def validate_supabase() -> None:
    if not SUPABASE_URL or not SUPABASE_URL.startswith("https://"):
        raise RuntimeError(".env 의 SUPABASE_URL 이 비어있거나 잘못됐습니다 (https://xxx.supabase.co).")
    if not SUPABASE_KEY or SUPABASE_KEY.startswith("sb_secret_...") or SUPABASE_KEY.startswith("여기에"):
        raise RuntimeError(".env 의 SUPABASE_KEY 가 비어있습니다. Supabase Secret key 를 넣어주세요.")

# 환경별 도메인
if KIS_ENV == "prod":
    BASE_URL = "https://openapi.koreainvestment.com:9443"
    WS_URL = "ws://ops.koreainvestment.com:21000"
else:  # vps (모의투자)
    BASE_URL = "https://openapivts.koreainvestment.com:29443"
    WS_URL = "ws://ops.koreainvestment.com:31000"


def validate() -> None:
    """키가 채워졌는지 확인. 안 채워졌으면 친절한 에러."""
    if not KIS_APP_KEY or KIS_APP_KEY.startswith("여기에"):
        raise RuntimeError(".env 의 KIS_APP_KEY 가 비어있습니다. 발급받은 값을 넣어주세요.")
    if not KIS_APP_SECRET or KIS_APP_SECRET.startswith("여기에"):
        raise RuntimeError(".env 의 KIS_APP_SECRET 가 비어있습니다. 발급받은 값을 넣어주세요.")
