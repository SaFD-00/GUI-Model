# Atlas-Collector

Android GUI world model 학습용 **Stage-1 (NEXT_STATE_PREDICTION)** 데이터를 수집하는 host-pull 수집기다.
호스트의 Python 프로세스 **하나**가 ADB 로 디바이스를 직접 몰고, 관측을 스스로 끌어온다(pull).
**Android 앱도, AccessibilityService 도, TCP 서버도 없다.**

형제 프로젝트 [`Monkey-Collector/`](../Monkey-Collector) 는 device-push 설계(디바이스가 화면 변화를 감지해
서버로 밀어 올리는 구조)다. Atlas 는 그 설계를 **의도적으로 승계하지 않았다** — push 신호를 기다리는
구조가 예산의 44-56% 를 signal timeout 으로 태웠기 때문이다. 호스트가 폴링하면 기다릴 신호 자체가 없다.

---

> # ⚠️ 실계정 디바이스에서 돈다 — action guard 가 **없다**
>
> 이 수집기는 **실제 계정으로 로그인된 개인 디바이스**(Pixel 6)에 대고 자율 탐색을 돌린다.
> WhatsApp · Telegram · Signal · Slack · Discord 가 **로그인된 상태**이고, 수집기에는
> **파괴적 action 을 막는 가드가 전혀 없다.** 탐색기는 화면에 보이는 것을 누르므로
> **실제 메시지를 보내거나 실제 게시물을 올릴 수 있다.** 전송 버튼도 그냥 버튼이다.
>
> 이건 버그도 TODO 도 아니다. 사용자가 위험을 인지한 뒤 내린 **의도적이고 승인된 결정**이다 —
> 가드를 넣으면 도달 가능한 화면이 줄어 수집 가치가 떨어지고, 이 기기는 그 대가를 치르기로 한 기기다.
> 따라서 "가드를 추가한다" 는 개선 제안이 아니라 **설계 변경**이며 승인 없이 넣지 않는다.
>
> **다른 기기에서 돌리려는 사람에게**: 실행 전에 그 기기가 **어떤 계정으로 로그인돼 있는지** 확인하라.
> 더미 계정 / 로그아웃 상태 / 전용 테스트 기기가 아니라면 상대방에게 실제로 메시지가 나간다.
> 계정 앱을 통째로 빼려면 `--exclude-auth` 를 쓴다 (`auth_required=account_required` 15개 제외).

---

## 개요

구성 요소:

- Python 패키지: [`src/atlas_collector/`](./src/atlas_collector)
- 앱 카탈로그: [`catalog/apps.csv`](./catalog/apps.csv) (52행, 실기기에서 해소된 **커밋 데이터**)
- 설정: [`config/run.yaml`](./config/run.yaml)
- CLI entrypoint: `atlas-collect`
- 테스트: [`tests/`](./tests)

핵심 동작 (설계 기준):

- **host-pull 루프**: `uiautomator dump` → `before_xml`, `exec-out screencap -p` → `before_screenshot`,
  `input tap/swipe/text` → `action`, 화면 안정화 후 다시 dump/screencap → `after_xml` / `after_screenshot`.
- **출력 단위는 triple** — `(before_xml, before_screenshot, action, after_xml, after_screenshot)`.
- **탐색 개념은 LLM-Explorer 계열**: coverage-guided unexplored-first 선택, 미탐색 action 까지의 shortest-path
  navigation, BM25 + pixel page matching. page 식별은 **항상 LLM-free** 다.
- **LLM 은 단 하나에만 쓰인다 — 텍스트 입력 필드의 입력값 생성.** OpenRouter Chat Completions,
  기본 모델 `qwen/qwen3.8-flash`.
- **auth-gated 앱**: 카탈로그의 `auth_required` 컬럼이 `account_required` 인 앱은 러너가 **기본적으로 건너뛴다**.
  `--include-auth` 를 줬을 때만 포함한다.

### ⚠️ 구현 상태 (milestone)

