"""Finite-difference gradient checks for the hand-written backward passes.

Every gradient in ``nn.py``, ``model.py`` and ``metrics.py`` is written by hand,
so it is verified against central finite differences rather than trusted. A sign
error in any one of them would train a model that silently fails to converge.
"""

from __future__ import annotations

import numpy as np
import pytest

from spilltrace_ml import nn
from spilltrace_ml.dataset import LABEL_BACKGROUND, LABEL_INVALID, LABEL_OIL
from spilltrace_ml.metrics import bce_with_logits, combined_loss, soft_dice, split_target
from spilltrace_ml.model import UNet

EPS = 1e-5
TOLERANCE = 1e-6


@pytest.fixture(autouse=True)
def _float64_precision():
    """Run every check in float64.

    At float32 the truncation error of a central difference is the same order as
    the gradient itself, so a real sign error would be indistinguishable from
    rounding. ``nn.precision`` raises the working dtype for the duration.
    """
    with nn.precision(np.float64):
        yield


def _numeric_input_grad(forward, x: np.ndarray, seed_grad: np.ndarray) -> np.ndarray:
    """d(sum(seed * forward(x)))/dx by central differences."""
    grad = np.zeros_like(x, dtype=np.float64)
    flat = x.reshape(-1)
    for index in range(flat.size):
        original = flat[index]
        flat[index] = original + EPS
        plus = float((forward(x) * seed_grad).sum())
        flat[index] = original - EPS
        minus = float((forward(x) * seed_grad).sum())
        flat[index] = original
        grad.reshape(-1)[index] = (plus - minus) / (2 * EPS)
    return grad


def _relative_error(analytic: np.ndarray, numeric: np.ndarray) -> float:
    denominator = np.maximum(np.abs(analytic) + np.abs(numeric), 1e-6)
    return float(np.max(np.abs(analytic - numeric) / denominator))


@pytest.mark.parametrize("kernel", [1, 3])
def test_conv2d_input_gradient(kernel: int) -> None:
    rng = np.random.default_rng(11)
    layer = nn.Conv2d(2, 3, kernel=kernel, rng=rng)
    x = rng.standard_normal((2, 5, 4, 2)).astype(np.float64)
    seed = rng.standard_normal((2, 5, 4, 3))

    analytic_out = layer.forward(x, training=True)
    analytic = layer.backward(seed)
    numeric = _numeric_input_grad(
        lambda arr: layer.forward(arr, training=False), x, seed
    )
    assert analytic_out.shape == (2, 5, 4, 3)
    assert _relative_error(analytic, numeric) < TOLERANCE


def test_conv2d_weight_gradient() -> None:
    rng = np.random.default_rng(12)
    layer = nn.Conv2d(2, 2, kernel=3, rng=rng)
    x = rng.standard_normal((1, 4, 4, 2))
    seed = rng.standard_normal((1, 4, 4, 2))

    layer.forward(x, training=True)
    layer.backward(seed)
    analytic = layer.weight.grad.copy().astype(np.float64)

    numeric = np.zeros_like(analytic)
    flat = layer.weight.value.reshape(-1)
    for index in range(flat.size):
        original = float(flat[index])
        flat[index] = original + EPS
        plus = float((layer.forward(x, training=False) * seed).sum())
        flat[index] = original - EPS
        minus = float((layer.forward(x, training=False) * seed).sum())
        flat[index] = original
        numeric.reshape(-1)[index] = (plus - minus) / (2 * EPS)
    assert _relative_error(analytic.astype(np.float64), numeric) < TOLERANCE


def test_batchnorm_gradient_in_training_mode() -> None:
    rng = np.random.default_rng(13)
    layer = nn.BatchNorm(3)
    layer.gamma.value = rng.uniform(0.5, 1.5, 3)
    layer.beta.value = rng.standard_normal(3)
    x = rng.standard_normal((3, 4, 4, 3)).astype(np.float64)
    seed = rng.standard_normal((3, 4, 4, 3))

    layer.forward(x, training=True)
    analytic = layer.backward(seed)

    def forward(arr: np.ndarray) -> np.ndarray:
        # Batch statistics must be recomputed for the perturbed input, so the
        # check runs in training mode with the running buffers restored.
        saved = (layer.running_mean.copy(), layer.running_var.copy())
        out = layer.forward(arr, training=True)
        layer.running_mean, layer.running_var = saved
        return out

    numeric = _numeric_input_grad(forward, x, seed)
    assert _relative_error(analytic, numeric) < TOLERANCE


