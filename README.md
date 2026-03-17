# Brainjack

Codex OAuth 토큰을 이용해 로컬 프록시 서버를 띄우고, Claude Code가 이를 통해 Codex API를 사용하게 해주는 프로젝트입니다.

## 실행 방법

### 1. Codex 로그인
```bash
codex login
```

아래 파일이 있어야 합니다.
```bash
~/.codex/auth.json
```

### 2. 필요한 패키지 설치
```bash
pip install fastapi uvicorn httpx pydantic
```

### 3. 실행
가장 간단한 방법:
```bash
bash connect.sh
```

직접 서버만 실행:
```bash
python3 app/main.py
```

헬스 체크:
```bash
curl http://localhost:8741/health
```

## 프로젝트 구조

```text
brainjack/
├── README.md
├── connect.sh
└── app/
    ├── main.py
    ├── server.py
    ├── config.py
    ├── models.py
    ├── adapters/
    │   └── translator.py
    └── clients/
        ├── codex_client.py
        └── token_manager.py
```

## 구성 설명

- `connect.sh`: 프록시 실행 + Claude 연결
- `app/main.py`: 실행 진입점
- `app/server.py`: API 서버
- `app/adapters/translator.py`: Anthropic ↔ Codex 변환
- `app/clients/token_manager.py`: OAuth 토큰 관리
- `app/clients/codex_client.py`: Codex API 호출

## 주요 엔드포인트

- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/responses`
- `POST /v1/messages`

## 면책

이 프로젝트의 사용에 대한 책임은 사용자에게 있습니다.
