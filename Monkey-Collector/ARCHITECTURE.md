# Monkey-Collector Architecture

설계 레퍼런스다. 운영 절차와 CLI 사용법은 [README.md](./README.md), 작업 규칙은 [AGENTS.md](./AGENTS.md).

---

## 1. 시스템 개요

**host-pull.** 호스트 Python 프로세스 하나가 ADB 로 디바이스를 몰고, `uiautomator dump` 와
`exec-out screencap` 으로 관측을 **끌어온다**. 디바이스에서 도는 우리 코드는 없다 — Android 앱도,
AccessibilityService 도, TCP 서버도 없다.

```
┌──────────────────────── host (Python) ────────────────────────┐
│                                                                │
│  catalog ──▶ loop ──┬──▶ stabilize ──▶ adb.screencap           │
│                     ├──▶ adb.dump_ui ──▶ xml (data-bbox)       │
│                     ├──▶ pagematch ──▶ page_id                 │
│                     ├──▶ semantic  ──▶ LLM (label / group)     │
│                     ├──▶ explore   ──▶ action                  │
│                     ├──▶ adb.tap/swipe/text/key                │
│                     └──▶ session ──▶ raw/{pkg}/…  +  aig       │
│                                                                │
│  export ──▶ stage1_{train,test_id,test_ood}.jsonl + images/    │
└────────────────────────────────────────────────────────────────┘
                              │ ADB (USB)
                              ▼
                    Pixel 6  19101FDF6004EH
```

### 왜 host-pull 인가

이전 구조(device-push)는 Android 앱이 접근성 이벤트를 보고 프레임을 밀어주는 방식이었다.
문제는 **이벤트 없이 정착한 화면**이다 — 앱은 보낼 프레임이 있는데도 조용했고, 서버는 signal
window 를 통째로 기다렸다. 실측으로 **수집 예산의 44~56% 가 오지 않을 신호를 기다리는 데** 들어갔다.
`poke` 와 escalation 사다리는 전부 그 구멍을 덧대던 장치였다.

host-pull 은 기다릴 이유가 없다. 화면이 멎었는지는 호스트가 스크린샷을 비교해 직접 판정한다.

### 형제 프로젝트와의 관계

[`../Atlas-Collector/`](../Atlas-Collector) 와 **같은 기기·같은 카탈로그·같은 export 계약**을 쓴다.
다른 것은 탐색 정책 하나뿐이고, 그게 두 프로젝트가 따로 존재하는 이유다.

| | Atlas-Collector | Monkey-Collector |
|---|---|---|
| 탐색 | coverage-guided (activity coverage 주도) | **LLM-Explorer 방식** — semantic 추상화 + 미탐색 우선 + 최단경로 |
| LLM | 입력 텍스트 생성만 (현재 미배선) | 라벨링 + same-function 그룹핑 + 입력 텍스트 |
| 그래프 산출물 | 없음 | **AIG (`graph.json`)** |
| page 식별 | LLM-free | LLM-free (동일) |
| export | EXP08 Stage-1 | EXP08 Stage-1 (동일, **단 좌표 리스케일 추가**) |

두 수집기의 산출물을 비교할 때 달라지는 변수가 탐색 정책 하나가 되도록, 나머지는 의도적으로
동일하게 유지한다.

---

## 2. 모듈 구조

| 모듈 | 책임 | 상태 |
|---|---|---|
| `adb.py` | 유일한 디바이스 채널. dump / screencap / input / wm size / 재연결 | DONE |
| `paths.py` | 하나의 collection root 아래 `raw` · `runtime` · export 해소 | DONE |
| `xml/` | `uiautomator` XML → EXP08 html-like XML (`data-bbox`, 840x1876) | DONE |
| `pagematch.py` | page 정체성 (LLM-free) | DONE |
| `stabilize.py` | 스크린샷 픽셀 비교로 화면 정착 판정 | DONE |
| `session.py` | observation / triple 디스크 레이아웃, resume | DONE |
| `catalog.py` | 수집 대상 52행 카탈로그 | DONE |
| `provision.py` | APK 해소 · 설치 · `installed` 동기화 | DONE |
| `config.py` / `cli.py` | 설정 해소, 서브커맨드 | DONE |
| `domain/actions.py` | **action space (고정 계약)** | 불변 |
| `domain/activity_coverage.py` | activity coverage 시계열 | DONE |
| `domain/cost_tracker.py` | LLM 토큰 · 비용 CSV | DONE |
| `llm/client.py` | OpenRouter Chat Completions (`qwen/qwen3.8-flash`) | DONE |
| `text_input.py` | `input_text` action 의 입력값 생성 | DONE |
| `semantic.py` | semantic state / element, same-function 그룹핑 | M3 |
| `aig.py` | AIG 그래프 + `graph.json` | M3 |
| `explore.py` | LLM-Explorer 탐색 정책 | M3 |
| `loop.py` | host-pull 수집 루프 | M4 |
| `export.py` / `_exp08_prompt.py` | EXP08 Stage-1 export | M5 |

