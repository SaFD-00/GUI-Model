#!/usr/bin/env python
"""GUI-Model Unsloth merge/export entrypoint.

Stage 1 (full FT) 체크포인트는 이미 full weights 이므로 단순 copy+push,
Stage 2 (LoRA) 체크포인트는 base model 에 merge 한 뒤 merged_16bit 로 export.

stage{1,2}_merge.sh 가 backend=="unsloth" 분기에서 이 스크립트를 호출한다.
산출물 레이아웃은 기존 llamafactory-cli export 결과와 동일하다
(``outputs/{MODEL}/{DS}/stage{1,2}_merged[/{variant}]``).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--mode",
        required=True,
        choices=["full", "lora"],
        help="'full' for Stage 1 full FT checkpoint, 'lora' for Stage 2 LoRA.",
    )
    p.add_argument(
        "--base-model",
        help="Base model path or HF repo (LoRA mode only).",
    )
    p.add_argument(
        "--checkpoint",
        required=True,
        type=Path,
        help="Full-FT checkpoint dir (full mode) or LoRA adapter dir (lora mode).",
    )
    p.add_argument(
        "--export-dir",
        required=True,
        type=Path,
        help="Destination directory for merged model artifacts.",
    )
    p.add_argument(
        "--hub-id",
        default=None,
        help="HuggingFace Hub repo id (e.g. SaFD-00/...); omit to skip push.",
    )
    p.add_argument(
        "--hub-private",
        action="store_true",
        help="Create private repo on push (default: public).",
    )
    p.add_argument(
        "--max-shard-size",
        default="5GB",
        help="save_pretrained max_shard_size (mirrors LlamaFactory export_size=5).",
    )
    return p.parse_args()


def push_to_hub(local_dir: Path, repo_id: str, private: bool) -> None:
    """Upload export_dir to HF Hub. Requires HF_TOKEN env var."""
    import os

    from huggingface_hub import HfApi, create_repo

    token = os.environ.get("HF_TOKEN")
    create_repo(repo_id, token=token, private=private, exist_ok=True)
    HfApi().upload_folder(
        folder_path=str(local_dir),
        repo_id=repo_id,
        token=token,
    )
    print(f"[+] Pushed to https://huggingface.co/{repo_id}", file=sys.stderr)


def merge_full(args: argparse.Namespace) -> None:
    """Full FT checkpoint is a complete HF model — copy to export_dir as-is.

    Vision-aware Stage 1 merge: ``AutoModelForImageTextToText`` 우선 시도해
    vision tower / mm projector weight 가 누락되지 않도록 한다. multimodal 모델이
    아닌 경우 ``AutoModelForCausalLM`` 으로 fallback.
    """
    from transformers import AutoModelForCausalLM, AutoProcessor, AutoTokenizer
    try:
        from transformers import AutoModelForImageTextToText
    except ImportError:  # transformers < 4.45 등 구버전
        AutoModelForImageTextToText = None  # type: ignore

    src = args.checkpoint.resolve()
    dst = args.export_dir.resolve()
    dst.mkdir(parents=True, exist_ok=True)

    model = None
    if AutoModelForImageTextToText is not None:
        try:
            model = AutoModelForImageTextToText.from_pretrained(str(src), torch_dtype="auto")
            print("[+] Loaded with AutoModelForImageTextToText (vision-aware)", file=sys.stderr)
        except Exception as exc:
            print(f"[!] AutoModelForImageTextToText failed ({exc}); "
                  f"falling back to AutoModelForCausalLM", file=sys.stderr)
            model = None
    if model is None:
        model = AutoModelForCausalLM.from_pretrained(str(src), torch_dtype="auto")
        print("[+] Loaded with AutoModelForCausalLM (fallback)", file=sys.stderr)
    model.save_pretrained(str(dst), max_shard_size=args.max_shard_size, safe_serialization=True)

    try:
        tokenizer = AutoTokenizer.from_pretrained(str(src))
        tokenizer.save_pretrained(str(dst))
    except Exception as exc:  # best-effort
        print(f"[!] tokenizer save skipped: {exc}", file=sys.stderr)

    try:
        processor = AutoProcessor.from_pretrained(str(src))
        processor.save_pretrained(str(dst))
    except Exception as exc:
        print(f"[!] processor save skipped: {exc}", file=sys.stderr)

    # Copy aux files that save_pretrained sometimes omits (chat template, generation cfg)
    for name in ("chat_template.json", "generation_config.json", "preprocessor_config.json"):
        candidate = src / name
        if candidate.is_file() and not (dst / name).is_file():
            shutil.copy2(candidate, dst / name)

    print(f"[+] Stage 1 merged model saved: {dst}", file=sys.stderr)


def merge_lora(args: argparse.Namespace) -> None:
    """LoRA adapter + base model → merged_16bit safetensors.

    Unsloth 가 학습 시 base model 에 Gemma4ClippableLinear 같은 커스텀 레이어를
    patch 한다. 이 레이어는 peft(>=0.19) 의 generic LoRA injector 가 recognize
    하지 못해 `PeftModel.from_pretrained(base, adapter)` 호출 시
    ``ValueError: Target module ... is not supported`` 로 실패한다.

    대신 Unsloth 의 공식 재로드 경로 — adapter 디렉토리를
    ``FastModel.from_pretrained(model_name=<adapter_dir>)`` 에 직접 주는 방식 — 을
    사용한다. Unsloth 가 ``adapter_config.json`` 을 읽어 base model 을 resolve
    하고, peft 에 Gemma-4 monkey-patch 를 적용한 뒤 adapter 를 load 한다.
    이후 ``save_pretrained_merged`` 로 merged_16bit safetensors 를 내보낸다.
    """
    from unsloth import FastModel

    model, tokenizer = FastModel.from_pretrained(
        model_name=str(args.checkpoint),
        load_in_4bit=False,
        dtype=None,  # auto
    )

    dst = args.export_dir.resolve()
    dst.mkdir(parents=True, exist_ok=True)

    model.save_pretrained_merged(
        str(dst),
        tokenizer,
        save_method="merged_16bit",
    )
    print(f"[+] LoRA merged model saved: {dst}", file=sys.stderr)


def main() -> None:
    args = parse_args()
    if args.mode == "full":
        merge_full(args)
    else:
        merge_lora(args)

    if args.hub_id:
        push_to_hub(args.export_dir.resolve(), args.hub_id, args.hub_private)


if __name__ == "__main__":
    main()
