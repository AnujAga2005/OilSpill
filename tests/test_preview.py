"""The PNG encoder and the raster overlays the dashboard renders.

Every image on the product goes through `write_png`, which is written by hand against the
specification because no image library is available. That makes this the one module whose
failure mode is invisible to Python: a wrong CRC, a wrong colour type or a mis-declared
scanline filter produces a file that every function here still accepts and that a browser
silently refuses to draw. So these tests do not read the encoder's output with the encoder's
own assumptions -- they decode it with an independent decoder written below, which parses the
chunk stream and inflates the pixels the way a renderer would.

The overlay tests are about honesty rather than aesthetics. A preview is transparent where
there is nothing to report, because a layer that tints open water reads as a detection; and
masks are max-pooled rather than mean-pooled on the way down, because a narrow filament
averaged against the water around it would disappear from the picture while remaining in the
numbers.
"""

from __future__ import annotations

import struct
import zlib

import numpy as np
import pytest

from spilltrace_ml import preview as P

# ---------------------------------------------------------------------------
# An independent PNG decoder
# ---------------------------------------------------------------------------

SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS_FOR = {0: 1, 2: 3, 4: 2, 6: 4}


def chunks(blob: bytes) -> list[tuple[bytes, bytes]]:
    """Split a PNG into `(tag, payload)`, verifying every length and every CRC."""
    assert blob[:8] == SIGNATURE, "not a PNG"
    out: list[tuple[bytes, bytes]] = []
    offset = 8
    while offset < len(blob):
        (length,) = struct.unpack(">I", blob[offset:offset + 4])
        tag = blob[offset + 4:offset + 8]
        payload = blob[offset + 8:offset + 8 + length]
        assert len(payload) == length, f"chunk {tag!r} is truncated"
        (crc,) = struct.unpack(">I", blob[offset + 8 + length:offset + 12 + length])
        assert crc == zlib.crc32(tag + payload) & 0xFFFFFFFF, f"chunk {tag!r} has a bad CRC"
        out.append((tag, payload))
        offset += 12 + length
    return out


def decode(blob: bytes) -> tuple[np.ndarray, dict[str, int]]:
    """Decode a PNG to `(pixels, header)` without using the encoder's own code.

    Only what this encoder emits is supported: 8-bit, non-interlaced, filter type 0 on
    every scanline. Anything else is a failure of the encoder, so it asserts rather than
    growing a decoder branch.
    """
    parsed = chunks(blob)
    assert parsed[0][0] == b"IHDR", "IHDR must come first"
    assert parsed[-1][0] == b"IEND", "IEND must come last"
    assert parsed[-1][1] == b"", "IEND carries no payload"

    width, height, depth, colour, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", parsed[0][1]
    )
    assert depth == 8, f"expected 8-bit samples, got {depth}"
    assert compression == 0 and filtering == 0 and interlace == 0
    channels = CHANNELS_FOR[colour]

    body = b"".join(payload for tag, payload in parsed if tag == b"IDAT")
    raw = zlib.decompress(body)
    stride = width * channels
    assert len(raw) == height * (stride + 1), "scanline count or stride is wrong"

    rows = np.frombuffer(raw, dtype=np.uint8).reshape(height, stride + 1)
    assert (rows[:, 0] == 0).all(), "a scanline declares a filter this encoder never applies"
    pixels = rows[:, 1:].reshape(height, width, channels)
    return pixels, {
        "width": width,
        "height": height,
        "colourType": colour,
        "channels": channels,
    }


def written(tmp_path, image, alpha=None, name="t.png"):
    """Encode to disk, then decode it back independently."""
    path = P.write_png(tmp_path / name, image, alpha)
    return decode(path.read_bytes())


# ---------------------------------------------------------------------------
# The encoder
# ---------------------------------------------------------------------------

