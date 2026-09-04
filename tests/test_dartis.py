"""DARTIS 2019 reader tests: the index is the label source, so it must be read exactly.

Every negative example this project has comes through this reader. A column found by
position instead of by name, a patch with three oil objects counted as three patches, or
a spacing assumed to be 10 m when the archive is 20 m would each corrupt the only
false-positive rate the project can quote -- quietly, and in the direction that flatters
it. The fixtures below are hand-written to the real export's shape, taken from
``data/raw/dartis2019/DARTIS_2019.tab``: a 49-line citation block closed by ``*/``, then
one header row, then one row per annotated object rather than per patch.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from spilltrace_ml import dartis

HEADER = "\t".join(
    [
        "Image set (subset; oc : oil/coast; ow : ...)",
        "IMAGE (jpg_file)",
        "Binary (xml_file)",
        "ID (tag)",
        "ID (patch_name)",
        "Date/Time (start_time)",
        "Date/Time (end_time)",
        "ID (Sentinel_ID)",
        "Width [pixel] (patch_width)",
        "Height [pixel] (patch_height)",
        "Longitude (patch_ul_lon)",
        "Latitude (patch_ul_lat)",
        "Longitude (patch_ur_lon)",
        "Latitude (patch_ur_lat)",
        "Longitude (patch_br_lon)",
        "Latitude (patch_br_lat)",
        "Longitude (patch_bl_lon)",
        "Latitude (patch_bl_lat)",
        "Pos X [pixel] (obj_patchloc_xmin)",
        "Pos Y [pixel] (obj_patchloc_ymin)",
        "Pos X [pixel] (obj_patchloc_xmax)",
        "Pos Y [pixel] (obj_patchloc_ymax)",
        "Size [pixel] (label_size)",
    ]
)

# 640 px across 0.128 degrees of longitude at the equator is about 22 m/pixel, close to
# the archive's real spacing and far enough from this project's 9.5 m to matter.
CORNERS = ["0.0", "0.064", "0.128", "0.064", "0.128", "-0.064", "0.0", "-0.064"]


def row(
    subset: str,
    name: str,
    *,
    tag: str = "t",
    patch: str = "S1_20190101_034235",
    sentinel: str = "S1B_IW_GRDH_1SDV_20190101.SAFE",
    box: tuple[str, str, str, str] | None = None,
    xml: str = "",
    corners: list[str] | None = None,
) -> str:
    box_cells = list(box) if box else ["", "", "", ""]
    return "\t".join(
        [
            subset,
            name,
            xml,
            tag,
            patch,
            "2019-01-01T03:42:35",
            "2019-01-01T03:43:50",
            sentinel,
            "640",
            "640",
            *(corners or CORNERS),
            *box_cells,
            "1058",
        ]
    )


def write_index(tmp_path, rows: list[str], *, name: str = "DARTIS_2019.tab"):
    """A file with the real citation block, so the block-skipping is exercised."""
    block = ["/*"] + [f"comment line {n}" for n in range(47)] + ["*/"]
    path = tmp_path / name
    path.write_text("\n".join(block + [HEADER] + rows) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------

def test_the_citation_block_is_skipped_and_the_header_is_found_by_name(tmp_path) -> None:
    path = write_index(tmp_path, [row("ow", "ow-0001.jpg", box=("10", "20", "30", "50"))])
    patches = dartis.read_index(path)
    assert len(patches) == 1
    patch = patches[0]
    assert patch.name == "ow-0001.jpg"
    assert patch.subset == "ow"
    assert patch.width == 640 and patch.height == 640
    assert patch.boxes == (dartis.Box(10, 20, 30, 50),)
    assert patch.start is not None and patch.start.tzinfo is not None
    assert patch.start.year == 2019


def test_three_objects_in_one_patch_are_one_patch_with_three_boxes(tmp_path) -> None:
    """5515 index rows are 3655 patches. Getting this wrong triples the oil count."""
    path = write_index(
        tmp_path,
        [
            row("ow", "ow-0001.jpg", box=("10", "10", "20", "20")),
            row("ow", "ow-0001.jpg", box=("30", "30", "50", "60")),
            row("ow", "ow-0001.jpg", box=("100", "100", "140", "180")),
            row("ow", "ow-0002.jpg", box=("5", "5", "9", "9")),
        ],
    )
    patches = dartis.read_index(path)
    assert [p.name for p in patches] == ["ow-0001.jpg", "ow-0002.jpg"]
    assert len(patches[0].boxes) == 3
    assert patches[0].boxes[1].area_px == 20 * 30
    summary = dartis.summarise(patches)
    assert summary["patches"] == 2
    assert summary["annotatedObjects"] == 4


def test_a_no_oil_patch_has_no_boxes_and_keeps_its_kmeans_cluster(tmp_path) -> None:
    """The cluster field is what lets a false-positive rate be reported per family."""
    path = write_index(
        tmp_path,
        [
            row("nw", "nw-0123-07-000456.jpg"),
            row("nc", "nc-0001-00-000001.jpg"),
            row("ow", "ow-0001.jpg", box=("1", "1", "5", "5")),
        ],
    )
    patches = {p.name: p for p in dartis.read_index(path)}
    assert patches["nw-0123-07-000456.jpg"].cluster == "07"
    assert patches["nw-0123-07-000456.jpg"].boxes == ()
    assert patches["nw-0123-07-000456.jpg"].has_oil is False
    assert patches["nc-0001-00-000001.jpg"].cluster == "00"
    assert patches["nc-0001-00-000001.jpg"].coastal is True
    # An oil patch has no cluster field in its filename at all.
    assert patches["ow-0001.jpg"].cluster is None
    assert patches["ow-0001.jpg"].has_oil is True
    assert patches["ow-0001.jpg"].coastal is False

    summary = dartis.summarise(patches.values())
    assert summary["oilPatches"] == 1 and summary["noOilPatches"] == 2
    assert summary["byCluster"] == {"nc-00": 1, "nw-07": 1}
    assert summary["bySubset"] == {"nc": 1, "nw": 1, "ow": 1}


def test_a_zero_area_box_is_dropped_rather_than_published(tmp_path) -> None:
    path = write_index(
        tmp_path,
        [
            row("ow", "ow-0001.jpg", box=("10", "10", "10", "40")),
            row("ow", "ow-0001.jpg", box=("10", "10", "40", "40")),
        ],
    )
    patch = dartis.read_index(path)[0]
    assert len(patch.boxes) == 1


def test_a_long_subset_label_and_a_short_row_are_both_tolerated(tmp_path) -> None:
    """PANGAEA exports the subset as a code or as "code: description"; both appear."""
    truncated = row("ow: oil/open water", "ow-0009.jpg").rsplit("\t", 3)[0]
    path = write_index(tmp_path, [truncated])
    patch = dartis.read_index(path)[0]
    assert patch.subset == "ow"
    assert patch.boxes == ()


def test_a_missing_column_is_an_error_naming_the_column(tmp_path) -> None:
    """Reading by position instead of by name is the failure this prevents."""
    path = tmp_path / "broken.tab"
    path.write_text(
        "/*\n*/\n" + HEADER.replace("(Sentinel_ID)", "(something_else)") + "\n",
        encoding="utf-8",
    )
    with pytest.raises(dartis.DartisError, match="Sentinel_ID"):
        dartis.read_index(path)


def test_an_index_with_no_rows_after_its_metadata_is_an_error(tmp_path) -> None:
    path = tmp_path / "empty.tab"
    path.write_text("/*\ncitation\n*/\n", encoding="utf-8")
    with pytest.raises(dartis.DartisError, match="no rows"):
        dartis.read_index(path)


# ---------------------------------------------------------------------------
# Locating the export
# ---------------------------------------------------------------------------

def test_the_index_is_found_by_extension_not_by_name(tmp_path) -> None:
    path = write_index(tmp_path, [row("ow", "ow-0001.jpg")], name="whatever-they-called-it.tab")
    assert dartis.find_index(tmp_path) == path


def test_a_missing_index_says_how_to_get_one(tmp_path) -> None:
    with pytest.raises(dartis.DartisError, match="PANGAEA"):
        dartis.find_index(tmp_path)


def test_two_candidate_indexes_are_refused_rather_than_picked_between(tmp_path) -> None:
    write_index(tmp_path, [row("ow", "ow-0001.jpg")], name="a.tab")
    write_index(tmp_path, [row("ow", "ow-0002.jpg")], name="b.tsv")
    with pytest.raises(dartis.DartisError, match="several candidate"):
        dartis.find_index(tmp_path)


# ---------------------------------------------------------------------------
# Geometry and imagery
# ---------------------------------------------------------------------------

def test_the_spacing_comes_from_the_published_corners(tmp_path) -> None:
    """~20 m/pixel, not the 10 m this project's own scenes use."""
    path = write_index(tmp_path, [row("ow", "ow-0001.jpg")])
    patch = dartis.read_index(path)[0]
    rows_m, cols_m = patch.spacing_m()
    expected = 0.128 * math.pi / 180.0 * dartis.EARTH_RADIUS_M / 640.0
    assert rows_m == pytest.approx(expected, rel=1e-3)
    assert cols_m == pytest.approx(expected, rel=1e-3)
    assert 20.0 < rows_m < 26.0
    lon, lat = patch.centre()
    assert lon == pytest.approx(0.064, abs=1e-6)
    assert lat == pytest.approx(0.0, abs=1e-6)


