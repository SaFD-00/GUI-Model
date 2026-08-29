# AGENTS.md

`Monkey-Collector/` 하위 프로젝트에서 작업하는 에이전트를 위한 가이드다.

## 0. 지금 이 프로젝트가 무엇인가 (2026-08-29 재구축 중)

**host-pull Android GUI 수집기.** 호스트 Python 프로세스 하나가 ADB 로 디바이스를 몰고
`uiautomator dump` + `exec-out screencap` 으로 관측을 **끌어온다**.

**Android 앱도, AccessibilityService 도, TCP 서버도 없다.** 예전에는 있었다 — 그 device-push 구조는
2026-08-29 에 통째로 걷어냈다. 되살리려 하지 마라: 앱이 접근성 이벤트를 볼 때만 프레임을 밀어주는
구조라, 이벤트 없이 정착한 화면은 signal window 를 통째로 기다렸고 **예산의 44~56% 가 오지 않을
신호를 기다리는 데 들어갔다.** `recovery.py` 의 poke·escalation 사다리는 전부 그걸 덧대던 것이었다.

형제 프로젝트 [`../Atlas-Collector/`](../Atlas-Collector)와 **같은 기기·같은 카탈로그·같은 export
계약**을 쓴다. 다른 것은 **탐색 정책 하나뿐**이고, 그게 이 프로젝트가 따로 존재하는 이유다:

| | Atlas-Collector | Monkey-Collector |
|---|---|---|
| 탐색 | coverage-guided, 완전 LLM-free | **LLM-Explorer 방식 — semantic state/element 추상화 + 미탐색 우선 + 최단경로 네비게이션** |
| 그래프 산출물 | 없음 (page_graph 내부용) | **AIG (`graph.json`)** |
| LLM 용도 | 입력 텍스트 생성만 (게다가 현재 미배선) | 라벨링 + same-function 그룹핑(탐색 가지치기) + 입력 텍스트 |

`Atlas-Collector/` 는 **읽기 전용 참조**다. 이 리포에서 작업하며 그쪽 파일을 수정하지 마라.

### 재구축 진행 상황

| 단계 | 내용 | 상태 |
|---|---|---|
| M1a | `pagematch` · `stabilize` · `session` 이식 | **DONE** |
| M1b | device-push · Mobile3M · 구 export · 구 파서 · Android 앱 철거 | **DONE** |
| M1c | host-pull `adb` · `paths` · `xml` 파서 · `catalog` · `provision` · `config` · `cli` | **DONE** |
| M2 | LLM 클라이언트 재타깃(`qwen3.8-flash`) + cost 배선 + 입력 텍스트 | **DONE**(라이브러리 + 팩토리; `run` 루프에서 `CostTracker`/`LLMClient`/`TextGenerator` 를 실제로 엮는 배선은 M4) |
| M3a | element 레이어(`explore.py`) + AIG(`aig.py`) + 네비게이션 — LLM 없음 | **DONE** |
| M3b | `semantic.py`(라벨 + same-function 그룹핑) + 탐색 정책(`Explorer`) | **미구현** |
| M4 | host-pull 수집 루프 + `monkey-collect run` | **미구현** |
| M5 | EXP08 Stage-1 export + `monkey-collect export` | **미구현** |

**미구현을 구현된 것처럼 쓰지 마라.** 이 표와 `cli.py` 의 실제 서브파서와 README 의 CLI 표를
**같이** 갱신한다. 셋 중 하나만 고치면 문서가 거짓말을 시작한다. 반대로 이미 구현된 것을
"비어 있음" 이라고 적어 두면 다음 사람이 다시 만든다.

## 0.5. ⚠️ 실계정 기기 · action guard 없음 (먼저 읽어라)

**수집 대상은 실제 계정으로 로그인된 개인 디바이스다.** WhatsApp · Telegram · Signal · Slack ·
Discord 가 로그인돼 있고, 이 수집기에는 **파괴적 action 을 막는 가드가 하나도 없다.**
탐색기는 화면에 보이는 것을 누른다 — 전송 버튼도 그냥 버튼이다. 따라서 자율 탐색이
**실제 메시지를 보내거나 실제 게시물을 올릴 수 있다.**

이건 미구현 항목도 TODO 도 아니다. **사용자가 위험을 인지한 뒤 승인한 의도적 결정**이다.
가드를 넣으면 도달 가능한 화면이 줄어 수집 가치가 떨어진다는 판단이었다.

