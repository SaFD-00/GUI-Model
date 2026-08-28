# ARCHITECTURE — Atlas-Collector

Atlas-Collector 의 설계 문서다. **§4 Export contract 는 명세(spec)이지 설명이 아니다** —
ubuntu1.fclab 의 실제 파일에서 역설계한 사실이고, 재검증 없이 바꾸면 학습 파이프라인이 조용히 깨진다.

> ⚠️ **구현 상태**: 이 문서는 목표 설계를 기술한다. 현재 리포에는 **M1 (scaffold + catalog + config + docs)
> 과 M2 (`xml/` 인코딩 + 좌표 프레임)** 가 있다. **M3 (exploration), M4 (collection loop),
> M5 (export) 는 미구현**이다. M2 는 라이브러리라 CLI 서브커맨드가 없고, 따라서 실행 가능한
> 서브커맨드는 여전히 `atlas-collect catalog` 하나다.

> ⚠️ **실계정 기기 · action guard 없음**: 수집 대상은 실제 계정(WhatsApp · Telegram · Signal ·
> Slack · Discord)으로 로그인된 개인 디바이스이고, 이 설계에는 파괴적 action 을 막는 가드가
> **의도적으로 없다**. 자율 탐색이 실제 메시지를 보내거나 실제 게시물을 올릴 수 있다.
> 사용자가 위험을 인지하고 승인한 결정이다 — 자세한 내용과 다른 기기에서의 주의사항은
> [`AGENTS.md` §0.5](./AGENTS.md) 와 [`README.md`](./README.md) 최상단 경고를 본다.

---

## 1. 왜 host-pull 인가

형제 프로젝트 Monkey-Collector 는 **device-push** 다: 디바이스의 AccessibilityService 앱이 화면 전환을
감지해 screenshot 과 XML 을 TCP 로 서버에 밀어 올리고, 서버가 그 신호를 기다린다. 이 구조에서
**예산의 44-56% 가 signal timeout 으로 소모**됐다. 화면이 이미 안정됐는데도 앱이 이벤트를 못 뱉으면
서버는 타임아웃 창을 통째로 기다려야 한다.

Atlas 는 신호를 **없앤다**. 호스트가 자기 스케줄로 직접 끌어온다:

- Android 앱 **없음**
- AccessibilityService **없음**
- TCP 서버 **없음**
- 디바이스에서 도는 것은 stock `uiautomator` / `screencap` / `input` 뿐

대가는 화면 안정화를 호스트가 직접 판단해야 한다는 것이다 (§2 stabilization).

## 2. host-pull 루프

한 step 의 형태:

```
    ┌─ observe(before) ────────────────────────────────┐
    │  adb shell uiautomator dump /sdcard/…xml         │  → before_xml
    │  adb exec-out cat /sdcard/…xml                   │
    │  adb exec-out screencap -p                       │  → before_screenshot (PNG bytes)
    └──────────────────────────────────────────────────┘
                          ↓
    ┌─ decide ─────────────────────────────────────────┐
    │  page 식별 (LLM-free): state_str → structure_str  │
    │                        → 대칭차 ≤ 2 (같은 activity)│
    │  action 선택: coverage-guided unexplored-first,   │
    │               미탐색 action 까지 shortest-path     │
    │  (텍스트 입력이 필요할 때만) LLM 으로 입력값 생성    │
    └──────────────────────────────────────────────────┘
                          ↓
    ┌─ act ────────────────────────────────────────────┐
    │  adb shell input tap / swipe / text / keyevent   │  → action
    └──────────────────────────────────────────────────┘
                          ↓
    ┌─ stabilize ──────────────────────────────────────┐
    │  action_delay_ms 대기 후,                          │
    │  stabilize_poll_ms 간격으로 dump 를 반복해           │
    │  연속 두 dump 가 같아지면 안정으로 본다.              │
    │  stabilize_max_wait_sec 초과 시 마지막 dump 채택.    │
    └──────────────────────────────────────────────────┘
                          ↓
    ┌─ observe(after) ─────────────────────────────────┐
    │  dump + screencap 다시                            │  → after_xml, after_screenshot
    └──────────────────────────────────────────────────┘
```

