from setuptools import setup


INSTALL_REQUIRES = [
    # Core ML
    "torch>=2.4.0",
    "torchvision",
    "torchaudio",
    "transformers>=4.55.0,<=5.2.0",
    "peft>=0.18.0,<=0.18.1",
    "accelerate>=1.3.0,<=1.11.0",
    "safetensors",
    "datasets>=2.16.0,<=4.0.0",
    "trl>=0.18.0,<=0.24.0",
    # LLaMA-Factory runtime extras
    "deepspeed",
    "vllm>=0.8.2",
    # Tokenizers
    "sentencepiece",
    "tiktoken",
    # Evaluation metrics
    "beautifulsoup4",
    "munkres",
    "nltk",
    "rouge",
    "jieba",
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