def test_a_patch_with_fewer_than_four_corners_refuses_to_guess_a_spacing(tmp_path) -> None:
    broken = row("ow", "ow-0001.jpg", corners=["", "", "", "", "", "", "", ""])
    path = write_index(tmp_path, [broken])
    patch = dartis.read_index(path)[0]
    assert patch.corners == ()
    with pytest.raises(dartis.DartisError, match="4 corners"):
        patch.spacing_m()


def test_patches_are_grouped_by_parent_product_for_splitting(tmp_path) -> None:
    """Two crops of one pass must not land on opposite sides of a fold."""
    path = write_index(
        tmp_path,
        [
            row("nw", "nw-0001-00-000001.jpg", sentinel="S1A_PASS_ONE.SAFE"),
            row("nw", "nw-0002-00-000002.jpg", sentinel="S1A_PASS_ONE.SAFE"),
            row("nw", "nw-0003-00-000003.jpg", sentinel="S1A_PASS_TWO.SAFE"),
        ],
    )
    patches = dartis.read_index(path)
    assert len({p.product for p in patches}) == 2


def test_only_patches_whose_jpeg_exists_are_reported_as_present(tmp_path) -> None:
    """A partial download is the normal state, so it must be measurable."""
    path = write_index(
        tmp_path,
        [row("nw", "nw-0001-00-000001.jpg"), row("nw", "nw-0002-00-000002.jpg")],
    )
    patches = dartis.read_index(path)
    images = tmp_path / "images"
    images.mkdir()
    cv2.imwrite(str(images / "nw-0001-00-000001.jpg"), np.full((640, 640), 120, np.uint8))
    present = dartis.present(patches, images)
    assert [p.name for p in present] == ["nw-0001-00-000001.jpg"]


