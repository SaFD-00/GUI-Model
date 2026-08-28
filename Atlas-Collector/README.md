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
  navigation, 그리고 **LLM-Explorer 의 page matching**(activity 로 스코프된 구조 해시 + content-free
  signature 대칭차). page 식별은 **항상 LLM-free** 다.
- **픽셀은 page 식별에 쓰지 않는다.** 스크린샷 비교의 용도는 화면 **안정화** 하나뿐이다.
- **LLM 은 단 하나에만 쓰인다 — 텍스트 입력 필드의 입력값 생성.** OpenRouter Chat Completions,
  기본 모델 `qwen/qwen3.8-flash`.
- **auth-gated 앱**: 카탈로그의 `auth_required` 컬럼이 `account_required` 인 앱은 러너가 **기본적으로 건너뛴다**.
  `--include-auth` 를 줬을 때만 포함한다.

### ⚠️ 구현 상태 (milestone)

| Milestone | 내용 | 상태 |
|---|---|---|
| M1 | scaffold + catalog + config + docs | **DONE** |
| M2 | `xml/` — XML 인코딩, actionable element 추출, 좌표 변환 | **DONE** (`src/atlas_collector/xml/`, 테스트 통과) |
| M3 | coverage-guided exploration + provision | 완료 |
| M4 | host-pull collection loop | 완료 |
| M5 | Stage-1 export | 완료 |

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
| `sync-installed` | **구현됨** | `adb pm list packages` 로 `installed` 컬럼 갱신 (그 컬럼만) |
| `provision` | **구현됨** | 수집에 필요한 APK 를 해석·설치하고 실패를 원장에 누적 |
| `run` | 완료 | host-pull 수집 루프 |
| `export` | 완료 | 수집된 triple → EXP08 Stage-1 jsonl |


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

### `sync-installed` (동작함)

기기의 `pm list packages` 를 읽어 `installed` 컬럼을 갱신한다. **그 컬럼만** 쓴다 —
`status` 는 사람의 큐레이션 판정이라 동기화가 절대 건드리지 않는다 (위의 "독립적인 두 컬럼" 참조).
`status=excluded` 행도 **건너뛰지 않는다**: 제외된 앱이 실제로 기기에 있으면 `installed=true` 가
사실이고, `is_collectable` 이 별도로 막으므로 수집되지는 않는다. 여기서 excluded 를 건너뛰는 것은
`installed` 에서 `status` 를 유도하는 것과 같은 종류의 오염이다.

```bash
uv run atlas-collect sync-installed --dry-run     # diff 만 출력, 아무것도 쓰지 않음
uv run atlas-collect sync-installed               # diff 출력 + catalog/apps.csv 갱신
uv run atlas-collect sync-installed --serial 19101FDF6004EH --catalog /tmp/copy.csv
```

실측 출력 (2026-08-28, Pixel 6 연결 상태):

```
device 19101FDF6004EH: 370 packages present
catalog: 52 rows — installed 48 -> 48
  0 rows flipped — the catalog already matches the device
  status column: untouched (curation verdict, not a device fact)
dry-run: catalog NOT written
```

> 쓰기는 `csv.writer` 왕복이며 커밋된 `catalog/apps.csv` 를 **바이트 단위로 동일하게** 재생산한다
> (2026-08-28 실측). `tests/test_provision.py` 가 이 성질을 `read_bytes()` 비교로 고정한다 —
> `read_text()` 는 개행을 정규화해서 CRLF→LF 재포맷을 통과시키므로 쓰면 안 된다.

### `provision` (동작함)

수집에 필요한 APK 를 **우선순위대로** 해석해 설치한다.