- **"안전을 위해 action guard 를 추가" 를 임의로 하지 마라.** 확정된 결정을 되돌리는 설계 변경이다.
  필요하다고 판단되면 구현하지 말고 **먼저 물어라**.
- 이 경고를 마일스톤 표나 "남은 작업" 목록으로 옮기지 마라. 미구현이 아니라 **선택**이다.
- **다른 기기에서 돌릴 때는 경고로 취급하라**: 실행 전에 그 기기의 로그인 계정을 확인한다.

## 1. 디바이스 — 어느 것을 몰고, 어느 것을 건드리면 안 되는가

| serial | 정체 | 취급 |
|---|---|---|
| `19101FDF6004EH` | Pixel 6 (`oriole`), Android 16 / SDK 36, `wm size` 1080x2400, density 420 | **수집 타깃** |
| `emulator-5556` | 다른 작업이 점유 중 | **절대 건드리지 마라** |

둘 다 붙어 있어서 serial 자동탐지는 모호하거나 **틀린 쪽을 고른다.** 그래서
`config/run.yaml` 의 `device.serial` 기본값이 실기기로 못박혀 있다. null 로 되돌리지 마라.

- **USB 링크는 끊어진다.** 실측으로 세션 중 2회 끊겼고 `adb kill-server && adb start-server` 로
  복구됐다. 이 복구는 `AdbClient.reconnect()` 에 있다. 끊김을 예외가 아니라 **정상 경로**로 다뤄라.
- `AdbClient` 생성자는 USB 를 건드리지 않는다(serial lazy 해소). 이 성질을 깨지 마라 —
  모듈 import 만으로 디바이스가 필요해지면 테스트가 디바이스에 묶인다.
- `uiautomator dump` 는 디바이스에 쓴 뒤 `exec-out cat` 으로 읽어 온다. `/dev/tty` 로 직접 덤프하면
  "UI hierarchy dumped to:" 배너가 XML 에 섞인다.
- `input text` 는 공백에서 잘린다. `escape_text_for_adb()` 의 `" " → "%s"` 치환은 **load-bearing** 이다.

### 수집 전 기기 전제 — "stuck outside the app" 을 코드 버그로 읽지 마라

파일럿이 `10 triples / 15 observations / could not stay in the app` 으로 죽는 경우, 원인은
대개 **화면에 앱이 없었던 것**이다. 로그는 수집기 버그처럼 찍힌다.

1. **화면 꺼짐 + 잠금화면** → dump 가 `com.android.systemui` 를 반환한다. `screen_off_timeout` 이
   30분이라 유휴 30분이면 잠긴다. **`adb shell svc power stayon true`** 로 막는다.
   USB 를 뽑았다 꽂으면 풀리므로 재개할 때마다 다시 건다.
2. **GMS "System update needed" 모달** → dump 가 통째로 `com.google.android.gms` 다. 버튼이
   **"Download & install now" 하나뿐**이라, 가드 없는 탐색기가 1.35GB 다운로드+재부팅을 눌러버릴 수
   있다(§0.5 와 직접 충돌하는 유일한 지점). `settings put global ota_disable_automatic_update 1` 로 억제한다.
   그 모달과 정상적인 Google 설정 화면(`OctarineActivity`)은 **로그 줄이 같다.** 판별은 raw XML 뿐:
   `grep -rl "System update needed" data/MonkeyCollection/raw/`.

`cmd statusbar collapse` / `service call statusbar 2` / `KEYCODE_HOME` 은 **셋 다 안 먹는다** —
잠금 상태에서는 위로 스와이프해야 풀린다. "stuck outside the app" 이 연속으로 나오면 **코드를 고치기
전에** `dumpsys window | grep mCurrentFocus` 와 스크린샷을 먼저 본다.

## 2. 하드 제약 (HARD CONSTRAINTS)

협상 대상이 아니다. 어기는 변경은 리뷰에서 되돌린다.

### (a) action space 는 고정이다

`src/monkey_collector/domain/actions.py` 의 7종 — `tap` · `swipe` · `input_text` · `press_back` ·
`press_home` · `long_press` · `open_app`(record-only) — 은 사용자가 "기존과 동일하게 유지" 로
못박은 계약이다. **타입을 추가하지도 이름을 바꾸지도 마라.**

