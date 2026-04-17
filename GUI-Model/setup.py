import os

from setuptools import setup


HERE = os.path.abspath(os.path.dirname(__file__))
LLAMAFACTORY_DIR = os.path.join(HERE, "LlamaFactory")
UNSLOTH_DIR = os.path.join(HERE, "unsloth")


# Vendored 서브프로젝트는 PEP 508 file:// 직접 참조로 엮어서
# `pip install -e .` 한 번으로 연쇄 설치되도록 한다.
#   - LlamaFactory: ./LlamaFactory  (transformers / torch / peft 등 transitive)
#   - unsloth[huggingface,triton]: ./unsloth (torch/transformers/triton 포함 full backend)
# 두 서브프로젝트의 소스를 수정하며 쓰고 싶으면 아래 명령으로 editable 로 덮어쓸 수 있다:
#   pip install -e ./LlamaFactory --no-deps
#   pip install -e ./unsloth[huggingface,triton] --no-deps
#
# 설치 명령 (권장):
#   conda activate gui-model
#   PIP_USER=0 pip install --no-user -e .
# PIP_USER=0 / --no-user 는 root 유저 + PYTHONUSERBASE 조합에서 pip 가
# deps 를 user-site (예: /root/.local/workspace/python-packages) 로 흘려
# env/bin 에 CLI entry point (accelerate 등) 가 만들어지지 않는 사고를 막는다.
INSTALL_REQUIRES = [
    # Local subprojects
    f"llamafactory @ file://{LLAMAFACTORY_DIR}",
    f"unsloth[huggingface,triton] @ file://{UNSLOTH_DIR}",
    # accelerate 는 Gemma-4 Unsloth 학습에서 `accelerate launch` 엔트리포인트로
    # 직접 호출되므로 transitive 에 맡기지 않고 명시적으로 선언한다. (CLI 보장)
    "accelerate>=1.3.0,<=1.11.0",
    # LlamaFactory requirements/metrics.txt
    "nltk",
    "jieba",
    "rouge-chinese",
    # LlamaFactory requirements/deepspeed.txt
    "deepspeed>=0.10.0,<=0.18.4",
    # LlamaFactory requirements/vllm.txt
    "vllm>=0.4.3,<=0.11.0",
    # Unsloth 보조 의존성
    "bitsandbytes>=0.45.5",
    "huggingface_hub>=0.34.0",
    # Tokenizers
    "sentencepiece",
    "tiktoken",
    # Additional evaluation
    "beautifulsoup4",
    "munkres",
    "rouge",
    "lxml",
    # Data processing
    "pillow",
    "pyarrow",
    # Utilities
    "python-dotenv",
]


setup(
    name="gui-model",
    version="0.1.0",
    description="Training and evaluation pipeline for GUI world modeling and action prediction.",
    python_requires=">=3.10,<3.13",
    packages=["gui_model"],
    install_requires=INSTALL_REQUIRES,
)