---

## 3. 한 step 의 흐름

```
1. stabilize.wait_for_stable_screen(adb)      스크린샷을 반복 캡처해 changed_frac < threshold 까지
2. adb.dump_ui()                              정착 후 dump 를 딱 한 번
3. session.write_observation(png, xml)        관측 N 저장
4. pagematch.classify(state)                  page_id 확정 (LLM 없음)
5. semantic.observe(state, page_id)           새 state 면 LLM 라벨 + same-function 그룹 (M3)
6. explore.select(...)                        다음 action 결정 (규칙 기반)
7. adb.tap / swipe / text / key               실행
8. (다음 루프의 1~3 이 관측 N+1 을 만든다)
9. session.write_triple(N, N+1, action, …)    + aig.add_transition(...)
```

**dump 는 정착 후 한 번만 뜬다.** Pixel 6 실측으로 `uiautomator dump` 는 2.341s, `screencap -p` 는
0.680s — **3.44배**다. 정착 폴링을 dump 로 돌리면 step 당 비용이 두 배 이상 된다(실측 7.80s vs 3.43s).

---

## 4. Page 식별 (LLM-free)

`pagematch.py`. LLM-Explorer 의 `device_state.py` 방식이다.

```
view_signature          = [class]C[resource_id]R[visible]V[text]T[enabled,checked,selected]
content_free_signature  = [class]C[resource_id]R[visible]V
state_str      = md5(activity + sorted(view_signature))[:6]
structure_str  = md5(activity + sorted(content_free_signature))[:6]
```

병합 순서: `state_str` 일치 → `structure_str` 일치 → (`merge_policy=similar_elements` 일 때)
같은 activity 안에서 content-free signature **대칭차 ≤ `max_diff_elements`** → 없으면 새 page.

실측 근거(Pixel 6 덤프, `tests/fixtures/pages/`): **같은 page 를 스크롤한 경우 대칭차 2,
가장 가까운 다른 화면은 18.** 예산 2 를 안전하게 만드는 것은 이 16 의 여유다. 다른 기기에서는
이 분포를 다시 재고 나서 예산을 논해야 한다.

**픽셀은 page 식별에 쓰지 않는다.** 스크린샷 비교는 §3 의 정착 판정 전용이다.

> 레퍼런스의 `_classify_state` 에는 LLM 선택 분기가 있지만 **dead code** 다
> (`input_policy3.py:559` 가 `state_id = None` 을 무조건 대입한 뒤 `if state_id is not None` 을
> 검사한다). 그래서 (a) 충실히 이식해도 page 식별은 LLM-free 이고, (b) 대칭차 필터는 실행되지 않는
> 선택자에게 후보만 넘기고 끝나므로 **문자 그대로 이식하면 구조 해시만 남아 스크롤마다 page 가
> 쪼개진다.** `merge_policy: structure_only` 가 그 실제 동작이고, 기본값 `similar_elements` 가
> 그 빈자리를 LLM 없이 메운다.

---

## 5. 탐색 — LLM-Explorer 정책

### 5.1 무엇이 결정을 내리는가

**"다음에 무엇을 누를지" 는 100% 규칙 기반이다.** 레퍼런스도 그렇다 — `GPT.query()` 호출은
코드 전체에서 두 곳뿐이고, 둘 다 액션 선택이 아니다.

우선순위(먼저 매칭되는 분기가 그 step 의 action 을 결정하고 즉시 반환):

```
1. 진행 중이던 다중 step 네비게이션이 있으면 이어간다
2. 앱이 포그라운드에 없으면 되돌린다 (연속 이탈 max_steps_outside 초과 시)
3. 같은 구조 프레임이 max_explore_current_state 회 연속 반복되면 BACK 으로 탈출
4. pick_target        — 현재 화면 안의 미탐색 (element, action_type) 중 랜덤
                        (long_press 는 여기서 제외하고 5로 미룬다)
5. pick_navigate_target — 알고 있는 모든 in-app state 의 미탐색 후보 중,
                        최단 네비게이션 경로를 가진 것
6. 폴백 — 현재 화면의 아무 실행 가능한 action, 없으면 BACK
```

