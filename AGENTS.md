# AGENTS.md (repo root)

이 저장소는 **두 개의 독립 하위 프로젝트**로 구성된다. 작업 대상에 맞는 트리오를 본다.

- **[`Implicit-World-Modeling/`](./Implicit-World-Modeling)** — 메인 2-stage VLM 파이프라인. 작업 지침 [`AGENTS.md`](./Implicit-World-Modeling/AGENTS.md), 사용자 가이드 [`README.md`](./Implicit-World-Modeling/README.md), 시스템 레퍼런스 [`ARCHITECTURE.md`](./Implicit-World-Modeling/ARCHITECTURE.md).
- **[`Monkey-Collector/`](./Monkey-Collector)** — Android GUI 수집기, **LLM-Explorer 탐색 + AIG** (자체 트리오).

### ⚠️ 실계정 기기를 몬다

수집 대상은 실제 계정으로 로그인된 개인 폰이고, **파괴적 action 을 막는 가드가 없다.**
사용자가 위험을 인지하고 내린 결정이며 미구현 항목이 아니다. 가드를 임의로 넣지 말고 먼저 물어라.
같은 머신의 `emulator-5556` 은 **다른 작업이 점유 중이라 건드리면 안 된다.**

> 대용량 정본 `data/`·`outputs/` 는 gitignore 대상(심볼릭 링크 구조). 산출물 자체는 문서에 복사하지 말고 경로·요약만 기록한다.