class TestWritePng:
    def test_a_greyscale_image_survives_the_round_trip(self, tmp_path):
        image = np.arange(48, dtype=np.uint8).reshape(6, 8)
        pixels, header = written(tmp_path, image)
        assert header["colourType"] == 0
        assert pixels[..., 0].tolist() == image.tolist()

    def test_the_header_is_width_then_height_and_not_the_array_order(self, tmp_path):
        """A transposed IHDR is the classic encoder bug: it decodes, and it is skewed."""
        _, header = written(tmp_path, np.zeros((7, 13), dtype=np.uint8))
        assert (header["width"], header["height"]) == (13, 7)

    def test_rgb_channels_are_not_swapped(self, tmp_path):
        image = np.zeros((4, 4, 3), dtype=np.uint8)
        image[..., 0] = 200  # red
        image[..., 1] = 100
        image[..., 2] = 25
        pixels, header = written(tmp_path, image)
        assert header["colourType"] == 2
        assert pixels[0, 0].tolist() == [200, 100, 25]

    def test_an_alpha_channel_is_appended_to_rgb(self, tmp_path):
        image = np.full((4, 4, 3), 90, dtype=np.uint8)
        alpha = np.zeros((4, 4), dtype=np.uint8)
        alpha[1, 1] = 255
        pixels, header = written(tmp_path, image, alpha)
        assert header["colourType"] == 6
        assert pixels[1, 1].tolist() == [90, 90, 90, 255]
        assert pixels[0, 0, 3] == 0

    def test_greyscale_plus_alpha_is_promoted_to_rgba(self, tmp_path):
        """Greyscale-with-alpha exists in the format, but the overlays are composited over
        a colour tile in the browser, so the encoder normalises to RGBA."""
        pixels, header = written(
            tmp_path, np.full((3, 3), 40, dtype=np.uint8), np.full((3, 3), 128, dtype=np.uint8)
        )
        assert header["colourType"] == 6
        assert pixels[0, 0].tolist() == [40, 40, 40, 128]

    def test_a_float_array_is_refused_rather_than_silently_truncated(self, tmp_path):
        with pytest.raises(ValueError, match="expected uint8"):
            P.write_png(tmp_path / "f.png", np.zeros((4, 4), dtype=np.float32))

    def test_an_unsupported_channel_count_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="unsupported channel count"):
            P.write_png(tmp_path / "f.png", np.zeros((4, 4, 5), dtype=np.uint8))

    def test_a_single_pixel_image_is_still_a_valid_png(self, tmp_path):
        pixels, header = written(tmp_path, np.array([[7]], dtype=np.uint8))
        assert (header["width"], header["height"]) == (1, 1)
        assert pixels[0, 0, 0] == 7

    def test_the_file_is_replaced_atomically_and_leaves_no_partial(self, tmp_path):
        """The API serves these files while a run is writing them, so a reader must never
        see a half-written PNG under the real name."""
        P.write_png(tmp_path / "a.png", np.zeros((4, 4), dtype=np.uint8))
        P.write_png(tmp_path / "a.png", np.full((4, 4), 9, dtype=np.uint8))
        names = sorted(p.name for p in tmp_path.iterdir())
        assert names == ["a.png"]
        assert decode((tmp_path / "a.png").read_bytes())[0][0, 0, 0] == 9

    def test_a_missing_parent_directory_is_created(self, tmp_path):
        path = P.write_png(tmp_path / "deep" / "er" / "a.png", np.zeros((2, 2), dtype=np.uint8))
        assert path.is_file()

    def test_the_encoder_is_deterministic(self, tmp_path):
        """Same array in, same bytes out: the previews are committed artefacts, and a
        nondeterministic encoder would make every rebuild look like a change."""
        image = np.arange(64, dtype=np.uint8).reshape(8, 8)
        first = P.write_png(tmp_path / "1.png", image).read_bytes()
        second = P.write_png(tmp_path / "2.png", image).read_bytes()
        assert first == second

    def test_a_large_flat_image_compresses_rather_than_being_stored_raw(self, tmp_path):
        """Not a performance test: the previews go in the repository, and an encoder that
        forgot to deflate would put megabytes there."""
        blob = P.write_png(tmp_path / "b.png", np.zeros((512, 512), dtype=np.uint8)).read_bytes()
        assert len(blob) < 512 * 512 // 100

    def test_a_non_contiguous_array_is_encoded_as_seen(self, tmp_path):
        """Slicing a scene produces views, not copies; `tobytes` on a view of the wrong
        order would shear the image."""
        full = np.arange(256, dtype=np.uint8).reshape(16, 16)
        view = full[::2, ::2]
        pixels, _ = written(tmp_path, view)
        assert pixels[..., 0].tolist() == view.tolist()