`activity coverage` 가 `max_activity_stagnation` step 동안 늘지 않으면 앱을 재시작한다.

### 5.2 LLM 이 개입하는 지점

정확히 세 곳이고, 그 중 탐색에 영향을 주는 것은 **하나뿐**이다.

| # | 무엇 | 탐색 영향 |
|---|---|---|
| 1 | semantic state / element **라벨 생성** | 없음 (사람이 읽는 이름) |
| 2 | **same-function element 그룹핑** | **있음 — 후보 가지치기** |
| 3 | `input_text` 의 **입력값 생성** | 없음 |

(2)가 핵심이다. LLM 이 "이 element 들은 같은 기능" 이라고 묶으면, 그 중 하나를 눌러본 순간
**나머지가 미탐색 목록에서 통째로 빠진다.** 리스트의 50개 행을 다 눌러보지 않게 해 주는 장치이자,
**이 프로젝트에서 LLM 이 되돌릴 수 없는 손상을 낼 수 있는 유일한 지점**이다.

그래서 두 가지가 필수다:

- **감사 가능성**: 모든 그룹을 그것이 만들어진 state 와 함께 `graph.json` 에 기록한다. 잘못된
  그룹핑은 수집된 데이터만 봐서는 절대 발견되지 않는다 — 시도조차 안 한 action 이 explored 로
  찍혀 있을 뿐이다. 기록이 없으면 사후에도 못 찾는다.
- **열화 경로**: LLM 이 없어도(`OPENROUTER_API_KEY` 없음, `input_mode=random`, API 실패) 탐색은
  계속돼야 한다. 라벨은 구조 해시로, 그룹은 빈 집합으로 떨어진다 — 즉 **가지치기가 없어질 뿐**이다.
  그리고 **semantic 라벨링이 실제로 활성이었는지를 `metadata.json` 에 기록한다.** 안 그러면 같은
  앱의 두 run 이 다른 결과를 내는데 그 이유를 알 방법이 없다.

### 5.3 LLM 호출 빈도

`_gen_state_semantic_info` 는 page 가 아니라 **state 마다** 불린다. Atlas 의 `org.tasks` 세션 실측은
**1,047 observations / 597 steps / 30 pages** 였다 — state 수는 page 수보다 훨씬 많다.

레퍼런스의 방어는 **구조 프레임 완전 일치 재사용**이다(`input_policy3.py:388-404`): 새 state 의
구조 프레임이 이미 아는 state 의 것과 같으면 GPT 를 부르지 않고 기존 정보를 재사용한다.
**이 가드를 반드시 같이 이식한다.** 그리고 앱당 호출 수를 로그에 남겨, 첫 실제 세션이 청구서가
아니라 로그로 그 숫자를 알려주게 한다.

### 5.4 element 정체성 — 단 하나의 함수

```
element_signature = (class, resource-id, subtree-text, checked/selected)
```

**좌표 비의존**이라 스크롤에 생존한다. 이 하나를 **세 곳 모두**에서 쓴다:

1. AIG 엣지 키
2. `get_unexplored_actions` 의 dedup
3. 네비게이션 재해소 (경로의 한 step 을 현재 화면에서 다시 찾을 때)

**레퍼런스의 `view['desc']` 문자열 매칭을 이식하지 않는다**(`input_policy3.py:1475-1495`).
구조가 같아도 desc 가 다르면 재해소가 실패하고, `add_nav_failed_actions()` 가 그
`(semantic_state, semantic_element, action_type)` 을 **영구히 스킵 목록에 넣는다.** 그동안 수집은
계속 정상으로 보인다. 두 정체성 함수가 공존하면 이 실패는 조용히, 그리고 누적해서 일어난다.

---

## 6. AIG — App Interaction Graph

레퍼런스에는 "AIG" 라는 용어가 없다. 그쪽의 그래프는 `UTG`(`utg.py` 의 `G` / `G2` networkx
DiGraph, `utg.js` 로 덤프) 하나뿐이다. **AIG 는 그 UTG 를 이 프로젝트의 page 식별 위에 다시
세운 것**이고, 스키마는 아래가 정본이다.