탐색기 내부 vocabulary 와 export wire format 은 **다른 층**이다. 내부는 위 7종을 쓰고,
export 가 EXP08 이름으로 번역한다:

| 내부 (`domain/actions.py`) | EXP08 wire |
|---|---|
| `tap` | `click` |
| `long_press` | `long_press` |
| `input_text` | `type` |
| `swipe` | `swipe` |
| `press_back` | `navigate_back` |
| `press_home` | `navigate_home` |
| `open_app` | `open` |

LLM-Explorer 정책이 이 7종에 없는 이벤트를 내면 **매핑하거나 버려라. 타입을 추가하지 마라.**

### (b) page 식별은 LLM-free 다

page 정체성은 `pagematch.py` 만 결정한다: `state_str` 일치 → `structure_str` 일치 →
(같은 activity 안에서) content-free signature 대칭차 `≤ max_diff_elements`.

**픽셀을 page 식별에 넣지 마라.** 스크린샷 비교는 화면 안정화 전용이다. Mobile3M BM25+pixel
매처는 2026-08-29 에 삭제했다 — `element_jaccard_min` / `page_pixel_diff_threshold` 같은 knob 을
다시 끌어오지 않는다.

**LLM 을 page 식별 경로에 넣지 않는다.** 성능이 아니라 **재현성 계약**이다 — 같은 화면이 API 응답의
변덕에 따라 다른 page 로 갈리면 AIG 와 coverage 수치가 통째로 무의미해진다. `OPENROUTER_API_KEY` 가
없어도 page 식별은 그대로 동작해야 한다.

> 참고: 레퍼런스(LLM-Explorer)의 `_classify_state` 에도 LLM 분기가 있지만 **dead code** 다
> (`input_policy3.py:559` 가 `state_id = None` 을 무조건 대입한 뒤 `if state_id is not None` 을
> 검사한다). 즉 레퍼런스를 충실히 이식해도 page 식별은 LLM-free 가 공짜로 따라온다.

### (c) LLM 이 관여하는 곳은 정확히 세 곳이다

모델은 `qwen/qwen3.8-flash`, OpenRouter **Chat Completions** (`chat.completions`, Responses API 아님).

1. semantic state/element **라벨 생성**
2. **same-function element 그룹핑** — 한 element 를 눌러보면 같은 그룹의 나머지가 미탐색 목록에서
   빠진다. **LLM 이 탐색에 개입하는 유일한 지점이고, 되돌릴 수 없게 가지친다.**
3. `input_text` action 의 **입력값 생성**

**"다음에 무엇을 누를지" 를 LLM 이 고르지 않는다.** 레퍼런스도 그러지 않는다 — `pick_target` /
`pick_navigate_target` / `navigate` 는 100% 규칙 기반(미탐색 랜덤 + networkx 최단경로)이다.
랭킹 단계를 LLM 으로 바꾸고 싶다면 설계 변경이므로 ARCHITECTURE 를 먼저 고치고 승인을 받아라.

**Atlas 의 "LLM 은 입력 텍스트 전용" 제약을 이 프로젝트에 가져오지 마라.** 그러면
LLM-Explorer 정책이 coverage-guided 복제본으로 퇴화해 두 수집기를 나눌 이유가 사라진다.

### (d) export 계약 — ubuntu1.fclab 재검증 없이 바꾸지 않는다

소비자는 `Implicit-World-Modeling/scripts/build_exp08_data.py`, 정본 입력은
`data/AndroidControl/EXP08_stage1_state.jsonl` (ubuntu1.fclab). 2026-08-29 실측으로 확정:

- 최상위 키가 정확히 `{"messages","images"}` — **`sample_id` 없음**
- ShareGPT 3턴, 키는 `from`/`value`, 순서 system→human→gpt
- system value 는 전 레코드 byte-identical (md5 `2b25a54f5ace1e15a94640ef64481809`)
- human 은 **XML-FIRST**: `"Current UI State:\n" + XML + "\n\n[Screenshot]\n<image>\n\nAction:\n<action>{json}</action>"`
- gpt 는 다음 상태의 **bare html-like XML** (래퍼 없음, `>` 로 끝남 — 파서의 `pretty_xml` 은
  `lstrip` 만 하므로 `rstrip` 필요)
