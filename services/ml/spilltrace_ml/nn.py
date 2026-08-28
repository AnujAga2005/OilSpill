"""NumPy neural-network primitives with explicit backward passes.

No deep-learning framework is installed in this environment, so the segmentation
model is built from these layers directly. Design notes:

* Tensors are ``(batch, height, width, channels)``. Channels-last keeps the
  contraction axis innermost, so every convolution becomes a large BLAS ``sgemm``.
* A 3x3 convolution is evaluated as nine shifted 1x1 convolutions rather than a
  single ``im2col`` matrix. The FLOP count is identical, but the temporary buffer
  is ``C_in`` wide instead of ``9 * C_in``, which matters at 128x128 with skip
  connections concatenated in.
* Gradients are hand-written and checked against central finite differences in
  ``tests/test_nn_gradients.py``.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

import numpy as np

# Working precision. Training runs in float32 for speed and memory; the
# finite-difference gradient checks temporarily raise it to float64, because at
# float32 the truncation error of a central difference swamps the signal.
DTYPE: Any = np.float32


@contextmanager
def precision(dtype: Any) -> Iterator[None]:
    """Temporarily change the working precision of newly built and run layers."""
    global DTYPE
    previous = DTYPE
    DTYPE = np.dtype(dtype).type
    try:
        yield
    finally:
        DTYPE = previous


@dataclass
class Parameter:
    """A trainable tensor and its accumulated gradient."""

    name: str
    value: np.ndarray
    grad: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self.value = np.asarray(self.value, dtype=DTYPE)
        self.grad = np.zeros_like(self.value)

    def zero_grad(self) -> None:
        self.grad[...] = 0.0


class Layer:
    """Base class: forward caches what backward needs, nothing more."""

    def parameters(self) -> list[Parameter]:
        return []

    def state(self) -> dict[str, np.ndarray]:
        return {}

    def load_state(self, state: dict[str, np.ndarray]) -> None:  # noqa: B027
        return None

    def forward(self, x: np.ndarray, training: bool = True) -> np.ndarray:
        raise NotImplementedError

    def backward(self, grad: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    __call__ = forward


class Conv2d(Layer):
    """Same-padded convolution over a channels-last tensor."""

    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel: int = 3,
        bias: bool = True,
        rng: np.random.Generator | None = None,
        name: str = "conv",
    ):
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.kernel = kernel
        self.pad = kernel // 2
        rng = rng or np.random.default_rng(0)
        # He initialisation: the network is all ReLU, so the forward variance is
        # preserved with std = sqrt(2 / fan_in).
        fan_in = kernel * kernel * in_channels
        scale = np.sqrt(2.0 / fan_in)
        self.weight = Parameter(
            f"{name}.weight",
            rng.standard_normal((kernel, kernel, in_channels, out_channels)) * scale,
        )
        self.bias = (
            Parameter(f"{name}.bias", np.zeros(out_channels)) if bias else None
        )
        self._padded: np.ndarray | None = None
        self._shape: tuple[int, int, int, int] | None = None

    def parameters(self) -> list[Parameter]:
        return [self.weight] + ([self.bias] if self.bias else [])

    def forward(self, x: np.ndarray, training: bool = True) -> np.ndarray:
        batch, height, width, channels = x.shape
        if channels != self.in_channels:
            raise ValueError(
                f"{self.weight.name}: expected {self.in_channels} channels, got {channels}"
            )
        padded = (
            np.pad(x, ((0, 0), (self.pad, self.pad), (self.pad, self.pad), (0, 0)))
            if self.pad
            else x
        )
        flat = np.zeros((batch * height * width, self.out_channels), dtype=DTYPE)
        weight = self.weight.value
        for dy in range(self.kernel):
            for dx in range(self.kernel):
                window = padded[:, dy : dy + height, dx : dx + width, :]
                flat += window.reshape(-1, channels) @ weight[dy, dx]
        out = flat.reshape(batch, height, width, self.out_channels)
        if self.bias is not None:
            out += self.bias.value
        if training:
            self._padded = padded
            self._shape = (batch, height, width, channels)
        return out

    def backward(self, grad: np.ndarray) -> np.ndarray:
        if self._padded is None or self._shape is None:
            raise RuntimeError(f"{self.weight.name}.backward called before forward")
        batch, height, width, channels = self._shape
        padded = self._padded
        flat_grad = np.ascontiguousarray(grad).reshape(-1, self.out_channels)
        grad_padded = np.zeros_like(padded)
        weight = self.weight.value
        for dy in range(self.kernel):
            for dx in range(self.kernel):
                window = padded[:, dy : dy + height, dx : dx + width, :]
                self.weight.grad[dy, dx] += window.reshape(-1, channels).T @ flat_grad
                grad_padded[:, dy : dy + height, dx : dx + width, :] += (
                    flat_grad @ weight[dy, dx].T
                ).reshape(batch, height, width, channels)
        if self.bias is not None:
            self.bias.grad += flat_grad.sum(axis=0)
        if self.pad:
            return grad_padded[
                :, self.pad : self.pad + height, self.pad : self.pad + width, :
            ]
        return grad_padded


class BatchNorm(Layer):
    """Batch normalisation over the channel axis of an NHWC tensor."""

    def __init__(
        self, channels: int, momentum: float = 0.9, eps: float = 1e-5, name: str = "bn"
    ):
        self.channels = channels
        self.momentum = momentum
        self.eps = eps
        self.gamma = Parameter(f"{name}.gamma", np.ones(channels))
        self.beta = Parameter(f"{name}.beta", np.zeros(channels))
        self.running_mean = np.zeros(channels, dtype=DTYPE)
        self.running_var = np.ones(channels, dtype=DTYPE)
        self._cache: tuple[np.ndarray, np.ndarray] | None = None

    def parameters(self) -> list[Parameter]:
        return [self.gamma, self.beta]

    def state(self) -> dict[str, np.ndarray]:
        return {"running_mean": self.running_mean, "running_var": self.running_var}

    def load_state(self, state: dict[str, np.ndarray]) -> None:
        if "running_mean" in state:
            self.running_mean = np.asarray(state["running_mean"], dtype=DTYPE)
        if "running_var" in state:
            self.running_var = np.asarray(state["running_var"], dtype=DTYPE)

    def forward(self, x: np.ndarray, training: bool = True) -> np.ndarray:
        if training:
            mean = x.mean(axis=(0, 1, 2))
            var = x.var(axis=(0, 1, 2))
            self.running_mean = (
                self.momentum * self.running_mean + (1.0 - self.momentum) * mean
            ).astype(DTYPE)
            self.running_var = (
                self.momentum * self.running_var + (1.0 - self.momentum) * var
            ).astype(DTYPE)
        else:
            mean = self.running_mean
            var = self.running_var
        inv_std = 1.0 / np.sqrt(var + self.eps)
        normalised = (x - mean) * inv_std
        if training:
            self._cache = (normalised, inv_std.astype(DTYPE))
        return normalised * self.gamma.value + self.beta.value

    def backward(self, grad: np.ndarray) -> np.ndarray:
        if self._cache is None:
            raise RuntimeError(f"{self.gamma.name}.backward called before forward")
        normalised, inv_std = self._cache
        axes = (0, 1, 2)
        self.gamma.grad += (grad * normalised).sum(axis=axes)
        self.beta.grad += grad.sum(axis=axes)
        grad_norm = grad * self.gamma.value
        count = grad.shape[0] * grad.shape[1] * grad.shape[2]
        term = grad_norm.sum(axis=axes) / count
        scaled = (grad_norm * normalised).sum(axis=axes) / count
        return (grad_norm - term - normalised * scaled) * inv_std


class ReLU(Layer):
    def __init__(self) -> None:
        self._positive: np.ndarray | None = None

    def forward(self, x: np.ndarray, training: bool = True) -> np.ndarray:
        positive = x > 0
        if training:
            self._positive = positive
        return np.where(positive, x, 0.0).astype(DTYPE, copy=False)

    def backward(self, grad: np.ndarray) -> np.ndarray:
        if self._positive is None:
            raise RuntimeError("ReLU.backward called before forward")
        return grad * self._positive


class MaxPool2(Layer):
    """2x2 max pooling. The winning index per window is cached for the backward pass."""

    def __init__(self) -> None:
        self._argmax: np.ndarray | None = None
        self._shape: tuple[int, int, int, int] | None = None

    def forward(self, x: np.ndarray, training: bool = True) -> np.ndarray:
        batch, height, width, channels = x.shape
        if height % 2 or width % 2:
            raise ValueError(f"MaxPool2 needs even spatial dims, got {height}x{width}")
        grouped = x.reshape(batch, height // 2, 2, width // 2, 2, channels)
        windows = grouped.transpose(0, 1, 3, 5, 2, 4).reshape(
            batch, height // 2, width // 2, channels, 4
        )
        argmax = windows.argmax(axis=-1)
        out = np.take_along_axis(windows, argmax[..., None], axis=-1)[..., 0]
        if training:
            self._argmax = argmax
            self._shape = (batch, height, width, channels)
        return np.ascontiguousarray(out)

    def backward(self, grad: np.ndarray) -> np.ndarray:
        if self._argmax is None or self._shape is None:
            raise RuntimeError("MaxPool2.backward called before forward")
        batch, height, width, channels = self._shape
        scattered = np.zeros(
            (batch, height // 2, width // 2, channels, 4), dtype=grad.dtype
        )
        np.put_along_axis(
            scattered, self._argmax[..., None], grad[..., None], axis=-1
        )
        return (
            scattered.reshape(batch, height // 2, width // 2, channels, 2, 2)
            .transpose(0, 1, 4, 2, 5, 3)
            .reshape(batch, height, width, channels)
        )


class UpsampleNearest2(Layer):
    """2x nearest-neighbour upsampling; the adjoint sums each 2x2 block."""

    def __init__(self) -> None:
        self._shape: tuple[int, int, int, int] | None = None

    def forward(self, x: np.ndarray, training: bool = True) -> np.ndarray:
        batch, height, width, channels = x.shape
        if training:
            self._shape = x.shape  # type: ignore[assignment]
        expanded = np.repeat(np.repeat(x, 2, axis=1), 2, axis=2)
        return np.ascontiguousarray(expanded)

    def backward(self, grad: np.ndarray) -> np.ndarray:
        if self._shape is None:
            raise RuntimeError("UpsampleNearest2.backward called before forward")
        batch, height, width, channels = self._shape
        return grad.reshape(batch, height, 2, width, 2, channels).sum(axis=(2, 4))


class Sequential(Layer):
    """Run layers in order; backward walks them in reverse."""

    def __init__(self, *layers: Layer):
        self.layers = list(layers)

    def parameters(self) -> list[Parameter]:
        return [p for layer in self.layers for p in layer.parameters()]

    def state(self) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {}
        for index, layer in enumerate(self.layers):
            for key, value in layer.state().items():
                out[f"{index}.{key}"] = value
        return out

    def load_state(self, state: dict[str, np.ndarray]) -> None:
        for index, layer in enumerate(self.layers):
            prefix = f"{index}."
            subset = {
                key[len(prefix) :]: value
                for key, value in state.items()
                if key.startswith(prefix)
            }
            if subset:
                layer.load_state(subset)

    def forward(self, x: np.ndarray, training: bool = True) -> np.ndarray:
        for layer in self.layers:
            x = layer.forward(x, training)
        return x

    def backward(self, grad: np.ndarray) -> np.ndarray:
        for layer in reversed(self.layers):
            grad = layer.backward(grad)
        return grad


def conv_block(
    in_channels: int,
    out_channels: int,
    rng: np.random.Generator,
    name: str,
) -> Sequential:
    """Conv-BN-ReLU twice: the standard U-Net double convolution."""
    return Sequential(
        Conv2d(in_channels, out_channels, 3, bias=False, rng=rng, name=f"{name}.c1"),
        BatchNorm(out_channels, name=f"{name}.n1"),
        ReLU(),
        Conv2d(out_channels, out_channels, 3, bias=False, rng=rng, name=f"{name}.c2"),
        BatchNorm(out_channels, name=f"{name}.n2"),
        ReLU(),
    )


class Adam:
    """Adam with decoupled gradient zeroing, seeded entirely by the caller."""

    def __init__(
        self,
        parameters: Sequence[Parameter],
        lr: float = 1e-3,
        betas: tuple[float, float] = (0.9, 0.999),
        eps: float = 1e-8,
        weight_decay: float = 0.0,
    ):
        self.parameters = list(parameters)
        self.lr = lr
        self.beta1, self.beta2 = betas
        self.eps = eps
        self.weight_decay = weight_decay
        self.step_count = 0
        self._m = [np.zeros_like(p.value) for p in self.parameters]
        self._v = [np.zeros_like(p.value) for p in self.parameters]

    def zero_grad(self) -> None:
        for param in self.parameters:
            param.zero_grad()

    def step(self) -> None:
        self.step_count += 1
        bias1 = 1.0 - self.beta1**self.step_count
        bias2 = 1.0 - self.beta2**self.step_count
        for index, param in enumerate(self.parameters):
            grad = param.grad
            if self.weight_decay:
                grad = grad + self.weight_decay * param.value
            self._m[index] = self.beta1 * self._m[index] + (1.0 - self.beta1) * grad
            self._v[index] = self.beta2 * self._v[index] + (1.0 - self.beta2) * (
                grad * grad
            )
            m_hat = self._m[index] / bias1
            v_hat = self._v[index] / bias2
            param.value -= (self.lr * m_hat / (np.sqrt(v_hat) + self.eps)).astype(DTYPE)


def clip_gradients(parameters: Sequence[Parameter], max_norm: float) -> float:
    """Scale gradients down to ``max_norm``; returns the norm before clipping."""
    total = float(np.sqrt(sum(float((p.grad * p.grad).sum()) for p in parameters)))
    if max_norm > 0 and total > max_norm:
        scale = max_norm / (total + 1e-12)
        for param in parameters:
            param.grad *= scale
    return total


def sigmoid(x: np.ndarray) -> np.ndarray:
    """Numerically stable logistic function; preserves a floating input dtype."""
    values = np.asarray(x)
    dtype = values.dtype if values.dtype.kind == "f" else np.dtype(DTYPE)
    out = np.empty(values.shape, dtype=dtype)
    positive = values >= 0
    out[positive] = 1.0 / (1.0 + np.exp(-values[positive]))
    exp_x = np.exp(values[~positive])
    out[~positive] = exp_x / (1.0 + exp_x)
    return out