**stabilization 이 host-pull 의 유일한 추가 비용**이다. device-push 는 앱이 "안정됐다" 를 알려주지만,
host-pull 은 호스트가 폴링으로 알아내야 한다. 대신 기다릴 신호가 없으니 timeout 으로 예산을 태울 일이 없다.

**USB 링크 복구**: 실기기 링크는 세션 중 끊어진다(실측 2회). `AdbClient.reconnect()` 가
`kill-server` → `start-server` → `wait-for-device` → serial 재탐색을 수행한다. 재시도 정책은 호출자 몫이다.

## 3. 출력 단위 — the TRIPLE

수집의 원자 단위는 **triple** 이다:

```
(before_xml, before_screenshot, action, after_xml, after_screenshot)
```

이 다섯이 한 덩어리로 `data/raw/{package}/` 아래 저장된다. Stage-1 학습 레코드는 이 triple 에서
직접 파생된다 — `before_*` 와 `action` 이 human turn 을, `after_xml` 이 gpt turn 을 만든다
(`after_screenshot` 은 Stage-1 레코드에 들어가지 않지만, 검증과 후속 stage 를 위해 보관한다).

### ⚠️ 두 스키마를 혼동하지 말 것

이름이 비슷한 서로 **다른** 두 레코드가 있다:

| 이름 | 위치 | 소유자 |
|---|---|---|
| **triple record** (내부) | `data/raw/{package}/` | Atlas-Collector 자신 |
| **export record** (jsonl) | `data/export/` → ubuntu1.fclab | §4 의 export contract |

**triple record** 는 Atlas 내부 포맷이라 우리가 자유롭게 정한다. 여기에 **optional `task` 필드를
예약해 둔다** — Stage 2 는 이번 범위 밖이지만, 나중에 task 라벨을 붙일 자리를 지금 열어 두기 위함이다.
현재는 비워 둔다.

**export record** 는 §4 의 계약이 지배하며 최상위 키가 `{"messages", "images"}` 로 고정이다.
**내부 triple 에 `task` 가 있든 없든 export record 에는 절대 실리지 않는다.**

### 탐색 (M3, 미구현)

LLM-Explorer (`.claude/references/LLM-Explorer`) 의 개념을 가져온다:

- **coverage-guided unexplored-first**: 아직 실행하지 않은 action 을 우선 고른다
- **shortest-path navigation**: 현재 page 에서 미탐색 action 이 있는 page 까지 최단 경로로 이동
- **page matching (LLM-Explorer 이식)**: `device_state.py` 의 정체성 해시를 그대로 옮긴다.

  ```
  signature              [class]C[resource_id]R[visible]V[text]T[en,ch,se]   (text 50자 초과는 None)
  content_free_signature [class]C[resource_id]R[visible]V
  state_str      = md5("{activity}{" + ",".join(sorted(signature)) + "}")[:6]
  structure_str  = 같은 식을 content_free_signature 로
  ```

  병합 순서: `state_str` 일치 → `structure_str` 일치 → (같은 activity 안에서) content-free signature
  대칭차 `≤ max_diff_elements` 인 가장 가까운 page. 셋 다 빗나가면 새 page 를 발급한다.

  > **레퍼런스의 LLM 선택 분기는 dead code 다.** `_classify_state` 는 `state_id = None` 을 무조건
  > 대입한 뒤 `if state_id is not None` 을 검사한다(input_policy3.py:557-559). 즉 **레퍼런스의 page
  > 병합은 LLM 유무와 무관하게 순수 휴리스틱**이고, LLM 은 page 의 *제목 문자열*만 만든다. 이 알고리즘을
  > 이식해도 LLM-free 계약에 포기할 것이 없다. 대신 대칭차 필터는 실행되지 않는 선택자에게 후보만
  > 넘기고 끝나므로, 문자 그대로 이식하면 구조 해시 매칭만 남아 스크롤마다 page 가 쪼개진다.
  > `merge_policy: structure_only` 가 그 **실제** 동작이고, 기본값 `similar_elements` 가 빈자리를 메운다.

  Pixel 6 실측 여유 (`tests/fixtures/pages/`): 같은 page 스크롤 = 대칭차 **2**, 가장 가까운 **다른**
  화면 = **18**. 이 16 의 여유가 예산 2 를 안전하게 만든다. 다른 기기에서는 재측정하고, 넓히지 말고
  `structure_only` 로 좁혀라 — 과병합은 시도하지도 않은 action 을 explored 로 표시하며 그 손상은
  수집 데이터만 봐서는 발견되지 않는다.