| 순위 | 소스 | 왜 이 순서인가 |
|---|---|---|
| 1 | `../Monkey-Collector/catalog/apks/{pkg}.apk` (129개 캐시) | 오프라인이고 ABI 확인이 끝났다. F-Droid 는 예고 없이 인덱스에서 앱을 내리므로 로컬 캐시가 유일한 항구적 사본이다 |
| 2 | `EpochDroid/benchmarks/apks/android_world/{pkg}_{versionCode}.apk` | **버전이 핀 고정**돼 있다. `androidworld_fdroid` tier 에서는 F-Droid 최신판보다 이쪽이 옳다 — AndroidWorld 태스크가 바로 이 빌드를 대상으로 작성됐다 |
| 3 | f-droid.org (`/api/v1/packages/<pkg>` → `/repo/<pkg>_<vc>.apk`) | 저장소 밖 사정으로 실패할 수 있는 유일한 소스. **인증서 검증은 어떤 경우에도 끄지 않는다** |

- `status=excluded` 행은 `--force` 로도 설치하지 않는다. 제외는 이 기기에 대한 판단이 아니라
  **앱에 대한 큐레이션 결정**이라 CLI 플래그가 뒤집을 수 있는 것이 아니다.
- 이미 설치된 행은 건너뛴다 (`--force` 로 재설치). "이미 설치됨"의 판단은 카탈로그 컬럼이 아니라
  **기기 조회 결과**로 한다 — 컬럼이 낡아 있으면 정말 없는 앱을 건너뛰게 된다.
- 설치 성공 여부는 `adb install` 의 종료 코드가 아니라 **`pm list packages` 재조회**로 판정한다.
  OsmAnd(335MB)는 호스트에서 타임아웃을 냈지만 기기에는 실제로 설치돼 있었다. 타임아웃은 1회 재시도한다.
- split APK (`INSTALL_FAILED_MISSING_SPLIT`, 예: TickTick) 는 재시도하지 않고 `adb install-multiple`
  이 필요하다고 즉시 보고한다 — 나머지 split 이 없는 이상 두 번째 시도도 똑같이 실패한다.

```bash
uv run atlas-collect provision --dry-run                       # 계획만 출력
uv run atlas-collect provision --only org.tasks net.osmand     # 범위 한정
uv run atlas-collect provision --force                         # 설치된 것도 재설치
uv run atlas-collect provision --no-download                   # 로컬 소스만 (네트워크 금지)
```

실측 출력 (2026-08-28, `--force --dry-run --no-download`):

```
  ?       com.typo: no such row in the catalog
  skip    com.simplemobiletools.contacts.pro  (excluded by curation (status=excluded))
  skip    com.ticktick.task  (excluded by curation (status=excluded))
2 package(s) to provision
  plan    org.tasks  <- monkey-cache:org.tasks.apk
  plan    com.simplemobiletools.calendar.pro  <- android-world:com.simplemobiletools.calendar.pro_238.apk
```

#### 실패 원장 `catalog/PROVISION_MISSING.json`

`package_id -> {source, reason, first_seen, last_seen}` 의 **누적** 기록이다. 뜻은 "지금 여기서
설치할 수 없다"이지 "마지막 시도가 실패했다"가 아니다. 그래서:

- `--only` 로 범위를 좁힌 실행은 **자기 범위 안의 항목만** 갱신하고 나머지는 손대지 않는다.
  아니면 첫 부분 실행이 누적 기록을 통째로 잘라 버린다.
- 범위 안에서 실제로 설치가 확인된 패키지는 항목이 **삭제**된다.
- `first_seen` 은 갱신에도 살아남는다 (얼마나 오래 못 구하고 있는지가 정보다).
- JSON 이 깨져 있으면 빈 원장으로 리셋하지 않고 **예외를 낸다**. 조용한 리셋은 이 원장이 막으려는
  바로 그 손실을 재현한다. 복구는 의도적으로 수동이다.

### export 산출물

`atlas-collect export` 는 `data/AtlasCollection/` 에 쓴다 — 학습 파이프라인이 형제 수집기의
코퍼스를 읽는 `data/MonkeyCollection/` 옆자리다.