| Milestone | 내용 | 상태 |
|---|---|---|
| M1 | scaffold + catalog + config + docs | **DONE** |
| M2 | `xml/` — XML 인코딩, actionable element 추출, 좌표 변환 | **DONE** (`src/atlas_collector/xml/`, 테스트 통과) |
| M3 | coverage-guided exploration | **미구현** |
| M4 | host-pull collection loop | **미구현** |
| M5 | Stage-1 export | **미구현** |

M2 는 **라이브러리**이고 자체 CLI 서브커맨드가 없다. 그래서 M1+M2 가 끝난 지금도
**실제로 동작하는 서브커맨드는 `catalog` 하나**다. 나머지는 `--help` 에 표면을 드러내되
호출하면 어느 milestone 이 채울지 명시한 `NotImplementedError` 를 낸다.

## 설치

Atlas-Collector 는 uv 가 관리하는 `.venv` 를 쓴다 (`uv sync` 가 `.python-version` 의 3.11 을 자동 설치).

```bash
# 1) uv 설치 (한 번만)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2) Atlas-Collector 설치
cd /path/to/Implicit-World-Modeling/Atlas-Collector
uv sync                    # runtime 만
uv sync --extra dev        # 개발 도구 (pytest, ruff, mypy) 포함

# 3) 실행
uv run atlas-collect --help
# (또는 `source .venv/bin/activate` 후 직접 호출)
```

`uv.lock` 은 함께 커밋한다. 다른 머신에서는 `uv sync --frozen` 으로 동일 환경을 복구한다.

런타임 의존성: `pillow`, `loguru`, `openai` (OpenAI 호환 SDK 로 OpenRouter 호출), `pyyaml`.
정확한 버전은 [`pyproject.toml`](./pyproject.toml) 참조.

추가 전제:

- Python 3.11+
- ADB 가 PATH 에 있거나 `ANDROID_HOME` 이 설정돼 있어야 한다
- 타깃 디바이스가 `adb devices` 에 online 으로 보여야 한다 (M4 부터 필요; M1 의 `catalog` 는 디바이스 불필요)

### 타깃 디바이스 (이번 세션 실측)

| 항목 | 값 |
|---|---|
| serial | `19101FDF6004EH` |
| model | Pixel 6 (`oriole`) |
| OS | Android 16 / SDK 36 |
| `wm size` | 1080x2400 |
| density | 420 |

`uiautomator dump` 와 `exec-out screencap -p` 모두 SDK 36 에서 동작을 확인했다.

> ⚠️ **USB 링크가 세션 중 끊어진다.** 실제로 probing 중 두 번 끊겼고, `adb kill-server && adb start-server` 로
> 복구됐다. 이 복구는 `AdbClient.reconnect()` 에 들어 있다(kill-server → start-server → wait-for-device → serial 재탐색).

### LLM (선택)

```bash
export OPENROUTER_API_KEY=...
```

없으면 입력 텍스트 생성이 hardcoded `random` 으로 폴백하고 수집은 그대로 진행된다.
**page 식별은 LLM 을 전혀 타지 않으므로 영향이 없다.**
`OPENROUTER_MODEL` (기본 `qwen/qwen3.8-flash`), `OPENROUTER_BASE_URL` (기본 `https://openrouter.ai/api/v1`) 로 덮어쓸 수 있다.

## 카탈로그

[`catalog/apps.csv`](./catalog/apps.csv) 는 **실기기(Pixel 6)에 대고 해소한 커밋 데이터**다.
생성 산출물이 아니므로 재생성하거나 "개선" 하지 않는다.

컬럼: `tier,category,sub_category,app_name,package_id,source,priority,auth_required,installed,device_verified,status,notes`

| 컬럼 | 값 | 의미 |
|---|---|---|
| `tier` | `androidworld_fdroid` (14) / `fdroid` (8) / `playstore` (30) | 출처 계층 |
| `auth_required` | `none` (26) / `account_optional` (11) / `account_required` (15) | `account_required` 는 러너가 기본 skip, `--include-auth` 로만 포함 |
| `installed` | `"true"` (48) / `"false"` (4) | **디바이스 실측 사실.** 리터럴 문자열이므로 **`bool(field)` 로 파싱 금지** — `bool("false")` 는 True 다 |
| `status` | `ok` (46) / `clone_accepted` (2) / `excluded` (4) | **카탈로그 큐레이션 상태.** `installed` 와 다른 축이다 (아래) |

