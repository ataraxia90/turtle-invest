# 운영 가이드

## 현재 운영 모드

현재 시스템은 실계좌 조회를 사용하지만 실제 주문은 실행하지 않는다.

- 잔고 조회: 실계좌 조회
- 시세 조회: 실계좌 API 조회
- 주문 후보 생성: 활성화
- 텔레그램 승인 요청: 주문 후보가 있을 때만 발송
- 주문 실행: `DRY_RUN`
- 장 종료 보고: 조회 및 로컬 저장, 옵션으로 텔레그램 전송

## 수동 실행 명령

### 상태 확인

자주 쓰는 운영 명령을 보려면:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest commands --config config.local.json
```

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest status --config config.local.json
```

실주문 잠금 상태만 확인하려면:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest safety-check --config config.local.json
```

거래일 여부를 확인하려면:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest calendar --date 2026-07-03 --config config.local.json
```

주문 후보, 승인, 실행 상태를 확인하려면:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest orders --config config.local.json
```

### DB 백업

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest backup-db --config config.local.json
```

백업 파일은 기본적으로 `data/backups/` 아래에 생성된다.

### 로컬 리허설

KIS와 텔레그램을 호출하지 않고 승인/드라이런 실행/중복 방지 흐름을 확인하려면:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest rehearse-local --config config.local.json
python -m turtle_invest orders --trade-date 2099-01-01 --config config.local.json
python -m turtle_invest close-report --report-date 2099-01-01 --local-only --config config.local.json
```

### 최근 일봉 백테스트

KIS에서 제공하는 최근 일봉을 사용해 현재 유니버스 기준 리허설 백테스트를 실행한다.

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest backtest --equity 100000 --output data\backtests\recent_100.json --config config.local.json
```

1차 백테스트 가정:

- 체결가: 신호 발생일 종가
- 수수료/세금/환율: 0
- 파킹 ETF: 제외
- 유니버스: 현재 설정된 10개 종목 고정
- 매수 수량: 전략 Unit 전체를 살 현금이 있을 때만 체결

### 개장 전

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest pre-market --config config.local.json
```

총자산을 수동으로 지정해서 유닛 수량을 확인하려면:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest pre-market --equity 100000 --config config.local.json
```

승인 요청 후 텔레그램 응답을 최대 60초 기다리려면:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest pre-market --collect-timeout 60 --config config.local.json
```

### 승인 응답 수집

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest collect-approval --timeout 30 --config config.local.json
```

### 승인 후보 드라이런 실행

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest execute-approved --config config.local.json
```

최신 잔고/가격을 재검증한 뒤 통과한 후보만 드라이런 실행하려면:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest execute-approved --validate --config config.local.json
```

검증만 먼저 확인하려면:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest validate-approved --config config.local.json
```

검증 결과를 텔레그램 최종 리뷰 메시지로 보내려면:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest validate-approved --send --config config.local.json
```

### 장 종료 보고

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest market-close --config config.local.json
```

텔레그램으로 장 종료 보고를 보내려면:

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest market-close --send-report --config config.local.json
```

## Windows 작업 스케줄러 예시

가장 단순한 방식은 `scripts/` 아래 PowerShell 스크립트를 작업 스케줄러에 등록하는 것이다.

개장 전:

```text
C:\Users\atara\OneDrive\Documents\turtle-invest\scripts\pre_market.ps1
```

장 종료:

```text
C:\Users\atara\OneDrive\Documents\turtle-invest\scripts\market_close.ps1
```

작업 스케줄러에서는 프로그램을 `python`으로 두고, 인수에 아래처럼 입력한다.

```text
-m turtle_invest pre-market --config config.local.json
```

시작 위치:

```text
C:\Users\atara\OneDrive\Documents\turtle-invest
```

환경 변수 `PYTHONPATH=src`를 작업 환경에 넣기 어렵다면 PowerShell을 프로그램으로 사용한다.

프로그램:

```text
powershell
```

인수:

```text
-NoProfile -ExecutionPolicy Bypass -Command "$env:PYTHONPATH='src'; python -m turtle_invest pre-market --config config.local.json"
```

장 종료 보고용 인수:

```text
-NoProfile -ExecutionPolicy Bypass -Command "$env:PYTHONPATH='src'; python -m turtle_invest market-close --config config.local.json"
```

## 권장 스케줄

한국시간 기준 미국 정규장 운영을 전제로 한다.

- 개장 전 승인 요청: 미국장 개장 20-30분 전
- 승인 응답 수집: 승인 요청 후 5-10분 간격으로 반복
- 드라이런 실행: 승인 수집 직후
- 장 종료 보고: 미국장 종료 후 10-30분 뒤

서머타임과 휴장일은 아직 자동 반영하지 않는다. 다음 단계에서 시장 캘린더를 추가한다.

현재는 최소 안전장치로 주말 실행을 자동 스킵한다. 주말에도 강제로 테스트하려면 `--force`를 붙인다.

```powershell
$env:PYTHONPATH='src'
python -m turtle_invest pre-market --force --config config.local.json
```

## 안전장치

- `config.local.json`은 gitignore 대상이다.
- `data/`는 gitignore 대상이다.
- KIS 접근토큰은 `data/kis_token.json`에 저장된다.
- 현재 실제 주문 API 실행은 운영 워크플로우에 연결되어 있지 않다.
- 실제 주문 실행 기능을 열기 전에는 별도 사용자 승인이 필요하다.
- 승인 요청은 거래일별로 중복 전송되지 않도록 상태를 저장한다.
- 승인된 후보 실행 이벤트는 멱등성 키로 중복 기록을 방지한다.
- 운영 DB는 `backup-db` 명령으로 수동 백업할 수 있다.
- `validate-approved`는 매수 현금, 매도 보유수량, 승인 기준가 대비 최신가격 괴리를 확인한다.
- `validate-approved --send`는 실제 주문 전 최종 리뷰 메시지를 텔레그램으로 보낸다.
