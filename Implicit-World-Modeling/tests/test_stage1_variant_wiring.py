"""stage1 ablation variant 배선이 (a) 메인 런을 안 건드리고 (b) 실제로 갈라지는지 고정한다.

왜 이 테스트가 필요한가
------------------------
`--stage1-variant` 는 stage1 ablation(예: action-only) 을 지목한다. merge/eval 체인의
경로·HF repo id 중 **하나라도** variant 를 모르면 에러 없이 메인 런의 어댑터를 merge 해
같은 HF repo id 로 push 한다 — HF 에 실재하는 stage1 체크포인트를 되돌릴 수 없게
덮어쓰는 경로다. 그 위험 때문에 `stage1_merge.sh` 는 한동안 `--stage1-variant` 를
통째로 거절하는 임시 가드를 달고 있었고, 이 테스트가 그 가드를 대체한다.

고정하는 것 셋:
  (a) variant 없음 → 경로·repo id 가 **현행 문자열과 바이트 동일** (골든 문자열로 못박는다)
  (b) variant 있음 → 경로·repo id 가 메인 런과 갈라지고 세그먼트를 포함
  (c) `lf_registry` 에 등록된 **모든** stage1 ablation variant 가 위 배선을 통과

왜 셸을 실행하는가 (그리고 왜 source 하지 않는가)
--------------------------------------------------
정본은 셸 함수(`_common.sh`)다. 문자열을 파이썬으로 재구현하면 재구현본끼리만 맞고
실행 경로는 갈릴 수 있으므로 **진짜 함수를 실행**한다. 다만 `_common.sh` 를 통째로
source 하면 conda env·CUDA·dataset_info 가드가 먼저 돌아 테스트 환경을 가린다 —
그래서 필요한 함수/배열만 원문에서 잘라내 최소 하니스에서 실행한다.
(`tests/test_pixel_xy_consistency.py` 가 같은 이유로 셸 원문을 파싱한다.)

"조용한 0" 을 막는 층
----------------------
이 리포는 "매치 0 건 → 빈 집합끼리 비교 → 통과" 유형 사고를 반복해서 겪었다. 그래서
① 함수/배열 추출 실패, ② 하니스 출력이 빈 문자열, ③ 등록된 variant 0 개, ④ long→short
DS 키 브리지 실패 를 전부 **명시적 실패**로 만든다. 합성 스크립트에도 `set -euo pipefail`
을 걸어 배열 추출이 조용히 빈 slug 를 만드는 경로를 막는다.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from implicit_world_modeling.lf_registry import _DATASET_CONFIG  # noqa: E402

_COMMON_SH = _SCRIPTS / "_common.sh"
_STAGE1_MERGE_SH = _SCRIPTS / "stage1_merge.sh"
_STAGE1_EVAL_SH = _SCRIPTS / "stage1_eval.sh"
_STAGE2_MERGE_SH = _SCRIPTS / "stage2_merge.sh"
_STAGE2_EVAL_SH = _SCRIPTS / "stage2_eval.sh"
_STAGE2_TRAIN_SH = _SCRIPTS / "stage2_train.sh"

# 하니스가 BASE_DIR 로 쓰는 고정 경로 (실제 리포 위치와 무관하게 문자열을 못박기 위해).
_FAKE_BASE = "/BASE"

# 하니스에 실어야 하는 _common.sh 의 조각들. 하나라도 못 찾으면 AssertionError.
_NEEDED_FUNCS = (
    "ds_outputs_code",
    "ds_model_suffix",
    "ds_version_suffix",
    "ds_hf_version",
    "stage1_variant_seg",
    "ds_stage1_source",
    "hf_repo_id_stage1",
    "hf_repo_id_stage2_base",
    "hf_repo_id_stage2_world_model",
    "hf_repo_id_stage2_world_model_adapter",
    "local_merged_epoch_dir",
    "resolve_eval_model_path",
)
_NEEDED_ARRAYS = ("HF_SLUG",)


def _extract_func(src: str, name: str, where: str = "_common.sh") -> str:
    m = re.search(rf"^{re.escape(name)}\(\)\s*\{{.*?^\}}", src, re.S | re.M)
    if m is None:
        raise AssertionError(
            f"{where} 에서 {name}() 함수 본문을 찾지 못했다 — 함수가 사라졌거나 "
            "이 테스트의 파서가 낡았다. 어느 쪽이든 variant 배선이 무방비가 되므로 "
            "조용히 통과시키지 않는다."
        )
    return m.group(0)


def _extract_array(src: str, name: str) -> str:
    m = re.search(rf"^declare -A {re.escape(name)}=\(.*?^\)", src, re.S | re.M)
    if m is None:
        raise AssertionError(
            f"_common.sh 에서 `declare -A {name}` 블록을 찾지 못했다 — 추출이 실패하면 "
            "하니스가 빈 slug 로 '정상처럼 보이는' repo id 를 만든다."
        )
    return m.group(0)


def _harness(
    body: str,
    base_dir: str = _FAKE_BASE,
    extra: tuple[tuple[Path, str], ...] = (),
) -> str:
    """추출한 정본 조각 + 호출 본문으로 실행 가능한 bash 스크립트를 만든다.

    ``base_dir`` 는 로컬 hit/miss 를 좌우한다 (실제 디렉토리를 만들어 hit 을 만든다).
    ``extra`` 는 _common.sh 밖의 함수 — (파일, 함수명) 쌍.
    """
    src = _COMMON_SH.read_text()
    parts = [
        "set -euo pipefail",
        f'BASE_DIR="{base_dir}"',
        *(_extract_array(src, a) for a in _NEEDED_ARRAYS),
        *(_extract_func(src, f) for f in _NEEDED_FUNCS),
        *(_extract_func(path.read_text(), name, path.name) for path, name in extra),
        body,
    ]
    return "\n".join(parts) + "\n"


def _run(body: str, **kw) -> list[str]:
    """하니스를 실행하고 stdout 을 줄 리스트로 반환. 빈 출력은 실패로 본다."""
    script = _harness(body, **kw)
    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"셸 하니스가 실패했다 (rc={proc.returncode}):\n"
            f"--- stderr ---\n{proc.stderr}\n--- script ---\n{script}"
        )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        raise AssertionError(
            "셸 하니스가 아무것도 출력하지 않았다 — 빈 문자열끼리 비교해 조용히 "
            f"통과하는 것을 막기 위해 실패시킨다.\n--- script ---\n{script}"
        )
    return lines


def _registered_variants() -> list[str]:
    """lf_registry 에 등록된 stage1 ablation variant 전체 (DS 합집합)."""
    return sorted(
        {
            v
            for cfg in _DATASET_CONFIG.values()
            for v in cfg.get("stage1_extra_variants", {})
        }
    )


def _variant_datasets() -> list[tuple[str, str]]:
    """(셸 DS 키, variant) 쌍. 긴 레지스트리 이름 → 셸 짧은 키 브리지 포함."""
    pairs: list[tuple[str, str]] = []
    for long_name, cfg in _DATASET_CONFIG.items():
        variants = cfg.get("stage1_extra_variants", {})
        if not variants:
            continue
        # 레지스트리는 긴 이름(AndroidControl_EXP08), 셸은 짧은 키(AC_EXP08) 를 쓴다.
        short = long_name.replace("AndroidControl_EXP", "AC_EXP")
        for v in variants:
            pairs.append((short, v))
    return pairs


class TestNoVariantIsByteIdentical(unittest.TestCase):
    """(a) variant 를 안 주면 메인 런 문자열이 한 글자도 안 움직인다.

    골든 문자열은 손으로 못박는다 — 헬퍼를 다시 호출해 비교하면 헬퍼가 함께 바뀔 때
    같이 따라가서 아무것도 지키지 못한다.

    ⚠️ 아래 stage1 repo id 는 **HF 에 실재하는 산출물의 이름**이다
    (`SaFD-00/qwen2.5-vl-3b-ac-exp08-world-model-stage1-full-epoch{0.25 … 3}` 12 개).
    이 값이 바뀌면 기존 체크포인트가 고아가 되고 eval 의 HF fallback 이 전부 404 다 —
    **테스트를 통과시키려고 골든을 갱신하지 마라.** 실패했다면 헬퍼 쪽이 틀린 것이다.
    """

    GOLDEN = [
        # (설명, 호출, 기대 문자열)
        (
            "stage1 repo id (EXP08 메인 런)",
            'hf_repo_id_stage1 qwen2.5-vl-3b AC_EXP08 full 3',
            "SaFD-00/qwen2.5-vl-3b-ac-exp08-world-model-stage1-full-epoch3",
        ),
        (
            "stage1 repo id (분수 epoch)",
            'hf_repo_id_stage1 qwen2.5-vl-3b AC_EXP08 full 0.25',
            "SaFD-00/qwen2.5-vl-3b-ac-exp08-world-model-stage1-full-epoch0.25",
        ),
        (
            "stage1 repo id (EXP01 ratio variant)",
            'hf_repo_id_stage1 qwen3-vl-8b AC_EXP01_ratio37 lora 1',
            "SaFD-00/qwen3-vl-8b-ac-exp01-ratio37-world-model-stage1-lora-epoch1",
        ),
        (
            "stage1 repo id (EXP07 버전 접미사)",
            'hf_repo_id_stage1 qwen2.5-vl-3b AC_EXP07_v1 full 1',
            "SaFD-00/qwen2.5-vl-3b-ac-exp07-world-model-stage1-full-epoch1-v1",
        ),
        (
            "stage1 merged dir (EXP08 메인 런)",
            'local_merged_epoch_dir stage1 qwen2.5-vl-3b AC_EXP08 full 3',
            f"{_FAKE_BASE}/outputs/AndroidControl_EXP08/merged/"
            "qwen2.5-vl-3b_stage1_full_world-model/epoch-3",
        ),
        (
            "stage1 merged dir (EXP01 ratio + EXP07 버전)",
            'local_merged_epoch_dir stage1 qwen2.5-vl-3b AC_EXP07_v1 full 1',
            f"{_FAKE_BASE}/outputs/AndroidControl_EXP07/merged/"
            "qwen2.5-vl-3b_stage1_full_world-model_v1/epoch-1",
        ),
        (
            "stage2 merged dir (world-model 계보)",
            'local_merged_epoch_dir stage2 qwen2.5-vl-3b AC_EXP08 '
            'lora_world-model_from_full-ep2 1',
            f"{_FAKE_BASE}/outputs/AndroidControl_EXP08/merged/"
            "qwen2.5-vl-3b_stage2_lora_world-model_from_full-ep2/epoch-1",
        ),
        (
            "stage2 repo id (world-model 계보)",
            'hf_repo_id_stage2_world_model qwen2.5-vl-3b AC_EXP08 full 2 lora 1',
            "SaFD-00/qwen2.5-vl-3b-ac-exp08-world-model-stage1-full-epoch2-stage2-lora-epoch1",
        ),
        (
            "stage2 repo id (merge X = adapter 계보)",
            'hf_repo_id_stage2_world_model_adapter qwen2.5-vl-3b AC_EXP07_v1 lora 1 lora 1',
            "SaFD-00/qwen2.5-vl-3b-ac-exp07-world-model-stage1-lora-epoch1"
            "-stage2-lora-adapter-epoch1-v1",
        ),
    ]

    def test_golden_strings_unchanged(self):
        # 헬퍼가 개행 없이 printf 하므로 줄 단위로 나오게 감싼다.
        body = "\n".join(f'echo "$({call})"' for _, call, _ in self.GOLDEN)
        got = _run(body)
        self.assertEqual(len(got), len(self.GOLDEN),
                         f"출력 줄 수가 골든 개수와 다르다: {got}")
        for (label, _, want), have in zip(self.GOLDEN, got, strict=True):
            self.assertEqual(have, want, f"{label} 가 움직였다")

    def test_empty_variant_arg_equals_omitted_arg(self):
        """빈 문자열을 명시적으로 넘겨도(셸이 실제로 하는 일) 결과가 같아야 한다."""
        body = (
            'echo "$(hf_repo_id_stage1 qwen2.5-vl-3b AC_EXP08 full 3)"\n'
            'echo "$(hf_repo_id_stage1 qwen2.5-vl-3b AC_EXP08 full 3 "")"\n'
            'echo "$(local_merged_epoch_dir stage1 qwen2.5-vl-3b AC_EXP08 full 3)"\n'
            'echo "$(local_merged_epoch_dir stage1 qwen2.5-vl-3b AC_EXP08 full 3 "")"\n'
        )
        got = _run(body)
        self.assertEqual(got[0], got[1], "hf_repo_id_stage1: 빈 variant 가 문자열을 바꿨다")
        self.assertEqual(got[2], got[3], "local_merged_epoch_dir: 빈 variant 가 경로를 바꿨다")


class TestVariantSplitsEverything(unittest.TestCase):
    """(b)(c) 등록된 모든 variant 가 경로·repo id 를 실제로 가른다."""

    def test_registry_has_variants(self):
        """등록 variant 0 개면 아래 루프가 0 회 돌아 조용히 통과한다 — 먼저 막는다."""
        self.assertTrue(
            _registered_variants(),
            "lf_registry 에 stage1_extra_variants 가 하나도 없다 — 이 테스트가 "
            "아무것도 검사하지 않게 되므로 실패시킨다.",
        )

    def test_short_key_bridge_holds(self):
        """레지스트리 긴 이름 → 셸 짧은 DS 키 브리지가 성립해야 루프가 의미를 갖는다."""
        pairs = _variant_datasets()
        self.assertTrue(pairs, "(DS, variant) 쌍이 하나도 없다")
        src = _COMMON_SH.read_text()
        for short, _ in pairs:
            self.assertRegex(
                src, rf"\[{re.escape(short)}\]=",
                f"셸 DS 키 '{short}' 가 _common.sh 의 매핑에 없다 — 긴 이름→짧은 키 "
                "브리지가 깨지면 아래 검사가 엉뚱한 키로 돈다.",
            )

    def test_every_registered_variant_splits_paths_and_repo_ids(self):
        pairs = _variant_datasets()
        for ds, variant in pairs:
            with self.subTest(ds=ds, variant=variant):
                seg = f"-{variant}"
                body = "\n".join(
                    [
                        f'echo "$(hf_repo_id_stage1 qwen2.5-vl-3b {ds} full 1)"',
                        f'echo "$(hf_repo_id_stage1 qwen2.5-vl-3b {ds} full 1 {variant})"',
                        f'echo "$(local_merged_epoch_dir stage1 qwen2.5-vl-3b {ds} full 1)"',
                        f'echo "$(local_merged_epoch_dir stage1 qwen2.5-vl-3b {ds} full 1 {variant})"',
                        f'echo "$(hf_repo_id_stage2_world_model qwen2.5-vl-3b {ds} full 1 lora 1)"',
                        f'echo "$(hf_repo_id_stage2_world_model qwen2.5-vl-3b {ds} full 1 lora 1 {variant})"',
                        f'echo "$(stage1_variant_seg {variant})"',
                    ]
                )
                got = _run(body)
                s1_main, s1_var, dir_main, dir_var, s2_main, s2_var, got_seg = got

                self.assertEqual(got_seg, seg, "세그먼트 규칙이 -{variant} 가 아니다")
                for label, main, var in (
                    ("stage1 repo id", s1_main, s1_var),
                    ("stage1 merged dir", dir_main, dir_var),
                    ("stage2 repo id", s2_main, s2_var),
                ):
                    self.assertNotEqual(
                        main, var,
                        f"{label}: variant 를 줘도 메인 런과 같은 문자열이 나왔다 — "
                        "이 상태로 merge 하면 메인 런 산출물을 덮어쓴다.",
                    )
                    self.assertIn(seg, var, f"{label} 에 세그먼트가 없다")

                # 명명 규칙: 세그먼트는 world-model 토큰 바로 뒤에 온다.
                self.assertIn(f"world-model{seg}-stage1-", s1_var)
                self.assertIn(f"world-model{seg}-stage1-", s2_var)
                self.assertTrue(
                    dir_var.endswith(f"_world-model{seg}/epoch-1"),
                    f"merged 경로 규칙 위반: {dir_var}",
                )

                # HF repo id 로 안전한 문자만 (하이픈 포함 variant 이름이 들어온다).
                name = s1_var.split("/", 1)[1]
                self.assertRegex(
                    name, r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
                    f"HF repo id 에 쓸 수 없는 문자가 있다: {name}",
                )

    def test_merged_dir_matches_committed_training_output_dir(self):
        """merge 가 읽는 adapters 경로가 학습이 실제로 쓴 output_dir 과 일치하는가.

        merge 는 `world-model{VER}{SEG}` 를 조립하고 학습 YAML 은 gen_configs 가
        `save_s1_{mode}` + `-{variant}` 로 만든다. 둘이 갈리면 **다른 계보의 체크포인트**
        를 merge 한다 — 커밋된 YAML 원문을 정답으로 두고 대조한다.
        """
        yamls = sorted(
            (_ROOT / "configs" / "train").glob(
                "*/stage1_*/*_world-model-*.yaml"
            )
        )
        self.assertTrue(
            yamls,
            "variant 학습 YAML(configs/train/*/stage1_*/*_world-model-*.yaml) 을 "
            "하나도 못 찾았다 — 대조 대상이 없으면 이 검사는 무의미하다.",
        )
        merge_src = _STAGE1_MERGE_SH.read_text()
        m = re.search(r'^\s*TRAIN_DIR_REL="([^"]+)"', merge_src, re.M)
        self.assertIsNotNone(
            m, "stage1_merge.sh 에서 TRAIN_DIR_REL 대입을 찾지 못했다"
        )
        train_dir_expr = m.group(1)

        checked = 0
        for yaml in yamls:
            out_dir = None
            for line in yaml.read_text().splitlines():
                if line.startswith("output_dir:"):
                    out_dir = line.split(":", 1)[1].strip()
                    break
            self.assertIsNotNone(out_dir, f"{yaml} 에 output_dir 이 없다")
            # 파일명 규약: {model}_world-model-{variant}{ver}.yaml
            stem = yaml.stem
            model_short, _, tail = stem.partition("_world-model-")
            variant = tail
            mode = yaml.parent.name.replace("stage1_", "")
            body = (
                f'MODEL_SHORT={model_short}\n'
                f'OUT_DS="$(ds_outputs_code AC_EXP08)"\n'
                f'SFX="$(ds_model_suffix AC_EXP08)"\n'
                f'VER="$(ds_version_suffix AC_EXP08)"\n'
                f'STAGE1_MODE={mode}\n'
                f'VARSEG="$(stage1_variant_seg {variant})"\n'
                f'echo "{train_dir_expr}"\n'
            )
            got = _run(body)[0]
            self.assertEqual(
                got, out_dir,
                f"{yaml.name}: merge 가 읽을 adapters 경로와 학습 output_dir 이 갈렸다",
            )
            checked += 1
        self.assertGreater(checked, 0, "대조한 YAML 이 0 개다")


class TestStage2TrainMergeAgree(unittest.TestCase):
    """stage2_train 이 **쓰는** adapters 경로 == stage2_merge 가 **읽는** adapters 경로.

    이 둘이 갈리면 두 계보(메인 stage1 / ablation stage1)에서 온 stage2 학습이 같은
    디렉토리에 쓰거나, merge 가 없는 디렉토리를 보고 조용히 skip 한다. 양쪽 셸 원문에서
    실제 표현식을 잘라내 같은 값으로 평가한다 (재구현이 아니라 원문 실행).
    """

    S2_YAML = (
        _ROOT / "configs" / "train" / "IWM-AC_EXP08" / "stage2_lora"
        / "qwen2.5-vl-3b_world-model-full.yaml"
    )

    def _train_output_dir(self, variant: str) -> str:
        src = _STAGE2_TRAIN_SH.read_text()
        m = re.search(r"^VARSEG_SED=\(.*\)$", src, re.M)
        self.assertIsNotNone(m, "stage2_train.sh 에서 VARSEG_SED 대입을 찾지 못했다")
        body = "\n".join(
            [
                f'STAGE1_VARIANT="{variant}"',
                'VARSEG="$(stage1_variant_seg "$STAGE1_VARIANT")"',
                m.group(0),
                f'sed -e "s|__STAGE1_EPOCH__|3|g" "${{VARSEG_SED[@]}}" "{self.S2_YAML}" '
                '| grep "^output_dir:" | sed "s/^output_dir: //"',
            ]
        )
        return _run(body)[0]

    def _merge_train_dir_rel(self, variant: str) -> str:
        src = _STAGE2_MERGE_SH.read_text()
        wm = re.search(r'^\s*WM_VARIANT_KEY="([^"]+)"$', src, re.M)
        tdr = re.search(
            r'^\s*TRAIN_DIR_REL="(\.\./outputs/\$\{OUT_DS\}/adapters[^"]+)"$', src, re.M
        )
        self.assertIsNotNone(wm, "stage2_merge.sh 에서 WM_VARIANT_KEY 를 찾지 못했다")
        self.assertIsNotNone(tdr, "stage2_merge.sh 에서 TRAIN_DIR_REL 을 찾지 못했다")
        body = "\n".join(
            [
                'OUT_DS=AndroidControl_EXP08; SFX=""; VER=""',
                "MODEL_SHORT=qwen2.5-vl-3b; STAGE2_MODE=lora; STAGE1_MODE=full; STAGE1_EPOCH=3",
                f'STAGE1_VARIANT="{variant}"',
                'VARSEG="$(stage1_variant_seg "$STAGE1_VARIANT")"',
                f'WM_VARIANT_KEY="{wm.group(1)}"',
                'ADAPTER_SUFFIX="$WM_VARIANT_KEY"',
                f'TRAIN_DIR_REL="{tdr.group(1)}"',
                'echo "$TRAIN_DIR_REL"',
            ]
        )
        return _run(body)[0]

    def test_paths_agree_for_main_and_every_variant(self):
        self.assertTrue(self.S2_YAML.is_file(), f"대조용 stage2 YAML 이 없다: {self.S2_YAML}")
        variants = [""] + _registered_variants()
        for variant in variants:
            with self.subTest(variant=variant or "(메인 런)"):
                trained = self._train_output_dir(variant)
                merged_from = self._merge_train_dir_rel(variant)
                self.assertEqual(
                    trained, merged_from,
                    "stage2_train 이 쓰는 output_dir 과 stage2_merge 가 읽는 adapters "
                    "경로가 갈렸다 — 계보가 섞이거나 merge 가 조용히 skip 한다.",
                )
                if variant:
                    self.assertIn(f"_world-model-{variant}_from_", trained)


class TestStage1BaseHfFallback(unittest.TestCase):
    """`stage2_train.sh::resolve_stage1_base` 의 local-우선 / HF-폴백.

    eval 경로(`resolve_eval_model_path`)는 예전부터 로컬 miss 를 HF repo id 로 폴백했지만
    stage2 **학습** 경로는 하드 실패였다 — 그래서 "기존 3-epoch 런의 중간 체크포인트를
    stage2 base 로 쓰는" 비교(stage1 ep1 vs ep3)가 아예 불가능했다. 여기서 고정하는 것:
      (a) 로컬 hit  → LF cwd 기준 상대경로, 기존 문자열 바이트 불변
      (b) 로컬 miss → `hf_repo_id_stage1` 과 **정확히 같은 문자열** (재구현이 아니라 위임)
      (c) variant   → 양쪽 모두 세그먼트 포함
    """

    EXTRA = ((_STAGE2_TRAIN_SH, "resolve_stage1_base"),)

    def _resolve(self, base_dir: str, epoch: str, variant: str = "") -> str:
        body = "\n".join(
            [
                f'STAGE1_VARIANT="{variant}"',
                'VARSEG="$(stage1_variant_seg "$STAGE1_VARIANT")"',
                f'resolve_stage1_base qwen2.5-vl-3b AC_EXP08 full {epoch} 2>/dev/null',
            ]
        )
        return _run(body, base_dir=base_dir, extra=self.EXTRA)[0]

    def _hub_id(self, epoch: str, variant: str = "") -> str:
        body = f'echo "$(hf_repo_id_stage1 qwen2.5-vl-3b AC_EXP08 full {epoch} {variant})"'
        return _run(body)[0]

    def _with_local_merged(self, variant: str, epoch: str):
        """variant/epoch 에 해당하는 로컬 merged 디렉토리를 만든 임시 BASE_DIR 을 준다."""
        tmp = tempfile.TemporaryDirectory()
        seg = f"-{variant}" if variant else ""
        (
            Path(tmp.name)
            / "outputs" / "AndroidControl_EXP08" / "merged"
            / f"qwen2.5-vl-3b_stage1_full_world-model{seg}" / f"epoch-{epoch}"
        ).mkdir(parents=True)
        return tmp

    def test_local_hit_returns_unchanged_relative_path(self):
        with self._with_local_merged("", "3") as base:
            got = self._resolve(base, "3")
        self.assertEqual(
            got,
            "../outputs/AndroidControl_EXP08/merged/"
            "qwen2.5-vl-3b_stage1_full_world-model/epoch-3",
            "로컬 hit 의 반환 문자열이 움직였다 — 메인 경로(epoch-3)가 바뀌면 "
            "기존 stage2 학습 계보가 다른 base 를 잡는다.",
        )

    def test_local_miss_falls_back_to_exact_hub_id(self):
        with tempfile.TemporaryDirectory() as base:  # merged 디렉토리 없음
            got = self._resolve(base, "1")
        self.assertEqual(
            got, self._hub_id("1"),
            "로컬 miss 폴백이 hf_repo_id_stage1 과 다른 문자열을 냈다 — repo id 조립이 "
            "복제됐다는 뜻이다.",
        )
        self.assertEqual(
            got, "SaFD-00/qwen2.5-vl-3b-ac-exp08-world-model-stage1-full-epoch1",
            "HF 에 실재하는 stage1 epoch1 repo id 와 다르다 (임의로 갱신 금지).",
        )

    def test_variant_axis_on_both_branches(self):
        for variant in _registered_variants():
            with self.subTest(variant=variant):
                seg = f"-{variant}"
                with self._with_local_merged(variant, "3") as base:
                    hit = self._resolve(base, "3", variant)
                with tempfile.TemporaryDirectory() as base:
                    miss = self._resolve(base, "1", variant)
                self.assertTrue(
                    hit.endswith(f"_world-model{seg}/epoch-3"),
                    f"로컬 hit 경로에 세그먼트가 없다: {hit}",
                )
                self.assertEqual(miss, self._hub_id("1", variant))
                self.assertIn(f"world-model{seg}-stage1-", miss)

    def test_delegates_instead_of_reimplementing(self):
        """판정·문자열 조립을 다시 쓰면(복제) 언젠가 eval 경로와 갈린다."""
        src = _STAGE2_TRAIN_SH.read_text()
        body = _extract_func(src, "resolve_stage1_base", _STAGE2_TRAIN_SH.name)
        self.assertIn(
            "resolve_eval_model_path stage1", body,
            "resolve_stage1_base 가 정본 해석기에 위임하지 않는다.",
        )
        # 주석은 규칙을 **설명**하려고 repo id 예시를 담는다 — 코드 라인만 본다.
        code = "\n".join(
            ln for ln in body.splitlines() if not ln.lstrip().startswith("#")
        )
        self.assertNotIn(
            "SaFD-00/", code,
            "resolve_stage1_base 안에서 HF repo id 를 직접 조립하고 있다 — "
            "hf_repo_id_stage1 이 정본이다.",
        )

    def test_hub_id_is_safe_for_the_sed_substitution(self):
        """model_name_or_path 치환은 sed 구분자 `|` 를 쓴다 — repo id 에 `|`/`&`/`\\` 금지."""
        for variant in [""] + _registered_variants():
            with self.subTest(variant=variant or "(메인 런)"):
                hub = self._hub_id("1", variant)
                for bad in ("|", "&", "\\"):
                    self.assertNotIn(
                        bad, hub,
                        f"repo id 에 sed 치환에서 특수문자로 해석되는 '{bad}' 가 있다: {hub}",
                    )


class TestShellWiringPresent(unittest.TestCase):
    """셸 쪽 배선이 사라지면(리팩터링 사고 포함) 실패시킨다."""

    def test_temporary_reject_guard_is_gone(self):
        src = _STAGE1_MERGE_SH.read_text()
        self.assertNotIn(
            "아직 --stage1-variant 를 지원하지 않습니다", src,
            "stage1_merge.sh 의 임시 거절 가드가 남아 있다 — 배선이 끝났으면 "
            "의미 있는 불변식 가드로 대체돼야 한다.",
        )

    def test_merge_scripts_have_invariant_guard(self):
        for path in (_STAGE1_MERGE_SH, _STAGE2_MERGE_SH):
            src = path.read_text()
            self.assertIn(
                "assert_variant_segment()", src,
                f"{path.name} 에 불변식 가드 함수가 없다 — variant 가 경로/repo id 에서 "
                "빠진 채 merge 되는 것을 아무도 막지 않는다.",
            )
            self.assertGreaterEqual(
                len(re.findall(r"assert_variant_segment ", src)), 2,
                f"{path.name} 에서 불변식 가드 호출이 2 회 미만이다 (입력 경로·출력 "
                "경로·repo id 를 모두 검사해야 한다).",
            )

    def test_eval_scripts_thread_variant_into_paths(self):
        """eval 산출 경로에 세그먼트가 안 들어가면 ablation 지표가 메인 leaf 를 덮는다."""
        for path in (_STAGE1_EVAL_SH, _STAGE2_EVAL_SH):
            src = path.read_text()
            self.assertIn(
                'stage1_variant_seg "$STAGE1_VARIANT"', src,
                f"{path.name} 이 VARIANT 경로에 stage1_variant_seg 를 쓰지 않는다.",
            )
            self.assertRegex(
                src, r'resolve_eval_model_path stage1 [^\n]*"\$STAGE1_VARIANT"',
                f"{path.name} 이 resolve_eval_model_path stage1 에 variant 를 넘기지 않는다.",
            )

    def test_stage2_train_injects_segment_into_output_dir(self):
        src = _STAGE2_TRAIN_SH.read_text()
        self.assertIn(
            's|_world-model_from_|_world-model${VARSEG}_from_|', src,
            "stage2_train.sh 이 output_dir 에 stage1 계보 세그먼트를 주입하지 않는다 — "
            "메인 stage1 에서 온 stage2 와 ablation stage1 에서 온 stage2 가 같은 "
            "adapters 디렉토리에 쓴다.",
        )

    def test_parse_args_does_not_hardcode_variant_list(self):
        """허용 목록을 셸에 다시 적으면 새 variant 가 등록돼도 셸만 모른다."""
        src = _COMMON_SH.read_text()
        self.assertIn("_require_known_stage1_variant", src)
        self.assertIn("stage1_extra_variants", src)
        for variant in _registered_variants():
            self.assertNotRegex(
                src, rf"^\s*{re.escape(variant)}\)\s*STAGE1_VARIANT=",
                f"_common.sh 이 variant '{variant}' 를 case 문에 하드코딩했다 — "
                "정본은 lf_registry.stage1_extra_variants 다.",
            )


if __name__ == "__main__":
    unittest.main()