```
data/AtlasCollection/
├── stage1_train.jsonl       # seen 앱, train
├── stage1_test_id.jsonl     # seen 앱의 미사용 화면 (ID eval)
├── stage1_test_ood.jsonl    # 통째로 홀드아웃한 앱 (OOD eval)
├── export_meta.json         # split 파라미터·드롭 사유별 집계
└── images/episode_{package}_step_{NNNN}.jpg
```

파일명은 `data/AndroidControl_EXP08/` 의 `stage1_train` / `stage1_test_*` 모양을 따른다.
ID/OOD 분할은 EXP08 이 만들지 못한 부분이다 — EXP08 의 meta 가 직접 적고 있다:
"원본에 앱 파티션 메타가 없어 앱 단위 분할을 재현할 수 없다".

**화면이 바뀌지 않은 triple 은 기본적으로 제외**한다 (`--keep-unchanged` 로 포함).
수집기는 사실로 기록하고 무엇을 학습에 쓸지는 export 가 정한다 — 수집 단계에서 거른 것은
복구할 수 없고 무엇이 빠졌는지 흔적도 남지 않기 때문이다.


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

- `collection.stabilize_*` — 디바이스가 "화면 바뀜" 신호를 주지 않으므로 호스트가 스스로 판정한다.
  **스크린샷 픽셀 비교**로 폴링하고, 화면이 멎은 뒤 `uiautomator dump` 를 딱 한 번 뜬다 (아래).

### 화면 안정화 (host-pull 의 핵심 비용)

Pixel 6 실측 (6회 중앙값):

| 연산 | 시간 |
|---|---:|
| `uiautomator dump /dev/tty` | 2.341s |
| `screencap -p` (1.7MB PNG) | 0.680s |
| `screencap` (raw RGBA, 10.4MB) | 0.803s |
| 호스트 디코드+축소+비교 | 0.022s |

스크린샷 폴링이 hierarchy 폴링보다 **3.44배 싸고**, 호스트 비교 비용은 전송의 3% 라 무시된다.
그래서 스크린샷으로 폴링하고 dump 는 마지막에 한 번만 뜬다. 폴 수 `k` 에 대해
`k×0.680 + 2.341` vs `k×2.341` 이고, 안정화 판정에 최소 두 프레임이 필요하므로 `k ≥ 2` — 항상 이긴다.

실기기 6개 전환으로 측정한 실제 절감:

| 전환 | polls | 신방식 | 구방식 |
|---|---:|---:|---:|
| launch markor | 3 | 3.42s | 7.02s |
| tap | 2 | 2.94s | 4.68s |
| back | 3 | 3.26s | 7.02s |
| launch settings | 4 | 3.54s | 9.36s |
| swipe up | 4 | 3.74s | 9.36s |
| back | 4 | 3.70s | 9.36s |
| **합계** | | **20.60s** | **46.82s** |

**step 당 7.80s → 3.43s, 56% 절감.** 2시간 예산 안에 들어가는 step 수가 그만큼 늘어난다.

`stabilize_pixel_threshold` 는 **0 이 아니다**. 실측상 실제 전환은 changed_frac 0.0000 까지 정확히
수렴하지만, 영상·스피너·깜빡이는 커서처럼 **영원히 픽셀이 같아지지 않는 화면**이 있다. 0 을 요구하면
그런 화면에서 매 step 마다 `stabilize_max_wait_sec` 를 통째로 태우는데, 데이터는 계속 나오므로
**조용하다** — 이 모듈이 가진 가장 비싼 실패 모드다. 임계값과 max_wait 백스톱이 그 방어다.
안 멎은 화면은 `settled=False` 로 마지막 프레임을 그대로 반환한다 (에러가 아니다).

설계 근거 전문은 [`src/atlas_collector/stabilize.py`](./src/atlas_collector/stabilize.py) 의 모듈 docstring 에 있다.

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
    data/AtlasCollection/        # 영속: Stage-1 jsonl + images (export 산출물)
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
