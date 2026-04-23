#!/usr/bin/env python
"""GUI-Model Unsloth inference runner (Gemma-4 계열 평가 우회 경로).

`LlamaFactory/scripts/vllm_infer.py` 는 vllm 의 `ModelConfig` validator 를 타는데,
`gui-model-unsloth` env 에 설치된 vllm 0.11.0 은 Gemma-4 아키텍처를 `ModelRegistry`
에 등록하지 않아 transformers 5.6 의 `rope_parameters` config 를 받지 못한다
(`ValidationError: rope_scaling should have a 'rope_type' key`). vllm 0.11.1+ 는
torch 2.9+ 를 pin 해 unsloth env 와 torch ABI 가 disjoint.

이 runner 는 Unsloth `FastModel.from_pretrained` 로 HF merged repo 또는 base
모델을 로드한 뒤, HF processor + `model.generate` 경로로 추론한다. 출력 JSONL
포맷은 `vllm_infer.py` 와 동일 (`prompt / predict / label`) 이므로 후속 단계인
`_hungarian_eval.py` / `_action_eval.py` 가 수정 없이 consume 한다.

stage{1,2}_eval.sh 가 `MODEL_BACKEND[MODEL_SHORT] == "unsloth"` 일 때
`_common.sh::build_infer_cmd` 를 통해 이 스크립트를 호출한다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model_name_or_path", required=True,
                   help="HF repo id or local dir (merged weights).")
    p.add_argument("--test", required=True, type=Path,
                   help="ShareGPT-format JSONL (messages, images).")
    p.add_argument("--image_dir", required=True, type=Path,
                   help="Root dir that `images` relative paths resolve against.")
    p.add_argument("--save_name", required=True, type=Path,
                   help="Output JSONL path — vllm_infer.py 포맷 그대로.")
    p.add_argument("--matrix_save_name", type=Path, default=None,
                   help="Optional runtime-stats JSON path.")
    p.add_argument("--max_samples", type=int, default=None,
                   help="Truncate test set; for smoke / smoke testing.")
    # vllm_infer.py 기본값과 일치시켜 qwen/llava 경로와 동일 생성 조건.
    p.add_argument("--temperature", type=float, default=0.95)
    p.add_argument("--top_p", type=float, default=0.7)
    p.add_argument("--top_k", type=int, default=50)
    p.add_argument("--max_new_tokens", type=int, default=1024)
    p.add_argument("--repetition_penalty", type=float, default=1.0)
    p.add_argument("--image_max_pixels", type=int, default=4233600)
    p.add_argument("--seed", type=int, default=None)
    return p.parse_args()


def _resize_image(img, max_pixels: int):
    w, h = img.size
    if w * h <= max_pixels:
        return img
    scale = (max_pixels / float(w * h)) ** 0.5
    new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
    return img.resize(new_size)


def _split_conversation(messages: list[dict]) -> tuple[list[dict], str]:
    """마지막 `from==gpt` 턴을 label, 그 앞을 context 로 분리.

    Stage 1/2 의 test JSONL 은 single-turn (system → human → gpt) 구조라 첫 gpt
    턴에서 멈추면 된다.
    """
    convo: list[dict] = []
    label = ""
    for m in messages:
        if m["from"] == "gpt":
            label = m["value"]
            break
        convo.append(m)
    return convo, label


def _build_hf_messages(convo: list[dict], images: list[Any]) -> list[dict]:
    """LlamaFactory ShareGPT → HF messages (content parts list).

    human 턴의 `<image>` 마커를 실제 image part 로 치환한다. system 턴은 그대로
    유지 (Gemma-4 chat_template 이 system role 지원).
    """
    role_map = {"system": "system", "human": "user", "gpt": "assistant"}
    out: list[dict] = []
    image_iter = iter(images)
    for m in convo:
        role = role_map[m["from"]]
        text = m["value"]
        parts: list[dict] = []
        while "<image>" in text:
            pre, _, text = text.partition("<image>")
            if pre:
                parts.append({"type": "text", "text": pre})
            try:
                parts.append({"type": "image", "image": next(image_iter)})
            except StopIteration:
                # JSONL 의 `<image>` 갯수와 `images` 길이 불일치는 데이터 오류.
                # 조용히 넘기지 말고 즉시 raise.
                raise ValueError("messages 안의 <image> 개수가 images 보다 많습니다.")
        if text:
            parts.append({"type": "text", "text": text})
        if not parts:
            parts = [{"type": "text", "text": ""}]
        out.append({"role": role, "content": parts})
    return out


def main() -> int:
    args = parse_args()

    # 의존성 지연 로드 — --help 만 찍을 때 CUDA 초기화 비용 회피.
    import torch
    from PIL import Image
    from unsloth import FastModel

    print(f"[unsloth-infer] loading model: {args.model_name_or_path}", file=sys.stderr)
    model, tok = FastModel.from_pretrained(
        model_name=args.model_name_or_path,
        load_in_4bit=False,
        dtype=None,
    )
    model.eval()

    # FastModel 은 multimodal 모델에서 processor 를 tokenizer 자리로 돌려주는
    # 경우가 있고, 순수 tokenizer 만 돌려주는 경우도 있다. `apply_chat_template`
    # 과 image 입력을 처리할 수 있는 객체가 필요하므로 processor 를 별도로 load
    # 하는 경로를 보장한다 (AutoProcessor 는 multimodal 모델에서 항상 동작).
    if hasattr(tok, "image_processor") and hasattr(tok, "apply_chat_template"):
        processor = tok
    else:
        from transformers import AutoProcessor
        processor = AutoProcessor.from_pretrained(args.model_name_or_path)

    # test JSONL 로드
    samples: list[dict] = []
    with open(args.test, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    if args.max_samples is not None:
        samples = samples[: args.max_samples]
    print(f"[unsloth-infer] {len(samples)} samples to run", file=sys.stderr)

    if args.seed is not None:
        torch.manual_seed(args.seed)

    device = next(model.parameters()).device
    do_sample = args.temperature > 0

    args.save_name.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with open(args.save_name, "w", encoding="utf-8") as fout:
        for idx, sample in enumerate(samples):
            convo, label = _split_conversation(sample["messages"])

            # 이미지 로드 + 다운스케일
            images = []
            for rel in sample.get("images") or []:
                img = Image.open(args.image_dir / rel).convert("RGB")
                images.append(_resize_image(img, args.image_max_pixels))

            hf_messages = _build_hf_messages(convo, images)

            # HF processor 는 images 인자가 필요 (chat_template 만으론 vision
            # embed 가 생성되지 않음). images=None 시에도 호환.
            inputs = processor.apply_chat_template(
                hf_messages,
                add_generation_prompt=True,
                tokenize=True,
                return_dict=True,
                return_tensors="pt",
            ).to(device)

            with torch.inference_mode():
                out_ids = model.generate(
                    **inputs,
                    do_sample=do_sample,
                    temperature=args.temperature if do_sample else None,
                    top_p=args.top_p if do_sample else None,
                    top_k=args.top_k if do_sample else None,
                    max_new_tokens=args.max_new_tokens,
                    repetition_penalty=args.repetition_penalty,
                )

            input_len = inputs["input_ids"].shape[-1]
            gen_ids = out_ids[0, input_len:]
            # processor / tokenizer 둘 다 decode 가능해야 하므로 processor 가
            # tokenizer 를 감싸고 있을 때는 그 tokenizer 를 직접 사용.
            decoder = getattr(processor, "tokenizer", processor)
            pred = decoder.decode(gen_ids, skip_special_tokens=True).strip()

            prompt_text = decoder.decode(
                inputs["input_ids"][0], skip_special_tokens=True
            )
            fout.write(json.dumps(
                {"prompt": prompt_text, "predict": pred, "label": label},
                ensure_ascii=False,
            ) + "\n")
            fout.flush()

            if (idx + 1) % 20 == 0 or idx + 1 == len(samples):
                elapsed = time.time() - t0
                print(
                    f"[unsloth-infer] {idx + 1}/{len(samples)} "
                    f"({elapsed:.1f}s, {(idx + 1) / max(elapsed, 1e-6):.2f} samples/s)",
                    file=sys.stderr,
                )

    elapsed = time.time() - t0
    print(f"[unsloth-infer] saved {len(samples)} preds → {args.save_name} in {elapsed:.1f}s",
          file=sys.stderr)

    if args.matrix_save_name is not None:
        args.matrix_save_name.parent.mkdir(parents=True, exist_ok=True)
        stats = {
            "predict_runtime": elapsed,
            "predict_samples_per_second":
                (len(samples) / elapsed) if elapsed > 0 else 0.0,
            "predict_num_samples": len(samples),
        }
        with open(args.matrix_save_name, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)
        print(f"[unsloth-infer] stats → {args.matrix_save_name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