- `images` 는 1원소, 경로 `myset/images/episode_{EP}_step_{STEP}.jpg` — **EP 무패딩 / STEP 4자리
  zero-pad**. step 을 패딩 안 하면 regex 는 통과하지만 없는 파일을 가리켜 **조용히 드롭**된다.
- 좌표는 **840x1876 절대 픽셀**, `data-bbox="x1 y1 x2 y2"` (공백 구분 4정수).
  `840x1876 = smart_resize_dims(2400, 1080)`.

#### ⚠️ action 좌표도 840x1876 프레임이다 (Atlas 가 이걸 틀린다)

`<action>` JSON 의 `coordinate` / `coordinate1` / `coordinate2` 는 **`data-bbox` 와 같은 리사이즈
프레임**이다. 실측(각 20,000 레코드):

| | x p50/p99/max | y p50/p99/max | 프레임 밖 |
|---|---|---|---|
| `EXP08_stage1_state.jsonl` | — / — / 817 | — / — / 1853 | **0 / 20,000** |
| `stage1_train.jsonl` | 420 / 790 / 1682 | 877 / 1777 / 1852 | 2 / 15,514 |
| `stage2_train.jsonl` | 420 / 790 / 835 | 934 / 1776 / 1844 | 0 / 13,618 |

**Atlas-Collector 는 기기 픽셀을 그대로 적는다** (`raw/org.tasks/triples.jsonl` 실측: max 1027x2279,
657개 좌표 중 67개가 프레임 밖). 나머지는 프레임 안이지만 **엉뚱한 지점**을 가리켜 같은 레코드의
`data-bbox` 와 어긋난다 — 조용히 틀리는 쪽이라 더 위험하다.

→ **Monkey 의 export 는 반드시 리스케일한다.** 하드코딩 금지: 세션이 기록한
`device_width`/`device_height` 와 `smart_resize_dims` 로 파서와 **동일한 비균일 스케일**
(`x_scale = new_w/orig_w`, `y_scale = new_h/orig_h`)을 적용한다. 재수집은 불필요하다.

또한 uiautomator 는 뷰포트 밖 내용에 **역전 bbox**(`bounds="[221,2390][650,2337]"`, top>bottom)를
내고 파서는 충실히 통과시킨다. 코퍼스 3,000 레코드 266,407 박스에 역전 0 / 음수 0 이므로
**export 가 제거해야 한다.**

### (e) AIG 는 export 의 입력이 아니다

`triples.jsonl` 이 export 입력이고 스키마는 Atlas 와 같다(+ `from_page`/`to_page` 2필드).
`graph.json`(AIG)은 **병렬 산출물**이다. 그래프에서 직접 export 하면 Atlas 계약과의 호환이
조용히 깨진다.

### (f) element 정체성 함수는 하나다

`element_signature` = `(class, resource-id, subtree-text, checked/selected)` — **좌표 비의존**,
스크롤에 생존한다. 이 하나를 **세 곳 모두**에서 쓴다: AIG 엣지 키, `get_unexplored_actions` dedup,
네비게이션 재해소.

**레퍼런스의 `view['desc']` 문자열 매칭을 이식하지 마라** (`input_policy3.py:1475-1495`).
구조가 같아도 desc 가 다르면 재해소가 실패하고, `add_nav_failed_actions()` 가 발동해 그
`(semantic_state, semantic_element, action_type)` 이 **영구히 스킵**된다. 그동안 수집은 계속 돌아서
데이터만 봐서는 발견되지 않는다.

## 3. 손대면 안 되는 데이터

- **`catalog/apps.csv` 는 커밋 데이터다.** 실기기 Pixel 6 에 대고 해소한 **52행**이고 생성 산출물이
  아니다. 재생성하거나 "개선" 하지 않는다. 현재 사실: tier `androidworld_fdroid=14 / fdroid=8 /
  playstore=30`, `installed=true` **48**, status `ok=46 / clone_accepted=2 / excluded=4`,
  `auth_required` `none=26 / account_optional=11 / account_required=15`.
- `installed` 는 리터럴 문자열 `"true"` / `"false"` 다. **`bool(field)` 로 파싱하지 마라** —
  `bool("false")` 는 `True` 라, 48이라는 숫자가 엉뚱한 이유로 맞아떨어진다.
- **`installed` 와 `status` 는 독립 컬럼이다.** `installed` = 지금 이 기기에 APK 가 있는가(디바이스
  실측). `status` = 이 행에 대한 큐레이션 판정(사람이 손으로). 한쪽에서 다른 쪽을 유도하면 첫
  동기화에서 큐레이션 기록이 **조용히** 사라진다. 둘이 어긋나는 것이 정상이다.
