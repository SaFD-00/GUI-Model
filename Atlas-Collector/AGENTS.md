# AGENTS.md

`Atlas-Collector/` 하위 프로젝트에서 작업하는 에이전트를 위한 가이드다.

## 0. 지금 무엇이 있고 무엇이 없는가

| Milestone | 내용 | 상태 |
|---|---|---|
| M1 | scaffold + catalog + config + docs | **DONE** |
| M2 | `xml/` — XML 인코딩 / actionable element / 좌표 변환 | **DONE** — `src/atlas_collector/xml/`, 테스트 통과 |
| M3 | coverage-guided exploration | **미구현** |
| M4 | host-pull collection loop | **미구현** |
| M5 | Stage-1 export | **미구현** |

M2 는 **라이브러리**이고 자체 CLI 서브커맨드가 없다. 그래서 실제로 동작하는 서브커맨드는 여전히
**`atlas-collect catalog` 하나**다. `sync-installed` / `run` / `export` 는 `--help` 표면만 있고
호출하면 `NotImplementedError` 를 낸다 (`cli.py` 의 `UNIMPLEMENTED` 테이블).

새 milestone 을 구현하면 이 표, `cli.py:UNIMPLEMENTED`, README.md 의 CLI 표, ARCHITECTURE.md §7 을
**같이** 갱신한다. 넷 중 하나만 고치면 문서가 거짓말을 시작한다.

## 0.5. ⚠️ 실계정 기기 · action guard 없음 (먼저 읽어라)

**수집 대상은 실제 계정으로 로그인된 개인 디바이스다.** WhatsApp · Telegram · Signal · Slack ·
Discord 가 로그인돼 있고, 이 수집기에는 **파괴적 action 을 막는 가드가 하나도 없다.**
탐색기는 화면에 보이는 것을 누른다 — 전송 버튼도 그냥 버튼이다. 따라서 자율 탐색이
**실제 메시지를 보내거나 실제 게시물을 올릴 수 있다.**

이건 미구현 항목도 TODO 도 아니다. **사용자가 위험을 인지한 뒤 승인한 의도적 결정**으로
확정 기록돼 있다. 가드를 넣으면 도달 가능한 화면이 줄어 수집 가치가 떨어진다는 판단이었다.

에이전트에게 이것이 의미하는 바:

- **"안전을 위해 action guard 를 추가" 를 임의로 하지 마라.** 확정된 결정을 되돌리는 설계 변경이다.
  필요하다고 판단되면 구현하지 말고 **먼저 물어라**.
- 이 경고를 milestone 표나 "남은 작업" 목록에 옮기지 마라. 미구현이 아니라 **선택**이다.
- **다른 기기에서 돌릴 때는 경고로 취급하라**: 실행 전에 그 기기의 로그인 계정을 확인한다.
  더미 계정 / 로그아웃 / 전용 테스트 기기가 아니면 상대방에게 실제 메시지가 나간다.
  계정 앱을 통째로 빼려면 `--exclude-auth` (`account_required` 15개 제외).

## 1. 현재 코드 기준 요약

- 진입점은 [`src/atlas_collector/cli.py`](./src/atlas_collector/cli.py) 의 `atlas-collect` CLI 다.
- 아키텍처는 **host-pull**: 호스트 Python 프로세스 하나가 ADB 로 디바이스를 몰고 관측을 끌어온다.
  **Android 앱 / AccessibilityService / TCP 서버는 없다.** Monkey-Collector 의 device-push 설계를
  가져오려 하지 말 것 — 그 설계가 예산의 44-56% 를 signal timeout 으로 태운 것이 Atlas 가 존재하는 이유다.
- 디바이스 채널은 [`adb.py`](./src/atlas_collector/adb.py) 의 `AdbClient` 뿐이다.
- 설정 해석 순서: builtin defaults → `config/run.yaml` → `AC_*` env → CLI 플래그.
  `main()` 이 **dispatch 전에** 해석해 `args.run_config` 로 실어 보낸다. `--config` 가 없거나 깨진
  파일을 가리키면 **exit 2** 로 죽는다 (오타 난 경로를 조용히 무시하는 플래그는 없느니만 못하다).
  오타 난 **최상위 섹션**(`collectoin:`)도 키 오타와 똑같이 거부한다 — 잘못 설정된 실행이
  성공한 것처럼 보이는 게 최악이다. 새 설정 키를 추가하면 `_BUILTIN_DEFAULTS`, `config/run.yaml`,
  해당 dataclass, `_validate()` 를 **같이** 갱신한다.

