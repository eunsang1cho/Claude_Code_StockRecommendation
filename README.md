# AI Stock Alarm — 한국주식 패턴 알림 텔레그램 봇

KOSPI·KOSDAQ 중소형주를 자동으로 스캔해 **5가지 차트 패턴**을 감지하고, 결과를 텔레그램으로 전송하는 단일 사용자 봇입니다.
Claude AI가 뉴스·공시 분석과 알고리즘 파라미터 자동 조정에 활용됩니다.

---

## 주요 기능

| 기능 | 내용 |
|---|---|
| **자동 스캔** | 평일 15:40 장 마감 후 KOSPI+KOSDAQ 중소형주(시총 5조↓) 자동 패턴 탐지 |
| **자동 뉴스 분석** | 평일 08:00 워치리스트 종목 뉴스·DART 공시 Claude AI 분석 |
| **텔레그램 명령** | `/scan`, `/news`, `/watchlist`, `/add`, `/remove`, `/dashboard` |
| **웹 대시보드** | FastAPI 기반 실시간 대시보드 (포트 8080, ngrok 외부 공개 옵션) |
| **알고리즘 튜닝** | 대시보드에서 파라미터 정정 요청 → Claude Haiku 자동 반영 |
| **수익률 분석** | `analyze_algo.py`로 알고리즘별 실제 수익률·승률·개선 시사점 출력 |

---

## 패턴 설명

### 골삼이 (🔵)
대양봉(+15% 이상, 거래량 20MA의 10배+) 이후 현재가가 **대양봉 시가 ±5% + 20MA ±5% 구간**에서 지지받는 패턴.
거래량이 감소하면서 조정 후 재상승을 노리는 구조.

### 골든샘플 (🔑)
대양봉 이후 **거래량이 고갈**(대양봉의 20% 미만)되면서 주가가 대양봉 종가의 90% 이상을 유지하는 패턴.
매도세 고갈 신호로 해석.

### 레드삼각 (📐)
**박스권 횡보 → 대양봉 돌파 → 60MA까지 조정** 후 반등을 노리는 패턴.
60MA가 우상향 중이며 현재가가 박스권 상단 93% 이상을 유지해야 함.

### 골삼이(상승초입) (🚀)
최근 3~5 거래일 내 대양봉(+5% 이상, 거래량 20MA의 3배+) 발생 후 20MA·60MA 골든크로스가 임박한 초기 상승 구조.
240MA 우상향 + 과열 미달(20일 전 대비 50% 이내) 조건 포함.

### MA압축지지 (📦)
60일 이내 장대양봉(+7% 이상) 발생 후 **20MA가 장대 저가에 수렴**하면서 거래량이 줄어드는 압축 지지 패턴.
ATR이 낮고 박스권 내에서 20MA·60MA가 수렴 중일 때 탐지.

> **공통 조건**: 현재가 1,000원 이상 · 현재가 > 240MA · 240MA 우상향

---

## 아키텍처

```
main.py (텔레그램 봇 + 스케줄러)
├── data_fetcher.py   ← pykrx 데이터 수집 + 후보 종목 필터
├── scanner.py        ← 패턴 탐지 (5가지 알고리즘)
├── news_analyzer.py  ← Google News RSS + DART + Claude AI 분석
├── database.py       ← SQLite (스캔 결과, 알고리즘 파라미터, 현재가 스냅샷)
├── watchlist.py      ← JSON 워치리스트 관리
├── sector_info.py    ← 업종 정보 (pykrx)
├── analyze_algo.py   ← 알고리즘별 수익률 분석 리포트 (CLI)
└── web_server.py     ← FastAPI 대시보드 (port 8080)
    └── templates/index.html
```

---

## 설치 및 실행

### 1. 의존성 설치

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install python-telegram-bot anthropic python-dotenv httpx
```

### 2. 환경 변수 설정

프로젝트 **상위 디렉토리** (또는 동일 디렉토리)에 `.env` 파일 생성:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_USER_ID=123456789          # 정수, 본인 텔레그램 ID
CLAUDE_API_KEY=sk-ant-api03-...     # Anthropic API 키
DART_API_KEY=                       # (선택) DART 공시 API 키
NGROK_AUTH_TOKEN=                   # (선택) ngrok 터널 토큰
```

