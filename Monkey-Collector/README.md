# Monkey-Collector

Android GUI world model 학습용 데이터를 수집하는 App + Server 파이프라인이다. Android AccessibilityService 앱이 화면 전환을 감지해 screenshot 과 XML 을 보내고, Python 서버가 다음 action 을 선택해 ADB 로 실행한다.

## 개요

구성 요소:

- Android app: [`app/`](./app)
- Python server package: [`server/`](./server)
- CLI entrypoint: `monkey-collect`
- 테스트: [`tests/`](./tests)

현재 코드 기준 핵심 동작:

- 서버 드리븐 파이프라인: Python 서버가 TCP 로 `START {package}` 메시지를 보내면 Android 앱이 해당 앱을 수집한다. 사용자가 앱 측에서 버튼을 누르는 단계는 없다.
- 수집 대상은 `apps.csv` 의 `installed=true` 로 표시된 앱들 중에서 고른다. `sync-installed` 서브커맨드가 `adb pm list packages` 기반으로 이 컬럼을 자동 갱신한다.
- App 이 screen stabilization 과 visual change 판정을 담당하고, Server 가 SmartExplorer 로 action 을 선택하고 raw session 을 저장한다.
- 세션 디렉토리는 `data/raw/{package}/` 형식이다. `metadata.json` 의 `completed_at` 이 채워진 앱은 다음 `run` 에서 **자동으로 건너뛴다** (중단된 세션은 resume). `--force` 로 완료된 앱도 다시 수집 가능.
- input text 생성은 OpenAI 기반 `api` 모드와 hardcoded `random` 모드를 지원한다. `OPENAI_API_KEY` 가 없으면 자동으로 random fallback 한다.

## 설치

### Python server

Monkey-Collector 는 conda env **`monkey-collector`** 하나만 사용한다. `.venv` / `uv` 는 쓰지 않는다. (형제 프로젝트 `../GUI-Model/` 은 백엔드별로 `gui-model-llamafactory` / `gui-model-unsloth` env 를 쓴다.)

```bash
conda create -n monkey-collector python=3.10 -y
conda activate monkey-collector
cd /path/to/Monkey-Collector
pip install -e .
```

개발용 도구(pytest, ruff, mypy) 가 필요하면 `pip install -e '.[dev]'`.

선택 사항:

```bash
cp .env.example .env
```

`.env` 또는 환경변수에 `OPENAI_API_KEY` 를 넣으면 `--input-mode api` 에서 LLM 기반 입력 텍스트 생성을 사용한다.

추가 전제:

- Python 3.10+
- ADB 가 PATH 에 있거나 `ANDROID_HOME` 이 설정되어 있어야 한다
- 디바이스 한 대가 `adb devices` 에 연결되어 있어야 한다 (USB 또는 단일 에뮬레이터)

### Android app

```bash
cd app
./gradlew assembleDebug
adb install app/build/outputs/apk/debug/app-debug.apk
```

설치 후 디바이스에서 AccessibilityService 를 활성화해야 한다.

## 빠른 시작

### 1. Android 앱 준비

1. Monkey Collector 앱에서 server IP / port 입력
2. Save & Ready → Accessibility 권한 + MediaProjection 권한 허용
3. 이후 앱은 백그라운드에서 서버 연결을 유지한다. 사용자가 수집 시작 버튼을 누를 필요가 없다.

### 2. 디바이스 설치 앱을 `apps.csv` 에 반영

```bash
monkey-collect sync-installed
```

`adb pm list packages` 결과를 읽어 `apps.csv` 의 `installed` 컬럼(`true`/`false`)을 in-place 로 갱신한다.

### 3. 수집 실행

```bash
# apps.csv 의 installed=true 인 앱 전부 순차 수집 (이미 완료된 앱은 자동 skip)
monkey-collect run --apps all --steps 100

# 원하는 앱만 지정 (완료 여부는 동일하게 체크)
monkey-collect run --apps com.google.android.deskclock com.google.android.calculator --steps 50

# 완료된 앱도 다시 수집
monkey-collect run --apps all --force

# 특정 앱의 기존 세션을 폐기하고 새로 시작
monkey-collect run --apps com.google.android.deskclock --new-session

# 입력 텍스트를 hardcoded 로 (API 비용 없음)
monkey-collect run --apps all --input-mode random
```

동작:

- 서버가 각 앱마다 `adb shell am start` 로 앱을 실행하고, TCP 로 `{"type": "START", "package": "com.X"}` 를 보낸다. Android 앱은 standby 연결을 유지하다가 START 를 받아 자동으로 수집을 시작한다.
- 한 세션이 끝나면 서버가 `SESSION_END` 를 보내 클라이언트를 정리하고, 다음 앱으로 이동한다.
- 큐 구성 시 `data/raw/{pkg}/metadata.json` 의 `completed_at` 이 채워진 앱은 **완료로 판정되어 스킵**. `--force` 로 우회하거나, 중단된(미완료) 세션은 `completed_at` 이 `null` 이라 자동으로 resume 된다.

## CLI

### `run`

서버 드리븐 수집. `apps.csv` 의 `installed=true` 앱 전부 또는 지정한 패키지 목록을 순차 수집한다.

```bash
monkey-collect run --apps all --steps 100
monkey-collect run --apps com.google.android.deskclock --steps 50
```

주요 옵션:

- `--apps` (필수): `all` 이면 `apps.csv` 의 `installed=true` 전부. 아니면 하나 이상의 package_id.
- `--steps`: 세션당 최대 step 수 (기본 100)
- `--seed`: explorer 랜덤 시드 (기본 42)
- `--delay`: action 사이 대기 시간(ms, 기본 1500)
- `--port`: TCP server port (기본 12345)
- `--output`: raw session 저장 루트 (기본 `data/raw`)
- `--input-mode`: `api` 또는 `random`
- `--new-session`: 해당 패키지의 기존 세션을 삭제하고 새로 시작
- `--force`: `completed_at` 이 채워진 앱도 다시 수집 (기본은 완료 앱 skip)

### `sync-installed`

디바이스에서 `pm list packages` 를 조회해 `apps.csv` 의 `installed` 컬럼을 갱신한다. `run --apps all` 이전에 한 번 실행해두면 대상 큐가 최신 상태에서 구성된다.

```bash
monkey-collect sync-installed
monkey-collect sync-installed --apps-csv custom_apps.csv
```

주요 옵션:

- `--apps-csv`: 갱신할 apps.csv 경로 (기본 `apps.csv`)

### `reset`

수집된 세션 데이터를 범위 단위로 삭제한다. 특정 패키지만 재수집하거나 전체 결과를 날리고 싶을 때 사용한다.

```bash
# 전체 삭제
monkey-collect reset --all --yes

# 특정 패키지만
monkey-collect reset --packages com.example.foo,com.example.bar --yes

# 미리 보기
monkey-collect reset --packages com.example.foo --dry-run
```

주요 옵션:

- `--all`: `--output` 전체를 삭제 (다른 스코프 플래그와 상호 배타)
- `--packages`: 삭제할 package_id 리스트
- `--output`: 데이터 루트 (기본 `data/raw`)
- `--dry-run`: 삭제 없이 대상 경로만 출력
- `--yes`: 확인 프롬프트 스킵

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