- **픽셀은 page 식별에 관여하지 않는다.** 스크린샷 비교는 화면 **안정화** 전용이다
  (`atlas_collector.stabilize`). 두 관심사를 섞으면 안정화 임계값을 건드릴 때마다 page 정체성이
  조용히 따라 바뀐다.

**page 식별은 LLM 을 절대 타지 않는다.** 이건 성능 최적화가 아니라 재현성 계약이다 —
같은 화면이 API 응답의 변덕에 따라 다른 page 로 갈리면 page_graph 전체가 무의미해진다.

### LLM 의 유일한 용도

OpenRouter Chat Completions, 기본 모델 `qwen/qwen3.8-flash`, base URL `https://openrouter.ai/api/v1`,
키는 `OPENROUTER_API_KEY`. **쓰이는 곳은 텍스트 입력 필드의 입력값 생성 하나뿐이다.**
`create_llm_client()` 는 키가 없으면 예외 대신 `None` 을 돌려주고, 입력 텍스트는 hardcoded `random` 으로
폴백한다. page 식별은 어느 쪽이든 영향받지 않는다.

`LLMClient` 는 `TokenUsage` 누적과 `usage_hook` 콜백(`(agent, prompt_tokens, completion_tokens, model)`)을
갖는다 — 후속 milestone 의 cost tracker 가 붙을 seam 이다. 이 모듈 자체는 파일을 쓰지 않는다.

---

## 4. EXPORT CONTRACT (FACTS — 명세)

아래는 ubuntu1.fclab 의 **실제 파일에서 역설계한 사실**이다. 추측이 아니다.
소비자는 `Implicit-World-Modeling/scripts/build_exp08_data.py`, 입력 파일은
`data/AndroidControl/EXP08_stage1_state.jsonl` 이다.

**Export 타깃은 Stage 1 (NEXT_STATE_PREDICTION) 뿐이다. Stage 2 는 범위 밖이다.**

이 절이 규정하는 것은 **export record (jsonl)** 하나뿐이다. 내부 triple record 의 스키마(§3, optional
`task` 필드 포함)와는 별개이며, **내부 스키마에 무엇이 있든 export record 에는 아래 키만 나간다.**

### 4.1 export record 스키마

- 최상위 키는 **정확히** `{"messages", "images"}` — 그 외 아무것도 없다. **`sample_id` 없음.**
  (`task` 도 없다. Stage 2 예약 필드는 내부 triple 에만 존재한다 — §3.)
- `messages` 는 `"from"` / `"value"` 키를 가진 dict **3개** (ShareGPT 형식), role 순서는
  `'system'`, `'human'`, `'gpt'`.
- `system` 의 value 는 60,871 레코드 전체에서 **byte-identical** 하다 (Mode: NEXT_STATE_PREDICTION).
  → **하드코딩한다.**
- `human` 의 value 는 **XML-FIRST** 템플릿이다:

  ```text
  "Current UI State:\n" + XML + "\n\n[Screenshot]\n<image>\n\nAction:\n<action>" + JSON + "</action>"
  ```

