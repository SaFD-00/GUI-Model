from setuptools import setup


# 설치는 단일 conda env (`gui-model`) 에서 두 단계로 한다.
# 서브프로젝트 (`./LlamaFactory`) 의 transitive 상한 (LF `transformers<=5.2.0`) 이
# 우리 extras 의 `transformers==5.5.4` 와 충돌하므로 pip resolver 한 번으로는 해결되지 않는다.
# 따라서 서브프로젝트는 `--no-deps` 로 먼저 editable 설치한 뒤, 루트 extras 를 올린다.
#
#   conda activate gui-model
#   PIP_USER=0 pip install --no-user -e ./LlamaFactory --no-deps
#   PIP_USER=0 pip install --no-user -e '.[llamafactory]'
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
    # accelerate 는 `accelerate launch` 로 직접 호출되므로 transitive 에 맡기지
    # 않고 명시적으로 고정한다 (CLI 보장).
    "accelerate>=1.3.0,<=1.11.0",
]

# LlamaFactory 전용 (Qwen2/2.5/3-VL 계열).
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

EXTRAS = {
    "llamafactory": LLAMAFACTORY,
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