# ---------------------------------------------------------------------------
# Downsampling
# ---------------------------------------------------------------------------

class TestDownsample:
    def test_a_factor_of_one_is_a_no_op(self):
        values = np.arange(16, dtype=np.float32).reshape(4, 4)
        assert P.downsample(values, 1) is values
        assert P.downsample_max(values, 1) is values

    def test_the_mean_is_the_mean_of_each_block(self):
        values = np.array([[0.0, 2.0], [4.0, 6.0]], dtype=np.float32)
        assert P.downsample(values, 2).tolist() == [[3.0]]

    def test_a_ragged_edge_is_trimmed_not_padded(self):
        """Padding with zeros would darken the last row and column of every preview."""
        values = np.ones((7, 7), dtype=np.float32)
        assert P.downsample(values, 2).shape == (3, 3)
        assert P.downsample(values, 2) == pytest.approx(1.0)

    def test_max_pooling_keeps_a_one_pixel_filament(self):
        """The reason masks use the max: a slick one pixel wide is a real detection, and
        a mean would reduce it to a quarter of an intensity and then to nothing."""
        mask = np.zeros((8, 8), dtype=np.uint8)
        mask[3, :] = 1
        assert P.downsample_max(mask, 4).sum() == 2
        assert P.downsample(mask, 4).max() == pytest.approx(0.25)

    def test_max_pooling_preserves_the_dtype_it_was_given(self):
        mask = np.zeros((8, 8), dtype=np.uint8)
        assert P.downsample_max(mask, 2).dtype == np.uint8


# ---------------------------------------------------------------------------
# Contrast stretch
# ---------------------------------------------------------------------------

class TestStretch:
    def test_the_percentiles_are_reported_with_the_image(self):
        """The stretch is published because a decibel range is not recoverable from an
        8-bit picture, and without it the picture cannot be read quantitatively."""
        band = np.linspace(-25.0, -5.0, 100, dtype=np.float32).reshape(10, 10)
        _, info = P.stretch(band)
        assert info["unit"] == "dB"
        assert info["low"] < info["high"]
        assert info["percentiles"] == [2.0, 98.0]

    def test_the_range_is_mapped_across_the_full_eight_bits(self):
        band = np.linspace(-25.0, -5.0, 10000, dtype=np.float32).reshape(100, 100)
        out, _ = P.stretch(band)
        assert out.min() == 0
        assert out.max() == 255

    def test_no_data_is_black_and_out_of_the_percentiles(self):
        """A no-data border holds zeros, which in decibels is brighter than any water. If
        it entered the percentiles the whole scene would be stretched into a narrow band."""
        band = np.full((32, 32), -14.0, dtype=np.float32)
        band[:, :8] = 0.0
        invalid = np.zeros((32, 32), dtype=bool)
        invalid[:, :8] = True
        out, info = P.stretch(band, invalid)
        assert out[:, :8].max() == 0
        assert info["high"] < -13.0

    def test_an_entirely_invalid_band_is_black_and_says_it_has_no_range(self):
        out, info = P.stretch(
            np.zeros((8, 8), dtype=np.float32), np.ones((8, 8), dtype=bool)
        )
        assert out.max() == 0
        assert info["low"] is None and info["high"] is None

    def test_a_flat_band_does_not_divide_by_zero(self):
        out, _ = P.stretch(np.full((8, 8), -12.0, dtype=np.float32))
        assert np.isfinite(out).all()

    def test_nan_samples_do_not_reach_the_percentiles(self):
        band = np.full((8, 8), -14.0, dtype=np.float32)
        band[0, 0] = np.nan
        _, info = P.stretch(band)
        assert info["low"] == pytest.approx(-14.0)

    def test_the_result_is_uint8(self):
        out, _ = P.stretch(np.linspace(-30, 0, 64, dtype=np.float32).reshape(8, 8))
        assert out.dtype == np.uint8