- `gpt` 의 value 는 다음 state 의 **bare html-like XML** 이다 — 래퍼 없음, `<thought>` 없음.
- `images` 는 **1-element 리스트**. human turn 당 `"<image>"` 토큰은 **정확히 하나**.

### 4.2 이미지 경로 = episode/step 의 유일한 식별자

경로 형식:

```text
myset/images/episode_{EP}_step_{STEP}.jpg
```

- `EP` 는 **zero-padding 없음(UNPADDED)**
- `STEP` 은 **4자리 zero-padding**
- **episode 와 step 정체성은 오직 이 파일명으로만 전달된다.** 레코드 본문에 episode/step 필드는 없다.

> 참고: 빌더는 `episode_(\d+)_step_(\d+)` 정규식과 **별개로** `myset/images/home.jpg` passthrough 케이스를
> 갖고 있다. Atlas 는 이걸 절대 emit 하지 않는다 — 스키마 요구사항으로 오해하지 말 것.

### 4.3 좌표계

- 좌표는 **840x1876 프레임의 절대 픽셀**이다.
- 표기: `data-bbox="x1 y1 x2 y2"` — **공백으로 구분된 네 개의 int**. 콤마 아님.
- `840x1876` 은 정확히 `smart_resize_dims(2400, 1080)` 이다.

### 4.4 Action JSON

`<action>...</action>` 안의 JSON 은 다음 중 하나다:

| action | payload |
|---|---|
| `click` | `coordinate` |
| `long_press` | `coordinate` |
| `type` | `text` |
| `swipe` | `coordinate1`, `coordinate2` |
| `navigate_home` | — |
| `navigate_back` | — |
| `open` | `app_name` |
| `wait` | — |
| `terminate` | `status`, `answer` |

**`stage1_state` 의 human turn action 은 `terminate` 나 `wait` 가 되는 일이 없다.**

---

## 5. 840x1876 프레임 — 왜 이 숫자인가

Qwen 계열의 `smart_resize` 는 **28px 정렬**로 리사이즈하고, 결과 픽셀 수를 visual token 예산 안에 맞춘다.

```
840  = 30 × 28
1876 = 67 × 28
visual tokens = 30 × 67 = 2010   ≤ 2048 토큰 예산
pixels        = 840 × 1876 = 1,575,840   ≤ 1,605,632 픽셀 예산
```

즉 840x1876 은 1080x2400 세로 화면을 28px 격자에 맞추면서 토큰/픽셀 예산을 넘지 않는 최대치다.
"둥근 숫자" 가 아닌 이유가 이것이다. 빌더 주석(`scripts/build_exp08_data.py`)이 같은 예산
(`1,605,632`)을 명시한다.

### ⚠️ 디바이스 → 프레임 변환은 ANISOTROPIC 하다

```
x: 1080 → 840    scale_x = 840 / 1080  = 0.77778
y: 2400 → 1876   scale_y = 1876 / 2400 = 0.78167
```

**두 스케일이 다르다. 단일 스케일 팩터를 쓰면 틀린다.**
축마다 따로 곱해야 하고, 결과 범위는 `x ∈ [0, 840]`, `y ∈ [0, 1876]` 이다.

M2 (`xml/` 좌표 변환) 는 이 사실 위에 구현돼 있고 (`smart_resize_dims` / `source_frame`),
앞으로의 M5 (export) 도 같은 프레임을 그대로 써야 한다.
`uiautomator dump` 의 `bounds="[x1,y1][x2,y2]"` 는 **디바이스 픽셀(1080x2400)** 이므로,
export 시 축별로 변환한 뒤 `data-bbox="x1 y1 x2 y2"` (공백 구분 int 4개)로 써야 한다.

---

## 6. Eval split — 서로 독립인 두 knob