### 3. 실행

```bash
source venv/bin/activate
python main.py
```

웹 대시보드: `http://localhost:8080`

---

## 텔레그램 명령어

| 명령어 | 설명 |
|---|---|
| `/start` | 봇 소개 및 명령어 목록 |
| `/scan` | 즉시 패턴 스캔 (캐시 무시, 전체 재수집) |
| `/news` | 워치리스트 종목 뉴스 분석 |
| `/watchlist` | 현재 워치리스트 목록 조회 |
| `/add <종목코드>` | 종목 수동 추가 (예: `/add 005930`) |
| `/remove <종목코드>` | 종목 제거 |
| `/dashboard` | 웹 대시보드 링크 (ngrok 사용 시) |
| `지금 스캔해줘` | 자연어로 스캔 트리거 |

---

## 스케줄

| 시각 | 동작 |
|---|---|
| 평일 15:40 | 장 마감 후 자동 패턴 스캔 |
| 평일 08:00 | 워치리스트 뉴스·공시 분석 |

---

## 웹 대시보드

5개 탭으로 구성:

| 탭 | 설명 |
|---|---|
| **오늘 스캔** | 최근 스캔 세션 결과 (신뢰도순) |
| **스캔 이력** | 최근 N일 전체 스캔 이력 |
| **종목별 추적** | 종목별 감지 횟수·최초감지가·현재가·수익률, 가상 포트폴리오 요약 |
| **알고리즘별 추적** | 알고리즘별 서브탭, 종목 수익률 비교 및 포트폴리오 요약 |
| **알고리즘 요청** | 파라미터 정정 요청 제출 및 Claude 자동 반영 이력 |

### API 엔드포인트

| 엔드포인트 | 설명 |
|---|---|
| `GET /` | 대시보드 HTML |
| `GET /api/latest` | 최근 스캔 결과 |
| `GET /api/history?days=30` | 최근 N일 스캔 이력 |
| `GET /api/stocks` | 종목별 감지 통계 |
| `GET /api/price/{ticker}` | 실시간 현재가 (5분 캐시) |
| `GET /api/price-snapshots` | 저장된 현재가 스냅샷 전체 조회 |
| `POST /api/price-snapshots` | 현재가 스냅샷 저장 |
| `GET /api/algorithm-configs` | 알고리즘 파라미터 조회 |
| `POST /api/algorithm-request` | 파라미터 정정 요청 제출 |
| `GET /api/algorithm-requests` | 요청 목록 조회 |
| `PATCH /api/algorithm-request/{id}/status` | 요청 승인/반려 (Claude 자동 반영) |
| `DELETE /api/scan-result/{id}` | 스캔 결과 단건 삭제 |
| `DELETE /api/stock/{ticker}` | 특정 종목 전체 스캔 이력 삭제 |

---

## 수익률 분석 스크립트

```bash
# DB에 저장된 현재가 기준으로 분석
python analyze_algo.py

# KIS API로 현재가 직접 조회 후 분석
python analyze_algo.py --fetch
```

알고리즘별 평균 수익률·승률·최대 수익/손실과 신뢰도 구간별 성과, 개선 시사점을 출력합니다.

---

## 사용 모델

| 용도 | 모델 |
|---|---|
| 뉴스·공시 감성 분석 | `claude-opus-4-6` |
| 알고리즘 파라미터 자동 조정 | `claude-haiku-4-5-20251001` |

---

## 주의사항

- 단일 사용자 봇입니다. `TELEGRAM_USER_ID`에 등록된 사용자만 명령을 사용할 수 있습니다.
- `.env` 파일은 절대 커밋하지 마세요 (`.gitignore`에 포함됨).
- 이 봇은 **투자 참고용**입니다. 패턴 감지 결과가 수익을 보장하지 않습니다.
