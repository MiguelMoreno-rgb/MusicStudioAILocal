"""Lower the CPU footprint of the pinned CUDA text-encoder offload path."""
from __future__ import annotations

import os


class TextEncoderCPUOffloadMixin:
    """Load the encoder directly in its eventual CUDA inference dtype.

    ACE-Step's stock loader creates the text encoder with Transformers' default
    dtype (float32), then keeps that float32 copy on CPU for offload. In the
    Windows CUDA profile the encoder is only evaluated after it is moved to the
    CUDA dtype, so materializing it as float32 on CPU wastes resident storage.
    """

    def _load_text_encoder_and_tokenizer(self, *, checkpoint_dir: str, device: str) -> str:
        target_type = getattr(device, "type", None) or str(device).split(":", 1)[0]
        if not getattr(self, "offload_to_cpu", False) or target_type != "cuda":
            return super()._load_text_encoder_and_tokenizer(
                checkpoint_dir=checkpoint_dir, device=device
            )

        from transformers import AutoModel, AutoTokenizer

        text_encoder_path = os.path.join(checkpoint_dir, "Qwen3-Embedding-0.6B")
        if not os.path.exists(text_encoder_path):
            raise FileNotFoundError(f"Text encoder not found at {text_encoder_path}")

        self.text_tokenizer = AutoTokenizer.from_pretrained(text_encoder_path)
        # Transformers 4.57.6 supports `dtype` here (ACE-Step uses the same
        # argument for its DiT). Without it, from_pretrained explicitly defaults
        # to float32 even when the checkpoint stores lower-precision tensors.
        # No device_map is supplied, so weights remain on CPU until the existing
        # _load_model_context transfers them to CUDA.
        self.text_encoder = AutoModel.from_pretrained(
            text_encoder_path, dtype=self.dtype
        )

        saw_floating_parameter = False
        for name, parameter in self.text_encoder.named_parameters():
            if parameter.device.type != "cpu":
                raise RuntimeError(
                    f"Text encoder parameter {name} loaded on {parameter.device}; expected CPU offload."
                )
            if parameter.is_floating_point():
                saw_floating_parameter = True
                if parameter.dtype != self.dtype:
                    raise RuntimeError(
                        f"Text encoder parameter {name} loaded as {parameter.dtype}; "
                        f"expected inference dtype {self.dtype}."
                    )
        if not saw_floating_parameter:
            raise RuntimeError("Text encoder loaded without floating-point parameters.")

        self.text_encoder.eval()
        return text_encoder_path
