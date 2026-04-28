from setuptools import setup


# 설치는 단일 conda env (`gui-model`) 에서 두 단계로 한다.
# 서브프로젝트 (`./LlamaFactory`) 의 transitive 상한 (LF `transformers<=5.2.0`) 과
# 우리 extras 의 `transformers>=4.56.0,<5` 는 4.56–4.57.x 구간에서 겹치므로 한 번에
# 풀린다. 두 단계 분리는 historical 순서 보존용이며 `--no-deps` 우회는 더 이상 필요 없다.
#
#   conda activate gui-model
#   PIP_USER=0 pip install --no-user -e ./LlamaFactory
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
# `llamafactory` 서브프로젝트는 별도 단계로 `pip install -e ./LlamaFactory` 로 먼저 깐다.
LLAMAFACTORY = [
    # vllm 0.11.2 가 `transformers<5,>=4.56.0` 을 강제하므로 5.x 는 사용 불가.
    # LlamaFactory 서브프로젝트 pin (`>=4.55.0,<=5.2.0,!=4.57.0`) 과의 교집합 = 4.56–4.57.x.
    "transformers>=4.56.0,<5",
    "deepspeed>=0.10.0,<=0.18.4",
    # vllm 0.11.0 은 Qwen2.5-VL config 의 `rope_parameters.rope_type` (modern) +
    # `type=mrope` (legacy) 동시 존재 케이스에서 pydantic ValidationError 를 던지므로
    # 0.11.2 이상 사용. 단 `transformers<5` 제약 때문에 0.11.x 대 안에서만 검증됨.
    "vllm>=0.11.2",
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