def test_maxpool_and_upsample_are_adjoint_shapes() -> None:
    rng = np.random.default_rng(14)
    pool = nn.MaxPool2()
    up = nn.UpsampleNearest2()
    x = rng.standard_normal((2, 6, 4, 3)).astype(np.float32)

    pooled = pool.forward(x, training=True)
    assert pooled.shape == (2, 3, 2, 3)
    # The pooled value must equal the max of each 2x2 window.
    expected = x.reshape(2, 3, 2, 2, 2, 3).max(axis=(2, 4))
    assert np.allclose(pooled, expected)

    grad = rng.standard_normal(pooled.shape).astype(np.float32)
    back = pool.backward(grad)
    assert back.shape == x.shape
    # Each window receives its gradient exactly once.
    assert np.isclose(float(back.sum()), float(grad.sum()), atol=1e-4)

    expanded = up.forward(pooled, training=True)
    assert expanded.shape == (2, 6, 4, 3)
    assert np.allclose(expanded[:, 0, 0], pooled[:, 0, 0])
    up_grad = up.backward(np.ones_like(expanded))
    assert np.allclose(up_grad, 4.0)


def test_maxpool_gradient_matches_finite_differences() -> None:
    rng = np.random.default_rng(15)
    pool = nn.MaxPool2()
    x = rng.standard_normal((1, 4, 4, 2)).astype(np.float64)
    seed = rng.standard_normal((1, 2, 2, 2))
    pool.forward(x, training=True)
    analytic = pool.backward(seed)
    numeric = _numeric_input_grad(
        lambda arr: pool.forward(arr, training=False), x, seed
    )
    assert _relative_error(analytic, numeric) < TOLERANCE


def _batchnorms(model: UNet) -> list[nn.BatchNorm]:
    out: list[nn.BatchNorm] = []
    for layer in model.layers():
        children = getattr(layer, "layers", None)
        for candidate in (children or [layer]):
            if isinstance(candidate, nn.BatchNorm):
                out.append(candidate)
    return out


def _make_target(rng: np.random.Generator, shape: tuple[int, ...]) -> np.ndarray:
    target = np.full(shape, LABEL_BACKGROUND, dtype=np.uint8)
    target[rng.random(shape) < 0.25] = LABEL_OIL
    target[rng.random(shape) < 0.10] = LABEL_INVALID
    return target


def test_bce_gradient() -> None:
    rng = np.random.default_rng(16)
    logits = rng.standard_normal((2, 6, 6)).astype(np.float64)
    target = _make_target(rng, logits.shape)
    oil, valid = split_target(target)

    _, analytic = bce_with_logits(logits, oil, valid, pos_weight=2.0)
    numeric = np.zeros_like(logits)
    flat = logits.reshape(-1)
    for index in range(flat.size):
        original = flat[index]
        flat[index] = original + EPS
        plus, _ = bce_with_logits(logits, oil, valid, pos_weight=2.0)
        flat[index] = original - EPS
        minus, _ = bce_with_logits(logits, oil, valid, pos_weight=2.0)
        flat[index] = original
        numeric.reshape(-1)[index] = (plus - minus) / (2 * EPS)
    assert _relative_error(analytic.astype(np.float64), numeric) < TOLERANCE