현재 스냅샷: 총 **52행**, `installed=true` **48**, 수집 대상(`collectable`) **48**, `package_id == "PENDING"` 인 행은 **0개**다
(카탈로그는 완전히 해소됐다). `clone_accepted` **2행**은 `com.emijotify.feesound` (Newpipe 클론) 과
`com.yummely.app` 로, 정식 배포본 대신 기기에 실제로 깔려 있는 클론을 **의도적으로 채택**한 것이다 —
수집은 결국 그 기기에 있는 것을 몰아야 하기 때문이다.

#### `installed` 와 `status` 는 서로 독립적인 컬럼이다

같은 질문에 대한 두 답이 아니라 **다른 두 질문**에 대한 답이고, 관리 주체도 다르다.

| 컬럼 | 질문 | 주체 |
|---|---|---|
| `installed` | 지금 이 기기에 APK 가 있는가? | **디바이스**. M3 의 `sync-installed` 가 `adb pm list packages` 로 갱신한다 |
| `status` | 이 행에 대해 큐레이션이 내린 판정은? | **사람**. 카탈로그 확정 시 한 번 손으로 기록했다 |

그래서 둘이 어긋나는 것이 **정상**이다. `status=ok` 이면서 `installed=false` 인 행이 다수 있고
(행 자체는 멀쩡하지만 이 기기에 지금 안 깔려 있을 뿐), `--status excluded` 는 4행,
`--installed false` 는 18행을 돌려준다 — **다른 질의다.**

> **M3 구현자 주의**: `sync-installed` 는 `installed` 컬럼**만** 쓴다. `installed` 에서 `status` 를
> 유도하면 첫 동기화에서 큐레이션 기록이 통째로, 그것도 **조용히** 날아간다 (모든 행이 여전히
> 그럴듯해 보이기 때문에 눈치채기 어렵다). `tests/test_catalog.py` 가 이 독립성을 고정해 둔다.

## CLI

```
atlas-collect [--config PATH] <command>
```

| 서브커맨드 | 상태 | 설명 |
|---|---|---|
| `catalog` | **구현됨** | `catalog/apps.csv` 를 필터링해 나열하거나 요약한다 |
| `sync-installed` | 미구현 (M3) | `adb pm list packages` 로 `installed` 컬럼 갱신 |
| `run` | 미구현 (M4) | host-pull 수집 루프 |
| `export` | 미구현 (M5) | 수집된 triple → Stage-1 jsonl |


### 수집 제외 4행 (`status=excluded`)

사용자가 **수집하지 않기로 결정**한 행이다. 삭제하지 않고 남기는 이유는 `notes` 에 적힌
사유를 보존하기 위해서다 — 지우면 다음 카탈로그 작업에서 같은 앱을 "친절하게" 다시
추가하고 같은 막다른 길을 재발견한다. `is_collectable` 이 False 라 M4 러너가 절대 구동하지 않는다.

| 앱 | package_id | 제외 사유 |
|---|---|---|
| Simple Contacts Pro | `com.simplemobiletools.contacts.pro` | SimpleMobileTools 인수 후 F-Droid 인덱스에서 제거. AndroidWorld pin 세트에도 없어 획득 경로 없음 |
| Metro | `io.github.muntashirakon.Music` | F-Droid 에 존재(versionCode 10603)하나 `/repo` 바이너리 경로가 TLS 검증 실패 |
| GnuCash for Android | `org.gnucash.android` | F-Droid 인덱스에 없음 (2026-07-14 `MISSING.json` 원장으로도 확인) |
| TickTick | `com.ticktick.task` | split APK — base 단독 설치가 `INSTALL_FAILED_MISSING_SPLIT` 로 실패. `install-multiple` 용 split 세트 필요 |

### `catalog` (동작함)