## 2. 하드 제약 (HARD CONSTRAINTS)

이 셋은 협상 대상이 아니다. 어기는 변경은 리뷰에서 되돌린다.

### (a) export contract 는 ubuntu1.fclab 재검증 없이 바꾸지 않는다

ARCHITECTURE.md §4 의 사실들 — 최상위 키가 정확히 `{"messages","images"}` (`sample_id` 없음),
ShareGPT 3-turn `from`/`value`, byte-identical system 문자열, XML-FIRST human 템플릿,
bare XML gpt turn, 1-element `images`, `myset/images/episode_{EP}_step_{STEP}.jpg`
(EP unpadded / STEP 4자리 zero-pad), 840x1876 절대 픽셀 + 공백 구분 `data-bbox` —
는 **ubuntu1.fclab 의 실제 파일에서 역설계한 명세**다.

- 소비자: `Implicit-World-Modeling/scripts/build_exp08_data.py`
- 입력: `data/AndroidControl/EXP08_stage1_state.jsonl`

"더 깔끔해 보인다" 는 이유로 필드를 추가하거나(예: `sample_id` 복원), 패딩을 통일하거나,
`data-bbox` 를 콤마 구분으로 바꾸지 **않는다**. 바꿔야 한다고 판단되면 **먼저 ubuntu1.fclab 의 실제
파일에 대고 재검증**하고, 그 증거를 ARCHITECTURE.md §4 에 함께 남긴 뒤에 바꾼다.

### (b) page 식별은 LLM-free 다

page 정체성은 **LLM-Explorer 방법**으로만 결정한다: `state_str` 일치 → `structure_str` 일치 →
(같은 activity 안에서) content-free signature 대칭차 `≤ max_diff_elements`.

**픽셀을 page 식별에 넣지 마라.** 스크린샷 비교는 화면 안정화 전용이다. Monkey-Collector 의
Mobile3M BM25+pixel 매처를 다시 끌어오지 않는다 — 이 프로젝트는 의도적으로 그 계보가 아니다.

LLM 을 page 식별 경로에 넣지 않는다. 성능 문제가 아니라 **재현성 계약**이다 — 같은 화면이 API 응답의
변덕에 따라 다른 page 로 갈리면 page_graph 와 coverage 수치가 통째로 무의미해진다.
`OPENROUTER_API_KEY` 가 없어도 page 식별은 그대로 동작해야 한다.

### (c) LLM 은 입력 텍스트 생성 전용이다

`llm/client.py` 의 유일한 소비자는 텍스트 입력 필드의 입력값 생성이다.
모델은 `qwen/qwen3.8-flash`, OpenRouter **Chat Completions** (`chat.completions`, Responses API 아님).

action 선택, page 판정, 종료 판단, XML 요약 등 **다른 어떤 결정에도 LLM 을 끌어들이지 않는다.**
용도를 넓히고 싶다면 그건 설계 변경이므로 ARCHITECTURE.md 를 먼저 고치고 승인을 받는다.

## 3. 손대면 안 되는 데이터

- **`catalog/apps.csv` 는 커밋 데이터다.** 실기기(Pixel 6, serial `19101FDF6004EH`)에 대고 해소한
  **52행**이고, 생성 산출물이 아니다. 재생성하거나 "개선" 하지 않는다.
  현재 사실: tier `androidworld_fdroid=14 / fdroid=8 / playstore=30`, `installed=true` **48**,
  status `ok=46 / clone_accepted=2 / excluded=4`, `package_id == "PENDING"` **0개**.
- `installed` 는 리터럴 문자열 `"true"` / `"false"` 다. **`bool(field)` 로 파싱하지 마라** —
  `bool("false")` 는 `True` 라, 48이라는 숫자가 엉뚱한 이유로 맞아떨어진다.
