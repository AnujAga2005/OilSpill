"""The oil-spill segmentation network: a small U-Net over VV + VH decibels.

Depth and width are set by the compute budget, not by ambition: this runs on
NumPy with hand-written gradients, so the architecture is the smallest one that
still has an encoder-decoder with skip connections - the structure that lets the
model keep slick edges sharp while using enough context to reject speckle.

The checkpoint is a single ``.npz`` holding every weight, every batch-norm
running statistic and the architecture description, so a saved model can be
reloaded without the training script.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from . import nn
from .nn import (
    Adam,
    BatchNorm,
    Conv2d,
    Layer,
    MaxPool2,
    Parameter,
    ReLU,
    Sequential,
    UpsampleNearest2,
    conv_block,
    sigmoid,
)

# Working precision is owned by nn so the gradient checks can raise it globally.


class UNet:
    """Encoder-decoder with skip connections, channels-last throughout."""

    def __init__(
        self,
        in_channels: int = 2,
        base_channels: int = 8,
        depth: int = 3,
        seed: int = 26143,
    ):
        if depth < 1:
            raise ValueError("depth must be at least 1")
        self.in_channels = in_channels
        self.base_channels = base_channels
        self.depth = depth
        self.seed = seed
        rng = np.random.default_rng(seed)

        widths = [base_channels * (2**level) for level in range(depth)]
        self.encoders: list[Sequential] = []
        self.pools: list[MaxPool2] = []
        channels = in_channels
        for level, width in enumerate(widths):
            self.encoders.append(conv_block(channels, width, rng, f"enc{level}"))
            self.pools.append(MaxPool2())
            channels = width

        bottleneck_width = base_channels * (2**depth)
        self.bottleneck = conv_block(channels, bottleneck_width, rng, "bottleneck")
        channels = bottleneck_width

        self.ups: list[UpsampleNearest2] = []
        self.decoders: list[Sequential] = []
        for level in reversed(range(depth)):
            self.ups.append(UpsampleNearest2())
            skip = widths[level]
            self.decoders.append(
                conv_block(channels + skip, widths[level], rng, f"dec{level}")
            )
            channels = widths[level]

        self.head = Conv2d(channels, 1, kernel=1, bias=True, rng=rng, name="head")
        self._skip_channels: list[int] = []

    # -- introspection -----------------------------------------------------
    def layers(self) -> list[Layer]:
        out: list[Layer] = []
        for encoder, pool in zip(self.encoders, self.pools):
            out.extend([encoder, pool])
        out.append(self.bottleneck)
        for up, decoder in zip(self.ups, self.decoders):
            out.extend([up, decoder])
        out.append(self.head)
        return out

    def parameters(self) -> list[Parameter]:
        return [p for layer in self.layers() for p in layer.parameters()]

    def parameter_count(self) -> int:
        return int(sum(p.value.size for p in self.parameters()))

    def describe(self) -> dict[str, Any]:
        return {
            "architecture": "u-net",
            "framework": "numpy (hand-written backward pass)",
            "inChannels": self.in_channels,
            "baseChannels": self.base_channels,
            "depth": self.depth,
            "seed": self.seed,
            "parameters": self.parameter_count(),
            "encoderWidths": [
                self.base_channels * (2**level) for level in range(self.depth)
            ],
            "bottleneckWidth": self.base_channels * (2**self.depth),
            "normalisation": "batch norm",
            "activation": "relu",
            "upsampling": "nearest neighbour + concatenated skip",
            "output": "single logit per pixel",
        }

    # -- forward / backward -------------------------------------------------
    def forward(self, x: np.ndarray, training: bool = True) -> np.ndarray:
        """``x`` is (batch, height, width, in_channels); returns per-pixel logits."""
        activation = np.asarray(x, dtype=nn.DTYPE)
        skips: list[np.ndarray] = []
        for encoder, pool in zip(self.encoders, self.pools):
            activation = encoder.forward(activation, training)
            skips.append(activation)
            activation = pool.forward(activation, training)
        activation = self.bottleneck.forward(activation, training)
        self._skip_channels = []
        for up, decoder, skip in zip(self.ups, self.decoders, reversed(skips)):
            activation = up.forward(activation, training)
            self._skip_channels.append(activation.shape[-1])
            activation = np.concatenate([activation, skip], axis=-1)
            activation = decoder.forward(activation, training)
        return self.head.forward(activation, training)[..., 0]

    def backward(self, grad_logits: np.ndarray) -> None:
        """Accumulate parameter gradients from d(loss)/d(logits)."""
        grad = np.asarray(grad_logits, dtype=nn.DTYPE)[..., None]
        grad = self.head.backward(grad)
        # The decoder stack runs deepest-first in forward, so backward unwinds it
        # from the shallowest level (the one the head consumed) downwards.
        skip_grads: list[np.ndarray] = []
        for up, decoder, split in zip(
            reversed(self.ups), reversed(self.decoders), reversed(self._skip_channels)
        ):
            grad = decoder.backward(grad)
            # The decoder input was [upsampled | skip]; route each half back.
            skip_grads.append(np.ascontiguousarray(grad[..., split:]))
            grad = up.backward(np.ascontiguousarray(grad[..., :split]))
        grad = self.bottleneck.backward(grad)
        # skip_grads was filled shallow-to-deep; the encoders unwind deep-to-shallow.
        for pool, encoder, skip_grad in zip(
            reversed(self.pools), reversed(self.encoders), reversed(skip_grads)
        ):
            grad = pool.backward(grad)
            grad = encoder.backward(grad + skip_grad)

    def predict_proba(self, x: np.ndarray, batch_size: int = 8) -> np.ndarray:
        """Inference in bounded batches; returns per-pixel oil probability."""
        samples = np.asarray(x, dtype=nn.DTYPE)
        out = np.empty(samples.shape[:3], dtype=nn.DTYPE)
        for start in range(0, samples.shape[0], batch_size):
            chunk = samples[start : start + batch_size]
            out[start : start + chunk.shape[0]] = sigmoid(
                self.forward(chunk, training=False)
            )
        return out

    # -- persistence -------------------------------------------------------
    def state_dict(self) -> dict[str, np.ndarray]:
        state: dict[str, np.ndarray] = {}
        for param in self.parameters():
            state[f"param::{param.name}"] = param.value
        for index, layer in enumerate(self.layers()):
            for key, value in layer.state().items():
                state[f"buffer::{index}.{key}"] = value
        return state

    def load_state_dict(self, state: dict[str, np.ndarray]) -> None:
        params = {p.name: p for p in self.parameters()}
        for key, value in state.items():
            if key.startswith("param::"):
                name = key[len("param::") :]
                target = params.get(name)
                if target is None:
                    raise KeyError(f"checkpoint has unknown parameter {name}")
                if target.value.shape != tuple(np.shape(value)):
                    raise ValueError(
                        f"{name}: checkpoint shape {np.shape(value)} != "
                        f"model shape {target.value.shape}"
                    )
                target.value = np.asarray(value, dtype=nn.DTYPE)
        layers = self.layers()
        for index, layer in enumerate(layers):
            prefix = f"buffer::{index}."
            subset = {
                key[len(prefix) :]: np.asarray(value)
                for key, value in state.items()
                if key.startswith(prefix)
            }
            if subset:
                layer.load_state(subset)

    def save(self, path: str | Path, extra: dict[str, Any] | None = None) -> Path:
        import json

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(self.state_dict())
        payload["meta::json"] = np.frombuffer(
            json.dumps({"model": self.describe(), "extra": extra or {}}).encode("utf-8"),
            dtype=np.uint8,
        )
        # np.savez appends ".npz" to a *path* that lacks it, so the temporary file
        # is written through an open handle instead and renamed into place.
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("wb") as handle:
            np.savez_compressed(handle, **payload)
        tmp.replace(path)
        return path

    @classmethod
    def load(cls, path: str | Path) -> tuple["UNet", dict[str, Any]]:
        import json

        with np.load(Path(path), allow_pickle=False) as data:
            meta = json.loads(bytes(data["meta::json"]).decode("utf-8"))
            spec = meta["model"]
            model = cls(
                in_channels=int(spec["inChannels"]),
                base_channels=int(spec["baseChannels"]),
                depth=int(spec["depth"]),
                seed=int(spec["seed"]),
            )
            model.load_state_dict(
                {key: data[key] for key in data.files if key != "meta::json"}
            )
        return model, meta.get("extra", {})


def make_optimizer(model: UNet, lr: float, weight_decay: float = 0.0) -> Adam:
    return Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
