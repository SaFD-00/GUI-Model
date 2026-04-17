#!/usr/bin/env python
"""GUI-Model Unsloth training entrypoint.

Stage 1 (full fine-tuning) / Stage 2 (LoRA) 를 Unsloth 기반으로 실행한다.
scripts/stage{1,2}_train.sh 가 backend=="unsloth" 분기에서 이 스크립트를 호출한다.

YAML config 는 LlamaFactory YAML 의 키 이름을 최대한 공유하되, Unsloth 전용 키
(``full_finetuning``, ``load_in_4bit`` 등) 을 추가로 허용한다.

체크포인트는 ``<output_dir>/checkpoint-<step>`` 형태로 HF 표준 포맷(safetensors,
config.json, tokenizer*) 으로 저장되어 기존 vllm_infer.py + _hungarian_eval.py /
_action_eval.py 파이프라인과 호환된다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("UNSLOTH_RETURN_LOGITS", "1")

import unsloth  # noqa: F401 — must precede trl/transformers/peft imports

import yaml


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True, type=Path, help="YAML config path")
    p.add_argument(
        "--base-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Project base dir used to resolve relative dataset/output paths.",
    )
    p.add_argument(
        "--max-steps",
        type=int,
        default=-1,
        help="Override num train steps for smoke tests; -1 keeps YAML value.",
    )
    return p.parse_args()


def resolve(path: str | Path, base: Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (base / p).resolve()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


ROLE_MAP = {"system": "system", "human": "user", "gpt": "assistant"}


def resolve_image_path(
    img_rel: str,
    image_base_dir: Path,
    jsonl_parent: Path,
) -> str:
    candidates = [
        image_base_dir / img_rel,
        jsonl_parent / img_rel,
        jsonl_parent.parent / img_rel,
    ]
    for cand in candidates:
        if cand.is_file():
            return str(cand.resolve())
    raise FileNotFoundError(
        f"Image not found: {img_rel} (searched: {[str(c) for c in candidates]})"
    )


def build_light_row(
    row: dict[str, Any],
    image_base_dir: Path,
    jsonl_parent: Path,
) -> dict[str, Any]:
    """Build an Arrow-safe row: image paths only, no PIL objects.

    Output (stored in Dataset) ::
        {"messages": [
             {"role": "user", "content": [
                 {"type": "image", "path": "/abs/path.png"},
                 {"type": "text",  "text": "..."},
             ]},
             ...
         ]}

    PIL opening happens lazily in ``_materialize_images`` via ``set_transform``.
    """
    image_paths = [
        resolve_image_path(p, image_base_dir, jsonl_parent)
        for p in (row.get("images") or [])
    ]
    image_cursor = 0

    out_messages: list[dict[str, Any]] = []
    for msg in row["messages"]:
        role = ROLE_MAP[msg["from"]]
        raw_text = msg["value"]

        content: list[dict[str, Any]] = []
        if "<image>" in raw_text and image_paths:
            parts = raw_text.split("<image>")
            for idx, chunk in enumerate(parts):
                if idx > 0 and image_cursor < len(image_paths):
                    content.append({"type": "image", "path": image_paths[image_cursor]})
                    image_cursor += 1
                if chunk.strip():
                    content.append({"type": "text", "text": chunk})
        else:
            content.append({"type": "text", "text": raw_text})
        out_messages.append({"role": role, "content": content})

    while image_cursor < len(image_paths):
        for msg in out_messages:
            if msg["role"] == "user":
                msg["content"].insert(
                    0, {"type": "image", "path": image_paths[image_cursor]}
                )
                break
        image_cursor += 1

    return {"messages": out_messages}


def _materialize_images(batch: dict[str, list]) -> dict[str, list]:
    from PIL import Image

    out_messages: list[list[dict[str, Any]]] = []
    for msgs in batch["messages"]:
        new_msgs: list[dict[str, Any]] = []
        for msg in msgs:
            new_content: list[dict[str, Any]] = []
            for c in msg["content"]:
                if c.get("type") == "image" and "path" in c:
                    with Image.open(c["path"]) as im:
                        new_content.append({"type": "image", "image": im.convert("RGB")})
                else:
                    new_content.append(c)
            new_msgs.append({"role": msg["role"], "content": new_content})
        out_messages.append(new_msgs)
    return {"messages": out_messages}


def main() -> None:
    args = parse_args()
    base_dir = args.base_dir.resolve()
    cfg: dict[str, Any] = yaml.safe_load(args.config.read_text()) or {}

    # --- imports (heavy) ---
    import torch
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer
    from unsloth import FastModel, FastVisionModel, get_chat_template
    from unsloth.trainer import UnslothVisionDataCollator

    model_name: str = cfg["model_name_or_path"]
    max_seq_length = int(cfg.get("max_seq_length") or cfg.get("cutoff_len", 4096))
    load_in_4bit = bool(cfg.get("load_in_4bit", False))
    full_finetuning = bool(cfg.get("full_finetuning", False)) or (
        cfg.get("finetuning_type") == "full"
    )
    load_in_16bit = bool(cfg.get("load_in_16bit", full_finetuning and not load_in_4bit))
    dtype = torch.bfloat16 if cfg.get("bf16", True) else torch.float16

    gc_cfg = cfg.get("gradient_checkpointing", "unsloth")
    if isinstance(gc_cfg, str):
        gc_for_model: bool | str = (
            gc_cfg if gc_cfg.lower() not in {"false", "none", ""} else False
        )
    else:
        gc_for_model = bool(gc_cfg)

    # --- model / tokenizer ---
    model, tokenizer = FastModel.from_pretrained(
        model_name=model_name,
        max_seq_length=max_seq_length,
        load_in_4bit=load_in_4bit,
        load_in_16bit=load_in_16bit,
        full_finetuning=full_finetuning,
        dtype=dtype,
        use_gradient_checkpointing=gc_for_model,
    )

    chat_template = cfg.get("template")
    if chat_template:
        tokenizer = get_chat_template(tokenizer, chat_template)

    if cfg.get("finetuning_type") == "lora" and not full_finetuning:
        lora_target = cfg.get("lora_target", "all")
        target_modules = "all-linear" if lora_target == "all" else lora_target
        model = FastModel.get_peft_model(
            model,
            r=int(cfg["lora_rank"]),
            lora_alpha=int(cfg["lora_alpha"]),
            lora_dropout=float(cfg.get("lora_dropout", 0.0)),
            target_modules=target_modules,
            finetune_vision_layers=not bool(cfg.get("freeze_vision_tower", True)),
            finetune_language_layers=True,
            finetune_attention_modules=True,
            finetune_mlp_modules=True,
            use_gradient_checkpointing=cfg.get("gradient_checkpointing", "unsloth"),
            random_state=int(cfg.get("seed", 3407)),
        )

    FastVisionModel.for_training(model)

    if full_finetuning and bool(cfg.get("freeze_vision_tower", False)):
        vision_keywords = ("vision_tower", "vision_model", "visual", "image_encoder")
        frozen, frozen_params = 0, 0
        for name, param in model.named_parameters():
            if any(k in name for k in vision_keywords):
                if param.requires_grad:
                    param.requires_grad = False
                    frozen += 1
                    frozen_params += param.numel()
        print(
            f"[+] freeze_vision_tower=True: froze {frozen} tensors "
            f"({frozen_params:,} params)",
            file=sys.stderr,
        )

    # --- dataset ---
    dataset_path = resolve(cfg["dataset_path"], base_dir)
    if not dataset_path.is_file():
        raise FileNotFoundError(f"dataset_path not found: {dataset_path}")

    image_base_dir = resolve(
        cfg.get("image_base_dir", dataset_path.parent), base_dir
    )
    raw_rows = load_jsonl(dataset_path)
    light_rows = [
        build_light_row(r, image_base_dir, dataset_path.parent) for r in raw_rows
    ]
    dataset = Dataset.from_list(light_rows)
    dataset.set_transform(_materialize_images)

    # --- training args ---
    output_dir = resolve(cfg["output_dir"], base_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    sft_args = SFTConfig(
        output_dir=str(output_dir),
        per_device_train_batch_size=int(cfg.get("per_device_train_batch_size", 1)),
        gradient_accumulation_steps=int(cfg.get("gradient_accumulation_steps", 1)),
        learning_rate=float(cfg["learning_rate"]),
        num_train_epochs=float(cfg.get("num_train_epochs", 1)),
        lr_scheduler_type=str(cfg.get("lr_scheduler_type", "cosine")),
        warmup_ratio=float(cfg.get("warmup_ratio", 0.0)),
        weight_decay=float(cfg.get("weight_decay", 0.0)),
        max_grad_norm=float(cfg.get("max_grad_norm", 1.0)),
        optim=str(cfg.get("optim", "adamw_8bit")),
        bf16=bool(cfg.get("bf16", True)),
        fp16=bool(cfg.get("fp16", False)),
        save_strategy=str(cfg.get("save_strategy", "epoch")),
        save_total_limit=int(cfg.get("save_total_limit", 5)),
        logging_steps=int(cfg.get("logging_steps", 1)),
        max_steps=args.max_steps if args.max_steps > 0 else -1,
        max_length=max_seq_length,
        dataset_text_field="",
        dataset_kwargs={"skip_prepare_dataset": True},
        remove_unused_columns=False,
        ddp_find_unused_parameters=bool(cfg.get("ddp_find_unused_parameters", False)),
        seed=int(cfg.get("seed", 3407)),
        report_to="none",
    )

    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=dataset,
        args=sft_args,
        data_collator=UnslothVisionDataCollator(model, tokenizer),
    )

    trainer.train()
    trainer.save_model(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))
    print(f"[+] Unsloth training complete: {output_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
