-- Supabase 테이블 생성 스크립트 (1회 실행)
-- 실행 방법: Supabase 대시보드 → 왼쪽 [SQL Editor] → 아래 전체 붙여넣기 → [Run]
--
-- Secret key(service_role)로 접근하므로 RLS 정책은 따로 필요 없습니다.
-- (개인용 단일 사용자 앱 기준)

-- 관심 종목
create table if not exists watchlist (
    code    text primary key,          -- 종목코드 (예: 005930)
    name    text not null,             -- 종목명
    added   text                       -- 추가 시각 (YYYY-MM-DD HH:MM:SS)
);

-- 매매일지 메모 (종목+날짜당 1건)
create table if not exists memos (
    id      text primary key,          -- '{code}_{date}'
    code    text not null,
    name    text not null,
    date    text not null,             -- YYYYMMDD
    memo    text default '',
    updated text                       -- 수정 시각
);

-- 1분봉 데이터 (종목+날짜당 1건, Parquet 를 base64 로 압축 저장)
create table if not exists minute_data (
    id      text primary key,          -- '{code}_{date}'
    code    text not null,
    name    text not null,
    date    text not null,
    parquet text not null,             -- base64(parquet bytes)
    rows    int  default 0,
    updated text
);
