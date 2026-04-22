import os

from setuptools import setup


HERE = os.path.abspath(os.path.dirname(__file__))
LLAMAFACTORY_DIR = os.path.join(HERE, "LlamaFactory")
UNSLOTH_DIR = os.path.join(HERE, "unsloth")


# 설치는 백엔드별로 분리된 conda env 에서 한 벌만 한다.
#
#   conda activate gui-model-llamafactory
#   PIP_USER=0 pip install --no-user -e '.[llamafactory]'
#
#   conda activate gui-model-unsloth
#   PIP_USER=0 pip install --no-user -e '.[unsloth]'
#
# 각 extras 가 서브프로젝트를 PEP 508 `file://` direct reference 로 끌고 들어와
# editable-install 저장소와 함께 연쇄 설치된다:
#   - llamafactory: ./LlamaFactory
#   - unsloth[huggingface,triton]: ./unsloth
# 서브프로젝트 소스를 수정하며 쓰려면 해당 env 에서:
#   pip install -e ./LlamaFactory --no-deps
#   pip install -e './unsloth[huggingface,triton]' --no-deps
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
LLAMAFACTORY = [
    # 최상위 pin. LlamaFactory 서브프로젝트의 transitive 상한(<=5.2.0) 을 덮어쓴다.
    "transformers==5.5.4",
    f"llamafactory @ file://{LLAMAFACTORY_DIR}",
    "deepspeed>=0.10.0,<=0.18.4",
    "vllm>=0.4.3,<=0.11.0",
]

# Unsloth 전용 (google/gemma-4-E2B-it, google/gemma-4-E4B-it).
# - deepspeed 는 넣지 않는다 — FastModel 의 gradient checkpointing / 메모리 최적화가
#   deepspeed ZeRO 와 충돌하고, env 에 deepspeed 가 있으면 `accelerate launch` 가
#   deepspeed plugin 을 자동 활성화해 첫 step 에서 실패한다.
# - transformers 는 `>=5.5.4` 로 상한 없이 놓는다 — Gemma-4 (E2B / E4B) 를 구동하는
#   modeling 코드는 transformers 의 최신 Gemma-4 loader 에 의존한다. pip resolver 가
#   최신 호환 버전을 뽑도록 상한을 풀어 둔다 (LlamaFactory 쪽은 `==5.5.4` 유지).
UNSLOTH = [
    "transformers>=5.5.4",
    f"unsloth[huggingface,triton] @ file://{UNSLOTH_DIR}",
    "bitsandbytes>=0.45.5",
    "vllm>=0.4.3,<=0.11.0",
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