def test_an_image_is_resampled_to_this_projects_pixel_spacing(tmp_path) -> None:
    """A 20 m patch shown to a model trained at 9.5 m halves every slick it learned."""
    path = write_index(tmp_path, [row("ow", "ow-0001.jpg")])
    patch = dartis.read_index(path)[0]
    images = tmp_path / "images"
    images.mkdir()
    rng = np.random.default_rng(4)
    cv2.imwrite(
        str(images / "ow-0001.jpg"),
        rng.integers(40, 200, (640, 640), dtype=np.uint8),
    )

    native_plane, native = dartis.load_image(patch, images)
    assert native_plane.shape == (640, 640)
    assert native_plane.dtype == np.float32
    assert 20.0 < native < 26.0

    plane, spacing = dartis.load_image(patch, images, target_spacing_m=9.5)
    assert spacing == pytest.approx(9.5)
    factor = native / 9.5
    assert plane.shape[0] == pytest.approx(round(640 * factor), abs=1)
    assert plane.shape[0] > 1300  # grown, because the patch was coarser

    # Asking for the spacing it already has is a no-op, not a resample.
    same, _ = dartis.load_image(patch, images, target_spacing_m=native)
    assert same.shape == (640, 640)


def test_a_missing_or_unreadable_image_is_an_error_naming_the_file(tmp_path) -> None:
    path = write_index(tmp_path, [row("ow", "ow-0001.jpg")])
    patch = dartis.read_index(path)[0]
    with pytest.raises(dartis.DartisError, match="ow-0001.jpg"):
        dartis.load_image(patch, tmp_path / "images")


def test_the_oil_mask_scales_the_boxes_to_the_resampled_shape(tmp_path) -> None:
    """Boxes are in 640 px patch coordinates; the plane may no longer be 640 px."""
    path = write_index(tmp_path, [row("ow", "ow-0001.jpg", box=("160", "320", "320", "480"))])
    patch = dartis.read_index(path)[0]

    native = dartis.oil_mask(patch, (640, 640))
    assert native[320:480, 160:320].all()
    assert native.sum() == 160 * 160

    scaled = dartis.oil_mask(patch, (1280, 1280))
    assert scaled[640:960, 320:640].all()
    assert scaled.sum() == pytest.approx(320 * 320, rel=0.02)

    # A box beyond the shape is clipped, not wrapped or raised.
    clipped = dartis.oil_mask(patch, (200, 200))
    assert clipped.shape == (200, 200)
    assert clipped.sum() > 0


def test_a_patch_with_no_boxes_yields_an_empty_mask(tmp_path) -> None:
    path = write_index(tmp_path, [row("nw", "nw-0001-00-000001.jpg")])
    patch = dartis.read_index(path)[0]
    assert not dartis.oil_mask(patch, (640, 640)).any()


def test_the_subset_codes_cover_the_four_the_archive_publishes() -> None:
    assert set(dartis.SUBSET_LABELS) == {"oc", "ow", "nc", "nw"}
    assert dartis.OIL_SUBSETS == {"oc", "ow"}
    assert dartis.NO_OIL_SUBSETS == {"nc", "nw"}
    assert dartis.OIL_SUBSETS.isdisjoint(dartis.NO_OIL_SUBSETS)
