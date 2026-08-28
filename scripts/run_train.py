"""CLI: Phase 3 training and evaluation.

    python scripts/run_train.py [--epochs N] [--base-channels N] [--depth N]
                                [--batch-size N] [--lr F] [--max-train N]
                                [--samples N] [--quiet]

Reads the patch cache written by ``scripts/run_preprocess.py``, trains the U-Net,
selects the decision threshold on validation, evaluates once on test, calibrates a
classical baseline for comparison, and writes ``data/processed/metrics.json``
plus a checkpoint under ``models/``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "ml"))

from spilltrace_common import config as C  # noqa: E402
from spilltrace_ml.train import train  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the SpillTrace segmentation model")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--base-channels", type=int, default=None)
    parser.add_argument("--depth", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--lr", type=float, default=None)
    parser.add_argument("--max-train", type=int, default=None, help="cap training patches")
    parser.add_argument("--max-val", type=int, default=None)
    parser.add_argument("--max-test", type=int, default=None)
    parser.add_argument("--patience", type=int, default=None)
    parser.add_argument("--no-augment", action="store_true")
    parser.add_argument("--samples", type=int, default=8, help="comparison panels to write")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    cfg = C.TrainConfig()
    for attribute, value in (
        ("epochs", args.epochs),
        ("base_channels", args.base_channels),
        ("depth", args.depth),
        ("batch_size", args.batch_size),
        ("learning_rate", args.lr),
        ("max_train_patches", args.max_train),
        ("max_val_patches", args.max_val),
        ("max_test_patches", args.max_test),
        ("patience", args.patience),
    ):
        if value is not None:
            setattr(cfg, attribute, value)
    if args.no_augment:
        cfg.augment = False

    say = (lambda _msg: None) if args.quiet else print
    report = train(cfg, progress=say, sample_count=args.samples)

    print("")
    print("SpillTrace segmentation results")
    print(f"  model      {report['model']['parameters']} parameters, {C.MODEL_VERSION}")
    print(f"  threshold  {report['protocol']['thresholdSelection']['threshold']} "
          f"(chosen on validation)")
    for name in ("validation", "test"):
        m = report[name]
        print(
            f"  {name:10s} IoU {m['iou']} dice {m['dice']} "
            f"precision {m['precision']} recall {m['recall']}"
        )
    base = report["baseline"]["test"]
    print(f"  baseline   IoU {base['iou']} dice {base['dice']} "
          f"({report['baseline']['calibration']['config']['mode']} reference, "
          f"{report['baseline']['calibration']['config']['contrast_db']} dB)")
    print(f"  delta      {report['comparison']['iouDelta']:+.4f} IoU over the baseline")
    print(f"  metrics    {C.METRICS_PATH}")
    print(f"  checkpoint {C.CHECKPOINT_PATH}")
    print("")
    print("Limitations recorded in metrics.json:")
    for item in report["limitations"]:
        print(f"  - {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
