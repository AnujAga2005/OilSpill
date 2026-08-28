"""Splits, patch selection and normalisation.

The claim these tests exist to defend is the one every accuracy figure on the product rests
on: no crop of a Sentinel-1 acquisition appears in more than one split. If that is ever
false, the reported test IoU is measuring memorisation and every number downstream of it is
worthless. Everything else here is secondary.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from spilltrace_common import config as C
from spilltrace_ml import cache
from spilltrace_ml import dataset as ds
from spilltrace_ml.dataset import LABEL_BACKGROUND, LABEL_INVALID, LABEL_OIL


def scene_records(count: int, crops_per_group: int = 1) -> list[dict]:
    """`count` groups, each cut into `crops_per_group` named scenes."""
    out = []
    for group in range(count):
        for crop in range(crops_per_group):
            out.append({
                "name": f"S1A_{group:03d}_{crop:02d}",
                "group_key": f"S1A_{group:03d}",
            })
    return out


def patch(
    oil: float = 0.0,
    invalid: float = 0.0,
    size: int = 8,
    vv: float = -20.0,
    row: int = 0,
    col: int = 0,
    scene: str = "s",
) -> ds.Patch:
    """A patch with an exact oil and invalid fraction, for selection arithmetic."""
    target = np.full((size, size), LABEL_BACKGROUND, dtype=np.uint8)
    flat = target.reshape(-1)
    total = flat.size
    n_oil = int(round(oil * total))
    n_invalid = int(round(invalid * total))
    flat[:n_oil] = LABEL_OIL
    flat[total - n_invalid:] = LABEL_INVALID
    channels = np.stack([
        np.full((size, size), vv, dtype=np.float32),
        np.full((size, size), vv - 5.0, dtype=np.float32),
    ])
    return ds.Patch(scene=scene, row=row, col=col, channels=channels, target=target)


# -- the leakage guarantee ---------------------------------------------------

class TestSplitLeakage:
    def test_every_crop_of_an_acquisition_lands_in_one_split(self):
        splits = ds.make_splits(scene_records(60, crops_per_group=4))
        for group in range(60):
            names = [f"S1A_{group:03d}_{crop:02d}" for crop in range(4)]
            assigned = {ds.split_of(splits, name) for name in names}
            assert len(assigned) == 1, f"group {group} spans {assigned}"

    def test_no_scene_appears_in_two_splits(self):
        splits = ds.make_splits(scene_records(40, crops_per_group=3))
        scenes = splits["scenes"]
        for a, b in (("train", "val"), ("train", "test"), ("val", "test")):
            assert not set(scenes[a]) & set(scenes[b]), f"{a} and {b} overlap"

    def test_every_scene_is_assigned_exactly_once(self):
        records = scene_records(40, crops_per_group=3)
        splits = ds.make_splits(records)
        placed = splits["scenes"]["train"] + splits["scenes"]["val"] + splits["scenes"]["test"]
        assert sorted(placed) == sorted(r["name"] for r in records)
        assert len(placed) == len(set(placed))

    def test_group_key_is_what_groups_and_not_the_scene_name(self):
        """Two differently-named scenes sharing a group key must not be separated."""
        records = [
            {"name": "zzz_last", "group_key": "shared"},
            {"name": "aaa_first", "group_key": "shared"},
        ]
        splits = ds.make_splits(records)
        assert ds.split_of(splits, "zzz_last") == ds.split_of(splits, "aaa_first")

    def test_camel_case_group_key_is_honoured_too(self):
        records = [
            {"name": "one", "groupKey": "shared"},
            {"name": "two", "groupKey": "shared"},
        ]
        splits = ds.make_splits(records)
        assert ds.split_of(splits, "one") == ds.split_of(splits, "two")

    def test_a_scene_with_no_group_key_groups_by_itself(self):
        splits = ds.make_splits([{"name": "solo"}])
        assert ds.split_of(splits, "solo") is not None
        assert splits["groups"]["solo"] in ("train", "val", "test")


class TestAuditGroupKeysReachTheSplitter:
    """The wiring between the audit report and `make_splits`.

    `make_splits` groups correctly when it is handed a group key, and the tests above prove
    it. The leak this class defends against is upstream: if `annotate_from_audit` reads a
    field name the audit never writes, every key is None, every crop becomes its own group,
    and `make_splits` honours a guarantee it was never given anything to honour with. The
    splitter looks correct and the split is still per-crop.
    """

    def _audit(self, tmp_path, monkeypatch, scenes):
        path = tmp_path / "audit.json"
        C.write_json(path, {"scenes": scenes})
        monkeypatch.setattr(C, "AUDIT_JSON", path)
        return path

    def _pair(self, name):
        return cache.ScenePair(name=name, image=Path(f"{name}.tif"), mask=Path(f"{name}.tif"))

    def test_the_snake_case_the_audit_actually_writes_is_read(self, tmp_path, monkeypatch):
        self._audit(tmp_path, monkeypatch, [{
            "name": "00000",
            "group_key": "S1A_IW_GRDH_1SDV_20180803T172551",
            "acquired_start": "2018-08-03T17:25:57.581Z",
        }])
        annotated = cache.annotate_from_audit([self._pair("00000")])
        assert annotated[0].group_key == "S1A_IW_GRDH_1SDV_20180803T172551"
        assert annotated[0].acquired == "2018-08-03T17:25:57.581Z"

    def test_camel_case_is_read_too(self, tmp_path, monkeypatch):
        self._audit(tmp_path, monkeypatch, [{
            "name": "00000",
            "groupKey": "S1A_PRODUCT",
            "acquiredStart": "2018-08-03T17:25:57.581Z",
        }])
        annotated = cache.annotate_from_audit([self._pair("00000")])
        assert annotated[0].group_key == "S1A_PRODUCT"
        assert annotated[0].acquired == "2018-08-03T17:25:57.581Z"

    def test_sibling_crops_of_one_acquisition_end_up_in_one_split(self, tmp_path, monkeypatch):
        """The end-to-end claim: audit on disk in, single split out."""
        scenes = [
            {"name": f"{group:05d}{crop}", "group_key": f"S1A_{group:03d}"}
            for group in range(30)
            for crop in range(3)
        ]
        self._audit(tmp_path, monkeypatch, scenes)
        annotated = cache.annotate_from_audit([self._pair(s["name"]) for s in scenes])
        splits = ds.make_splits([
            {"name": p.name, "group_key": p.group_key} for p in annotated
        ])
        for group in range(30):
            assigned = {ds.split_of(splits, f"{group:05d}{crop}") for crop in range(3)}
            assert len(assigned) == 1, f"group {group} spans {assigned}"
        # And the grouping is coarser than the crop list -- the tell that keys arrived at
        # all. One entry per crop would mean every key came back None.
        assert set(splits["groups"]) == {f"S1A_{group:03d}" for group in range(30)}
        assert splits["groupCounts"]["train"] + splits["groupCounts"]["val"] + \
            splits["groupCounts"]["test"] == 30


class TestSplitDeterminism:
    def test_the_same_input_gives_the_same_split(self):
        records = scene_records(50, crops_per_group=2)
        first = ds.make_splits(records)
        second = ds.make_splits(records)
        assert first["scenes"] == second["scenes"]

    def test_input_order_does_not_change_the_split(self):
        records = scene_records(50, crops_per_group=2)
        forward = ds.make_splits(records)
        backward = ds.make_splits(list(reversed(records)))
        assert forward["scenes"] == backward["scenes"]

    def test_adding_a_scene_does_not_move_the_existing_ones(self):
        """The stated reason for hashing rather than shuffling. Worth pinning."""
        records = scene_records(50)
        before = ds.make_splits(records)
        after = ds.make_splits(records + [{"name": "new", "group_key": "S1A_999"}])
        for record in records:
            assert ds.split_of(before, record["name"]) == ds.split_of(after, record["name"])

    def test_a_different_seed_gives_a_different_split(self):
        records = scene_records(60)
        a = ds.make_splits(records, C.PreprocessConfig(seed=1))
        b = ds.make_splits(records, C.PreprocessConfig(seed=2))
        assert a["scenes"] != b["scenes"]

    def test_assignment_does_not_depend_on_pythonhashseed(self):
        """`_stable_unit` uses SHA-256 precisely so this holds across processes."""
        assert ds._stable_unit("S1A_000", 1337) == ds._stable_unit("S1A_000", 1337)
        assert ds._stable_unit("S1A_000", 1337) != ds._stable_unit("S1A_001", 1337)
        assert 0.0 <= ds._stable_unit("anything", 0) < 1.0


class TestSplitProportions:
    def test_fractions_are_roughly_honoured_over_many_groups(self):
        cfg = C.PreprocessConfig()
        splits = ds.make_splits(scene_records(600), cfg)
        counts = splits["groupCounts"]
        total = sum(counts.values())
        assert counts["test"] / total == pytest.approx(cfg.test_fraction, abs=0.05)
        assert counts["val"] / total == pytest.approx(cfg.val_fraction, abs=0.05)

    def test_every_split_is_non_empty_on_a_tiny_dataset(self):
        splits = ds.make_splits(scene_records(3))
        assert all(splits["scenes"][name] for name in ("train", "val", "test"))

    def test_reported_counts_match_the_lists(self):
        splits = ds.make_splits(scene_records(30, crops_per_group=2))
        for name, count in splits["counts"].items():
            assert count == len(splits["scenes"][name])

    def test_the_rule_is_published_with_the_split(self):
        """A split document without its rule cannot be audited later."""
        splits = ds.make_splits(scene_records(10))
        assert "group" in splits["rule"]
        assert splits["seed"] == C.PreprocessConfig().seed

    def test_split_of_returns_none_for_an_unknown_scene(self):
        splits = ds.make_splits(scene_records(10))
        assert ds.split_of(splits, "not_a_scene") is None


# -- patch selection --------------------------------------------------------

class TestSelectPatches:
    def test_patches_over_the_invalid_budget_are_dropped(self):
        cfg = C.PreprocessConfig()
        good = patch(oil=0.5, invalid=0.0, row=0)
        bad = patch(oil=0.5, invalid=0.9, row=8)
        kept = ds.select_patches([good, bad], cfg)
        assert bad not in kept
        assert good in kept

    def test_patches_below_the_oil_floor_are_not_positives(self):
        cfg = C.PreprocessConfig(min_oil_fraction=0.25, negatives_per_positive=0.0)
        weak = patch(oil=0.05, row=0)
        kept = ds.select_patches([weak], cfg)
        assert kept == []

    def test_a_partially_oiled_patch_is_neither_positive_nor_negative(self):
        """Below the floor but not empty: keeping it as a negative would be a wrong label."""
        cfg = C.PreprocessConfig(min_oil_fraction=0.25, negatives_per_positive=10.0)
        # Fractions exact in 64ths, so the assertion is about selection and not rounding.
        patches = [patch(oil=0.375, row=0), patch(oil=0.0625, row=8)]
        kept = ds.select_patches(patches, cfg)
        assert len(kept) == 1
        assert kept[0].oil_fraction == pytest.approx(0.375)

    def test_negatives_are_capped_by_the_ratio(self):
        cfg = C.PreprocessConfig(min_oil_fraction=0.05, negatives_per_positive=1.0)
        patches = [patch(oil=0.5, row=0)] + [patch(oil=0.0, row=8 * (i + 1)) for i in range(20)]
        kept = ds.select_patches(patches, cfg)
        assert sum(1 for p in kept if p.oil_fraction == 0.0) == 1

    def test_positives_are_capped_by_the_per_scene_budget(self):
        cfg = C.PreprocessConfig(
            min_oil_fraction=0.05, negatives_per_positive=0.0, max_patches_per_scene=4
        )
        patches = [patch(oil=0.1 + i * 0.01, row=8 * i) for i in range(20)]
        kept = ds.select_patches(patches, cfg)
        assert len(kept) <= 4

    def test_the_strongest_positives_survive_the_cap(self):
        cfg = C.PreprocessConfig(
            min_oil_fraction=0.05, negatives_per_positive=0.0, max_patches_per_scene=2
        )
        patches = [patch(oil=f, row=8 * i) for i, f in enumerate([0.125, 0.875, 0.25, 0.75])]
        kept = ds.select_patches(patches, cfg)
        assert sorted(round(p.oil_fraction, 4) for p in kept) == [0.75, 0.875]

    def test_selection_is_deterministic_for_a_seed(self):
        cfg = C.PreprocessConfig(min_oil_fraction=0.05, negatives_per_positive=2.0)
        patches = [patch(oil=0.5, row=0)] + [
            patch(oil=0.0, row=8 * (i + 1), vv=-30.0 + i) for i in range(30)
        ]
        a = ds.select_patches(patches, cfg, np.random.default_rng(7))
        b = ds.select_patches(patches, cfg, np.random.default_rng(7))
        assert [(p.row, p.col) for p in a] == [(p.row, p.col) for p in b]

    def test_output_is_in_raster_order(self):
        cfg = C.PreprocessConfig(min_oil_fraction=0.05, negatives_per_positive=1.0)
        patches = [patch(oil=0.5, row=r) for r in (32, 0, 16)]
        kept = ds.select_patches(patches, cfg)
        assert [p.row for p in kept] == sorted(p.row for p in kept)

    def test_no_positives_means_no_negatives_either(self):
        """Negatives are sampled in proportion to positives, so zero begets zero."""
        cfg = C.PreprocessConfig(min_oil_fraction=0.25, negatives_per_positive=3.0)
        kept = ds.select_patches([patch(oil=0.0, row=8 * i) for i in range(10)], cfg)
        assert kept == []

    def test_an_empty_input_is_not_an_error(self):
        assert ds.select_patches([], C.PreprocessConfig()) == []


class TestPatchProperties:
    def test_oil_fraction_counts_only_oil(self):
        assert patch(oil=0.25).oil_fraction == pytest.approx(0.25)

    def test_invalid_fraction_counts_only_invalid(self):
        assert patch(oil=0.0, invalid=0.5).invalid_fraction == pytest.approx(0.5)

    def test_invalid_pixels_are_not_counted_as_oil(self):
        p = patch(oil=0.25, invalid=0.25)
        assert p.oil_fraction + p.invalid_fraction == pytest.approx(0.5)


# -- normalisation ----------------------------------------------------------

class TestNormStats:
    def test_invalid_pixels_are_excluded_from_the_mean(self):
        target = np.full((4, 4), LABEL_BACKGROUND, dtype=np.uint8)
        target[0, :] = LABEL_INVALID
        channels = np.zeros((2, 4, 4), dtype=np.float32)
        channels[:, 0, :] = 1000.0  # only under the invalid row
        channels[:, 1:, :] = -20.0
        p = ds.Patch(scene="s", row=0, col=0, channels=channels, target=target)
        stats = ds.compute_norm_stats([p])
        assert stats["mean"][0] == pytest.approx(-20.0, abs=1e-3)

    def test_an_all_invalid_patch_raises_rather_than_returning_nonsense(self):
        target = np.full((4, 4), LABEL_INVALID, dtype=np.uint8)
        channels = np.zeros((2, 4, 4), dtype=np.float32)
        p = ds.Patch(scene="s", row=0, col=0, channels=channels, target=target)
        with pytest.raises(ds.PreprocessError):
            ds.compute_norm_stats([p])

    def test_the_source_of_the_statistics_is_recorded(self):
        stats = ds.compute_norm_stats([patch(oil=0.0, vv=-18.0)])
        assert "training split" in stats["source"]
        assert stats["channels"] == list(ds.CHANNEL_ORDER)
        assert stats["patchesUsed"] == 1


class TestNormaliseBatch:
    stats = {"mean": [-20.0, -25.0], "std": [4.0, 5.0], "clipLow": [-40.0, -45.0],
             "clipHigh": [0.0, -5.0]}

    def test_channel_first_in_channels_last_out(self):
        batch = np.zeros((3, 2, 8, 8), dtype=np.float32)
        out = ds.normalise_batch(batch, self.stats)
        assert out.shape == (3, 8, 8, 2)

    def test_the_transpose_keeps_each_channel_with_itself(self):
        batch = np.zeros((1, 2, 4, 4), dtype=np.float32)
        batch[0, 0] = -20.0  # exactly the VV mean
        batch[0, 1] = -25.0  # exactly the VH mean
        out = ds.normalise_batch(batch, self.stats)
        assert out[0, :, :, 0] == pytest.approx(0.0)
        assert out[0, :, :, 1] == pytest.approx(0.0)

    def test_standardisation_uses_the_supplied_mean_and_std(self):
        batch = np.full((1, 2, 2, 2), -16.0, dtype=np.float32)
        out = ds.normalise_batch(batch, self.stats)
        assert out[0, 0, 0, 0] == pytest.approx((-16.0 + 20.0) / 4.0)

    def test_values_outside_the_clip_range_are_bounded(self):
        batch = np.full((1, 2, 2, 2), 1e6, dtype=np.float32)
        out = ds.normalise_batch(batch, self.stats)
        assert out[0, 0, 0, 0] == pytest.approx((0.0 + 20.0) / 4.0)

    def test_a_wrong_rank_is_rejected_rather_than_broadcast(self):
        with pytest.raises(ds.PreprocessError):
            ds.normalise_batch(np.zeros((2, 8, 8), dtype=np.float32), self.stats)

    def test_batch_and_single_paths_agree(self):
        """Two code paths, one definition of normalisation. They must not drift apart."""
        single = np.stack([
            np.linspace(-40, 0, 16, dtype=np.float32).reshape(4, 4),
            np.linspace(-45, -5, 16, dtype=np.float32).reshape(4, 4),
        ])
        via_single = ds.normalise(single, self.stats)
        via_batch = ds.normalise_batch(single[None, ...], self.stats)[0]
        assert via_batch == pytest.approx(np.transpose(via_single, (1, 2, 0)), abs=1e-5)

    def test_a_zero_standard_deviation_does_not_divide_by_zero(self):
        stats = {"mean": [0.0, 0.0], "std": [0.0, 0.0]}
        out = ds.normalise_batch(np.ones((1, 2, 2, 2), dtype=np.float32), stats)
        assert np.isfinite(out).all()

    def test_nan_input_does_not_propagate(self):
        batch = np.full((1, 2, 2, 2), np.nan, dtype=np.float32)
        out = ds.normalise_batch(batch, self.stats)
        assert np.isfinite(out).all()