### 6.1 위치와 형식

`{root}/raw/{package}/graph.json` — 세션당 하나. 새 노드·엣지가 생길 때 갱신한다.

```json
{
  "package": "net.gsantner.markor",
  "device": {"width": 1080, "height": 2400},
  "semantic_labeling": true,
  "nodes": [
    {
      "page_id": 7,
      "activity": "net.gsantner.markor.activity.MainActivity",
      "package": "net.gsantner.markor",
      "state_strs": ["a1b2c3", "d4e5f6"],
      "structure_strs": ["9f8e7d"],
      "semantic_title": "Notes list",
      "first_observation": 42,
      "visits": 13
    }
  ],
  "edges": [
    {
      "from_page": 7,
      "to_page": 11,
      "action": {"action_type": "tap", "x": 540, "y": 1720, "element_index": 23},
      "element_signature": "android.widget.ImageButton|fab_add|Add|",
      "semantic_element": "Add note button",
      "count": 3,
      "effective": true,
      "steps": [58, 91, 140]
    }
  ],
  "same_function_groups": [
    {
      "minted_in_state": "a1b2c3",
      "page_id": 7,
      "members": ["...element_signature...", "...element_signature..."],
      "source": "llm"
    }
  ],
  "stats": {"pages": 30, "edges": 84, "explored_actions": 211, "unexplored_actions": 47,
            "llm_calls": 34}
}
```

- **노드는 page** 다 (§4 가 확정한 것). 여러 `state_str` 이 한 page 로 병합될 수 있으므로 리스트다.
- **엣지 키는 좌표가 아니라 `element_signature`** 다 (§5.4). 스크롤로 좌표가 바뀌어도 같은 엣지다.
- `action` 은 `domain/actions.py` 의 직렬화 그대로다 — **export wire format 이 아니다**(§8).
- `same_function_groups` 는 §5.2 의 감사 기록이다. `source` 가 `"llm"` 인지 `"none"` 인지로
  가지치기가 실제로 작동했는지 사후에 판별한다.

### 6.2 AIG 는 export 의 입력이 아니다

**`triples.jsonl` 이 export 입력이고, `graph.json` 은 병렬 산출물이다.**

이유는 계약 안정성이다. export 계약(§8)은 원격 코퍼스에서 역설계한 것이고 조용히 깨지기 쉽다.
그래프를 직접 export 소스로 쓰면 그래프 스키마를 손댈 때마다 export 가 함께 흔들린다.
triple 은 Atlas 와 동일한 스키마를 유지하므로, export 모듈은 형제 프로젝트와 나란히 검증할 수 있다.

triple 에는 Atlas 스키마에 **`from_page` / `to_page` 두 필드만 추가**한다. 그래프 엣지와 triple 을
상호 추적하기 위한 것이고, export 는 모르는 필드를 무시하므로 계약은 그대로다.

---

## 7. 저장 포맷

```
{root}/                              기본 data/MonkeyCollection
├── raw/{package}/                   내구성 — 코퍼스가 여기 있다
│   ├── observations/{index:04d}/
│   │   ├── screenshot.png
│   │   └── raw.xml
│   ├── triples.jsonl
│   ├── graph.json                   AIG
│   └── metadata.json
├── runtime/
│   ├── apps/{package}/
│   │   ├── activity_coverage.csv
│   │   └── cost.csv
│   └── logs/run_*.log               휘발성 — 스캐폴딩
├── stage1_train.jsonl               export 산출물
├── stage1_test_id.jsonl
├── stage1_test_ood.jsonl
├── images/
└── export_meta.json
```

**한 실행의 산출물은 하나의 root 아래 둔다.** 흩어 놓으면 예전 `raw/` 가 새 export 옆에 남아
일관된 것처럼 보이고, resume 이 예전 observation 번호를 이어받아 **옛 triple 이 새 화면을 가리킨다.**
데이터만 봐서는 발견되지 않는다.

### triple 스키마

```json
{"step": 58, "before": 42, "after": 43,
 "action": {"action_type": "tap", "x": 540, "y": 1720, "element_index": 23},
 "changed": true, "page_changed": true, "reason": "",
 "from_page": 7, "to_page": 11}
```

관측은 **참조 2개로 저장**한다 — before/after 가 실제로 같은 bytes 임이 보장되고 중복 저장도 없다.

### resume

