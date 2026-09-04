"""Look-alike screening tests: the scale-free claim, the fold discipline, the wiring.

The load-bearing claim in ``lookalike.py`` is that its seven model features survive an
arbitrary affine rescaling of the pixel values, because that is the only reason one
screen may be fitted on this project's calibrated decibels and then measured on the
DARTIS look-alike set, which is 8-bit JPEG with an unpublished stretch. If that claim
is false the cross-domain number means nothing, so it is tested first and directly.

The rest guards the things that would fail quietly: a fold that straddles a satellite
product, a saved screen that scores differently once reloaded, and a verdict that
reaches the map but not the GeoJSON a client actually reads.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace

import numpy as np
import pytest

from spilltrace_api import case as case_mod
from spilltrace_common.geotiff import Affine
from spilltrace_ml import lookalike as L
from spilltrace_ml.geometry import GeometryConfig, analyse, denoise

PIXEL_DEG = 8.9831528e-05


def transform_at(lat: float = 18.0, lon: float = 72.0) -> Affine:
    return Affine(lon, PIXEL_DEG, 0.0, lat, 0.0, -PIXEL_DEG)


def synthetic_scene(
    shape: tuple[int, int] = (256, 256),
    seed: int = 11,
) -> tuple[np.ndarray, np.ndarray]:
    """A decibel-like plane with one elongated dark film in it, and its mask."""
    rng = np.random.default_rng(seed)
    plane = (-21.5 + rng.normal(0.0, 1.0, shape)).astype(np.float32)
    mask = np.zeros(shape, dtype=bool)
    mask[60:100, 40:170] = True
    # A film is darker *and* smoother than the water: both are features.
    plane[mask] = (-28.5 + rng.normal(0.0, 0.35, int(mask.sum()))).astype(np.float32)
    return plane, mask


# ---------------------------------------------------------------------------
# The scale-free claim
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "gain,offset",
    [(1.0, 0.0), (2.5, 0.0), (0.4, 17.0), (100.0, -3.0), (255.0 / 21.9, 35.5)],
)
def test_the_seven_model_features_survive_any_affine_rescaling(gain, offset) -> None:
    """Fitted on decibels, measured on 8-bit JPEG: this is what licenses that.

    The last case is roughly the real mapping between this project's clip range and
    DARTIS's 8-bit digital numbers.
    """
    plane, mask = synthetic_scene()
    base = L.region_features(plane, mask, spacing_m=9.5, plane_unit="dB")
    assert base is not None

    rescaled = L.region_features(
        (plane * gain + offset).astype(np.float32),
        mask,
        spacing_m=9.5,
        plane_unit="DN",
    )
    assert rescaled is not None
    for name in L.FEATURE_NAMES:
        # 1e-3 rather than exact: the features are rounded to five places, and a
        # rescaling moves the last of them.
        assert base[name] == pytest.approx(rescaled[name], abs=1e-3, rel=2e-3), name


def test_the_features_are_not_invariant_to_inverting_the_image() -> None:
    """Invariance is to a *positive* rescaling only, which is the honest scope.

    Oil is dark in SAR and a greyscale stretch is monotone increasing, so no mapping
    between the two domains flips the sign. If these features survived inversion they
    would have stopped measuring darkness, which is the one thing they are for.
    """
    plane, mask = synthetic_scene()
    base = L.region_features(plane, mask, plane_unit="dB")
    inverted = L.region_features((-plane).astype(np.float32), mask, plane_unit="dB")
    assert base is not None and inverted is not None
    assert inverted["darknessZ"] == pytest.approx(-base["darknessZ"], abs=1e-3)


def test_absolute_contrast_does_not_survive_rescaling_and_is_not_a_model_input() -> None:
    """The reason contrast is computed, reported, and withheld from the screen."""
    plane, mask = synthetic_scene()
    base = L.region_features(plane, mask, plane_unit="dB")
    doubled = L.region_features((plane * 2.0).astype(np.float32), mask, plane_unit="dB")
    assert base is not None and doubled is not None
    assert doubled["contrast"] == pytest.approx(2.0 * base["contrast"], rel=1e-3)
    assert "contrast" not in L.FEATURE_NAMES
    assert "contrast" in L.CONTEXT_FIELDS


def test_a_film_reads_darker_and_smoother_than_the_water_around_it() -> None:
    """The features must point the way the physics does, not merely be stable."""
    plane, mask = synthetic_scene()
    features = L.region_features(plane, mask, spacing_m=9.5, plane_unit="dB")
    assert features is not None
    assert features["darknessZ"] > 3.0
    assert features["darknessP10Z"] > features["darknessZ"] * 0.5
    assert features["textureRatio"] < 1.0
    assert features["elongation"] > 2.0
    assert features["contrastUnit"] == "dB"


def test_a_region_without_usable_water_around_it_returns_no_verdict() -> None:
    """Honest silence, not a number computed from four pixels of background."""
    plane, _ = synthetic_scene((64, 64))
    everything = np.ones((64, 64), dtype=bool)
    assert L.region_features(plane, everything) is None

    small = np.zeros((64, 64), dtype=bool)
    small[30:34, 30:34] = True
    blocked = np.ones((64, 64), dtype=bool)
    blocked[28:36, 28:36] = False
    assert L.region_features(plane, small, exclude=blocked) is None


def test_depolarisation_needs_the_second_channel_and_is_omitted_without_it() -> None:
    plane, mask = synthetic_scene()
    alone = L.region_features(plane, mask)
    paired = L.region_features(plane, mask, second_plane=(plane - 11.0).astype(np.float32))
    assert alone is not None and paired is not None
    assert "depolarisation" not in alone
    assert "depolarisation" in paired


# ---------------------------------------------------------------------------
# The proposer
# ---------------------------------------------------------------------------

def test_the_proposer_finds_the_film_without_being_told_where_it_is() -> None:
    plane, mask = synthetic_scene()
    labels, count = L.propose(plane, cfg=L.ScreenConfig(min_area_px=200))
    assert count >= 1
    # The largest proposal is stamped 1, and it must be the film.
    assert float(mask[labels == 1].mean()) > 0.8


def test_the_proposer_respects_the_valid_mask_and_the_region_cap() -> None:
    plane, mask = synthetic_scene()
    valid = np.ones(plane.shape, dtype=bool)
    valid[:, :200] = False  # hide most of the film
    labels, count = L.propose(plane, valid, L.ScreenConfig(min_area_px=100))
    assert not (labels[~valid] > 0).any()

    capped = L.ScreenConfig(min_area_px=1, open_radius_px=0, close_radius_px=0, max_regions=3)
    _, few = L.propose(plane, cfg=capped)
    assert few <= 3


def test_the_proposer_threshold_is_relative_so_it_is_unit_free() -> None:
    plane, _ = synthetic_scene()
    a, count_a = L.propose(plane, cfg=L.ScreenConfig(min_area_px=200))
    b, count_b = L.propose((plane * 3.0 - 40.0).astype(np.float32), cfg=L.ScreenConfig(min_area_px=200))
    assert count_a == count_b
    assert np.array_equal(a > 0, b > 0)


# ---------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------

def separable_rows(n: int = 120, seed: int = 3) -> tuple[list[dict], list[int], list[str]]:
    """A synthetic two-class set with the same directions the real features have."""
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    labels: list[int] = []
    groups: list[str] = []
    for index in range(n):
        oil = index % 2 == 0
        rows.append(
            {
                "darknessZ": rng.normal(5.0 if oil else 2.0, 0.8),
                "darknessP10Z": rng.normal(6.5 if oil else 2.5, 0.8),
                "textureRatio": rng.normal(0.55 if oil else 1.0, 0.1),
                "edgeSharpness": rng.normal(1.8 if oil else 1.0, 0.2),
                "compactness": rng.normal(0.3 if oil else 0.7, 0.08),
                "solidity": rng.normal(0.6 if oil else 0.92, 0.06),
                "elongation": rng.normal(3.4 if oil else 1.3, 0.5),
            }
        )
        labels.append(1 if oil else 0)
        groups.append(f"product-{index // 8:02d}")
    return rows, labels, groups


def test_an_unfitted_screen_calls_everything_uncertain_rather_than_guessing() -> None:
    """No model file is not the same as a model that says "not oil"."""
    screen = L.Screen()
    assert not screen.fitted
    result = L.verdict({name: 0.0 for name in L.FEATURE_NAMES}, screen)
    assert result["label"] == "uncertain"
    assert result["oilLikelihood"] == pytest.approx(0.5)


def test_fitting_refuses_too_few_rows_and_a_single_class() -> None:
    rows, labels, _ = separable_rows(n=120)
    with pytest.raises(ValueError, match="too few"):
        L.fit(rows[:6], labels[:6])
    with pytest.raises(ValueError, match="both classes"):
        L.fit(rows, [1] * len(rows))
    with pytest.raises(ValueError, match="rows against"):
        L.fit(rows, labels[:-1])


def test_a_fitted_screen_separates_the_two_classes_and_reports_how() -> None:
    rows, labels, _ = separable_rows()
    screen = L.fit(rows, labels)
    assert screen.fitted
    assert screen.calibration.startswith("fitted on 120 dark regions")

    report = L.measure(screen, rows, labels)
    assert report["auc"] is not None and report["auc"] > 0.95
    assert report["keptSensitivity"] > 0.9
    assert report["rejectionSpecificity"] > 0.8
    # Three-way, and the three add up to the class total on both sides.
    assert sum(report["oilOutcomes"].values()) == report["oilRegions"]
    assert sum(report["lookAlikeOutcomes"].values()) == report["lookAlikeRegions"]


def test_the_weights_point_the_way_the_feature_rationales_claim() -> None:
    """A weight that disagrees with its stated direction would make the reasons lie."""
    rows, labels, _ = separable_rows(n=400)
    screen = L.fit(rows, labels, steps=6000)
    for spec in L.FEATURES:
        weight = screen.weights[spec.name]
        assert weight * spec.oil_direction > 0, f"{spec.name} weight {weight:+.3f}"


def test_a_saved_screen_scores_identically_once_reloaded(tmp_path) -> None:
    """Within the precision the file format keeps, which is five decimal places.

    The weights are rounded on the way out so a reader can check the arithmetic by
    hand; that costs a few parts in a billion of the likelihood and buys a model
    anyone can audit, which is the trade this project makes everywhere.
    """
    rows, labels, _ = separable_rows()
    screen = L.fit(rows, labels, trained_on={"scenes": 3})
    path = screen.save(tmp_path / "screen.json")
    reloaded = L.Screen.load(path)
    for row in rows[:20]:
        assert reloaded.likelihood(row) == pytest.approx(screen.likelihood(row), abs=1e-6)
        assert L.verdict(row, reloaded)["label"] == L.verdict(row, screen)["label"]
    assert reloaded.trained_on == {"scenes": 3}
    assert reloaded.cfg.reject_at_or_below == screen.cfg.reject_at_or_below
    assert json.loads(path.read_text())["features"][0]["name"] == L.FEATURE_NAMES[0]


def test_the_verdict_explains_itself_in_terms_a_reader_can_check() -> None:
    rows, labels, _ = separable_rows()
    screen = L.fit(rows, labels)
    oil = next(row for row, label in zip(rows, labels) if label == 1)
    result = L.verdict(oil, screen)
    assert result["label"] == "accepted"
    assert 1 <= len(result["reasons"]) <= 3
    # Every reason names a real feature and quotes both fitted class averages.
    assert any(spec.label in result["reasons"][0] for spec in L.FEATURES)
    assert "fitted oil regions average" in result["reasons"][0]
    # Contributions cover all seven and are ordered by magnitude.
    magnitudes = [abs(entry["logOdds"]) for entry in result["contributions"]]
    assert len(magnitudes) == len(L.FEATURE_NAMES)
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_contributions_sum_to_the_log_odds_the_likelihood_came_from() -> None:
    rows, labels, _ = separable_rows()
    screen = L.fit(rows, labels)
    row = rows[0]
    total = sum(value for _, value in screen.contributions(row)) + screen.intercept
    assert screen.likelihood(row) == pytest.approx(1.0 / (1.0 + math.exp(-total)), abs=1e-9)


def test_a_middling_region_is_kept_for_review_rather_than_dropped() -> None:
    """Suppressing an uncertain detection trades a measured error for an unmeasured one."""
    screen = L.Screen(
        weights={name: 0.0 for name in L.FEATURE_NAMES},
        intercept=0.0,
        calibration="fitted for the purposes of this test",
    )
    result = L.verdict({name: 0.0 for name in L.FEATURE_NAMES}, screen)
    assert result["label"] == "uncertain"
    assert "human review" in result["headline"]


# ---------------------------------------------------------------------------
# Fold discipline
# ---------------------------------------------------------------------------

def test_no_satellite_product_straddles_two_folds() -> None:
    """KNOWN-ISSUES item 1, encoded as a test so it cannot come back."""
    _, _, groups = separable_rows(n=200)
    folds = L.grouped_folds(groups, folds=5)
    seen: dict[str, int] = {}
    for group, fold in zip(groups, folds):
        assert seen.setdefault(group, fold) == fold


def test_the_folds_are_deterministic_and_reasonably_balanced() -> None:
    _, _, groups = separable_rows(n=200)
    first = L.grouped_folds(groups, folds=5)
    assert first == L.grouped_folds(groups, folds=5)
    assert first != L.grouped_folds(groups, folds=5, seed=1)
    sizes = [first.count(f) for f in range(5)]
    assert min(sizes) > 0 and max(sizes) <= 2 * min(sizes)


def test_auc_matches_a_hand_computed_value_and_averages_ties() -> None:
    assert L.auc([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0]) == 1.0
    assert L.auc([0.1, 0.2, 0.8, 0.9], [1, 1, 0, 0]) == 0.0
    assert L.auc([0.5, 0.5, 0.5, 0.5], [1, 1, 0, 0]) == 0.5
    # One positive ranked above one negative and below the other: 0.5.
    assert L.auc([0.3, 0.2, 0.4], [1, 0, 0]) == 0.5
    assert L.auc([0.1, 0.2], [0, 0]) is None


def test_every_published_feature_is_declared_scale_free() -> None:
    described = L.describe_features()
    assert [entry["name"] for entry in described] == list(L.FEATURE_NAMES)
    assert all(entry["scaleFree"] for entry in described)
    assert all(entry["rationale"] for entry in described)


# ---------------------------------------------------------------------------
# Pipeline wiring
# ---------------------------------------------------------------------------

def screened_geometry(seed: int = 5):
    """Run the real geometry stage with the real screening callback attached."""
    plane, mask = synthetic_scene(seed=seed)
    planes = np.stack([plane, (plane - 11.0).astype(np.float32)])
    valid = np.ones(plane.shape, dtype=bool)
    transform = transform_at()
    cfg = GeometryConfig(min_area_px=200)
    screen = L.fit(*separable_rows()[:2])
    screen_cfg = case_mod._screen_config(screen)
    screen_cfg.min_area_px = 200
    stamp = np.zeros(plane.shape, dtype=np.int16)
    geometry = analyse(
        mask,
        transform,
        cfg=cfg,
        source="test",
        annotate=case_mod.slick_screener(
            planes=planes,
            detected=denoise(mask, cfg),
            valid=valid,
            spacing_m=9.5,
            screen=screen,
            cfg=screen_cfg,
            stamp=stamp,
        ),
    )
    return geometry, stamp, planes, valid, transform, screen, screen_cfg


def test_the_verdict_reaches_the_geojson_and_not_only_the_slick_list() -> None:
    """The GeoJSON properties are a copy, so this is the assertion that matters."""
    geometry, _, _, _, _, _, _ = screened_geometry()
    assert geometry["summary"]["componentsPublished"] == 1
    slick = geometry["slicks"][0]
    assert slick["screening"]["label"] in {"accepted", "uncertain", "rejected"}

    properties = geometry["geojson"]["features"][0]["properties"]
    assert properties["screening"] == slick["screening"]
    assert properties["id"] == slick["id"]


def test_the_screening_hook_leaves_no_private_key_in_the_output() -> None:
    geometry, _, _, _, _, _, _ = screened_geometry()
    for slick in geometry["slicks"]:
        assert not [key for key in slick if key.startswith("_")]
    for feature in geometry["geojson"]["features"]:
        assert not [key for key in feature["properties"] if key.startswith("_")]


def test_geometry_is_unchanged_when_no_callback_is_supplied() -> None:
    """The hook is optional, and the numbers must not depend on it."""
    plane, mask = synthetic_scene()
    cfg = GeometryConfig(min_area_px=200)
    plain = analyse(mask, transform_at(), cfg=cfg, source="test")
    geometry, _, _, _, _, _, _ = screened_geometry()
    assert "screening" not in plain["slicks"][0]
    assert plain["slicks"][0]["areaM2"] == geometry["slicks"][0]["areaM2"]
    assert plain["summary"] == geometry["summary"]


def test_a_dark_patch_is_attributed_to_the_slick_it_overlaps() -> None:
    geometry, stamp, planes, valid, transform, screen, screen_cfg = screened_geometry()
    block = case_mod.screen_scene(
        planes=planes,
        valid=valid,
        transform=transform,
        stamp=stamp,
        slick_ids=[entry["id"] for entry in geometry["slicks"]],
        spacing_m=9.5,
        screen=screen,
        cfg=screen_cfg,
    )
    assert block["counts"]["proposed"] >= 1
    assert block["counts"]["proposed"] == len(block["patches"])
    assert block["fitted"] is True

    film = block["patches"][0]
    assert film["overlapsSlick"] == "slick-01"
    assert film["overlapFraction"] > 0.8
    assert film["label"] == "accepted"
    lon, lat = film["centroid"]
    assert 72.0 < lon < 72.1 and 17.9 < lat < 18.0


def test_the_scene_block_states_what_it_cannot_do() -> None:
    geometry, stamp, planes, valid, transform, screen, screen_cfg = screened_geometry()
    block = case_mod.screen_scene(
        planes=planes,
        valid=valid,
        transform=transform,
        stamp=stamp,
        slick_ids=[entry["id"] for entry in geometry["slicks"]],
        spacing_m=9.5,
        screen=screen,
        cfg=screen_cfg,
    )
    joined = " ".join(block["limits"]).lower()
    for phrase in ("cannot name the phenomenon", "algae", "low wind", "uncertain"):
        assert phrase in joined
    assert "rejected" in block["headline"]
    # No phenomenon is ever named as a verdict.
    for patch in block["patches"]:
        assert "algae" not in (patch["headline"] or "").lower()


def test_an_unfitted_screen_says_so_in_the_scene_block() -> None:
    geometry, stamp, planes, valid, transform, _, screen_cfg = screened_geometry()
    block = case_mod.screen_scene(
        planes=planes,
        valid=valid,
        transform=transform,
        stamp=stamp,
        slick_ids=[entry["id"] for entry in geometry["slicks"]],
        spacing_m=9.5,
        screen=L.Screen(),
        cfg=screen_cfg,
        unfitted="no screen has been fitted",
    )
    assert block["fitted"] is False
    assert block["limits"][0] == "no screen has been fitted"
    assert {patch["label"] for patch in block["patches"]} == {"uncertain"}


# ---------------------------------------------------------------------------
# Two populations of verdicts in one block
# ---------------------------------------------------------------------------
# The patches are cut out of the scene by a darkness threshold and the regions by the
# network. On a large slick with faint margins the threshold reaches into the half-shades,
# which dilutes the darkness the screen measures, so it can reject every patch while
# accepting every region -- both statements true of their own input. The block has to carry
# both counts or the first one reads as the screen overruling the detection.


def test_the_component_verdicts_travel_beside_the_patch_counts() -> None:
    geometry, stamp, planes, valid, transform, screen, screen_cfg = screened_geometry()
    block = case_mod.screen_scene(
        planes=planes,
        valid=valid,
        transform=transform,
        stamp=stamp,
        slick_ids=[entry["id"] for entry in geometry["slicks"]],
        spacing_m=9.5,
        screen=screen,
        cfg=screen_cfg,
        components=geometry["slicks"],
    )
    assert block["components"]["screened"] == len(geometry["slicks"])
    assert block["components"]["accepted"] == 1
    assert "disagree about the same water" in block["components"]["note"]


def test_records_without_a_verdict_are_counted_as_neither() -> None:
    """A region the screen could not measure must not inflate any of the four verdicts."""
    tally = case_mod._component_tally(
        [
            {"id": "slick-01", "screening": {"label": "accepted"}},
            {"id": "slick-02", "screening": {"label": "rejected"}},
            {"id": "slick-03"},
            {"id": "slick-04", "screening": {"label": "not-a-label"}},
            "not a record at all",
        ]
    )
    assert tally == {
        "screened": 2,
        "accepted": 1,
        "uncertain": 0,
        "rejected": 1,
        "unscreened": 0,
    }


def _all_rejected(blank_stamp: bool = False):
    """Screen the fixture with thresholds that reject everything, to exercise the wording.

    The labels come from comparing the likelihood against the two configured thresholds, so
    moving the thresholds is the honest way to produce a rejection here: the features, the
    weights and the image are the fixture's own. A reject threshold of 1.0 catches every
    likelihood there is, and the fixture's film scores above 0.999.
    """
    geometry, stamp, planes, valid, transform, screen, screen_cfg = screened_geometry()
    strict = replace(screen_cfg, reject_at_or_below=1.0, accept_at_or_above=1.1)
    return case_mod.screen_scene(
        planes=planes,
        valid=valid,
        transform=transform,
        stamp=np.zeros_like(stamp) if blank_stamp else stamp,
        slick_ids=[entry["id"] for entry in geometry["slicks"]],
        spacing_m=9.5,
        screen=screen,
        cfg=strict,
        components=geometry["slicks"],
    )


def test_the_headline_says_when_every_rejected_patch_is_on_a_published_slick() -> None:
    block = _all_rejected()
    assert block["counts"]["rejected"] == block["counts"]["proposed"] >= 1
    assert block["counts"]["overlappingPublishedSlick"] == block["counts"]["rejected"]
    assert "every one of them lies inside a published slick" in block["headline"]
    assert "not the detection" in block["headline"]


def test_a_rejection_that_touches_no_slick_is_not_dressed_up_as_one() -> None:
    block = _all_rejected(blank_stamp=True)
    assert block["counts"]["rejected"] >= 1
    assert block["counts"]["overlappingPublishedSlick"] == 0
    assert "lies inside a published slick" not in block["headline"]
    assert all(patch["overlapsSlick"] is None for patch in block["patches"])
