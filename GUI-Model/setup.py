from setuptools import setup


# 설치는 백엔드별로 분리된 conda env 에서 두 단계로 한다.
# 서브프로젝트 (`./LlamaFactory`, `./unsloth`) 의 transitive 상한
# (LF `transformers<=5.2.0` / Unsloth `transformers<=5.5.0`) 이 우리 extras 의
# `transformers==5.5.4` / `>=5.5.4` 와 충돌하므로 pip resolver 한 번으로는 해결되지 않는다.
# 따라서 서브프로젝트는 `--no-deps` 로 먼저 editable 설치한 뒤, 루트 extras 를 올린다.
#
#   conda activate gui-model-llamafactory
#   PIP_USER=0 pip install --no-user -e ./LlamaFactory --no-deps
#   PIP_USER=0 pip install --no-user -e '.[llamafactory]'
#
#   conda activate gui-model-unsloth
#   PIP_USER=0 pip install --no-user -e './unsloth[huggingface,triton]' --no-deps
#   # stage{1,2}_eval.sh 는 unsloth env 에서도 LlamaFactory `scripts/vllm_infer.py`
#   # 를 호출한다 (`from llamafactory.data import ...`). 평가 runtime 을 위해
#   # LlamaFactory 서브프로젝트도 `--no-deps` 로 editable 설치한다.
#   PIP_USER=0 pip install --no-user -e ./LlamaFactory --no-deps
#   PIP_USER=0 pip install --no-user -e '.[unsloth]'
#
# 서브프로젝트의 `pyproject.toml` 은 수정하지 않는다 — 업스트림 sync 를 깨뜨린다.
#
# PIP_USER=0 / --no-user 는 root + PYTHONUSERBASE 조합에서 pip 가 deps 를
# user-site (/root/.local/workspace/python-packages) 로 흘려 env/bin 에 CLI
# entry point (accelerate 등) 가 만들어지지 않는 사고를 막는다.

# 공통 (어떤 env 에서든 필요한 metric/data utils)
COMMON = [
    "python-dotenv",
    "pyyaml",
    "pillow",
    "pyarrow",
    "sentencepiece",
    "tiktoken",
    "beautifulsoup4",
    "munkres",
    "rouge",
    "lxml",
    "nltk",
    "jieba",
    "rouge-chinese",
    "huggingface_hub>=0.34.0",
    # accelerate 는 Unsloth 학습에서 `accelerate launch` 로 직접 호출되므로
    # transitive 에 맡기지 않고 명시적으로 고정한다 (CLI 보장).
    "accelerate>=1.3.0,<=1.11.0",
]

# LlamaFactory 전용 (Qwen2/2.5/3-VL, LLaVA 계열).
# `llamafactory` 서브프로젝트 자체는 미리 `pip install -e ./LlamaFactory --no-deps` 로
# 설치한다 — 여기서 `file://` ref 로 끌어오면 서브프로젝트의 transitive 상한이 pip resolver
# 에 노출돼 `transformers==5.5.4` 와 충돌한다.
LLAMAFACTORY = [
    # 최상위 pin. LlamaFactory 서브프로젝트의 transitive 상한(<=5.2.0) 을 덮어쓴다.
    "transformers==5.5.4",
    "deepspeed>=0.10.0,<=0.18.4",
    # transformers 5.5.x 의 새 `rope_parameters.rope_type` 필드와 레거시 `type=mrope` 가
    # 동시 존재하는 config 를 vllm<=0.11.0 의 pydantic validator 가 거부하므로 상한 해제.
    "vllm>=0.11.0",
    # LlamaFactory scripts/vllm_infer.py 가 비디오 처리용으로 `import av`, CLI 진입점으로 `import fire`,
    # HF 데이터셋 로더로 `from datasets import load_dataset` 를 한다.
    "av",
    "fire",
    "datasets",
    # LlamaFactory 서브프로젝트를 `--no-deps` 로 설치하므로 런타임 import 체인에서
    # 필요한 서브프로젝트 deps 를 여기서 재선언한다 (GUI/API 용 gradio/fastapi 등은 생략).
    "peft>=0.18.0,<=0.18.1",
    "trl>=0.18.0,<=0.24.0",
    "torchdata>=0.10.0,<=0.11.0",
    "einops",
    "modelscope",
    "hf-transfer",
    "omegaconf",
]