| knob | 단위 | 의미 |
|---|---|---|
| `--ood-apps <frac>` | **앱** | train 에서 통째로 제외하는 앱의 비율 → **OOD eval** |
| `--id-ratio <frac>` | **triple** | train 에 포함된(seen) 앱들의 triple 중 eval 로 떼는 비율 → **ID eval** |

두 값은 서로 곱해지거나 연동되지 않는다. 기본값은 `config/run.yaml` 의 `export.ood_apps: 0.3`,
`export.id_ratio: 0.1`.

---

## 7. 모듈 구조

| 모듈 | 책임 | 상태 |
|---|---|---|
| `cli.py` | argparse CLI, 서브커맨드 등록, dispatch 전 config 해석 | M1 (`catalog` 만 동작) |
| `config.py` | builtin defaults → `run.yaml` → `AC_*` env → CLI, + 검증 | M1 |
| `paths.py` | `data/` (영속) · `runtime/` (휘발성) root 해석 | M1 |
| `catalog.py` | `apps.csv` read/write/filter. **stdlib only** | M1 |
| `adb.py` | `AdbClient` — host-pull 의 유일한 디바이스 채널 | M1 (M4 가 소비) |
| `llm/client.py` | OpenRouter Chat Completions, 입력 텍스트 전용 | M1 (M4 가 소비) |
| `xml/` | XML 인코딩, actionable element 추출, 좌표 변환 | **M2 — DONE** |
| (미생성) exploration | coverage-guided explorer, page graph | **M3** |
| (미생성) collection loop | observe → decide → act → stabilize | **M4** |
| (미생성) export | triple → Stage-1 jsonl (§4 계약) | **M5** |

### 설계상의 의도적 선택

- **`catalog.py` 는 stdlib 전용**이다. `catalog` 서브커맨드와 테스트가 openai/pillow 상태와 무관하게
  돌아야 한다.
- **`AdbClient` 는 생성 시 USB 를 건드리지 않는다.** serial 은 첫 명령에서 lazy 하게 해소한다.
  Monkey-Collector 는 생성자에서 AVD 를 강제 확인하고 없으면 `RuntimeError` 를 냈지만, Atlas 는
  물리 디바이스가 대상이고 모듈 import 만으로 디바이스가 필요해지면 안 된다.
- **`run.yaml` 부재는 에러가 아니다.** builtin defaults 가 그대로 선다 — `--help` 가 환경에 의존하지 않도록.
  단 `--config` 로 **명시한** 파일이 없거나 깨졌으면 exit 2 로 죽는다. 기본 파일의 부재는 기본값이고,
  명시한 파일의 부재는 오타다. 같은 이유로 **오타 난 최상위 섹션**(`collectoin:`)도 거부한다 —
  조용히 무시하면 잘못 설정된 실행이 성공한 것처럼 보인다.
- **`device.width` / `device.height` 는 좌표 변환의 소스 프레임이므로 양의 정수만 받는다.**
  `0` 은 리사이즈 내부에서 `ZeroDivisionError` 로 터지고, 음수는 **아무 에러 없이**
  `smart_resize_dims(2400, -1080) = (2408, -1092)` 를 만들어 음수 스케일 박스를 뱉는다.
  §5 의 anisotropic 변환이 성립하려면 이 값이 먼저 온전해야 한다 (`config._validate`).
- **`catalog/apps.csv` 는 커밋 데이터**(52행)다. `installed` 는 디바이스 실측 사실,
  `status` 는 큐레이션 상태이며 **서로 독립적인 컬럼**이다 (AGENTS.md §3). `write_catalog()` 의 `csv.writer` 는 QUOTE_MINIMAL 이라
  read→write round-trip 이 byte-identical 하지 않다(불필요하게 인용된 행들이 있다). 그래서 M1 에서는
  writer 를 커밋된 CSV 에 절대 돌리지 않고, 테스트도 `tmp_path` 에서만 쓴다.