- **`status=excluded` 4행은 수집하지 않는다.** `is_collectable` 이 False 라 러너가 구동하지 않는다.
  삭제하지 말고 그대로 둔다 — `notes` 의 제외 사유가 사라지면 다음 작업에서 같은 앱을 다시
  추가하고 같은 막다른 길을 재발견한다.
- **`clone_accepted` 2행(`com.emijotify.feesound`, `com.yummely.app`)은 의도적 채택이다.**
  기기에 실제로 깔려 있는 클론을 수집하기로 한 결정이다. "잘못된 package id" 로 보고 고치지 마라.
- **`auth_required == "account_required"` 15개는 기본 스킵**, `--include-auth` 로만 포함.
  이 기본값을 뒤집지 마라 — 로그인 벽에 갇힌 세션은 수집 예산만 태운다.
- **안정화는 스크린샷 픽셀 비교다. `uiautomator dump` 로 폴링하지 마라.** dump 는 screencap 의
  3.44배(2.341s vs 0.680s, Pixel 6 실측)라, 폴링을 dump 로 돌리면 step 당 비용이 두 배 이상 된다
  (실측 7.80s vs 3.43s). 안정화가 끝난 뒤 dump 를 **딱 한 번** 뜬다.
- **`stabilize_pixel_threshold` 를 0 으로 낮추지 마라.** 실제 전환이 0.0000 까지 수렴하는 걸 보고
  "그럼 0 이면 되겠네" 로 가기 쉬운데, 영상·스피너·깜빡이는 커서는 영원히 같아지지 않는다.
  0 이면 그런 화면에서 매 step 대기 예산을 통째로 태우면서도 데이터는 계속 나와 **조용히** 느려진다.
- **한 실행의 산출물은 하나의 root 아래 둔다** (`--root`, 기본 `data/MonkeyCollection`).
  `raw/`·`runtime/`·export 를 다시 흩뜨리지 마라 — 어긋난 채 남은 `raw/` 는 resume 시 이전
  observation 번호를 이어받아 예전 triple 이 새 화면을 가리키게 만들고, 데이터만 봐서는 발견되지 않는다.
- **`catalog/activities.json` 은 48개 수집 대상 중 41개만 덮는다.** 나머지 7개는 `dumpsys` 폴백으로
  가는데, 그건 resolver table 만 보므로 분모가 불완전하다. activity coverage 를 앱 간에 비교할 때
  이 차이를 감안하라. 2개(`com.simplemobiletools.{calendar,gallery}.pro`)는 AndroidWorld APK 로
  채울 수 있다(`catalog/extract_activities.py`).

## 4. 게이트 (코드 변경 시)

```bash
./.venv/bin/python -m pytest tests    # bare `python` 금지 (venv 밖 인터프리터를 잡는다)
./.venv/bin/python -m ruff check src tests
./.venv/bin/python -m mypy src
```

현재 기준선은 **537 passed** 다(2026-08-29, M3a 완료 시점). ruff·mypy 는 **에러 0**.
이 수가 줄면 회귀로 본다 — 단 **테스트를 의도적으로 삭제한 변경은 예외**이고, 그때는 삭제 개수까지
세어 새 기준선을 여기에 갱신한다.

> 기준선 이력: 858(기재) → **861**(실측) → 911(M1a 이식) → **274**(M1b 철거, 911 − 637)
> → 363(M1c-1) → 378(M1c-2) → **442**(M1c-3) → **469**(M2, +27: `llm/client.py` 모델
> 우선순위 4 + `text_input.py` 19 + `cost_tracker` 미확인 모델 경고/가격 4, 0 삭제)
> → **537**(M3a, +68: `test_explore.py` 44 + `test_aig.py` 24, 0 삭제).
> 2026-08-29 시점에 `.venv` 의 editable 설치가 리포 이전 경로(`~/Desktop/Projects/...`)를 가리켜
> 스위트가 **아예 실행되지 않는** 상태였다. `ModuleNotFoundError: No module named 'monkey_collector'`
> 가 보이면 코드가 아니라 venv 를 먼저 의심하고 `uv sync --extra dev` 를 돌려라.

빠른 검증 포인트:

```bash
./.venv/bin/python -m monkey_collector.cli --help
./.venv/bin/python -m monkey_collector.cli catalog --stats
./.venv/bin/python -m monkey_collector.cli catalog --status excluded
./.venv/bin/python -m monkey_collector.cli --config /nonexistent/nope.yaml catalog --stats   # exit 2 여야 한다
./.venv/bin/python -m pytest tests/unit/test_catalog.py tests/unit/test_config.py tests/unit/test_cli.py
./.venv/bin/python -m pytest tests/unit/test_pagematch.py tests/unit/test_parser.py   # 실기기 픽스처 기반
```

## 5. 설정

해석 순서: builtin defaults → `config/run.yaml` → `MC_*` env → CLI 플래그.
`main()` 이 **dispatch 전에** 해석해 `args.run_config` 로 실어 보낸다. `--config` 가 없거나 깨진
파일을 가리키면 **exit 2** 로 죽는다. 오타 난 **최상위 섹션**(`collectoin:`)도 키 오타와 똑같이
거부한다 — 잘못 설정된 실행이 성공한 것처럼 보이는 게 최악이다.

**새 설정 키를 추가하면 `_BUILTIN_DEFAULTS` · `config/run.yaml` · 해당 dataclass · `_validate()`
네 곳을 같이 갱신한다.** 그리고 **소비자 없는 키를 미리 만들지 마라** — 탐색 상수는 탐색기와,
export 키는 export 와 함께 들어온다.

## 6. 문서 규칙

- 이 리포의 문서는 **한국어**다. 기술 용어와 식별자(`data-bbox`, `smart_resize`, package id 등)는 영어로 둔다.
- README.md = 무엇/설치/카탈로그/CLI/레이아웃, ARCHITECTURE.md = 설계 + export contract,
  AGENTS.md = 작업 규칙. 같은 내용을 세 곳에 복사하지 말고 링크한다.
- CLI·저장 구조·수집 흐름이 바뀌면 README·ARCHITECTURE·AGENTS 를 **함께** 수정한다.
- **실계정 / no-action-guard 경고(§0.5)는 README.md 와 AGENTS.md 양쪽에 눈에 띄게 유지한다.**
  경고이지 할 일이 아니다.

---

> **아래 프로토콜은 device-push 시절(에뮬레이터, iter6)의 실측에서 나왔다.** 수집 아키텍처는
> 바뀌었지만 **측정 방법론과 두 함정은 그대로 유효하다** — 앱 리셋 없는 arm 비교는 여전히
> confound 로 오염되고, run 간 노이즈는 여전히 크다. 디바이스 서술만 실기기 기준으로 고쳤다.

## 효과 측정 프로토콜 (수집기 변경의 효과를 판정할 때)

수집기 변경(가드·탐색정책·임계값)의 효과를 수치로 판정하려면 **반드시** 아래를 따른다. 이 프로토콜 없이 뽑은 비교는 confound 로 오염돼 인과 해석이 불가하다 — 과거에 실제로 "다양성 +78%" 를 fix 효과로 오귀속했다가 전면 정정한 사례가 있다.

- **앱 상태 리셋 (매 run 마다)**: 측정 run 시작 전 대상 앱을 **동일한 clean state 로 되돌린다**. 이전 run 이 만든 변경(생성된 레시피·바뀐 설정·캐시된 뷰)이 다음 run 으로 흘러 confound 가 된다. 헬퍼: [`../.claude/handoff/reset_app.sh`](../.claude/handoff/reset_app.sh).
  - **user app**(musicplayer/broccoli/osmand): `adb uninstall` **후** `install -r -g catalog/apks/<pkg>.apk`. `install -r` 단독은 앱 데이터를 보존하므로 리셋이 되지 않는다 — uninstall 이 필수다.
  - **system app**(`com.google.android.calendar` = `/product/app/CalendarGooglePrebuilt`): uninstall 불가 → `pm clear` 가 동등한 데이터 리셋이다.
  - **seed 코퍼스는 리셋 후에도 동일해야 한다**(검증됨): musicplayer 의 데모 mp3 3곡은 공유 저장소(`/sdcard/Music`)라 uninstall 에 생존하고, calendar 의 seed 이벤트 25건은 **별도 priv-app** 인 `com.android.providers.calendar` DB 에 있어 앱 `pm clear` 에 생존한다.
    - broccoli 레시피는 앱 자체 DB 라 uninstall 시 소멸 → 재시드 필요.
    - `reset_app.sh` 는 리셋 전후 seed 개수를 비교해 달라지면 실패한다.
