# AGENTS.md (repo root)

이 저장소는 **세 개의 독립 하위 프로젝트**로 구성된다. 작업 대상에 맞는 트리오를 본다.

- **[`Implicit-World-Modeling/`](./Implicit-World-Modeling)** — 메인 2-stage VLM 파이프라인. 작업 지침 [`AGENTS.md`](./Implicit-World-Modeling/AGENTS.md), 사용자 가이드 [`README.md`](./Implicit-World-Modeling/README.md), 시스템 레퍼런스 [`ARCHITECTURE.md`](./Implicit-World-Modeling/ARCHITECTURE.md).
- **[`Atlas-Collector/`](./Atlas-Collector)** — Android GUI 수집기, **coverage-guided 탐색** (자체 README/ARCHITECTURE/AGENTS 트리오).
- **[`Monkey-Collector/`](./Monkey-Collector)** — Android GUI 수집기, **LLM-Explorer 탐색 + AIG** (자체 트리오).

## 두 수집기는 대조군이다

같은 실기기(Pixel 6 `19101FDF6004EH`), 같은 52행 카탈로그, 같은 파서, 같은 page 식별,
같은 EXP08 Stage-1 export 계약을 쓴다. **의도적으로 다른 것은 탐색 정책 하나뿐**이다.

| | Atlas-Collector | Monkey-Collector |
|---|---|---|
| 탐색 | coverage-guided, 완전 LLM-free | LLM-Explorer 방식 (semantic 추상화 + 최단경로) |
| 그래프 산출물 | 없음 | AIG (`graph.json`) |
| 산출 위치 | `data/AtlasCollection/` | `data/MonkeyCollection/` |

한쪽에만 변경을 넣으면 그 변수가 늘어난다. **양쪽에 걸친 변경은 한쪽만 고치고 끝내지 마라** —
고칠 수 없으면 왜 한쪽에만 넣는지 기록하라.

### ⚠️ 두 수집기 모두 실계정 기기를 몬다

수집 대상은 실제 계정으로 로그인된 개인 폰이고, **파괴적 action 을 막는 가드가 없다.**
사용자가 위험을 인지하고 내린 결정이며 미구현 항목이 아니다. 가드를 임의로 넣지 말고 먼저 물어라.
같은 머신의 `emulator-5556` 은 **다른 작업이 점유 중이라 어느 쪽도 건드리면 안 된다.**

> 대용량 정본 `data/`·`outputs/` 는 gitignore 대상(심볼릭 링크 구조). 산출물 자체는 문서에 복사하지 말고 경로·요약만 기록한다.