# ---------------------------------------------------------------------------
# Overlays
# ---------------------------------------------------------------------------

class TestMaskOverlay:
    def test_the_overlay_is_transparent_where_there_is_no_oil(self):
        mask = np.zeros((4, 4), dtype=np.uint8)
        mask[1, 1] = 1
        colour, alpha = P.mask_png(mask, P.OIL_RGB)
        assert alpha[1, 1] == 255
        assert alpha[0, 0] == 0
        assert colour[1, 1].tolist() == list(P.OIL_RGB)

    def test_the_colour_is_the_one_the_legend_names(self):
        """The legend says predicted oil is amber and reference oil is blue. These are the
        two constants that claim has to match."""
        assert P.OIL_RGB == (255, 138, 76)
        assert P.REFERENCE_RGB == (94, 200, 255)

    def test_any_positive_value_counts_as_oil(self):
        colour, alpha = P.mask_png(np.array([[0, 1, 255]], dtype=np.uint8), P.OIL_RGB)
        assert alpha.tolist() == [[0, 255, 255]]
        assert colour[0, 2].tolist() == list(P.OIL_RGB)


class TestProbabilityOverlay:
    def test_water_is_fully_transparent(self):
        """Below the floor the layer must vanish, or the whole scene is tinted blue and
        the tint reads as a low-confidence detection everywhere."""
        _, alpha = P.probability_png(np.zeros((4, 4), dtype=np.float32))
        assert alpha.max() == 0

    def test_the_floor_is_where_the_layer_starts(self):
        values = np.array([[0.04, 0.06]], dtype=np.float32)
        _, alpha = P.probability_png(values, floor=0.05)
        assert alpha[0, 0] == 0
        assert alpha[0, 1] > 0

    def test_alpha_rises_with_probability(self):
        values = np.array([[0.1, 0.4, 0.7, 1.0]], dtype=np.float32)
        _, alpha = P.probability_png(values)
        assert list(alpha[0]) == sorted(alpha[0])

    def test_the_top_of_the_ramp_is_the_last_stop(self):
        colour, alpha = P.probability_png(np.ones((2, 2), dtype=np.float32))
        assert colour[0, 0].tolist() == [255, 244, 214]
        assert alpha[0, 0] == 255

    def test_the_operating_point_is_the_oil_accent(self):
        """0.75 is a ramp stop precisely so the usual operating point reads as the same
        colour the binary prediction layer uses."""
        colour, _ = P.probability_png(np.full((1, 1), 0.75, dtype=np.float32))
        assert colour[0, 0].tolist() == list(P.OIL_RGB)

    def test_the_ramp_is_monotonic_in_luminance(self):
        """Stated in the docstring, and the reason is print: the methodology screen is
        printed to PDF, and a ramp that is not monotonic in grey is unreadable there."""
        values = np.linspace(0.05, 1.0, 40, dtype=np.float32).reshape(1, 40)
        colour, _ = P.probability_png(values)
        grey = colour[0].astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722])
        assert np.all(np.diff(grey) > -1.0)

    def test_values_outside_zero_to_one_are_clipped_not_wrapped(self):
        colour, alpha = P.probability_png(np.array([[-3.0, 4.0]], dtype=np.float32))
        assert alpha[0, 0] == 0
        assert colour[0, 1].tolist() == [255, 244, 214]

    def test_the_output_is_uint8_and_encodable(self, tmp_path):
        colour, alpha = P.probability_png(
            np.linspace(0, 1, 64, dtype=np.float32).reshape(8, 8)
        )
        assert colour.dtype == np.uint8 and alpha.dtype == np.uint8
        pixels, header = written(tmp_path, colour, alpha)
        assert header["colourType"] == 6
        assert pixels.shape == (8, 8, 4)


