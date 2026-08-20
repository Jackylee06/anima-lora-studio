from __future__ import annotations

import gc
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "fancyfeast/llama-joycaption-beta-one-hf-llava"


class JoyCaptionAdapter:
    def __init__(self, model: str = DEFAULT_MODEL, cache_dir: str | None = None, precision: str = "nf4", revision: str | None = None):
        try:
            import torch
            from PIL import Image
            from transformers import AutoProcessor, BitsAndBytesConfig, LlavaForConditionalGeneration
        except ImportError as error:
            raise RuntimeError("JoyCaption 环境缺少 torch、transformers、bitsandbytes 或 Pillow") from error
        self.torch = torch
        self.Image = Image
        kwargs: dict[str, Any] = {"device_map": 0, "cache_dir": cache_dir}
        if precision == "nf4":
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_quant_type="nf4",
            )
        elif precision == "bf16":
            kwargs["torch_dtype"] = torch.bfloat16
        else:
            raise ValueError(f"不支持的 JoyCaption 精度：{precision}")
        self.processor = AutoProcessor.from_pretrained(model, cache_dir=cache_dir, revision=revision)
        self.model = LlavaForConditionalGeneration.from_pretrained(model, revision=revision, **kwargs)
        self.model.eval()

    def caption(self, image_path: Path, prompt: str, max_new_tokens: int = 320) -> str:
        conversation = [
            {"role": "system", "content": "You are a precise image captioner for diffusion-model training data."},
            {"role": "user", "content": prompt},
        ]
        text = self.processor.apply_chat_template(conversation, tokenize=False, add_generation_prompt=True)
        with self.Image.open(image_path) as image:
            inputs = self.processor(text=[text], images=[image.convert("RGB")], return_tensors="pt").to("cuda")
        inputs["pixel_values"] = inputs["pixel_values"].to(self.torch.bfloat16)
        with self.torch.inference_mode():
            generated = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=True,
                temperature=0.6,
                top_p=0.9,
                use_cache=True,
            )[0]
        generated = generated[inputs["input_ids"].shape[1]:]
        return self.processor.tokenizer.decode(generated, skip_special_tokens=True, clean_up_tokenization_spaces=False).strip()

    def close(self) -> None:
        del self.model
        del self.processor
        gc.collect()
        if self.torch.cuda.is_available():
            self.torch.cuda.empty_cache()


def mock_caption(image_path: Path) -> str:
    return (
        f"An anime illustration centered on a single character in the image named {image_path.stem}. "
        "The subject faces the viewer against a simple background with a clear, readable composition."
    )