- **arm 짝맞춤**: baseline 과 treatment 를 **같은 프로토콜로 각각 수집**한다. 과거 데이터(리셋 없이 수집된 iter3~5 아카이브)를 새 프로토콜 수치와 직접 비교하지 마라 — apples-to-oranges 다. arm 사이에 달라지는 것은 **수집기 코드 하나뿐**이어야 한다(같은 앱 리셋·같은 duration·같은 디바이스 `19101FDF6004EH`).
- **treatment 오염 금지**: 측정 도중 working tree 의 수집기 소스를 수정하지 마라.
  - 앱마다 새 프로세스가 뜨므로 중간에 코드가 바뀌면 뒤 앱이 다른 코드로 돈다.
  - baseline arm 은 `git worktree` 로 격리해 돌린다.
  - `env | grep MC_` 가 비어 있어야 한다(env override 가 treatment 를 덮는다).
  - `--root` 는 CWD-상대(`paths.py`)라 worktree arm 과 메인 arm 은 서로 다른 트리에 쓴다 — cross-arm 덮어쓰기는 없다. arm 마다 `--root` 를 명시적으로 갈라 주면 더 확실하다.
- **디바이스**: 실기기 Pixel 6 `19101FDF6004EH` 고정 (`config/run.yaml` 의 `device.serial`). 같은 머신의 `emulator-5556` 은 다른 작업이 점유 중이라 **어떤 arm 에도 쓰지 마라** — 점유 충돌이자 새 confound 다.

### ⚠️ 함정 1 — provider-backed 앱은 앱 리셋으로 오염이 안 지워진다 (iter6 실측)

**seed 가 리셋에 생존하는 바로 그 성질이, 수집기가 만든 오염도 생존시킨다.** calendar 이벤트는 별도 priv-app `com.android.providers.calendar` DB 에 살아서 `pm clear com.google.android.calendar` 가 닿지 않는다 — seed 25건이 살아남는 이유이자, **수집기가 탐색 중 만든 이벤트도 살아남는 이유**다.

iter6 실측: calendar armA 의 900s 수집이 이벤트를 58건 생성(**25 → 83**) → 다음 armB 가 **3.3배 데이터**에서 출발 → **calendar arm 쌍 전체가 비교 불가**가 됐다.

`reset_app.sh` 는 이걸 **못 잡는다**: run *내부*의 `before == after` 만 검사하고 **canonical baseline 으로의 복원**은 검사하지 않는다. 두 run 모두 자체 검증을 통과했다(25→25, 83→83).

→ provider-backed 앱을 arm 에 넣으려면 **매 run 전 provider DB 를 canonical seed 로 복원**하고, 검사를 `after == canonical` 로 바꿔야 한다.
**`pm clear com.android.providers.calendar` 는 쓰지 마라** — 계정 sync 상태까지 날린다.
musicplayer 는 안전하다(수집기가 mp3 를 만들 수 없다) — **그래서 musicplayer 가 load-bearing clean isolator 다.**

### ⚠️ 함정 2 — 노이즈 바닥이 크다. arm 당 n=1 로는 판정할 수 없다 (iter6 실측)

musicplayer 를 900s·리셋 프로토콜·동일 디바이스(당시 에뮬레이터)에서 **똑같은 코드로 두 번** 돌린 결과: **15p/33e/113steps vs 12p/23e/130steps** (pages −3, edges −10, steps +17).

같은 실험에서 측정한 **fix 효과**(pre-fix → fix)는 pages +1, edges +0, steps −32.

→ **동일 코드의 run 간 변동이 측정하려는 효과보다 크다.** "표본이 적으니 조심하라"가 아니라 실측된 노이즈 추정치다. 이 크기의 효과를 판정하려면 **arm 당 최소 3 run + 분산 병기**가 필요하다. **단일 run 델타를 효과로 주장하지 마라.**

## 문서 동기화 원칙

- README 는 실제 운영 절차와 CLI 예시 중심으로 유지한다.
- ARCHITECTURE 는 현재 파일 구조와 TCP / storage 계약 중심으로 유지한다.
- CLI, 저장 구조, Android 서비스 흐름이 바뀌면 README, ARCHITECTURE, AGENTS 를 함께 수정한다.
