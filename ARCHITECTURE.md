# Architecture — 모노레포 상위 흐름

> 이 문서는 **두 하위 프로젝트를 가로지르는** 데이터·실험 흐름만 다룬다. 각 하위 시스템의 상세 설계는
> 패키지 `ARCHITECTURE.md`를 정본으로 본다 — [메인 파이프라인](./Implicit-World-Modeling/ARCHITECTURE.md) ·
> [데이터 수집기](./Monkey-Collector/ARCHITECTURE.md).

## 전체 데이터 흐름

```
Monkey-Collector (host-pull — 호스트 Python 프로세스 하나)
   ADB 로 `uiautomator dump` + `exec-out screencap` 을 끌어옴 (디바이스에 앱 없음)
   → 스크린샷 픽셀 비교로 화면 안정화 판정 → LLM-Explorer 정책이 다음 action 선택
   → raw/<pkg>/{observations,triples.jsonl,graph.json} 저장
   → `review` 로 사람이 걸러내고 → `export` 가 EXP08 Stage-1 jsonl 로 변환
        │
        ▼   (data/ 정본 — 하위 프로젝트가 심볼릭 링크로 참조)
Implicit-World-Modeling (2-stage VLM fine-tuning)
   Stage 1  World Modeling     : screenshot + UI XML + action → next UI XML
   Stage 2  Action Prediction  : screenshot + UI XML + task   → action JSON
   비교축    base / stage2 / stage1+stage2
   흐름      train → merge → eval   (outputs/ 정본)
```

## 저장소 레이아웃 규약

- top-level `data/` · `outputs/` 가 **정본**, nested 하위 프로젝트의 `data`/`outputs`는 그 정본으로의
  **심볼릭 링크**. 둘 다 git 비추적(gitignore).
- 환경 분리: 메인 파이프라인 = conda env `implicit-world-modeling` + editable LlamaFactory,
  수집기 = uv `.venv` (Python 3.10).

## 데이터 계약 주의 (MC → IWM 인계 지점)

- **eval 분할 파일명이 어긋나 있다** (2026-09-03 실측). `configs/lf_dataset/dataset_info.json` 의
  `IWM-MC_stage1_test` 는 `data/MonkeyCollection/stage1_test.jsonl` **한 개**를 기대하는데,
  `monkey-collect export` 는 **두 개**를 낸다 — `stage1_test_id.jsonl`(seen 앱의 held-out triple)
  과 `stage1_test_ood.jsonl`(train 에서 통째로 뺀 앱). `stage1_train.jsonl` 은 이름이 맞는다.
  둘은 **의미가 다른 eval 축**이라 (`export.ood_apps` / `export.id_ratio` 는 독립 knob) 합쳐서
  하나로 만들면 ID/OOD 구분이 사라진다. 인계할 때 `dataset_info.json` 쪽을 두 항목으로 나누는 것이
  맞다.
- **좌표 프레임은 840x1876 이고 XML 과 action 이 같은 프레임을 쓴다.** 디바이스 원본(1080x2400)이
  아니다 — 상세와 실측 근거는 [Monkey-Collector ARCHITECTURE §8.2](./Monkey-Collector/ARCHITECTURE.md).
- **export 는 review 판정을 기본 적용한다.** `data/MonkeyCollection/review/` 의 사람 판정이
  없으면 raw 전량이 대상이 되므로, 재현하려면 export 산출물과 `review/` 를 함께 봐야 한다.

## 더 보기

- 모델 매트릭스·데이터셋(AC_EXP01~08 / MC / MB)·실행 절차: [메인 README](./Implicit-World-Modeling/README.md)
- 수집기 구조(host-pull · LLM-Explorer · AIG): [Monkey-Collector README](./Monkey-Collector/README.md)

<!-- project-sync: 구조/계약(contract) 변경 시 이 파일의 해당 섹션만 갱신. 상세는 패키지 트리오에. -->
