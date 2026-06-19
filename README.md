# turtle-invest

미국 대형주 터틀 추세추종 전략을 한국투자증권 API와 텔레그램 승인 흐름으로 운용하기 위한 포트폴리오 자동 관리 툴입니다.

## 현재 단계

마일스톤 1: 프로젝트 기반 구축

- Python 표준 라이브러리 기반 최소 실행 구조
- JSON 설정 로더
- 민감 정보 제외 예시 설정
- 기본 CLI
- 기본 로깅
- 초기 테스트

## 실행

```powershell
python -m turtle_invest --help
python -m turtle_invest doctor
python -m turtle_invest show-config --config config.example.json
```

## 문서

- [요구사항](docs/requirements.md)
- [개발 계획](docs/development-plan.md)
- [운영 가이드](docs/operations.md)

## 민감 정보

실제 API 키, 계좌번호, 텔레그램 토큰은 저장소에 커밋하지 않습니다. 로컬 실행 시에는 `config.local.json` 또는 환경 변수를 사용합니다.
