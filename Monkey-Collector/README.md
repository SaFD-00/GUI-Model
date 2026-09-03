# Monkey-Collector

**host-pull** 방식 Android GUI 데이터 수집기다. 호스트 Python 프로세스 하나가 ADB 로 디바이스를 몰고
`uiautomator dump` + `exec-out screencap` 으로 화면과 XML 을 **끌어온다**. **디바이스에서 도는 우리
코드는 없다** — Android 앱도, AccessibilityService 도, TCP 서버도 없다. (이전에는 device-push
구조였지만 2026-08-29 에 host-pull 로 전면 재구축됐다.)

**LLM-Explorer 방식**(semantic state/element 추상화 + 미탐색 우선 + 최단경로 네비게이션)으로
탐색하며 **AIG**(`graph.json`, App Interaction Graph)를 산출물로 남긴다. 자세한 설계는
[ARCHITECTURE.md §1](./ARCHITECTURE.md#1-시스템-개요).

설계 전체와 export 계약은 [ARCHITECTURE.md](./ARCHITECTURE.md), 작업 규칙과 하드 제약은
[AGENTS.md](./AGENTS.md) 가 정본이다. 이 문서는 무엇/설치/카탈로그/CLI/저장 레이아웃만 다룬다.

## ⚠️ 실계정 기기 · action guard 없음

**수집 대상은 실제 계정으로 로그인된 개인 디바이스다.** WhatsApp · Telegram · Signal · Slack ·
Discord 가 로그인돼 있고, 이 수집기에는 **파괴적 action 을 막는 가드가 하나도 없다.** 탐색기는
화면에 보이는 것을 그대로 누른다 — 전송 버튼도 그냥 버튼이다. 따라서 자율 탐색이 **실제 메시지를
보내거나 실제 게시물을 올릴 수 있다.**

이건 미구현 항목이 아니라 **사용자가 위험을 인지한 뒤 승인한 의도적 결정**이다(자세한 배경은
[AGENTS.md §0.5](./AGENTS.md#05--실계정-기기--action-guard-없음-먼저-읽어라)). 가드를 추가하는
설계 변경은 임의로 하지 않는다. **다른 기기에서 이 수집기를 돌릴 때는 실행 전에 그 기기의 로그인
계정을 반드시 확인하라.**

## 1. 설치

```bash
# 1) uv 설치 (한 번만)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2) 의존성 설치 (.python-version 의 버전을 uv 가 자동 설치)
cd /path/to/Monkey-Collector
uv sync --extra dev        # pytest, ruff, mypy 포함

# 3) 활성화 (또는 명령마다 'uv run <cmd>')
source .venv/bin/activate
```

Android 앱은 없으므로 빌드 절차도 없다 — gradle · AGP · JDK 설치가 전혀 필요 없다.

추가 전제: Python 3.10+, ADB 가 PATH 에 있거나 `ANDROID_HOME` 설정.

선택 사항 — LLM 사용 (입력 텍스트 생성 + semantic 라벨링/그룹핑):

```bash
cp .env.example .env       # OPENROUTER_API_KEY 채우기
```

`OPENROUTER_API_KEY` 가 **없어도 수집은 그대로 돈다** — 열화 경로다. 라벨은 구조 해시로, 입력
텍스트는 hardcoded 랜덤 값으로, same-function 그룹핑은 빈 집합으로 떨어질 뿐 탐색 자체는 멈추지
않는다. 모델·설정 전체는 [`config/run.yaml`](./config/run.yaml)(`llm.model` 등)이 정본이고,
`MC_*` 환경변수와 CLI 플래그로 단계적으로 덮어쓸 수 있다 — 전체 해석 순서와 키 목록은
[ARCHITECTURE.md §10](./ARCHITECTURE.md#10-설정).

> **함정**: `.venv` 의 editable 설치가 리포 이전 경로를 가리키고 있으면
> `ModuleNotFoundError: No module named 'monkey_collector'` 가 난다. 코드 문제가 아니라 venv
> 문제이고, `uv sync --extra dev` 를 다시 돌리면 고쳐진다.

## 2. 디바이스

| serial | 정체 | 취급 |
|---|---|---|
| `19101FDF6004EH` | Pixel 6 (`oriole`), Android 16 / SDK 36, `wm size` 1080x2400 | **수집 타깃** |
| `emulator-5556` | 같은 머신에서 다른 작업이 점유 중 | **절대 건드리지 않는다** |

두 디바이스가 동시에 붙어 있어서 serial 자동탐지는 모호하거나 틀린 쪽을 고른다. 그래서
`config/run.yaml` 의 `device.serial` 이 실기기로 고정돼 있다 — `null` 로 되돌리지 않는다.

`monkey-collect run` 은 기본적으로 수집 전 기기 전제(화면 stayon, GMS OTA 업데이트 모달 억제)를
자동으로 건다. `--no-prepare-device` 로 끌 수 있지만, 이 두 설정은 잠긴 화면이나 업데이트 모달이
전체 dump 를 엉뚱한 패키지로 만드는 것을 막아 준다 — 상세 배경은
[AGENTS.md §1](./AGENTS.md#1-디바이스--어느-것을-몰고-어느-것을-건드리면-안-되는가).

## 3. 카탈로그

`catalog/apps.csv` — **52행, 커밋 데이터**. 실기기 Pixel 6 에 대고 해소한 것이라 재생성하지 않는다.
실제 숫자는 항상 아래로 직접 확인한다:

```bash
./.venv/bin/python -m monkey_collector.cli catalog --stats
```

```
catalog: 52 rows total, 52 selected
  installed      : 48 (not installed: 4)
  device_verified: 51
  collectable    : 48  (installed AND package resolved AND not excluded)
  excluded       : 4  (status == excluded; never collected)
  ...
  status         : clone_accepted=2, excluded=4, ok=46
```

- `installed`(디바이스 실측: 이 기기에 APK 가 있는가)와 `status`(사람이 매긴 큐레이션 판정)는
  **독립 컬럼**이다. 문자열 리터럴 `"true"`/`"false"` 이므로 `bool()` 로 파싱하면 안 된다
  (`bool("false")` 는 `True`).
- **`status=excluded` 4행**은 수집하지 않지만(`is_collectable=False`) 삭제하지 않는다 — 제외 사유가
  다음에 같은 앱을 다시 시도해 같은 막다른 길을 재발견하는 것을 막아 준다. `apps.csv` 의 `notes`
  실측:
  - `com.simplemobiletools.contacts.pro` — F-Droid 인덱스에서 제거됨, AndroidWorld pin 에도 없음
  - `io.github.muntashirakon.Music` — F-Droid 에는 있으나 이 환경에서 바이너리 다운로드가 TLS
    검증 실패
  - `org.gnucash.android` — F-Droid 인덱스에 없음
  - `com.ticktick.task` — split APK, base.apk 단독 설치가 `INSTALL_FAILED_MISSING_SPLIT`
- **`status=clone_accepted` 2행**은 기기에 실제로 깔려 있는 클론을 의도적으로 채택한 것이다 — 잘못된
  package id 가 아니다.
  - `com.emijotify.feesound` — 진짜 F-Droid NewPipe(`org.schabi.newpipe`)가 아닌 "Newpipe" 라는
    이름의 클론
  - `com.yummely.app` — Yummly(`com.yummly.android`)와는 다른 "Yummely" 앱
- `auth_required=account_required` 15행은 `run` 이 **기본적으로 스킵**하고 `--include-auth` 로만
  포함한다.

## 4. 빠른 시작

```bash
# 1) 카탈로그가 요구하는 APK 를 설치 (캐시 → AndroidWorld pinned → f-droid.org 순으로 해소)
monkey-collect provision

# 2) 수집 (기본: 시간 예산, collectable 앱 전부, 앱당 2h)
monkey-collect run --apps all

# 3) 수집된 triple 을 사람이 보고 걸러낸다 (브라우저 UI, raw/ 는 읽기만)
monkey-collect review

# 4) 수집한 triple 을 EXP08 Stage-1 jsonl 로 export (review 판정이 기본 적용)
monkey-collect export
```

## 5. CLI

서브커맨드는 7개 — `catalog` · `sync-installed` · `provision` · `run` · `review` · `export` ·
`reset`.
전체 옵션은 `--help` 가 정본이다:

```bash
./.venv/bin/python -m monkey_collector.cli --help
./.venv/bin/python -m monkey_collector.cli <subcommand> --help
```

### `catalog`

`catalog/apps.csv` 를 읽어 행을 나열하거나 요약한다. 기기를 건드리지 않는다.

```bash
monkey-collect catalog --stats
monkey-collect catalog --status excluded
monkey-collect catalog --tier fdroid --installed true
monkey-collect catalog --exclude-auth
```

옵션: `--catalog`(기본 `catalog/apps.csv`) · `--tier {androidworld_fdroid,fdroid,playstore}` ·
`--installed {true,false}` · `--auth {none,account_optional,account_required}` ·
`--status {clone_accepted,excluded,not_installed,ok}` · `--exclude-auth`(account_required 행 제외)
· `--stats`.

### `sync-installed`

`adb pm list packages` 결과로 `apps.csv` 의 `installed` 컬럼**만** 갱신한다 (`status` 는 사람의
큐레이션 판정이라 절대 건드리지 않는다).

```bash
monkey-collect sync-installed
monkey-collect sync-installed --dry-run
```

옵션: `--catalog`(기본 `catalog/apps.csv`) · `--serial`(기본 `config` 의 `device.serial`) ·
`--dry-run`(diff 만 보고, 쓰지 않음).

### `provision`

대상 APK 를 **자체 캐시 → AndroidWorld 버전-고정 APK → f-droid.org** 순서로 해소해 설치하고,
디바이스를 재조회해 검증한다. 실패는 `catalog/PROVISION_MISSING.json`(기본 ledger)에 누적된다.

```bash
monkey-collect provision --dry-run
monkey-collect provision --only com.google.android.deskclock
monkey-collect provision --force --no-download
```

옵션: `--catalog` · `--serial` · `--only PKG [PKG ...]`(지정한 package 만) ·
`--force`(이미 설치된 것도 재설치, `status=excluded` 는 절대 덮지 않음) ·
`--dry-run`(해소·보고만, 설치 안 함) · `--no-download`(로컬 소스만, f-droid.org 접속 안 함) ·
`--apk-cache` · `--android-world`(AndroidWorld pinned APK 디렉터리 override) ·
`--ledger`(기본 `catalog/PROVISION_MISSING.json`).

### `run`

LLM-Explorer 정책으로 collectable 앱을 순차 수집해 `observations/` · `triples.jsonl` ·
`graph.json`(AIG) 를 하나의 collection root 아래 쓴다. `auth_required=account_required` 앱은
`--include-auth` 없이는 스킵된다.

> ⚠️ 실계정 · action guard 없음 — 위 경고 참조.

```bash
monkey-collect run --apps all                                       # 기본: 시간 예산, 앱당 2h
monkey-collect run --apps all --budget-mode time --max-duration 2h
monkey-collect run --apps net.gsantner.markor --budget-mode steps --max-steps 300
monkey-collect run --apps all --input-mode random                   # 입력 텍스트 API 호출 없이
monkey-collect run --apps all --force                                # 완료된 앱도 재수집
monkey-collect run --apps all --no-prepare-device                    # stayon/OTA 억제를 건너뜀
```

옵션: `--apps`(`all` 또는 package id 목록) · `--serial` · `--budget-mode {steps,time}`(기본
`time`, 켜지지 않은 쪽 예산은 종료에 관여하지 않음) · `--max-duration DURATION`(`2h`/`120m`/`7200s`)
· `--max-steps MAX_STEPS` · `--seed` · `--input-mode {api,random}` · `--root`(기본
`data/MonkeyCollection`) · `--force`(완료 앱도 재수집, 번호를 새로 시작) · `--include-auth` ·
`--no-prepare-device`(§2 의 기기 전제 적용을 건너뜀, 기본은 적용).

### `review`

`{root}/raw` 위에 로컬 웹 UI 를 띄워 사람이 before/action/after 를 앱별로 보고 제외한다.
**`raw/` 와 `runtime/` 을 읽기만 하므로 다른 세션이 수집 중이어도 안전하다** — 판정은
`{root}/review/by-<reviewer>.jsonl` 에만 append 되고, `export` 가 기본으로 적용한다.
**제외는 삭제가 아니다**: 수집된 바이트는 그대로 남고 export 레코드에서만 빠진다.

```bash
monkey-collect review                                  # http://127.0.0.1:8700
monkey-collect review --port 8700 --reviewer bsw
monkey-collect review --root data/MonkeyCollection --no-browser
```

옵션: `--root`(기본 `data/MonkeyCollection`) · `--port`(기본 8700) ·
`--host`(기본 `127.0.0.1` — 코퍼스는 실계정 기기의 스크린샷이므로 다른 값은 그걸 네트워크에
공개한다) · `--reviewer`(판정에 찍히는 이름이자 파일명, 기본 `$USER`) · `--no-browser`.

화면은 셋이다.

1. **앱 목록** — 앱마다 export 대상 수 · 검토/제외 진행률 · 수집 중 배지, 그리고
   **`서로 다른 화면 N개뿐`** 경고. 실측(2026-08-30 sweep)에서 `code.name.monkey.retromusic`
   은 export 대상 369건이 전부 **같은 레코드 하나의 반복**이었다 — 권한 온보딩 화면에서
   같은 탭을 369번 한 것으로, 필터링이 아니라 재수집이 답인 경우다.
2. **스텝 목록** — before/after 썸네일 + 액션 표시(tap 은 점, swipe 는 화살표,
   `press_back`/`press_home`/`open_app` 은 좌표가 없으므로 칩만). 동일한 export 레코드로
   묶이는 스텝은 `×N` 배지 하나로 접힌다.
3. **스텝 상세** — 좌/우 나란히 비교, `Space` 를 누르고 있으면 같은 자리에서 before↔after 가
   깜빡인다. XML 은 학습에 실제로 들어가는 html-like 가 기본이고 raw 로 토글, before→after
   차이는 색으로 표시된다.

단축키: `J`/`K` 이동 · `X` 제외 · `1`~`7` 사유 지정 후 제외 · `O` 유지 · `U` 판정 취소 ·
`Space` 깜빡 · `R` raw 토글 · `D` diff 토글 · `G` 목록 · `?` 도움말.

**벌크 룰**은 미리보기로 건수를 먼저 보여주고 누를 때만 적용하며, 룰 단위로 되돌릴 수 있다.
export 가 이미 버리는 것(`changed=false`, 파일 결손, 다른 앱 화면, 파싱 실패)에는 룰을 걸지
않는다 — 그런 룰은 숫자만 보여주고 결과를 바꾸지 못한다. 실제로 export 가 **살리는** 것에만
작용한다:

| 룰 | 뜻 | 실측 적중 (2026-08-31, 23앱 11,363건 중) |
|---|---|---|
| `duplicate` | export 형태가 같은 `(before, action, after)` 그룹에서 앞 N개만 남김 | 2,968 (26%) |
| `same_html` | 학습 타깃이 입력 XML 과 바이트 단위로 동일 | 59 |
| `tiny_dump` | 노드 수가 임계값 미만 (로딩 스켈레톤) | 347 |
| `coord_oof` | 액션 좌표가 프레임 밖 (export 는 세기만 하고 내보낸다) | 1 |

`coord_oof` 가 잡은 1건은 `com.google.android.apps.nbu.files` step 38 이다 — **가로 화면
(2400×1080)에서 `y1=1800` 인 swipe**, 즉 세로 기준으로 만들어진 좌표다. export 는 이걸
`coords_out_of_frame` 으로 세기만 하고 내보낸다.

### `export`

`{root}/raw` 의 `triples.jsonl` 을 읽어 `stage1_train.jsonl` · `stage1_test_id.jsonl` ·
`stage1_test_ood.jsonl` · `images/` · `export_meta.json` 을 root 에 쓴다. 계약은
[ARCHITECTURE.md §8](./ARCHITECTURE.md#8-export--exp08-stage-1-계약)이고 디바이스는 건드리지 않는다.

```bash
monkey-collect export
monkey-collect export --ood-apps 0.3 --id-ratio 0.1
monkey-collect export --keep-unchanged
```

`{root}/review` 의 판정은 **기본으로 적용된다**(필터를 잊는 사고를 막기 위해). 판정이 그
스텝의 화면과 더 이상 맞지 않으면(=`reset --raw` 후 재수집) 적용하지 않고 `review_stale` 로
세어 경고한다 — `--strict-review` 면 그때 exit 2.

옵션: `--root`(기본 `data/MonkeyCollection`) · `--seed`(분할 시드) ·
`--keep-unchanged`(`changed=false` triple 도 포함, 기본 제외) ·
`--ignore-review`(사람 판정을 무시하고 전부 export — 비교용 원본을 만들 때) ·
`--strict-review`(stale 판정이 있으면 exit 2) ·
`--ood-apps FRACTION`(앱 단위 홀드아웃 비율) · `--id-ratio FRACTION`(seen 앱마다 독립적으로 뽑는
ID eval 비율, `--ood-apps` 와 완전히 독립). **좌표는 export 가 리스케일한다** — `data-bbox` 와
action 좌표가 세션이 기록한 기기 해상도에서 유도한 동일 프레임(840x1876)에 놓인다, 상세는
[ARCHITECTURE.md §8.2](./ARCHITECTURE.md#82-좌표--xml-과-action-둘-다-840x1876).

### `reset`

collection root 의 산출물을 스코프 단위로 삭제한다 — root 하나 전체를 다루며, 앱 단위 스코프는
없다.

```bash
monkey-collect reset --all --dry-run
monkey-collect reset --raw --runtime
monkey-collect reset --export
```

옵션: `--root`(기본 `data/MonkeyCollection`) · `--raw`(`raw/` = 수집 코퍼스) ·
`--runtime`(`runtime/` = 세션 상태) · `--export`(Stage-1 jsonl + `images/`) ·
`--all`(root 아래 전부) · `--dry-run`(무엇이 지워질지만 나열).

`review/` 는 **`--all` 로도 지워지지 않는다** — `run.log` 와 같은 이유다. 데이터는 다시 수집할
수 있지만 사람이 내린 판단은 다시 만들 수 없다. 대신 재수집 후에는 옛 판정이 남으므로
`export` 가 내용 해시로 stale 을 잡아 준다.

## 6. 저장 레이아웃

```
data/MonkeyCollection/                 기본 root (--root)
├── raw/{package}/                     내구성 — 코퍼스가 여기 있다
│   ├── observations/{index:04d}/
│   │   ├── screenshot.png
│   │   └── raw.xml
│   ├── triples.jsonl
│   ├── graph.json                     AIG
│   └── metadata.json
├── review/                            내구성 — 사람이 내린 판정 (reset 이 지우지 않는다)
│   ├── by-{reviewer}.jsonl            append-only 판정 로그, 리뷰어당 하나
│   └── cache/                         썸네일·분석 캐시 (지워도 무해)
├── runtime/
│   ├── apps/{package}/
│   │   ├── activity_coverage.csv
│   │   └── cost.csv
│   └── logs/
├── run.log
├── stage1_train.jsonl                 export 산출물
├── stage1_test_id.jsonl
├── stage1_test_ood.jsonl
├── images/
└── export_meta.json
```

한 실행의 산출물은 이 root 하나 아래에 있다. triple/AIG/export 스키마 상세는
[ARCHITECTURE.md §6~§8](./ARCHITECTURE.md#6-aig--app-interaction-graph).

## 7. 개발 게이트

```bash
./.venv/bin/python -m pytest tests    # 현재 기준선 798 passed
./.venv/bin/python -m ruff check src tests
./.venv/bin/python -m mypy src
```

venv 밖 bare `python` 은 쓰지 않는다. 기준선이 줄면 회귀로 본다 — 갱신 이력은
[AGENTS.md §4](./AGENTS.md#4-게이트-코드-변경-시).

## 8. 프로젝트 구조

```
Monkey-Collector/
├── README.md, ARCHITECTURE.md, AGENTS.md
├── pyproject.toml, .env.example
├── config/run.yaml                   builtin defaults → 이 파일 → MC_* env → CLI 플래그
│
├── src/monkey_collector/
│   ├── cli.py                        서브커맨드 7개
│   ├── config.py                     설정 해석
│   ├── adb.py                        유일한 디바이스 채널 (dump/screencap/input/재연결)
│   ├── paths.py                      collection root 경로 해소
│   ├── catalog.py, catalog_activities.py
│   ├── provision.py                  APK 해소·설치
│   ├── pagematch.py                  page 식별 (LLM-free)
│   ├── stabilize.py                  화면 정착 판정 (픽셀 비교)
│   ├── session.py                    observation/triple 디스크 레이아웃, resume
│   ├── semantic.py                   semantic 라벨 + same-function 그룹핑
│   ├── aig.py                        AIG (graph.json)
│   ├── explore.py                    LLM-Explorer 탐색 정책
│   ├── loop.py                       host-pull 수집 루프
│   ├── export.py, _exp08_prompt.py   EXP08 Stage-1 export
│   ├── review/                       사람 필터링 — store(판정) · corpus(읽기 전용 뷰) ·
│   │                                 rules(벌크) · server(로컬 UI) · static/
│   ├── text_input.py                 input_text 값 생성
│   ├── domain/                       actions(고정 계약) · activity_coverage · cost_tracker
│   ├── llm/                          OpenRouter Chat Completions 클라이언트
│   └── xml/                          uiautomator XML → EXP08 html-like XML
│
├── catalog/
│   ├── apps.csv                      52행 카탈로그 (커밋 데이터)
│   ├── activities.json               manifest activity coverage 분모
│   ├── extract_activities.py         apks/*.apk → activities.json
│   └── apks/                         *.apk (MISSING.json/MISSING.md = 누락 대장)
│
├── tests/
│   ├── unit/
│   └── fixtures/                     실기기 uiautomator 실덤프
│
└── (gitignored) data/MonkeyCollection/, *.egg-info/
```

구조 설명은 [`ARCHITECTURE.md`](./ARCHITECTURE.md), 작업 규칙과 하드 제약은
[`AGENTS.md`](./AGENTS.md) 를 본다.
