# Monkey-Collector

Android GUI world model 학습용 데이터를 수집하는 App + Server 파이프라인이다. Android AccessibilityService 앱이 화면 전환을 감지해 screenshot 과 XML 을 보내고, Python 서버가 다음 action 을 선택해 ADB 로 실행한다.

## 개요

구성 요소:

- Android app: [`app/`](./app)
- Python server package: [`server/`](./server)
- CLI entrypoint: `monkey-collect`
- 테스트: [`tests/`](./tests)

현재 코드 기준 핵심 동작:

- App 이 screen stabilization 과 visual change 판정을 담당한다.
- Server 가 SmartExplorer 로 action 을 선택하고 raw session 을 저장한다.
- 세션 디렉토리는 `data/raw/{package}/` 형식이며, 기본 동작은 기존 세션 resume 이다.
- input text 생성은 OpenAI 기반 `api` 모드와 hardcoded `random` 모드를 지원한다. `OPENAI_API_KEY` 가 없으면 자동으로 random fallback 한다.

## 설치

### Python server

```bash
pip install -e .
```

선택 사항:

```bash
cp .env.example .env
```

`.env` 또는 환경변수에 `OPENAI_API_KEY` 를 넣으면 `--input-mode api` 에서 LLM 기반 입력 텍스트 생성을 사용한다.

추가 전제:

- Python 3.10+
- ADB 가 PATH 에 있거나 `ANDROID_HOME` 이 설정되어 있어야 한다

### Android app

```bash
cd app
./gradlew assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk
```

설치 후 디바이스에서 AccessibilityService 를 활성화해야 한다.

## 빠른 시작

### 1. 서버 실행

```bash
monkey-collect run --steps 100
```

기본 동작:

- 다중 세션 모드
- 클라이언트가 target package 를 보내면 그 앱 기준으로 수집 시작
- 같은 패키지의 기존 세션이 있으면 이어서 저장

### 2. Android 앱에서 연결

1. Monkey Collector 앱에서 server IP / port 입력
2. Save 후 필요한 권한 허용
3. 수집할 앱을 foreground 로 띄움
4. 플로팅 START 버튼 클릭

### 3. 단일 세션 또는 새 세션 강제 시작

```bash
monkey-collect run --app com.android.calculator2 --single --steps 50
monkey-collect run --steps 100 --new-session
monkey-collect run --steps 100 --input-mode random
```

## CLI

### `run`

```bash
monkey-collect run --steps 100
monkey-collect run --app com.android.calculator2 --single --steps 50
```

주요 옵션:

- `--app`: 서버에서 target package 를 강제 지정한다. 생략하면 클라이언트가 보낸 package 를 사용한다.
- `--steps`: 세션당 최대 step 수
- `--seed`: explorer 랜덤 시드
- `--delay`: action 사이 대기 시간(ms)
- `--port`: TCP server port
- `--output`: raw session 저장 루트
- `--device`: ADB device serial
- `--input-mode`: `api` 또는 `random`
- `--single`: 단일 세션 모드
- `--new-session`: 기존 패키지 세션 삭제 후 새로 시작

### `convert`

```bash
monkey-collect convert \
  --session data/raw/com.example.app \
  --output data/processed/gui-model_stage1.jsonl \
  --images-dir data/processed/images
```

### `convert-all`

```bash
monkey-collect convert-all \
  --raw-dir data/raw \
  --output data/processed/gui-model_stage1.jsonl \
  --images-dir data/processed/images
```

### `page-map`

```bash
monkey-collect page-map --session data/raw/com.example.app
monkey-collect page-map --session data/raw/com.example.app --threshold 0.9 --no-open
```

### `page-map-all`

```bash
monkey-collect page-map-all --raw-dir data/raw --no-open
```

### `regenerate`

```bash
monkey-collect regenerate --raw-dir data/raw
```

raw XML 을 기준으로 `_parsed.xml`, `_hierarchy.xml`, `_encoded.xml`, `_pretty.xml` 를 다시 만든다.

## 저장 구조

기본 raw session 구조:

```
data/raw/{package}/
├── metadata.json
├── screenshots/
├── xml/
├── events.jsonl
├── activity_coverage.csv
├── cost.csv
├── page_graph.json
└── page_graph.html
```

`xml/` 아래에는 raw XML 과 함께 다음 파생 파일이 저장된다.

- `{step}_parsed.xml`
- `{step}_hierarchy.xml`
- `{step}_encoded.xml`
- `{step}_pretty.xml`

## 코드 읽기 시작점

- [`server/cli.py`](./server/cli.py): 실제 CLI
- [`server/pipeline/collector.py`](./server/pipeline/collector.py): 수집 진입점
- [`server/pipeline/explorer.py`](./server/pipeline/explorer.py): action selection
- [`server/infra/storage/storage.py`](./server/infra/storage/storage.py): 세션 포맷
- [`app/app/src/main/java/com/monkey/collector`](./app/app/src/main/java/com/monkey/collector): Android 앱

구조 설명은 [`ARCHITECTURE.md`](./ARCHITECTURE.md), 작업 규칙은 [`AGENTS.md`](./AGENTS.md) 를 본다.