class TestComparison:
    @staticmethod
    def scene(size: int = 8):
        band = np.full((size, size), -14.0, dtype=np.float32)
        band[2:6, 2:6] = -20.0
        return band

    def test_the_three_outcomes_get_three_colours(self):
        band = self.scene()
        reference = np.zeros((8, 8), dtype=np.uint8)
        prediction = np.zeros((8, 8), dtype=np.uint8)
        reference[1, 1] = prediction[1, 1] = 1  # agreement
        prediction[2, 2] = 1                    # false positive
        reference[3, 3] = 1                     # missed
        image = P.render_comparison(band, None, reference, prediction)
        assert image[1, 1].argmax() == np.array(P.AGREEMENT_RGB).argmax()
        assert image[2, 2].argmax() == np.array(P.FALSE_POSITIVE_RGB).argmax()
        assert image[3, 3].argmax() == np.array(P.MISSED_RGB).argmax()
        assert len({tuple(image[1, 1]), tuple(image[2, 2]), tuple(image[3, 3])}) == 3

    def test_the_backscatter_stays_visible_under_the_overlay(self):
        """Composited, not painted over: a reviewer has to see what the model was looking
        at, not just what it decided."""
        band = self.scene()
        mask = np.zeros((8, 8), dtype=np.uint8)
        mask[2:6, 2:6] = 1
        over_dark = P.render_comparison(band, None, mask, mask)[3, 3]
        light = band.copy()
        light[2:6, 2:6] = -8.0
        over_light = P.render_comparison(light, None, mask, mask)[3, 3]
        assert over_dark.tolist() != over_light.tolist()

    def test_no_data_is_a_flat_dark_field_and_not_an_outcome(self):
        band = self.scene()
        invalid = np.zeros((8, 8), dtype=bool)
        invalid[:, :2] = True
        mask = np.ones((8, 8), dtype=np.uint8)
        image = P.render_comparison(band, invalid, mask, mask)
        assert image[:, :2].tolist() == np.full((8, 2, 3), 24, dtype=np.uint8).tolist()

    def test_a_missing_layer_is_treated_as_empty_rather_than_raising(self):
        band = self.scene()
        assert P.render_comparison(band, None, None, None).shape == (8, 8, 3)

    def test_the_result_is_uint8_rgb(self):
        band = self.scene()
        mask = np.ones((8, 8), dtype=np.uint8)
        image = P.render_comparison(band, None, mask, None)
        assert image.dtype == np.uint8
        assert image.shape == (8, 8, 3)


# ---------------------------------------------------------------------------
# The scene preview set
# ---------------------------------------------------------------------------

class FakeScene:
    """The attributes `render_scene_previews` reads, and nothing else."""

    def __init__(self, size: int = 64, with_mask: bool = True) -> None:
        self.name = "scene_test"
        self.height = size
        self.width = size
        rng = np.random.default_rng(2)
        vv = np.full((size, size), -14.0, dtype=np.float32)
        vv[16:48, 16:48] = -20.0
        vv += rng.normal(0.0, 0.4, vv.shape).astype(np.float32)
        self.channels = np.stack([vv, vv + 6.0])
        self.invalid = np.zeros((size, size), dtype=bool)
        self.invalid[:, :8] = True
        self.mask = None
        if with_mask:
            self.mask = np.zeros((size, size), dtype=np.uint8)
            self.mask[16:48, 16:48] = 1
        self.bounds = {"west": 54.6, "east": 54.8, "south": 25.5, "north": 25.7}
        self.epsg = 4326