**메타데이터가 아니라 디스크가 진실이다.** 세션이 step 중간에 죽으면 `metadata.json` 은 실제보다
적은 observation 수를 말하고, 다음 run 이 그 위에 덮어쓴다. `Session.open()` 은
`max(메타 값, 디스크 실측값)` 으로 시작한다.

---

## 8. Export — EXP08 Stage-1 계약

소비자는 `Implicit-World-Modeling/scripts/build_exp08_data.py`, 정본 입력은 ubuntu1.fclab 의
`data/AndroidControl/EXP08_stage1_state.jsonl`. **이 계약은 원격 실파일 재검증 없이 바꾸지 않는다.**

### 8.1 레코드

```json
{"messages": [{"from": "system", "value": "<byte-identical 리터럴>"},
              {"from": "human",  "value": "Current UI State:\n{XML}\n\n[Screenshot]\n<image>\n\nAction:\n<action>{json}</action>"},
              {"from": "gpt",    "value": "{다음 상태의 bare XML}"}],
 "images": ["myset/images/episode_{EP}_step_{STEP:04d}.jpg"]}
```

- 최상위 키는 **정확히 두 개**. `sample_id` **없음**.
- system value 는 전 레코드 byte-identical, md5 `2b25a54f5ace1e15a94640ef64481809`. **템플릿이 아니라
  리터럴이다** — 프레임 크기를 보간하지 마라.
- human 은 **XML-FIRST**. (Stage-2 는 반대로 image-first다. 순서가 뒤집히면 빌더의 문자열 split 이
  **조용히** 오작동한다.)
- gpt 는 래퍼 없는 bare XML 이고 `>` 로 끝난다. 파서의 `pretty_xml` 은 `lstrip` 만 하므로
  export 가 `rstrip` 해야 한다.
- 이미지 경로에서 **EP 는 무패딩, STEP 은 4자리 zero-pad.** step 을 패딩하지 않으면 regex 는
  통과하지만 없는 파일을 가리켜 **조용히 드롭**된다.

### 8.2 좌표 — XML 과 action **둘 다** 840x1876

`840x1876 = smart_resize_dims(2400, 1080)` 이라 1080x2400 기기와 정확히 맞는다.

XML 은 `data-bbox="x1 y1 x2 y2"`(공백 구분 4정수)로 이미 리사이즈된다 — 파서의
`coord_mode="resized"` 가 그 일을 한다.

**action JSON 의 `coordinate` / `coordinate1` / `coordinate2` 도 같은 프레임이다.**
2026-08-29 원격 실측:

| 파일 | x p50/p99/max | y p50/p99/max | 프레임 밖 |
|---|---|---|---|
| `EXP08_stage1_state.jsonl` (정본 입력) | — / — / 817 | — / — / 1853 | **0 / 20,000** |
| `stage1_train.jsonl` | 420 / 790 / 1682 | 877 / 1777 / 1852 | 2 / 15,514 |
| `stage2_train.jsonl` | 420 / 790 / 835 | 934 / 1776 / 1844 | 0 / 13,618 |

p50 x = 420 = 840/2. stage1 의 이탈 2건(0.013%)은 원본 코퍼스 노이즈다.

> **Atlas-Collector 는 이 지점을 틀린다.** 수집 루프가 `adb.tap(x, y)` 의 기기 픽셀을 그대로 triple 에
> 적고 export 가 그것을 verbatim 으로 넣는다 — 변환 코드가 없다. 실측(`raw/org.tasks/triples.jsonl`,
> 597 triple): max 1027x2279, 657개 좌표 중 67개가 프레임 밖. 나머지는 프레임 안이지만 **엉뚱한
> 지점**을 가리켜 같은 레코드의 `data-bbox` 와 어긋난다.

**Monkey 는 export 시점에 리스케일한다.** 세션이 기록한 `device_width`/`device_height` 와
`smart_resize_dims` 로 파서와 **동일한 비균일 스케일**(`x_scale = new_w/orig_w`,
`y_scale = new_h/orig_h`)을 적용한다. 하드코딩 금지. 재수집은 불필요하다 — 기기 좌표가 원본이므로
export 시점 변환으로 충분하다.

### 8.3 좌표 프레임의 출처