```bash
uv run atlas-collect catalog --stats                      # 요약
uv run atlas-collect catalog --tier fdroid                # tier 필터
uv run atlas-collect catalog --installed true             # 설치 여부 (값 있는 플래그: true|false)
uv run atlas-collect catalog --auth account_required      # auth_required 정확 일치
uv run atlas-collect catalog --status excluded              # ok | clone_accepted | excluded | not_installed
uv run atlas-collect catalog --exclude-auth               # account_required 제외 (러너 기본 태도)
```

`--stats` 출력 예:

```
catalog: 52 rows total, 52 selected
  installed      : 48 (not installed: 4)
  device_verified: 51
  collectable    : 48  (installed AND package resolved AND not excluded)
  excluded       : 4  (status == excluded; never collected)
  pending        : 0  (package_id == PENDING)
  tier           : androidworld_fdroid=14, fdroid=8, playstore=30
  auth_required  : account_optional=11, account_required=15, none=26
  status         : clone_accepted=2, excluded=4, ok=46
  category       : Browser=2, Communication=8, Finance=2, Food=4, Health=5, Media=14, Navigation=3, Productivity=10, System=1, Utility=3
```

### 미구현 서브커맨드

플래그는 이미 등록돼 있어 `--help` 로 표면을 볼 수 있다. 호출하면 다음처럼 실패한다:

```
NotImplementedError: `atlas-collect run` is not implemented yet — it lands in milestone 4
(collection loop). M1 (scaffold/catalog/config) and M2 (xml encoding) are done, but M2 is a
library with no CLI surface, so `atlas-collect catalog` is the one working subcommand.
See ARCHITECTURE.md for the milestone plan.
```

`export` 에는 서로 **독립적인 두 개의 eval split knob** 이 이미 등록돼 있다:

- `--ood-apps <frac>` — train 에서 통째로 제외할 **앱**의 비율 (OOD eval)
- `--id-ratio <frac>` — train 에 포함된(seen) 앱들의 **triple** 중 eval 로 뗄 비율 (ID eval)

## 설정

해석 순서 (뒤가 이김): **builtin defaults** (`src/atlas_collector/config.py`) → **`config/run.yaml`** →
**`AC_*` 환경변수** → **CLI 플래그**.

`run.yaml` 이 없어도 builtin defaults 로 동작한다 — `--help` 가 환경에 의존하지 않도록 한 의도적 선택이다.
환경변수 이름은 기계적이다: `AC_{SECTION}_{KEY}` 대문자 (`AC_DEVICE_SERIAL`, `AC_COLLECTION_MAX_STEPS`,
`AC_SCREEN_MATCHING_ELEMENT_DIFF_MAX` …). `export.target_size` 만 비-스칼라라 `"840x1876"` 형식을 받는다.

### `--config` 는 어떤 서브커맨드보다 먼저 해석된다

`main()` 이 dispatch **전에** 설정을 해석하고, 결과 `RunConfig` 를 `args.run_config` 로 실어 보낸다
(`catalog` 포함 — M3-M5 커맨드가 파일을 다시 읽지 않아도 되게).

| 상황 | 결과 |
|---|---|
| `--config` 없음, `config/run.yaml` 있음 | 그 파일을 읽는다 |
| `--config` 없음, `config/run.yaml` 없음 | builtin defaults, **조용히** (경고 없음) |
| `--config PATH`, 파일 없음 / YAML 깨짐 | **stderr 메시지 + exit 2** |
| 섹션·키 오타, 값 범위 초과 | **stderr 메시지 + exit 2** |

```console
$ uv run atlas-collect --config /nonexistent/nope.yaml catalog --stats
atlas-collect: Config file not found: /nonexistent/nope.yaml
  --config names the file explicitly, so a missing one is a typo, not a default.
$ echo $?
2
```

오타 난 경로를 조용히 무시하는 플래그는 없느니만 못하다. 같은 이유로 **오타 난 최상위 섹션도 거부한다** —
`collectoin: {max_steps: 7}` 은 예전엔 그대로 로드되면서 `max_steps` 를 1500 으로 남겼다.
잘못 설정된 실행이 **성공한 것처럼 보이는 것**이 이 검증이 막으려는 실패다.
`device.width` / `device.height` 는 좌표 변환의 소스 프레임이라 양의 정수만 받는다
(`0` 은 파서 깊은 곳에서 `ZeroDivisionError` 로 터졌고, `-5` 는 **아무 에러 없이** 음수 스케일 박스를 만들었다).