class TestScenePreviews:
    def test_the_two_bands_are_always_written(self, tmp_path):
        entry = P.render_scene_previews(FakeScene(with_mask=False), out_dir=tmp_path)
        assert set(entry["files"]) == {"vv", "vh"}
        for name in entry["files"].values():
            decode((tmp_path / name).read_bytes())

    def test_every_declared_file_exists_and_decodes(self, tmp_path):
        """The manifest is what the dashboard fetches. A name in it with no readable file
        behind it is a broken image in the interface."""
        scene = FakeScene()
        probability = np.clip(scene.mask.astype(np.float32) * 0.9 + 0.05, 0, 1)
        entry = P.render_scene_previews(
            scene, prediction=scene.mask, probability=probability, out_dir=tmp_path
        )
        assert set(entry["files"]) == {
            "vv", "vh", "referenceMask", "prediction", "probability", "comparison"
        }
        for name in entry["files"].values():
            pixels, header = decode((tmp_path / name).read_bytes())
            assert (header["width"], header["height"]) == tuple(entry["previewSize"])

    def test_the_comparison_needs_both_a_prediction_and_a_reference(self, tmp_path):
        scene = FakeScene(with_mask=False)
        entry = P.render_scene_previews(scene, prediction=np.zeros((64, 64), np.uint8),
                                       out_dir=tmp_path)
        assert "comparison" not in entry["files"]
        assert "referenceMask" not in entry["files"]

    def test_the_downsample_factor_is_reported_with_both_sizes(self, tmp_path):
        """A preview is not the scene. Without the factor and the source size, nothing
        downstream can put a pixel back in the acquisition it came from."""
        entry = P.render_scene_previews(FakeScene(size=64), out_dir=tmp_path, max_side=16)
        assert entry["downsampleFactor"] == 4
        assert entry["previewSize"] == [16, 16]
        assert entry["sourceSize"] == [64, 64]

    def test_a_scene_smaller_than_the_target_is_not_upscaled(self, tmp_path):
        entry = P.render_scene_previews(FakeScene(size=64), out_dir=tmp_path, max_side=512)
        assert entry["downsampleFactor"] == 1
        assert entry["previewSize"] == [64, 64]

    def test_the_legend_names_every_written_layer(self, tmp_path):
        """The layer picker in the interface is built from this legend, so a file without
        an entry appears as a toggle with no explanation of what it shows."""
        scene = FakeScene()
        entry = P.render_scene_previews(
            scene, prediction=scene.mask, probability=scene.mask.astype(np.float32),
            out_dir=tmp_path,
        )
        assert set(entry["files"]) <= set(entry["legend"])

    def test_the_bounds_travel_with_the_pixels(self, tmp_path):
        """The only thing that georeferences a preview. Losing it puts the slick in the
        wrong ocean."""
        scene = FakeScene()
        entry = P.render_scene_previews(scene, out_dir=tmp_path)
        assert entry["bounds"] == scene.bounds
        assert entry["epsg"] == 4326

    def test_the_manifest_entry_is_json_serialisable(self, tmp_path):
        import json

        scene = FakeScene()
        entry = P.render_scene_previews(
            scene, prediction=scene.mask, probability=scene.mask.astype(np.float32),
            out_dir=tmp_path,
        )
        assert json.loads(json.dumps(entry))["scene"] == "scene_test"

    def test_the_previews_carry_the_satellite_label(self, tmp_path):
        entry = P.render_scene_previews(FakeScene(), out_dir=tmp_path)
        assert entry["label"]