def test_soft_dice_gradient() -> None:
    rng = np.random.default_rng(17)
    logits = rng.standard_normal((2, 6, 6)).astype(np.float64)
    target = _make_target(rng, logits.shape)
    oil, valid = split_target(target)

    _, analytic = soft_dice(logits, oil, valid)
    numeric = np.zeros_like(logits)
    flat = logits.reshape(-1)
    for index in range(flat.size):
        original = flat[index]
        flat[index] = original + EPS
        plus, _ = soft_dice(logits, oil, valid)
        flat[index] = original - EPS
        minus, _ = soft_dice(logits, oil, valid)
        flat[index] = original
        numeric.reshape(-1)[index] = (plus - minus) / (2 * EPS)
    assert _relative_error(analytic.astype(np.float64), numeric) < TOLERANCE


def test_invalid_pixels_do_not_affect_the_loss() -> None:
    """Changing a logit under an invalid label must not move the loss."""
    rng = np.random.default_rng(18)
    logits = rng.standard_normal((1, 8, 8))
    target = _make_target(rng, logits.shape)
    invalid = np.argwhere(target == LABEL_INVALID)
    assert invalid.size, "test fixture produced no invalid pixels"

    before, grad, _ = combined_loss(logits, target)
    row = tuple(invalid[0])
    assert float(grad[row]) == 0.0
    moved = logits.copy()
    moved[row] += 25.0
    after, _, _ = combined_loss(moved, target)
    assert abs(after - before) < 1e-9


def test_unet_end_to_end_gradient() -> None:
    """The full network's parameter gradients match finite differences."""
    rng = np.random.default_rng(19)
    model = UNet(in_channels=2, base_channels=2, depth=2, seed=5)
    x = rng.standard_normal((2, 8, 8, 2))
    target = _make_target(rng, (2, 8, 8))

    def loss_of() -> float:
        # Training mode, to match the function the analytic gradient differentiates:
        # in eval mode batch norm uses running statistics, so the network computes a
        # different function of the weights and the comparison would be meaningless.
        saved = [
            (layer, layer.running_mean.copy(), layer.running_var.copy())
            for layer in _batchnorms(model)
        ]
        logits = model.forward(x, training=True)
        for layer, mean, var in saved:
            layer.running_mean, layer.running_var = mean, var
        value, _, _ = combined_loss(logits, target)
        return value

    logits = model.forward(x, training=True)
    _, grad, _ = combined_loss(logits, target)
    model.backward(grad)

    # Spot-check a spread of parameters: a full check would be O(parameters) forwards.
    checked = 0
    for param in model.parameters():
        if param.value.size == 0:
            continue
        indices = np.linspace(0, param.value.size - 1, 3, dtype=int)
        flat = param.value.reshape(-1)
        for index in np.unique(indices):
            original = float(flat[index])
            flat[index] = original + EPS
            plus = loss_of()
            flat[index] = original - EPS
            minus = loss_of()
            flat[index] = original
            numeric = (plus - minus) / (2 * EPS)
            analytic = float(param.grad.reshape(-1)[index])
            denominator = max(abs(analytic) + abs(numeric), 1e-9)
            assert abs(analytic - numeric) / denominator < 1e-5, (
                f"{param.name}[{index}]: analytic {analytic:.6g} vs numeric {numeric:.6g}"
            )
            checked += 1
    assert checked > 20


def test_unet_shapes_and_round_trip(tmp_path) -> None:
    model = UNet(in_channels=2, base_channels=4, depth=3, seed=7)
    rng = np.random.default_rng(20)
    x = rng.standard_normal((2, 32, 32, 2)).astype(np.float32)
    logits = model.forward(x, training=False)
    assert logits.shape == (2, 32, 32)

    path = model.save(tmp_path / "unet.npz", extra={"note": "test"})
    restored, extra = UNet.load(path)
    assert extra["note"] == "test"
    assert restored.parameter_count() == model.parameter_count()
    assert np.allclose(restored.forward(x, training=False), logits, atol=1e-6)


def test_sigmoid_is_stable_at_extremes() -> None:
    x = np.array([-800.0, -40.0, 0.0, 40.0, 800.0], dtype=np.float32)
    out = nn.sigmoid(x)
    assert np.all(np.isfinite(out))
    assert out[0] == 0.0
    assert out[-1] == 1.0
    assert abs(float(out[2]) - 0.5) < 1e-6