- **`write_catalog()` 를 커밋된 `catalog/apps.csv` 에 돌리지 마라.** `csv.writer` 는 QUOTE_MINIMAL 이고,
  커밋본에는 콤마 없이 인용된 행들이 있어 read→write round-trip 이 **byte-identical 하지 않다**.
  writer 는 M3 의 `sync-installed` 를 위한 scaffold 이며, 그때 자체 diff 규칙과 함께 도입한다.
  테스트는 `tmp_path` 에서만 writer 를 쓴다.
- **안정화는 스크린샷 픽셀 비교다. `uiautomator dump` 로 폴링하지 마라.** dump 는 screencap 의
  3.44배(2.341s vs 0.680s, Pixel 6 실측)라, 폴링을 dump 로 돌리면 step 당 비용이 두 배 이상
  된다(실측 7.80s vs 3.43s). 안정화가 끝난 뒤 dump 를 **딱 한 번** 뜬다.
- **`stabilize_pixel_threshold` 를 0 으로 낮추지 마라.** 실제 전환이 0.0000 까지 수렴하는 걸 보고
  "그럼 0 이면 되겠네" 로 가기 쉬운데, 영상·스피너·깜빡이는 커서는 영원히 같아지지 않는다.
  0 이면 그런 화면에서 매 step 대기 예산을 통째로 태우면서도 데이터는 계속 나와 **조용히** 느려진다.
- **한 실행의 산출물은 하나의 root 아래 둔다** (`--root`, 기본 `data/AtlasCollection`).
  `raw/`·`runtime/`·export 를 다시 흩뜨리지 마라 — 어긋난 채 남은 `raw/` 는 resume 시
  이전 observation 번호를 이어받아 예전 triple 이 새 화면을 가리키게 만들고, 데이터만
  봐서는 발견되지 않는다. 새 수집 전에는 `atlas-collect reset --all` 로 비운다.
- **`status=excluded` 4행은 수집하지 않는다** (사용자 결정, 2026-08-28). `is_collectable` 이
  False 라 M4 러너가 구동하지 않는다. 삭제하지 말고 그대로 둔다 — `notes` 의 제외 사유가
  사라지면 다음 작업에서 같은 앱을 다시 추가하고 같은 막다른 길을 재발견한다.
  사유는 README 의 "수집 제외 4행" 표를 본다.
- **`clone_accepted` 2행(`com.emijotify.feesound` = Newpipe 클론, `com.yummely.app`)은 의도적 채택이다.**
  정식 배포본이 아니라 기기에 실제로 깔려 있는 클론을 수집하기로 사용자가 결정했다.
  "잘못된 package id" 로 보고 고치지 마라.
- `PENDING` sentinel 을 다는 행은 **더 이상 없다** (카탈로그는 완전히 해소됐다).
  `PENDING_PACKAGE` 상수와 `is_pending` 은 재유입을 막는 **가드로만** 남아 있다.

### `installed` 와 `status` 는 독립적인 컬럼이다 (M3 가 합치지 못하게)

| 컬럼 | 질문 | 주체 |
|---|---|---|
| `installed` | 지금 이 기기에 APK 가 있는가? | **디바이스 실측**. M3 의 `sync-installed` 가 갱신한다 |
| `status` | 이 행에 대한 큐레이션 판정은? | **사람이 손으로** 기록한 값 (`ok` / `clone_accepted` / `excluded` / `not_installed`) |

둘이 어긋나는 것이 **정상**이다: `status=ok` + `installed=false` 인 행이 다수이고
(행은 멀쩡한데 이 기기에 지금 없을 뿐), `--status not_installed` 는 1행, `--installed false` 는 18행이다.

**`sync-installed` 는 `installed` 컬럼만 쓴다.** `installed` 로부터 `status` 를 유도하면(또는 그 반대)
첫 동기화에서 큐레이션 기록이 **조용히** 사라진다 — 모든 행이 여전히 그럴듯해 보여서 눈치채기 어렵다.
`tests/test_catalog.py::test_installed_and_status_are_independent_columns` 가 이걸 고정해 둔다.

## 4. 게이트 (코드 변경 시)

```bash
uv run pytest                        # 현재 기준선: 160 passed (M1+M2 전체 스위트)
uv run ruff check src tests
uv run mypy src
```

기준선이 줄면 회귀로 본다. **테스트를 의도적으로 삭제한 변경은 예외**이고, 그때는 삭제 개수까지 세어
새 기준선을 여기에 갱신한다.

