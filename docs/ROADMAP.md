# Roadmap — 상태

> **이 문서는 상태만 싣는다.** 실험군별로 무엇이 완료·진행·차단인가, 차단이면 **정확히 어디서 막히는가**.
> 수치·표·근거·메커니즘은 전부 [`ARCHITECTURE.md`](../Implicit-World-Modeling/ARCHITECTURE.md) 가 정본이고 여기서는 링크만 한다.
> 실험 **결과 지표**는 Notion `🧪 Experiments` DB 가 정본이다 (메트릭 정의는 [§6 메트릭](../Implicit-World-Modeling/ARCHITECTURE.md#6-메트릭)).
>
> **포맷 규약**: 실험군 8종(EXP01–07·MC)은 아래에서 **동일 포맷**으로 싣는다 — 한 줄 상태(요약 표) → 실험군별 `완료 / 남은 것 / 차단·쟁점` 섹션.

---

## 완료 판정 규칙 — 먼저 읽어라

> ⚠️ **`outputs/` 가 비어 있다고 미착수가 아니다.** 학습 산출물은 HF Hub (`SaFD-00/…`) 에 있고 로컬 `outputs/` 는 머신마다 비어 있을 수 있다. eval 은 `resolve_eval_model_path` 가 **local merged 우선 + HF fallback** 으로 푼다 ([§5](../Implicit-World-Modeling/ARCHITECTURE.md#5-실행-데이터-흐름과-산출물)) — 로컬이 비어도 평가는 돈다.
> **EXP03 이 실제 사례다**: 로컬엔 빈 eval 디렉토리 하나뿐인데 HF 에는 stage1·stage2 산출물이 다 있다. 로컬만 보고 "미착수" 로 판정하면 **틀린다.**

**무엇이 학습됐는지 확인하는 정본 커맨드:**

```bash
# 학습 산출물 (정본)
python -c "from huggingface_hub import HfApi; print(*sorted(m.id for m in HfApi().list_models(author='SaFD-00')), sep='\n')"

# 로컬 캐시 (보조 — 비어 있어도 무의미)
find outputs -name '*_metrics.json'
```

HF slug 규약은 [§3 이름 규약](../Implicit-World-Modeling/ARCHITECTURE.md#이름-규약), repo id 조립은 [§5](../Implicit-World-Modeling/ARCHITECTURE.md#5-실행-데이터-흐름과-산출물).
`ac-2-` slug 는 **구 스키마 사문화분**이다 — 현행 실험군이 아니다 (대응 등록 키 `IWM-AC_2_*` 는 2026-07-25 정본에서 제거됐다) ([§3 LF 등록](../Implicit-World-Modeling/ARCHITECTURE.md#3-데이터와-설정-계약)).

---

## 실험군 상태 — 한 줄 요약

| 실험군 | 상태 | 한 줄 요약 |
|---|---|---|
| **AC_EXP01** | ✅ 완료 (공백 1) | stage1+stage2 완료. **ratio55 만 미학습** |
| **AC_EXP02** | ✅ 완료 | diff loss **v1**. stage1+stage2 완료 |
| **AC_EXP03** | ✅ 완료 (eval 재추론 1건) | stage1+stage2 완료 — **자격 모순 1건 미판정** · **8b state OOD 재추론 대기** |
| **AC_EXP04** | ⛔ **차단** | **3중 차단** — 좌표계 모순 · 재빌드 소스 부재 · 등록 0 키 |
| **AC_EXP05** | ✅ **eval 완주** | `qwen2.5-vl-3b` stage1 full FT + stage2(full·lora world-model·base) **전부 eval 완주** (2026-07-21). **데이터 쟁점 4건 미판정** |
| **AC_EXP06** | 🔄 **merge/업로드** | EXP05 비증강 Stage-2 대조군. `base` variant 완료·업로드, **world-model variant 학습 미착수** |
| **AC_EXP07** | 🧱 **데이터·인프라 완비** | `qwen2.5-vl-3b` 단독 stage1 world-modeling. 등록·YAML·빌더 완비, **0725 실데이터 빌드 완료(누출 0)**, **학습 이력 0** |
| **AC_EXP08** | 🔄 **stage1 3B 완료, 나머지 진행 중** | anti-copy **3-포맷 관측성 분할** stage1. **stage1 3B full FT 12체크포인트 학습·평가 완료**(HF 실재). **7B stage1·stage2(3B/7B 전부)·ablation 2 종 은 학습 이력 0.** stage2 데이터 30K+7버킷 재빌드·eval 셸 배선 완료, ablation(`action-only`·`inverse-mix`) 데이터·variant 관통 배선 완료 |
| **MC** | ⬜ 미착수 | 데이터·등록·YAML 완비, 자격 제한 없음. **프로덕션 코퍼스 아님** |
| **MB** | ⬜ 미사용 | 평가 전용. `on-MB*` 산출물 0 |

---

## ✅ EXP01 — 완료 (공백 1)

**완료**: `qwen3-vl-8b` (ratio37 · ratio73) + `qwen2.5-vl-7b` (ratio73) 로 stage1 LoRA → stage2 LoRA (`base` / `world-model`) 학습·평가 완료. 지표는 Notion `🧪 Experiments` DB 정본.

**남은 것**: `ratio55` 학습 — 평가 기본값이 `--exp01-ratio ratio55` 이니 주의 ([§4 CLI](../Implicit-World-Modeling/ARCHITECTURE.md#4-파이프라인-컴포넌트)).

**차단·쟁점**: ⚠️ **`ratio55` 는 학습된 적이 없다** (HF·로컬 모두 산출물 0). ratio sweep 3종 중 하나가 비어 있으므로 **"ratio 매트릭스 완주" 는 아직 거짓이다.**

---

## ✅ EXP02 — 완료 (diff loss v1)

**완료**: diff loss **v1**. `qwen3-vl-8b` · `qwen2.5-vl-7b` stage1 LoRA → stage2 LoRA 완료.

**남은 것**: 없음. EXP02 재실행이 필요해지면 그때 판단한다 (v1 동결 주의).

**차단·쟁점**: v1 은 EXP02 재현성 때문에 **의도적으로 동결**돼 있다 — 경계 비대칭 버그도 고치지 않는다. **v1 4파일 삭제 금지** ([§3 함정 10](../Implicit-World-Modeling/ARCHITECTURE.md#3-데이터와-설정-계약)).

---

## ✅ EXP03 — 완료 (자격 모순 1건 미판정)

**완료**: `qwen3-vl-8b` · `qwen2.5-vl-7b` stage1 LoRA → stage2 LoRA 완료 (산출물은 HF. 로컬 `outputs/` 는 비어 있다 — 위 완료 판정 규칙 참조).

**남은 것**: 학습은 없음 (기존 HF 산출물의 **평가는 되고 재학습만 막힌다** — 아래 자격 모순). 다만 **state OOD 재추론 1 건이 대기 중이다** — `qwen3-vl-8b` stage1 `lora_world-model/epoch-3` 의 state OOD 예측이 **절단(1024)** 이다 (ID 는 2026-08-11 재추론분으로 정상, OOD 만 2026-07-27 옛 파일이 남았고 재추론이 중단됐다 — 판정은 split 별로 갈린다: [§6 메트릭](../Implicit-World-Modeling/ARCHITECTURE.md#6-메트릭)). GPU 배정 결정이라 사용자 몫.

**차단·쟁점**: ⚠️ **`qwen2.5-vl-7b` × EXP03 자격 모순** — HF 에 as-trained `ac-exp03-` 산출물이 있는데 현행 `eligible_models('AndroidControl_EXP03')` 는 Qwen3-VL 계열만 허용한다 → `require_model_eligible()` 이 **재학습을 막는다** (학습 당시엔 없던 가드). 열린 판정 2 참조. 커밋 YAML 은 재구성본이다 (아래 재현성 경고).

---

## ⛔ EXP04 — 차단 (3중)

**완료**: 데이터 파일과 stage1 YAML 은 디스크에 있다. **있다는 사실이 돌아간다는 뜻이 아니다** — `require_dataset_registered` 가 `llamafactory-cli` 진입 전에 죽인다 ([§7 함정 20](../Implicit-World-Modeling/ARCHITECTURE.md#7-중요한-운영-제약)).

**남은 것**: 선결 순서대로 — **좌표 규약 확정 → (원천 확보 후) 재빌드 → dataset_info 등록.** Stage 2 는 `_STAGE1_ONLY` 라 애초에 대상 아님. HF 에 EXP04 산출물 0.

**차단·쟁점**: 3중 차단 — **순서대로** 풀어야 한다:

1. **좌표계 모순 (선결)** — 디스크의 EXP04 데이터가 문서 전제(0–1000 정규화)를 **만족하지 않는다**. **버그인지 의도인지 아직 판정되지 않았다** — 어느 쪽으로도 단정하지 말 것. 이게 안 풀리면 아래 둘을 풀어도 **틀린 좌표계를 등록하게 된다**. 실측·근거는 [§2 EXP04 경고 블록](../Implicit-World-Modeling/ARCHITECTURE.md#2-모델-설정).
2. **재빌드 불가** — `mirror_experiment.py --experiment exp04` 의 원천 `data/AndroidControl/EXP04_stage1_{action,state}.jsonl` 이 **디스크에 없다** → **원천 확보가 물리적 선결**.
3. **등록 0 키** — `configs/lf_dataset/dataset_info.json` 에 `IWM-AC_EXP04_*` 키가 없다. 가드는 YAML 유무가 아니라 **등록 여부**를 본다 ([§3 함정 14](../Implicit-World-Modeling/ARCHITECTURE.md#3-데이터와-설정-계약)).

```bash
# 등록 상태 재확인 (빈 목록이면 여전히 차단)
python -c "import json;d=json.load(open('configs/lf_dataset/dataset_info.json'));print(sorted(k for k in d if 'EXP04' in k))"
```

---

## ✅ EXP05 — eval 완주 (`qwen2.5-vl-3b`)

절대 픽셀 좌표 실험군. 자격 밖 모델은 **코드 가드가 막는다** — 매트릭스는 [§2 자격 매트릭스 · 함정 3](../Implicit-World-Modeling/ARCHITECTURE.md#2-모델-설정).

**완료**:
- **데이터 빌드** — 0711 수정본 + diff loss **v2** 가중, 등록 완료. 빌드 정본 [`scripts/build_exp05_data.py`](../Implicit-World-Modeling/scripts/build_exp05_data.py).
- **stage1 full FT eval 완주** — state F1 ep2 정점 **0.5963** / action ep3 **0.7292** (A100×2 에서 학습, base→ep 추이 확인). **현행 실험군 최초의 완주 full FT 경로**다 (나머지 완료 실험군은 전부 LoRA).
- **Stage 2 도입 (2026-07-15)** — drive stage2 3 jsonl(train 15000 / test_id 3000 / test_ood 3000)을 저장소 이미지 경로 관례로 변환(`myset/images/…` → `AndroidControl/images/episode_<6자리>_step_<S>.jpg`, `home.png` → `home.jpg`)해 3키 등록 + `_STAGE1_ONLY` 에서 제거 + stage2 YAML **12개** 생성. 빌드 정본 [`scripts/build_exp05_stage2_data.py`](../Implicit-World-Modeling/scripts/build_exp05_stage2_data.py).
- **stage2 full·lora world-model + base 전부 eval 완주** (2026-07-21, RTX5090×2). 재채점 `step_acc`: **full-wm ep3 0.6950** / lora-wm ep3 0.6570 / lora-base ep3 0.6627 / base 0.4053.
- ⚠️ **terminate 채점 버그 수정** (`_action_eval.py`, 커밋 `dd17426`) — GT `terminate` 가 xy no-field 채점서 누락돼 stage2 18.6% 오채점 → 재추론 없이 재채점.

**남은 것**: 데이터 쟁점 4건 판정 후 산출물 유효성 재확인. `qwen2.5-vl-7b` stage2 는 **사용자 지시로 스킵** (감시 스크립트 `exp05_guard` 가 7b 학습 진입 직전 kill — 0바이트 로그·GPU idle·프로세스 없음으로 확인, 실제 GPU 연산 없음).

**차단·쟁점**:
- ⚠️ **데이터 쟁점 4건 — 조병웅님 판정 대기 (본실험 전 선결)**: `wait` 액션 전량 퍼지 · train 축소 · action/state 키 대칭 붕괴 · **좌표 범위이탈**(OOD 평가셋 오염). 실측·상세는 [§3 EXP05 데이터 쟁점](../Implicit-World-Modeling/ARCHITECTURE.md#3-데이터와-설정-계약).
- 분석: [`.claude/analysis/2026-07-21_00-32-13/`](../.claude/analysis/2026-07-21_00-32-13/README.md) — **동일 예산(LoRA)서 world modeling 사전학습 이득 미관찰** (single-run).

---

## 🔄 EXP06 — merge/업로드 (world-model variant 학습 미착수)

EXP05 의 **비증강(증강 X) Stage-2 대조군**. 좌표/budget/`--coord-mode xy` 규약을 EXP05 에서 승계하며 모델 자격도 EXP05 와 동일하게 **Qwen2.5-VL 계열 전용**이다 ([§2 자격 매트릭스 각주](../Implicit-World-Modeling/ARCHITECTURE.md#2-모델-설정)).

**완료**:
- **마이그레이션** (2026-07-18) — `AC_NOTAUG` 에서 표준 네이밍으로.
- **lf_registry 등록 완료** (2026-07-20) — `DATASET_MODEL_ELIGIBILITY`/`_STAGE2_ONLY`/`_LONG_CUTOFF_DS`/half-batch 편입. stage1 계보는 `stage1_hf_slug: "ac-exp05-"` override + 셸 `ds_stage1_source()` 로 **EXP05 stage1 체크포인트를 그대로 승계** (stage2 산출물 네이밍은 `ac-exp06-` 유지).
- **stage2 YAML 12종** — EXP05 stage2 매트릭스 완전 미러 (base/world-model-full/world-model-lora × `qwen2.5-vl-{3b,7b}` × stage2 {full,lora}).
- **`base` variant** — 학습 완료 (`qwen2.5-vl-3b` LoRA base ep1/2/3) · merged 3에폭 HF 업로드 (`SaFD-00/…ac-exp06-…epoch{1,2,3}`).

**남은 것**: **world-model variant (EXP05 stage1 full/lora 계승) 학습·평가 실행** — YAML 만 있고 학습 이력 0. `scripts/stage2_train.sh` 가 `ds_stage1_source` 로 EXP05 local merged 를 base 로 삼는다. eval 보류 (데이터 준비됨, 각 test 3000).

**차단·쟁점**: 없음 — 배선 완비, 실행만 남았다.

---

## 🧱 EXP07 — 데이터·인프라 완비 (학습 미착수)

`qwen2.5-vl-3b` **단독** stage1 world-modeling 실험군. 자격 밖 모델은 EXP05 와 동일하게 코드 가드가 막는다 ([§2 자격 매트릭스](../Implicit-World-Modeling/ARCHITECTURE.md#2-모델-설정)).

**스펙 요약**: 3B 전용 · **stage1** 1ep · `save_steps 0.25` (fractional 체크포인트 라벨 0.25/0.5/0.75/1) · train **50K** = state 40K + downstream 10K(이미지 제거) · LoRA **64/128** · diff v2 **1:0.2** (ADDED 1.0 / MODIFIED 1.0 / UNCHANGED 0.2, state 분량에 인라인). **stage2** 3ep · train **15K** · **merge O/X** 둘 다 지원 · rank 64 · thought 유사도 메트릭. 수치 정본은 [§2 하이퍼파라미터](../Implicit-World-Modeling/ARCHITECTURE.md#2-모델-설정) · [§6 메트릭](../Implicit-World-Modeling/ARCHITECTURE.md#6-메트릭), 데이터 계약은 [§3](../Implicit-World-Modeling/ARCHITECTURE.md#3-데이터와-설정-계약).

**완료 (인프라 + 데이터)**:
- **등록** — 자격 `qwen2.5-vl-3b` 단독, `dataset_info` **8키 전부 자체 경로**(stage1 train + stage1 test_{id,ood}_{state,action} + stage2 train/test_id/test_ood — EXP05 포인터 없음), `_LONG_CUTOFF_DS` 편입, stage2 **merge X 변형(`world-model-adapter`)** 을 EXP07 한정 opt-in.
- **YAML 9종** — stage1 {full,lora} 2 + stage2_full 3 + stage2_lora 4. `gen_configs --check` 통과.
- **데이터 빌드 (2026-07-25 실데이터)** — 빌드 정본 [`scripts/build_exp07_data.py`](../Implicit-World-Modeling/scripts/build_exp07_data.py). 원천은 0725 myset 필터링본 3 파일(`data/AndroidControl/EXP07_{stage1_state,stage2,open_aug}.jsonl` — 2026-07-26 에 `AndroidControl_EXP07_src/` 에서 공유 원본 디렉토리로 이동·개명)이고 **EXP05 파생이 아니다**. train 2(stage1 50K / stage2 15K) + **자체 test 6종** 전부 실파일 — EXP05 심링크·포인터는 폐기됐다. **누출 0**: EXP05 test 의 `(episode, step)` 키를 재현해 test 를 굽고 그 union 을 train 두 풀에서 전량 제외 → `train ∩ test = 0` (빌더 `verify()` 가 fail-closed 검사). 두 downstream 풀(stage1 10K / stage2 15K)도 **비중복**. 행수·분포는 sidecar 에서 읽는다: `cat data/AndroidControl_EXP07/stage1_train.jsonl.meta.json`.
- **thought 유사도 메트릭 배선** — `stage2_eval.sh` 가 action 채점 직후 자동 hook 으로 산출 ([§6](../Implicit-World-Modeling/ARCHITECTURE.md#6-메트릭)).

**남은 것**: **학습 실행** — 데이터·인프라 완비, **학습 이력 0**. 2026-07-26 에 `DRY_RUN=1` 로 stage1(full/lora)·stage2(base/world-model/adapter)·merge 경로를 관통 검증했고, 채점기 3종(hungarian `--match-mode pos` / action `--coord-mode xy` / thought)은 EXP07 test 실데이터 300 쌍 **오라클**(GT 를 예측으로 그대로 투입)로 **실행 검증**(parse 100%, 전 지표 1.0)했다 — 검증된 것은 채점 로직 절반뿐이고, 실제 예측을 만드는 `vllm_infer.py` 는 GPU 를 요구해 **미실행**이다. 남은 것은 GPU 연산(학습 + vLLM 추론) 전부다.

**차단·쟁점**:
- **로컬 GPU 가 없다 (2026-07-26 현재, 학습을 막는 유일한 요인)**: RTX 5090 2 장을 **다른 사용자(`byeongung.cho`)의 sglang 서버 2 개**가 상시 점유 중이라 (PID 7318·7328, 33 시간 가동, 카드당 여유 **3.2/3.1 GiB**) qwen2.5-vl-3b LoRA 조차 올라가지 않는다 — `cutoff_len 24576` × vocab 151936 의 lm_head logits 만 bf16 로 ~7.5 GiB 다. ZeRO-3·CPU offload 로도 못 줄이고 `SMOKE=1` 은 `max_samples/max_steps` 만 줄여 cutoff 를 그대로 두므로 역시 OOM 이다. **남의 프로세스를 죽이지 말 것** — 카드가 비기를 기다리거나(협의) 원격으로 가야 한다.
- 재빌드는 `python scripts/build_exp07_data.py --seed 7` 로 한다 — `--source-dir` 기본값이 **공유 원본 디렉토리 `data/AndroidControl/`** 이라 보통 넘길 필요가 없다 (`W_UNCHANGED=0.2`·metric v2 는 빌더 불변식으로 고정). 소스가 바뀌면 test 도 함께 다시 구워지므로 **누출 0 불변식은 빌더가 매번 재검사**한다. **`--revision` 을 반드시 고정하라** (sidecar 의 `revision_resolved`, 현재 `66285546…`) — 기본값 `None` 은 Hub HEAD 를 다시 해석하므로 토크나이저가 바뀌면 `token_weights` 가 조용히 달라진다.
- ⚠️ **다음 재빌드에서 sidecar 는 바뀐다 — 손상이 아니다.** train/test 8 파일은 2026-07-26 에 bit-identical 재현성이 증명됐지만, sidecar 의 `exp07_sampling.source_dir` 에는 **빌드 당시 경로**가 박힌다. 디스크의 현 sidecar 는 아직 이동 전 경로(`AndroidControl_EXP07_src`)를 담고 있으므로, 재빌드하면 그 문자열 하나가 갱신된 diff 가 나온다. 파일 내용·md5 와 무관하다.
- ~~thought eval 의존성 (`sentence-transformers`·`sacrebleu`) 미충족~~ → **2026-07-26 해소**. conda env 에 `pip install --no-deps sacrebleu portalocker tabulate colorama` 로 설치(sacrebleu 2.6.0 · portalocker 3.2.0 · colorama 0.4.6, tabulate 는 이미 충족). `pip freeze` 차분 추가 3 줄뿐 — numpy 2.2.6 / torch 2.8.0+cu128 / vLLM 0.11.0 불변. EXP07 test 300 쌍 오라클에서 cosine/rouge_l/**bleu** 모두 산출 확인, conda `pytest tests` 691 passed / 9 skipped (조건부 skip 0). **env 재구성 시에도 반드시 `--no-deps`** — 평범한 `pip install` 은 numpy 를 올려 torch/vLLM 을 깨뜨릴 수 있다.

---

## 🔄 EXP08 — anti-copy 3-포맷 분할 (stage1 3B 완료, 7B·stage2·ablation 미착수)

`qwen2.5-vl-3b` + `qwen2.5-vl-7b` stage1 world-modeling. **EXP07 과의 핵심 차이는 입력 관측성을 세 포맷으로 쪼갠 것**이다 — `full` 25% / `masked` 55% / `dropped` 20%. 모델이 입력 XML 을 그대로 베끼는 국소 최적(실측 복사만으로 토큰 절반 적중)에서 빠져나오게 하려고 **베낄 원본을 물리적으로 제거**한다. 설계 배경은 [`WM_FORMATS.md`](./WM_FORMATS.md), 파이프라인은 [`DIFF_TARGETS.md`](./DIFF_TARGETS.md).

**스펙 요약**: **stage1** 1ep · `save_steps 0.25` · train **50K** = state 40K(3-포맷·가중) + downstream 10K(이미지 **유지**, 균일 1.0) · diff **1 : 0.25** · full/lora YAML 둘 다 · **stage2** train **30K**(2026-08-28 재빌드). test 는 stage1 **자체 4 종**(state ×3 포맷 · action, **ID/OOD 없음**) + stage2 **7 버킷**(app 축 4 · step 축 3). 계약 정본은 [§3 계보](../Implicit-World-Modeling/ARCHITECTURE.md#3-데이터와-설정-계약), 채점 규약은 [§6](../Implicit-World-Modeling/ARCHITECTURE.md#6-메트릭).

**완료 — stage1 3B full FT 학습·평가 (2026-08-28 확인)**:
- **학습 산출물이 HF 에 실재한다** — `SaFD-00/qwen2.5-vl-3b-ac-exp08-world-model-stage1-full-epoch{0.25 … 3}` 12 개, 각 완전 병합본. **로컬 `outputs/.../adapters` 는 비어 있고** eval 은 `resolve_eval_model_path` 의 HF fallback 으로 돌았다 — 위 "완료 판정 규칙" 이 말하는 바로 그 사례다.
- **stage1 eval 완주** — `base` + 12 체크포인트 × leaf **7 종**(action 1 + state 3 포맷 × {기본, `-without-open_app`}). 지표 정본은 Notion `🧪 Experiments` DB.

**완료 — action 채점 버그 수정 + 13 leaf 재채점 (2026-08-28)**:
`_action_eval.py::_bbox_elements` 가 `bounds` 만 읽어 Cerebra `data-bbox` 를 못 봤다. click/long_press 가 **13 개 leaf 전부 `no_bbox` → 오답**이었고 `cond_bbox_acc` 가 전부 `0.0000` 이었다. `--xml-schema cerebra` 를 `_action_eval.py` 에 추가하고(기본값 `android` 유지) 재채점한 결과:

| leaf | `step_acc` before → after | `cond_bbox_acc` |
|---|---|---|
| `base` | 0.178 → **0.448** | 0.0000 → 0.6683 |
| `full_world-model/epoch-2.76` (최고) | 0.284 → **0.616** | 0.0000 → — |
| `full_world-model/epoch-3` | — | 0.0000 → 0.7222 |

**base 대비 world-model 이득이 +0.106 → +0.168 로 커졌다.** 버그가 WM 의 이득 자체를 과소평가하고 있었다. state 경로는 처음부터 정상이었다(78 leaf 전부 `xml_schema: cerebra` 스탬프). 옛 산출은 `action_metrics.pre_cerebra.json` 으로 보존.

**회귀 가드 — 표본 6개 통과, 전수 아님.** EXP05 stage1 action(base) · EXP05 stage2(base) · EXP06 stage2(base) · EXP07_v1 stage1 action(base) · EXP07_v2 stage1 action(ep1) · EXP07_v1 stage2(base) 를 새 코드로 재채점해 **`xml_schema` 키를 빼면 전 키·값이 기존 파일과 동일**하고 추가된 스탬프가 전부 `"android"` 임을 확인했다. ⚠️ EXP05/06/07 의 action leaf 는 **47개**이고(전부 xy 3섹션 모드라 모두 스탬프 영향권) 그중 **6개만 실제로 재채점했다** — 나머지 41개의 근거는 **구조적 논증**(android 분기 바이트 무변경 · `xml_schema` 기본값 `android` · `ds_xml_schema_flag` 가 EXP08 외 빈 문자열)이지 실측이 아니다. 메커니즘은 [§6 AC_EXP08 채점](../Implicit-World-Modeling/ARCHITECTURE.md#6-메트릭), 규칙은 [AGENTS 하드 제약 15f](../Implicit-World-Modeling/AGENTS.md).

**완료 — stage2 데이터 재빌드 (2026-08-28)**:
빌드 정본 [`scripts/build_exp08_stage2_v2.py`](../Implicit-World-Modeling/scripts/build_exp08_stage2_v2.py). 구 `stage2_train` 은 stage1 의 state 40K 와 step 을 공유하고 있었다("훈련 샘플이 겹치면 안 된다" 위반) → **stage1_train 의 모든 step(state ∪ action)을 제외한 풀**에서 다시 뽑았다.
- **train 15K → 30K.** 구본은 `data/AndroidControl_EXP08/_build/stage2_train.15k.bak.jsonl` 에 백업 (stage2 학습 이력 0 이라 덮어써도 손실 없음).
- **eval 7 버킷** — app 축 `app_{both,s1_only,s2_only,ood}` + step 축 `step_{id_s1,id_s2,ood}`. 앱 파티션은 `episodes_meta.jsonl::primary_app` 기준.
- **구 `stage2_test.jsonl` 폐기** — 등록 키·배선에서 제거했다. 파일 자체는 디스크에 남아 있고 새 7 버킷과 교집합 0 이다.
- 불변식(빌더 `verify()` 가 fail-closed 검사): stage1 산출물 5 파일 **sha256 빌드 전후 불변** · `stage2_train ∩ stage1_train` = 0 · train ∩ 각 버킷 = 0 · 버킷 21 개 쌍 상호 교집합 = 0 · 홀드아웃 앱(`APP_S1_ONLY`·`APP_OOD`) step 이 train 에 0 건 · `images` 전부 `AndroidControl/` 접두.
- 실현 N·앱수·에피소드수·action 분포는 sidecar 에서 읽는다: `cat data/AndroidControl_EXP08/stage2_train.jsonl.meta.json`, 또는 `python scripts/build_exp08_stage2_v2.py --verify-only` 가 표로 출력한다.

**완료 — stage2 eval 셸 배선 (2026-08-28)**:
- `stage2_eval.sh::run_exp08_stage2_eval()` 신설 — `run_variant_epoch_eval_on` 이 EXP08 을 조기 위임하고 MB 단일-파일 분기에서 EXP08 을 뺐다 (`stage1_eval.sh::run_exp08_eval` 과 대칭 구조). leaf 명명은 `on-AC_EXP08-<bucket>` (`_`→`-`).
- 버킷 선택 변수는 **`EVAL_BUCKETS`** 다 — stage1 의 `EVAL_TASKS` 와 **일부러 이름을 다르게 했다.** 값 공간이 겹치지 않아서, 같은 이름을 쓰면 stage1→stage2 를 잇는 래퍼에서 값이 새어 **조용히 엉뚱한 leaf 를 평가**한다.
- dry-run 조립 확인: dataset `IWM-AC_EXP08_stage2_test_app_both` · test `stage2_test_app_both.jsonl` · 채점 `--coord-mode xy --xml-schema cerebra` · cutoff 24576 · `thought_eval` 동반.
- **인라인 xy 목록 제거** — `stage2_eval.sh` 에 복제돼 있던 `[[ "$eval_ds" == "AC_EXP05" || … ]]` 를 `ds_score_mode_flag` 위임으로 바꿔 **셸 복제본이 2벌 → 1벌**이 됐다 ([AGENTS 하드 제약 15g](../Implicit-World-Modeling/AGENTS.md)). 이 교체로 `tests/test_pixel_xy_consistency.py` 의 파서가 형태를 못 알아보고 **실패했고**(가드가 제 역할을 했다), 위임 형태를 인정하도록 파서를 확장한 뒤 그 확장이 조용한 통과를 만들지 않는지 두 가지로 재실증했다.

**완료 — stage1 ablation 데이터 (2026-08-28)**:
`data/AndroidControl_EXP08/stage1_train_action_only.jsonl` — 부모 `stage1_train.jsonl` 에서 `fmt` 키가 **없는**(= downstream/action) 레코드를 **라인 단위 그대로** 필터링한 것이다. 재샘플링하지 않아 메인 런이 본 것과 바이트 동일하고, 그래야 "World Modeling 의 순수 이득" 대조군이 성립한다. 빌드 정본 [`scripts/build_exp08_ablation_data.py`](../Implicit-World-Modeling/scripts/build_exp08_ablation_data.py).

**완료 — inverse-mix stage1 ablation 데이터 (2026-09-01)**:
`data/AndroidControl_EXP08/stage1_train_inverse_mix.jsonl` — state 예측 몫의 일부를 **역동역학**(current+next XML → 사이의 action, `MID_ACTION_PREDICTION`)으로 갈아 끼운 대조군이다. 구성비는 **forward 6 : inverse 2 : action 2** 이고 총량은 메인 stage1 과 같다 — forward 는 부모의 3-포맷 비율을 유지한 서브샘플, action 은 부모의 downstream 몫 **그대로**다. 즉 움직인 변수는 "forward 를 무엇으로 대체했는가" 하나다. 빌드 정본 [`scripts/build_exp08_inverse_mix_data.py`](../Implicit-World-Modeling/scripts/build_exp08_inverse_mix_data.py), 원천 `data/AndroidControl/EXP08_stage1_inverse.jsonl`.
- **모든 행이 `task` 키(`forward`/`inverse`/`action`)를 갖는다.** 기존 "`fmt` 키 부재 ⇒ action" 규약은 inverse 가 같은 파일에 들어오는 순간 **에러 없이** 깨진다(inverse 를 action 으로 읽는다) — 그래서 명시 키를 도입했다. `build_exp08_ablation_data.py` 의 `fmt` 기반 필터는 부모 파일만 보므로 영향받지 않는다.
- **불변식(빌더 `verify()` 가 fail-closed)**: `task` 를 벗기면 부모 라인과 **바이트 동일** · test 12 파일에 등장하는 **에피소드 통째 제외**(step 단위 제외로는 누출이 남는다) · inverse 앱을 **`APP_BOTH ∪ APP_S1_ONLY`** 로 한정(`APP_OOD`/`APP_S2_ONLY` 가 stage1 에 들어가면 "OOD 앱은 stage1 train 에 한 번도 등장하지 않는다" 가 무효가 된다 — 앱 축 eval 전체가 여기 기대고 있다) · stage1 5 파일 sha256 불변 · images 전수 해석.
- ⚠️ **비교 해석 시 교란 요인** — inverse 의 action 타입은 워터필링으로 **균등**(희소 타입은 재고 전량)인데 forward 의 given-action 은 click 편중이다. "inverse 추가 효과" 와 "action 분포 변화 효과" 가 섞여 있다. 양쪽 실현 분포는 sidecar `forward.given_action_realized` / `inverse.action_balance` 에 있다.
- 실현 N·분포·드롭 사유는 세지 말고 확인 커맨드를 쓴다: `python scripts/build_exp08_inverse_mix_data.py --verify-only`.

**완료 (2026-08-22, 데이터·인프라)**:
- **원천 배치** — `data/AndroidControl/EXP08_{stage1_state,stage2}.jsonl`. 이미지는 **새로 받을 필요가 없었다**: 소스의 `myset/images/episode_{N}_...` 는 zero-pad 만 다를 뿐 공용 `AndroidControl/images/` 와 **전수 매핑**된다. 즉 AndroidControl 을 Cerebra 파서로 다시 뽑은 것이고 AC 계보가 맞다.
- **diff loss v2c** — `scripts/diff_loss/{hungarian_metric,hungarian_diff,token_weight_builder}_v2c.py`. v2 는 **덮지 않았다** (하드 제약 9·15e). Cerebra 스키마(`data-bbox`/`aria-label`) 지원 + **구조축 `div` 채택** + **컨테이너는 여는-태그만 가중**.
- **stage1 데이터 빌드** — `scripts/build_exp08_data.py --seed 8`. 길이 필터 → 95% 복사 필터 → 층화 표본 → 3-포맷 분할(skip 0) → C1~C11 전수 통과 → 헝가리안 diff **fallback 0** → `token_weights` 부착. 행수·분포·drop 수는 sidecar: `cat data/AndroidControl_EXP08/stage1_train.jsonl.meta.json`.
- **시각 감사** — `scripts/diff_loss/weight_site.py` → `outputs/AndroidControl_EXP08/_weight_site/index.html` (조병웅님 요청). 왼쪽에 모델이 실제로 본 current state, 오른쪽에 타깃 토큰별 가중치.
  **diff 를 raw 로 계산했다는 증거**(전량): 상향-가중 비율이 dropped p50 `.705` / full `.704` / masked `.702` 로 포맷 간 동일하고 "전부 상향" 샘플이 0.31~0.34% 다. applied(가려진 current)로 계산했다면 dropped 는 1.0 에 몰려야 한다.
- **배선** — `dataset_info`(stage1 train/test + ablation + stage2 train/7 버킷), `lf_registry`(자격 3b·7b), `_common.sh`, `gpu_policy`, `stage1_eval.sh`(EXP08 전용 단일-test helper), `eval_viewer`(stage1 7 leaf + stage2 7 버킷). 키·YAML 개수는 세지 말고 확인 커맨드를 쓴다 — 아래 "등록 상태 재확인".
- **채점기 Cerebra opt-in** — `--xml-schema cerebra` (기본 `android` → 기존 실험군 불변), 프롬프트 파서에 관측성 라벨 머리글 추가, copy 지표를 **`raw_current_state`** 로 계산.

```bash
# 등록 상태 재확인 (파생 수치를 문서에 적지 않는다)
python -c "import json;d=json.load(open('Implicit-World-Modeling/configs/lf_dataset/dataset_info.json'));print(*sorted(k for k in d if 'EXP08' in k),sep='\n')"
python -m implicit_world_modeling.gen_configs --check
python Implicit-World-Modeling/scripts/build_exp08_stage2_v2.py --verify-only
```

**완료 — stage1 ablation variant 관통 배선 (2026-09-01)**:
`--stage1-variant` 는 `stage1_train.sh` 와 YAML 렌더까지만 알던 축이었다. `stage1_merge.sh` 가 그 축을 모른 채 variant 없는 경로를 만들어 **ablation 을 merge 하면 조용히 메인 런 어댑터를 merge 해 같은 HF repo id 로 push** 했고(기존 stage1 체크포인트 12 개를 복구 불가로 덮어씀), 그래서 `--stage1-variant` 를 통째로 `exit 2` 로 막는 임시 가드가 있었다. 이제:
- **세그먼트 규칙 정본은 `_common.sh::stage1_variant_seg` 한 곳** — `-{variant}` 를 `world-model` 토큰 바로 뒤에 붙인다. `local_merged_epoch_dir` · `hf_repo_id_stage1` · `resolve_eval_model_path` · `stage1_eval.sh` · `stage2_{merge,eval}.sh` 가 전부 이걸 쓴다.
- **variant 없음 = 바이트 불변.** 변경 전후 `_common.sh` 를 나란히 실행해 4 개 DS × 경로·repo id 를 대조했고 diff 0 이다 — 메인 런 산출물·HF repo·기존 eval leaf 가 하나도 안 움직인다.
- **임시 가드는 `assert_variant_segment` 로 대체**됐다. variant 가 설정됐는데 adapters 입력·merged 출력·HF repo id 중 하나라도 세그먼트가 없으면 실패한다 — 기능 전체를 거절하던 옛 가드보다 **강하다**(실제 위험 상태만 정확히 막는다).
- **variant 목록은 `stage1_extra_variants` 에서 유도**한다(하드코딩 아님). 그래서 `inverse-mix` 등록 시 **셸을 한 글자도 안 고쳤다**.
- `resolve_stage1_base` 에 **HF fallback** 신설 — `resolve_eval_model_path` 에 위임한다(판정·repo id 조립 복제 0). 로컬엔 `epoch-3` 만 merged 라 그전엔 `--stage1-epoch 1` 이 아예 안 돌았다. 대가는 아래 쟁점 참조.

**epoch1 vs epoch3 비교는 stage1 재학습 없이 된다** (2026-09-01 사용자 확정). `SaFD-00/qwen2.5-vl-3b-ac-exp08-world-model-stage1-full-epoch{1,3}` 이 둘 다 HF 에 실재하므로 stage2 lora 를 `--stage1-epoch {1,3}` 으로 두 번 돌리면 끝난다. ⚠️ cosine LR 이 3-epoch 기준이라 **epoch-1 은 "1 epoch 만 학습한 모델" 이 아니라 "학습 도중" 상태**다 — 해석에서 이 차이를 빼지 마라.

**남은 것**:
1. **7B stage1 학습** — 자격은 있으나 산출물 0.
2. **stage2 학습 (3B·7B 전부)** — 데이터 30K 와 eval 배선은 준비됐고 **학습 이력 0**.
3. **ablation 2 종 학습** — `action-only` · `inverse-mix` 둘 다 데이터·YAML·배선은 끝났고 **학습 이력 0**.

**차단·쟁점**:
- ⚠️ **app 축은 "본다 = action 지도학습" 이라는 정의 위에 서 있다.** stage1 **state** 가 소스 822 앱 중 818 을 이미 봤고 어느 쪽으로도 완전 미관측인 앱은 **4 개(20 step)** 뿐이다. 그래서 `app_ood` 를 "stage1 이 전혀 못 본 앱" 으로 정의하면 표본이 성립하지 않아, **action 지도를 받지 않은 앱**으로 정의하고 각 레코드에 `s1_state_seen` 플래그를 달았다. `app_s2_only`(24 앱 / 49 에피소드) 와 `app_ood`(44 앱 / 76 에피소드) 는 **재고 물리 상한**이라 목표 500 을 못 채운다 — **유효 표본은 행수보다 훨씬 작다** (앱·에피소드 단위로 세라). 늘리려면 stage1 재학습이 필요한데 **사용자가 stage1 재학습을 금지**했다.
- ⚠️ **app 축이 stage1 state 노출과 교락(confound)돼 있다.** `s1_state_seen` 비율이 `app_both` 89.6% / `app_s1_only` 55.8% / `app_s2_only` 58.0% / `app_ood` 47.1% 로 앱 그룹마다 다르다. 앱 그룹 간 차이를 그대로 "action 지도 효과" 로 읽으면 state 노출 효과가 섞인다 — **`s1_state_seen` 으로 층화해서 읽어라.** 버킷별 실측 비율은 sidecar `buckets.*.s1_state_seen`.
- ⚠️ **버킷 비교는 `macro_step_accuracy` 로 하라.** action 타입 mix 가 버킷마다 다르다 — `step_id_s1` 은 **terminate 0%** 인데 `step_id_s2`·`step_ood` 는 20% 수준이다. 구조적 이유가 있다: stage1 state 소스는 "다음 화면" 이 있어야 하므로 종단(terminate) step 을 담지 못하고, 그래서 "state 가 본 step" 집합에는 terminate 가 원천적으로 없다. terminate 는 좌표·텍스트 없이 type 만 맞으면 정답이라 쉬운 축이고, `step_accuracy` 원값 격차의 상당 부분이 **난이도 차가 아니라 mix 효과**다.
- ⚠️ **`stage2_train` 30K 의 terminate 비중이 27.3% 다** (구 15K 는 14.9%). 위와 같은 원인 — stage1 step 을 제외하고 남은 풀 자체가 terminate 쪽으로 기울어 있고, 층화 샘플러가 그 marginal 을 보존한다. **사용자가 인위적 재층화를 거절하고 이 분포를 택했다** (2026-08-28). 따라서 구 15K 와 새 30K 를 비교하면 **규모와 분포가 함께 바뀐 이중 변수**다 — 단독 비교로 결론을 내지 마라.
- ⚠️ **`--stage1-epoch` 오타는 이제 학습 진입 전에 안 걸린다** (2026-09-01 HF fallback 신설의 대가). 예전엔 `resolve_stage1_base` 가 로컬 merged 없으면 `[!] Missing Stage 1 merged dir` 로 죽었지만, 지금은 존재하지 않는 HF repo id 를 반환하고 실패가 `llamafactory-cli train` 안의 **HF 404** 로 — GPU·DeepSpeed 초기화를 **다 하고 나서** 나타난다. eval 경로가 예전부터 지던 트레이드오프를 train 경로도 지게 된 것이다. 돌리기 전에 `DRY_RUN=1` 로 해석된 id 를 눈으로 확인해라.
- ⚠️ **`rebuild_eval_metrics.sh` 는 EXP08 을 커버하지 않는다** (기존 공백). leaf 를 `on-*-state` / `on-*-state-without-open_app` / `on-*-action` glob 으로 찾는데 EXP08 leaf 는 `on-AC_EXP08-state-full` 처럼 **포맷 접미사**가 붙고 stage2 는 `on-AC_EXP08-app-both` 형태라 어느 패턴에도 안 걸린다. EXP08 지표를 백필해야 하면 이 스크립트를 먼저 고쳐야 한다.
- **로컬 GPU 없음** — RTX 5090 2 장이 다른 사용자 서빙으로 상시 점유. EXP07 과 같은 상황이고, stage1 3B 는 이 때문에 원격에서 돌았다.
- **원격 제출은 여전히 UNVALIDATED** — `configs/remote/run.template.yaml` 은 `--dataset AC_EXP05 --stage1-mode full` 이 **하드코딩**돼 있다.
- **중간 체크포인트 → 로컬 eval 접착 스크립트는 없다.** 협업자가 요청한 "0.25 epoch 마다 내려받아 연구실 서버에서 validation eval" 은 리포에 없다. 체크포인트를 손으로 내려받은 뒤 `stage1_eval.sh --train-dataset AC_EXP08 --eval-datasets AC_EXP08` 로 7 leaf 를 도는 것은 배선돼 있다.
- **가중치 0.25 는 지난 실험(EXP07 v2 = 0.05)과 다르다** — 사용자 확정 사항이다. 포맷 분할이라는 새 변수가 이미 들어갔으므로 원인 분리 시 이 차이를 함께 고려해야 한다.
- **헝가리안 매칭 임계값은 EXP07 값을 그대로 승계했고 육안 검증을 하지 않았다.** [`WM_FORMATS.md`](./WM_FORMATS.md) §4.4 는 이 임계값을 "최대 리스크" 로 지목하며 무작위 30 샘플 육안 확인을 요구한다. 현행 값(`MATCH_THRESHOLD=1.7`, `UNCHANGED_COST_THRESHOLD=0.05`)은 `build_diff_targets.py` 에 CLI 로 노출돼 있지도 않다 — 변수 최소화 차원의 승계이지 검증된 값이 아니다.
- **초기 stage1 빌드는 `--revision` 핀 없이 돌았다.** 로컬 캐시 스냅샷이 `66285546…` 하나뿐이라 실제로 그 SHA 로 해석됐고(그래서 이제 빌더 기본값이다), 다만 그 빌드의 sidecar `diff_targets_meta.revision_resolved` 는 `null` 이다. **stage1 은 재빌드하지 마라** — 학습·평가가 끝난 불가침 자산이다(하드 제약 15d).

---

## ⬜ MC — 미착수 (차단 아님)

**완료**: 배선·정적 관통 확인 (jsonl 스키마 + 이미지 경로 해석). 레지스트리·`dataset_info`·`configs/train/IWM-MC/`·`split_data.py --dataset MC` 전부 존재.

**남은 것**: 원격 GPU 박스에서 `stage1_train.sh --dataset MC` 를 실제로 한 번 실행 (핸드오프만, **사용자 결정 2026-07-14 로 보류**). `--stage1-ratio` 기본값 0.95 를 164 행에 적용하면 test 가 9개뿐이라 실제 학습 전 비율을 다시 정해야 한다.

**차단·쟁점**: 현재 `data/MonkeyCollection/` 는 **프로덕션 코퍼스가 아니다** (Monkey-Collector 실험 잔여물 164 examples, 교차-앱 병합 오염). **이 데이터로 낸 학습 결과를 코퍼스 품질의 근거로 쓰지 마라** ([§3 MC 데이터 상태](../Implicit-World-Modeling/ARCHITECTURE.md#3-데이터와-설정-계약)).

---

## 열린 판정 (착수 전에 사람이 결정해야 하는 것)

1. **EXP04 좌표계 — 버그인가 스펙인가.** EXP04 차단 해제의 선결 조건. → [§2 경고 블록](../Implicit-World-Modeling/ARCHITECTURE.md#2-모델-설정)
2. **`qwen2.5-vl-7b` × EXP03 자격 모순** — HF 에 **as-trained `ac-exp03-` 산출물이 있는데**, 현행 `eligible_models('AndroidControl_EXP03')` 는 Qwen3-VL 계열만 허용한다 → `require_model_eligible()` 이 **재현을 막는다**. 학습 당시엔 없던 가드다.
   가드는 **학습 entry 에만** 걸리므로 (eval 은 검사하지 않는다) 기존 HF 산출물의 **평가는 되고 재학습만 막힌다**.
   판정 필요: 그 산출물을 (a) 좌표 규약 불일치로 폐기할지, (b) 자격을 넓힐지. **어느 쪽도 아직 정해지지 않았다** — 성급히 "깨진 모델" 로 단정하지 말 것. 메커니즘은 [§2 좌표 규약 · 자격 매트릭스](../Implicit-World-Modeling/ARCHITECTURE.md#2-모델-설정).
   ```bash
   python -c "from implicit_world_modeling.lf_registry import eligible_models as e; print(e('AndroidControl_EXP03'))"
   ```
3. **EXP05 데이터 쟁점 4건** — 조병웅님. → [§3](../Implicit-World-Modeling/ARCHITECTURE.md#3-데이터와-설정-계약)
4. **EXP05 7:3 분할 비율** — 교수님 최종 확인.
5. **`without_open_app` 필터가 전 실험군에서 무동작** (2026-07-13 실측) — `_hungarian_eval.py::_gt_action_type` 이 GT 의 action type 을 뽑지 못해 **항상 `None`** 을 돌려주고, 그 결과 `--exclude-action open_app` 이 **0 행을 drop** 한다 (EXP01 state test 3,000 행 전수: non-None 0건). 산출되는 `on-{DS}-without-open_app/` 메트릭은 **정규 메트릭과 동일한 수치**라 "open_app 제외 성능" 으로 읽으면 거짓 결론이 된다. **EXP05 의 `Action:` 마커만 고치는 것으로는 해결되지 않는다** — EXP01–EXP04 도 똑같이 0 행 drop 이다. 채점 규약을 학습·평가가 공유하므로 수정 전 확인 필요.
6. **`extract_elements` 의 aria-label 누락** — 포함 조건이 `description` 단독이라 `<div aria-label="...">` 류가 element 집합에서 빠진다. 포함시키면 element 집합이 커져 **pos 메트릭 값이 바뀐다**(= 채점 기준 변경) → 확인 필요. 근거 주석은 `scripts/_hungarian_eval.py` 의 `is_described` 위에 있다.

---

## 미착수 (차단 아님 — 그냥 안 돌렸다)

- **`qwen3-vl-4b`** — 2026-07-13 레지스트리 복원으로 EXP01–EXP04 **자격만** 생겼다. **학습·평가 이력 0.** 위 EXP01–03 완료 표시는 전부 `qwen3-vl-8b`·`qwen2.5-vl-7b` 기준이다. EXP05/06/07 은 자격 밖 ([§2 모델 레지스트리](../Implicit-World-Modeling/ARCHITECTURE.md#2-모델-설정)).
- **MB (MobiBench)** — 평가 전용. 등록돼 있으나 `on-MB*` eval 산출물 0.
- **Full FT 경로** — **EXP05 3B stage1 full FT 가 A100×2 에서 완주해 eval 까지 마쳤다** (현행 실험군 최초의 완주 full FT 경로 — 위 EXP05 참조). 로컬 RTX5090 시도는 OOM 으로 죽었다. 나머지 완료 실험군(EXP01–03)은 전부 LoRA 다.

---

## 마일스톤

- [x] 문서 트리오 정비 (README · ARCHITECTURE · AGENTS) + SSoT 재배치
- [x] 2-stage 파이프라인 자동화 (`scripts/stage{1,2}_{train,merge,eval}.sh`)
- [x] 학습 설정 정본화 — YAML 생성기(`gen_configs`) + 커밋된 `dataset_info.json` + 코드 가드(자격·등록)
- [ ] **실험 매트릭스 완주** (모델 × 데이터셋 × {base / stage2 / stage1+stage2}) — EXP04 차단, EXP06 world-model·EXP07 학습 미착수, **EXP08 은 stage1 3B 만 완료(7B·stage2 미착수)**, ratio55·`qwen3-vl-4b`·MC 공백
- [ ] 결과 종합 및 논문화 (AAAI/ICLR 2027 트랙)
- [ ] (추후) Obsidian 동기화 — Vault 있는 환경에서 `/project-sync init` 재실행

---

## 재현성 경고

> ⚠️ **EXP03/EXP04 의 커밋 YAML 은 `# [reconstructed 2026-07-13]` 재구성본이다 — as-trained 가 아니다** (원본 소실). 위 EXP03 "완료" 산출물이 이 YAML 로 학습됐다는 보장이 없다. 상세 [§7 함정 20](../Implicit-World-Modeling/ARCHITECTURE.md#7-중요한-운영-제약).
> 재구성 이후 **실제 학습으로 확인된 경로는 EXP02 3B LoRA 스모크 하나뿐이다.**

```bash
python -m implicit_world_modeling.gen_configs --check   # YAML 정합 (byte 대조 + orphan 검출)
grep -rl reconstructed configs/train                    # 재구성본 식별
```

<!-- project-sync: task/계획 진척 시 - [ ] / - [x] 상태와 항목만 갱신. -->