주요 키는 [`config/run.yaml`](./config/run.yaml) 에 주석과 함께 있다. host-pull 고유 키:

- `collection.stabilize_poll_ms` / `collection.stabilize_max_wait_sec` — 디바이스가 "화면 바뀜" 신호를
  주지 않으므로, 호스트가 `uiautomator dump` 를 반복해 **연속 두 dump 가 같아질 때까지** 폴링한다.

### 세션 예산

기본은 **시간 예산 2시간/앱**이다 (`budget_mode: time`, `max_duration: "2h"`).

```yaml
collection:
  budget_mode: time      # time | steps
  max_duration: "2h"     # "2h" / "120m" / "7200s" / 숫자(초)
  max_steps: 1500        # budget_mode=steps 일 때만 사용
```

step 이 아니라 시간으로 잡는 이유는 step 단가가 앱마다 크게 흔들리기 때문이다. Pixel 6 실측으로
`uiautomator dump` 2.33s + `screencap` 0.67s + `action_delay` 1.5s 이므로 2시간은 앱당 대략
1,000~1,500 step 에 해당하지만, 안정화가 오래 걸리는 화면이 많은 앱은 같은 step 수에 훨씬 더
오래 걸린다. 48개 앱 전체로는 시간 예산 쪽이 총 소요를 예측 가능하게 만든다.

`max_duration` 파싱 실패나 0 이하는 **에러**다. 형제 프로젝트 Monkey-Collector 의 파서는 같은
상황에서 경고만 남기고 2h 로 폴백하는데, 그러면 오타난 예산이 성공한 설정처럼 보인다 — 이 모듈이
오타난 YAML 섹션을 거부하는 것과 같은 이유로 여기서도 거부한다. 두 예산은 **활성 모드와 무관하게
둘 다 검증**되므로, 나중에 `budget_mode` 를 뒤집었을 때 비로소 깨지는 일이 없다.

CLI 로도 덮어쓸 수 있다: `--budget-mode` / `--max-duration 90m` / `--max-steps 500`.

## 저장소 레이아웃

```
Atlas-Collector/
├── pyproject.toml
├── .python-version              # 3.11
├── catalog/apps.csv             # 커밋 데이터 (52행)
├── config/run.yaml
├── src/atlas_collector/
│   ├── cli.py                   # atlas-collect entrypoint
│   ├── config.py                # run.yaml → dataclass (+ 검증; ConfigError)
│   ├── paths.py                 # data / runtime root 해석
│   ├── catalog.py               # apps.csv read/write/filter (stdlib only)
│   ├── adb.py                   # AdbClient (host-pull 의 유일한 디바이스 채널)
│   ├── xml/                     # M2 (DONE): XML 인코딩 · actionable element · 좌표 변환
│   └── llm/client.py            # OpenRouter — 입력 텍스트 생성 전용
├── tests/
├── README.md · ARCHITECTURE.md · AGENTS.md
└── (생성됨, gitignore)
    data/raw/{package}/          # 영속: 수집된 triple
    data/export/                 # 영속: Stage-1 jsonl + images (M5)
    runtime/apps/{package}/      # 휘발성: 세션 bookkeeping
    runtime/logs/                # 휘발성: 실행 로그
```

`data/` 와 `runtime/` 은 monorepo 루트 `.gitignore` 의 `**/data*/`, `**/runtime*/` 로 이미 제외된다.

## 개발 게이트

```bash
uv run pytest              # 현재 기준선: 160 passed (M1+M2 전체 스위트)
uv run ruff check src tests
uv run mypy src
```

더 자세한 작업 규칙 — 특히 **바꾸면 안 되는 것들** — 은 [`AGENTS.md`](./AGENTS.md) 를,
설계와 export contract 는 [`ARCHITECTURE.md`](./ARCHITECTURE.md) 를 본다.