빠른 검증 포인트:

```bash
uv run atlas-collect --help
uv run atlas-collect catalog --stats
uv run atlas-collect catalog --status excluded
uv run atlas-collect --config /nonexistent/nope.yaml catalog --stats   # exit 2 여야 한다
uv run pytest tests/test_catalog.py tests/test_config.py tests/test_cli.py
```

`uv run pytest` 가 pytest 를 못 찾으면 `uv run --extra dev pytest` 를 쓴다 (bare `uv run` 이
default group 으로 재동기화하며 extra 를 밀어낼 수 있다).

## 5. 디바이스 작업 규칙 (M4 이후)

- 타깃: Pixel 6 (`oriole`), serial `19101FDF6004EH`, Android 16 / SDK 36, `wm size` 1080x2400, density 420.
- **USB 링크는 끊어진다.** 실측으로 세션 중 2회 끊겼고 `adb kill-server && adb start-server` 로 복구됐다.
  이 복구는 `AdbClient.reconnect()` 에 있다. 끊김을 예외 상황이 아니라 **정상 경로**로 다뤄라.
- `AdbClient` 생성자는 USB 를 건드리지 않는다(serial lazy 해소). 이 성질을 깨지 마라 —
  모듈 import 만으로 디바이스가 필요해지면 테스트가 디바이스에 묶인다.
- `uiautomator dump` 는 디바이스에 쓴 뒤 `exec-out cat` 으로 읽어 온다. `/dev/tty` 로 직접 덤프하면
  "UI hierarchy dumped to:" 배너가 XML 에 섞인다.
- `input text` 는 공백에서 잘린다. `escape_text_for_adb()` 의 `" " → "%s"` 치환은 **load-bearing** 이다.

## 6. 기기 전제 — "stuck outside the app" 을 코드 버그로 읽지 마라

파일럿 두 번이 `10 triples / 15 observations / could not stay in the app` 으로 죽었고, 둘 다
원인은 **화면에 앱이 없었던 것**이었다. 로그는 수집기 버그처럼 보이게 찍힌다.

- 화면이 꺼져 잠기면 dump 가 `com.android.systemui` 를 반환한다 → `svc power stayon true`.
- GMS "System update needed" 모달이 뜨면 dump 가 통째로 `com.google.android.gms` 다
  → `settings put global ota_disable_automatic_update 1`.
- **그 모달과 정상적인 Google 설정 화면(`OctarineActivity`)은 로그 줄이 같다.** 판별은 raw XML 뿐이다:
  `grep -rl "System update needed" data/AtlasCollection/raw/`.

"stuck outside the app" 이 연속으로 나오면 **코드를 고치기 전에** `dumpsys window | grep mCurrentFocus`
와 스크린샷을 먼저 본다. 자세한 표는 README 의 "수집 전 기기 전제" 절에 있다.

## 7. auth-gated 앱

`auth_required == "account_required"` 인 앱은 러너가 **기본적으로 건너뛴다**. `--include-auth` 를
줬을 때만 포함한다. 카탈로그 기준 현재 **15개**가 여기 해당한다.
이 기본값을 뒤집지 마라 — 로그인 벽에 갇힌 세션은 수집 예산만 태운다.

## 8. 문서 규칙

- 이 리포의 문서는 **한국어**다. 기술 용어와 식별자(`data-bbox`, `smart_resize`, package id 등)는 영어로 둔다.
- README.md = 무엇/설치/카탈로그/CLI/레이아웃, ARCHITECTURE.md = 설계 + export contract,
  AGENTS.md = 작업 규칙. 같은 내용을 세 곳에 복사하지 말고 링크한다.
- **미구현 상태를 숨기지 마라.** 세 문서 모두 **M3-M5** 가 미구현임을 명시해야 한다
  (M1·M2 는 DONE). 구현되지 않은 것을 구현된 것처럼 쓰는 문서가 이 프로젝트에서 가장 비싼 실수다.
  반대 방향도 마찬가지다 — 이미 구현된 M2 를 "비어 있음" 이라고 적어 두면 다음 사람이 다시 만든다.
- **실계정 / no-action-guard 경고(§0.5)는 README.md 와 AGENTS.md 양쪽에 눈에 띄게 유지한다.**
  경고이지 할 일이 아니다.
