from setuptools import setup


INSTALL_REQUIRES = [
    # Core ML
    "torch>=2.4.0",
    "torchvision>=0.19.0",
    "torchaudio>=2.4.0",
    # transformers >=5.5.0 required for Gemma-4 (Unsloth backend)
    "transformers>=5.5.0,<=5.5.4",
    "peft>=0.18.0,<=0.18.1",
    "accelerate>=1.3.0,<=1.11.0",
    "safetensors",
    "datasets>=2.16.0,<=4.0.0",
    "trl>=0.18.2,<=0.24.0",
    # LLaMA-Factory (editable install from ./LlamaFactory)
    # pip install -e "./LlamaFactory[torch,metrics]" is handled via deps below
    "deepspeed>=0.10.0,<=0.18.4",
    "vllm>=0.4.3,<=0.11.0",
    # Unsloth backend — Gemma-4 전용
    # editable install 을 원하면 `pip install -e "./unsloth"` 로 덮어쓸 수 있음.
    # huggingface_hub 는 merge 시 HF push 용.
    "unsloth",
    "bitsandbytes>=0.45.5",
    "huggingface_hub>=0.34.0",
    # Tokenizers
    "sentencepiece",
    "tiktoken",
    # Evaluation metrics (LlamaFactory metrics.txt)
    "nltk",
    "jieba",
    "rouge-chinese",
    # Additional evaluation
    "beautifulsoup4",
    "munkres",
    "rouge",
    "lxml",
    # Data processing
    "numpy",
    "pandas",
    "scipy",
    "pillow",
    "pyarrow",
    # Utilities
    "tqdm",
    "pyyaml",
    "protobuf",
    "requests",
    "python-dotenv",
]


setup(
    name="gui-model",
    version="0.1.0",
    description="Training and evaluation pipeline for GUI world modeling and action prediction.",
    python_requires=">=3.10",
    packages=["gui_model"],
    install_requires=INSTALL_REQUIRES,
)