`parse_device_xml(raw, width, height)` 의 width/height 는 **수집 시점에 `wm size` 로 읽어
`metadata.json` 에 기록한 값**이다. dump 에서 유도하지 않는다 — 권한 다이얼로그처럼 화면 전체를
덮지 않는 창은 정당하게 작아서, 단일 dump 만으로는 "부분 창" 과 "잘못된 해상도" 를 구분할 수 없다.
Atlas 에서 이 유도 방식이 26개 triple 중 **6개(23%)를 frame mismatch 로 조용히 드롭**했다.

### 8.4 드롭 조건

`unchanged`(기본) · `missing_files` · `foreign`(before/after 의 dominant package 가 세션 패키지와
다름) · `unparsable` · `duplicate_step`.

또한 uiautomator 는 뷰포트 밖 내용에 **역전 bbox**(`bounds="[221,2390][650,2337]"`, top > bottom)를
내고 파서는 충실히 통과시킨다. 코퍼스 3,000 레코드 266,407 박스에 역전 0 / 음수 0 이므로
**export 가 제거한다.**

### 8.5 분할

- `--ood-apps <frac>` — **앱 단위 홀드아웃**. 그 앱의 triple 은 통째로 `test_ood`.
- `--id-ratio <frac>` — seen 앱 **각각에 대해 독립적으로** 표본을 뽑아 `test_id`. 앱마다 따로 뽑기
  때문에 특정 앱에 ID eval 이 몰리지 않는다.
- 두 knob 은 완전히 독립이다.

---

## 9. Action space

`domain/actions.py` 의 7종이 **고정 계약**이다. 타입을 추가하거나 이름을 바꾸지 않는다.

| action_type | 필드 | ADB |
|---|---|---|
| `tap` | `x, y` | `input tap` |
| `swipe` | `x1, y1, x2, y2, duration_ms` | `input swipe` |
| `input_text` | `text, x, y` | tap → `input text` → 키보드 닫기 |
| `press_back` | — | `KEYCODE_BACK` |
| `press_home` | — | `KEYCODE_HOME` |
| `long_press` | `x, y, duration_ms` | zero-movement `input swipe` |
| `open_app` | `package, app_name` | **record-only** — 탐색기가 만들지 않는다 |

공통 필드: `action_type: str`, `element_index: int`.

### 세 개의 층을 구분하라

```
탐색기 semantic action        domain/actions.py            EXP08 wire (export)
touch, select          →     tap                    →     click
long_touch             →     long_press             →     long_press
set_text               →     input_text             →     type
scroll                 →     swipe                  →     swipe
press(BACK)            →     press_back             →     navigate_back
press(HOME)            →     press_home             →     navigate_home
—                            open_app               →     open
```

내부 vocabulary 는 그대로 두고 **export 레이어에서만** EXP08 이름으로 번역한다. 그래서 "action space
유지" 와 "Atlas 와 같은 데이터셋 형태" 가 충돌하지 않는다. EXP08 의 `wait` / `terminate` 는
Stage-1 human turn 에 나오지 않으므로 emit 하지 않는다.

---

## 10. 설정

해석 순서: builtin defaults → `config/run.yaml` → `MC_*` env → CLI 플래그.
`main()` 이 dispatch 전에 해석해 `args.run_config` 로 넘긴다.

| 섹션 | 키 |
|---|---|
| `device` | `serial`(실기기 고정) · `width` · `height` |
| `collection` | `budget_mode` · `max_duration` · `max_steps` · `seed` · `action_delay_ms` · `stabilize_*`(5) |
| `page_matching` | `merge_policy` · `max_diff_elements` · `same_activity_only` |
| `llm` | `model` · `input_mode` |
| `export` | `target_size` · `ood_apps` · `id_ratio` |

`exploration` 섹션은 M3 에서 탐색기와 함께 들어온다. **소비자 없는 키를 미리 만들지 않는다.**

알 수 없는 최상위 섹션은 키 오타와 똑같이 거부한다 — 잘못 설정된 실행이 성공한 것처럼 보이는 게
최악이다. `--config` 가 없거나 깨진 파일을 가리키면 **exit 2**.

---

## 11. CLI

| 서브커맨드 | 상태 |
|---|---|
| `catalog` | DONE |
| `sync-installed` | DONE |
| `provision` | DONE |
| `reset` | DONE |
| `run` | **M4 — 미구현, 등록도 안 됨** |
| `export` | **M5 — 미구현, 등록도 안 됨** |

`NotImplementedError` 를 던지는 유령 서브커맨드를 만들지 않는다. 구현이 생길 때 이 표와
`cli.py` 의 서브파서와 README 의 CLI 절을 **같이** 갱신한다.