# Unsloth 전용 (google/gemma-4-E2B-it, google/gemma-4-E4B-it).
# `unsloth[huggingface,triton]` 서브프로젝트는 `pip install -e './unsloth[huggingface,triton]' --no-deps`
# 로 미리 설치한다 (LlamaFactory 와 동일한 이유).
# - deepspeed 는 넣지 않는다 — FastModel 의 gradient checkpointing / 메모리 최적화가
#   deepspeed ZeRO 와 충돌하고, env 에 deepspeed 가 있으면 `accelerate launch` 가
#   deepspeed plugin 을 자동 활성화해 첫 step 에서 실패한다.
# - transformers 는 `>=5.5.4` 로 상한 없이 놓는다 — Gemma-4 (E2B / E4B) 를 구동하는
#   modeling 코드는 transformers 의 최신 Gemma-4 loader 에 의존한다. pip resolver 가
#   최신 호환 버전을 뽑도록 상한을 풀어 둔다 (LlamaFactory 쪽은 `==5.5.4` 유지).
# - 서브프로젝트를 `--no-deps` 로 설치하므로 런타임에 필요한 서브프로젝트 deps
#   (peft / trl / datasets) 를 여기서 재선언한다. Stage 1 LoRA merge
#   (`_unsloth_merge.py::merge_lora`) 는 `from unsloth import FastModel` 와
#   `from peft import PeftModel` 를 사용하므로 peft 는 필수.
# - unsloth_zoo 는 `transformers<=5.5.0` 를 고정하여 우리의 `transformers>=5.5.4`
#   와 disjoint (pip ResolutionImpossible). env 에는 transformers 5.6.0 이 이미
#   설치되어 있고 런타임에서 Gemma-4 loader 가 정상 동작하므로 `--no-deps` 로
#   별도 설치한다. extras 에는 포함하지 않는다:
#     pip install --no-user --no-deps 'unsloth_zoo>=2026.4.8'
# - vllm 은 `--no-deps` 로 0.11.0 이 미리 설치되어 있어야 한다. extras 에 포함하면
#   peft / torchao 의 torch pin 과 vllm wheel 의 torch pin 이 맞물려 pip resolver 가
#   vllm 을 0.5.1 소스까지 backtrack → `CUDA_HOME is not set` 빌드 실패를 일으킨다.
#   학습/평가 runtime 이 요구하는 vllm 은 env 에 이미 존재하므로 별도 관리한다:
#     pip install --no-user --no-deps 'vllm==0.11.0'
UNSLOTH = [
    "transformers>=5.5.4",
    "bitsandbytes>=0.45.5",
    "peft>=0.18.0",
    "trl>=0.18.2,!=0.19.0,<=0.24.0",
    "datasets>=3.4.1,!=4.0.*,!=4.1.0,<4.4.0",
    # 평가 런타임 — stage{1,2}_eval.sh 가 unsloth env 에서도 LlamaFactory
    # `scripts/vllm_infer.py` 를 호출한다. 해당 스크립트는 비디오 처리용
    # `import av`, CLI 진입점 `import fire` 를 import 하고,
    # `from llamafactory.data import ...` 를 통해 LlamaFactory 서브프로젝트
    # 런타임을 불러온다. LlamaFactory 는 `--no-deps` 로 설치되므로 해당 런타임이
    # 요구하는 서브프로젝트 deps 를 여기서 재선언한다 (LLAMAFACTORY extras 와 동일 규약).
    "av",
    "fire",
    "einops",
    "modelscope",
    "hf-transfer",
    "omegaconf",
    "torchdata>=0.10.0,<=0.11.0",
]

EXTRAS = {
    "llamafactory": LLAMAFACTORY,
    "unsloth": UNSLOTH,
}


setup(
    name="gui-model",
    version="0.1.0",
    description="Training and evaluation pipeline for GUI world modeling and action prediction.",
    python_requires=">=3.10,<3.13",
    packages=["gui_model"],
    install_requires=COMMON,
    extras_require=EXTRAS,
)
