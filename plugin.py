"""Cinema Aspect Ratios: add film aspect ratios to Wan2GP's Megapixels resolution picker.

Wan2GP's picker offers a fixed list of ratios (shared.resolutions.ASPECT_RATIOS). This plugin, without modifying any
Wan2GP file:
* adds the ratios of aspect_ratios.json to that list (the picker reads it each time it is refreshed);
* shows them with their cinema names in the dropdown;
* keeps the ratio you chose: Wan2GP rounds sizes to blocks of 16 pixels and then works out the ratio again from the
  rounded size, which can land on a close neighbour (a 2.40:1 size can read as 2.35:1). The ratio that produced each
  size is remembered and used instead;
* optionally snaps the size slider to standard frame widths (1280, 1920, 2048, 3840...): the slider moves in steps of
  0.05 megapixels over a wide range, so a size like 1920x800 (1.536 MP) is otherwise hard or impossible to hit. A
  standard size is used when the slider is within SNAP_WINDOW (5%) of it; standard sizes are always more than 10% apart.
  A "Snap" checkbox next to the slider switches it on and off;
* avoids false matches: Wan2GP accepts a ratio when a size is within half a pixel block of it (16 px for 32-pixel
  models such as MiniMax H3), so with closely spaced cinema ratios a 2.50:1 size could read as 2.55:1. When Wan2GP
  reads a size as one of the added ratios but its real shape is more than MATCH_TOLERANCE (1%) away, Wan2GP's own
  answer without the added ratios is used.
"""

import functools
import json
import math
import os

import gradio as gr

from shared.utils.plugins import WAN2GPPlugin

from . import settings

LOG_PREFIX = "[Cinema Aspect Ratios]"
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aspect_ratios.json")
_WRAPPED_FLAG = "_cinema_aspect_ratios_wrapped"
_REMEMBERED_MAX = 256
SLIDER_STEP = 0.05      # megapixels, Wan2GP's Megapixels slider
SNAP_WINDOW = 0.05      # snap when the slider is within 5% of a standard size
MATCH_TOLERANCE = 0.01  # an added ratio is shown only when the size is within 1% of it
_original_ratios = []   # Wan2GP's own list, before the additions
_added_ratios = set()
STANDARD_WIDTHS = (640, 768, 960, 1024, 1280, 1440, 1600, 1920, 2048, 2560, 2880, 3200, 3840, 4096)
_internal = {"depth": 0}  # snapping only applies to the slider, not to Wan2GP's own limit computations

_labels = {}       # ratio value -> dropdown label
_remembered = {}   # "WxH" -> ratio that produced it


def _parse_ratio(value):
    """'12:5', '2.40:1', '2.4' or 2.4 -> the whole-number ratio Wan2GP needs ('12:5'), or None."""
    from fractions import Fraction
    try:
        parts = str(value).strip().split(":")
        if len(parts) == 1:
            parts.append("1")
        if len(parts) != 2:
            return None
        ratio = Fraction(parts[0].strip()) / Fraction(parts[1].strip())
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if ratio <= 0:
        return None
    ratio = ratio.limit_denominator(1000)
    return f"{ratio.numerator}:{ratio.denominator}"


def load_ratios(path=_CONFIG_PATH):
    """[(ratio, label)] from aspect_ratios.json; invalid entries are skipped with a message."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        print(f"{LOG_PREFIX} {os.path.basename(path)} not found: no ratio added")
        return []
    except Exception as error:
        print(f"{LOG_PREFIX} could not read {os.path.basename(path)}: {error}")
        return []
    entries = data.get("ratios", []) if isinstance(data, dict) else data
    ratios = []
    for entry in entries if isinstance(entries, list) else []:
        value, label = (entry.get("ratio"), entry.get("label")) if isinstance(entry, dict) else (entry, None)
        ratio = _parse_ratio(value)
        if ratio is None:
            print(f"{LOG_PREFIX} skipped {value!r}: not a ratio (write it like 2.40:1 or 12:5)")
            continue
        ratios.append((ratio, str(label).strip() if label else ratio))
    return ratios


def _same_ratio(a, b):
    (aw, ah), (bw, bh) = (map(int, a.split(":")), map(int, b.split(":")))
    return math.isclose(aw / ah, bw / bh, rel_tol=1e-9)


def snapped_size(megapixels, aspect_ratio, block, model_def=None):
    """Standard size (long side in STANDARD_WIDTHS, short side from the ratio, both multiples of block) within half a
    slider step of megapixels, or None."""
    rw, rh = map(int, aspect_ratio.split(":"))
    ratio = rw / rh
    model_def = model_def or {}
    max_w, max_h = model_def.get("max_width"), model_def.get("max_height")
    best = None
    for long_side in STANDARD_WIDTHS:
        if long_side % block:
            continue
        short_side = max(block, round(long_side / max(ratio, 1 / ratio) / block) * block)
        width, height = (long_side, short_side) if ratio >= 1 else (short_side, long_side)
        if (max_w is not None and width > max_w) or (max_h is not None and height > max_h):
            continue
        if abs((width / height) / ratio - 1.0) > 0.02:  # rounding drifted too far from the ratio (tiny sizes)
            continue
        size_mp = width * height / 1_000_000
        distance = abs(size_mp - float(megapixels))
        if (distance <= SLIDER_STEP / 2 + 1e-9 or distance <= SNAP_WINDOW * size_mp) and (best is None or distance < best[0]):
            best = (distance, f"{width}x{height}")
    return None if best is None else best[1]


def _ratio_error(resolution, ratio):
    width, height = map(int, str(resolution).lower().split("x"))
    rw, rh = map(int, ratio.split(":"))
    return abs((width / height) / (rw / rh) - 1.0)


def _without_added_ratios(module, controls, resolution, resolution_choices, model_def, enable_4k_resolutions):
    """Wan2GP's own reading of a size, as if the added ratios didn't exist (they stay listed in the dropdown)."""
    current = list(module.ASPECT_RATIOS)
    module.ASPECT_RATIOS[:] = _original_ratios
    _internal["depth"] += 1
    try:
        values = controls(resolution, resolution_choices, model_def, enable_4k_resolutions)
    finally:
        _internal["depth"] -= 1
        module.ASPECT_RATIOS[:] = current
    listed = {value for _, value in values["ratios"]}
    values["ratios"] = [*values["ratios"], *((value, value) for value in current if value not in listed)]
    return values


def install(module, ratios):
    """Add the ratios to module.ASPECT_RATIOS and wrap the picker functions. Safe to call more than once."""
    added = []
    if not _original_ratios:
        _original_ratios.extend(module.ASPECT_RATIOS)
    for ratio, label in ratios:
        existing = next((value for value in module.ASPECT_RATIOS if _same_ratio(value, ratio)), None)
        if existing is None:
            module.ASPECT_RATIOS.append(ratio)
            added.append(ratio)
            _added_ratios.add(ratio)
            existing = ratio
        _labels[existing] = label

    to_resolution = module.megapixels_to_resolution
    if not getattr(to_resolution, _WRAPPED_FLAG, False):
        @functools.wraps(to_resolution)
        def megapixels_to_resolution(megapixels, aspect_ratio, block_size, model_def=None):
            resolution = to_resolution(megapixels, aspect_ratio, block_size, model_def)
            if _internal["depth"] == 0 and settings.get()["snap"]:
                try:
                    snapped = snapped_size(megapixels, aspect_ratio, module.normalize_block_size(block_size), model_def)
                    resolution = snapped or resolution
                except Exception as error:
                    print(f"{LOG_PREFIX} snapping failed, unsnapped size used: {error!r}")
            if _internal["depth"] == 0:  # only sizes chosen with the slider, not Wan2GP's own limit computations
                if len(_remembered) >= _REMEMBERED_MAX:
                    _remembered.pop(next(iter(_remembered)))
                _remembered[resolution] = aspect_ratio
            return resolution
        setattr(megapixels_to_resolution, _WRAPPED_FLAG, True)
        module.megapixels_to_resolution = megapixels_to_resolution

    controls = module.megapixel_controls
    if not getattr(controls, _WRAPPED_FLAG, False):
        @functools.wraps(controls)
        def megapixel_controls(resolution, resolution_choices, model_def, enable_4k_resolutions=False):
            _internal["depth"] += 1
            try:
                values = controls(resolution, resolution_choices, model_def, enable_4k_resolutions)
            finally:
                _internal["depth"] -= 1
            try:
                chosen = _remembered.get(resolution)
                if chosen is None and values.get("ratio") in _added_ratios and _ratio_error(resolution, values["ratio"]) > MATCH_TOLERANCE:
                    values = _without_added_ratios(module, controls, resolution, resolution_choices, model_def, enable_4k_resolutions)
                if chosen is not None and chosen != values.get("ratio"):
                    values["ratio"] = chosen
                    if all(value != chosen for _, value in values["ratios"]):
                        values["ratios"] = [*values["ratios"], (chosen, chosen)]
                values["ratios"] = [(_labels.get(value, label), value) for label, value in values["ratios"]]
            except Exception as error:
                print(f"{LOG_PREFIX} could not relabel the ratios: {error!r}")
            return values
        setattr(megapixel_controls, _WRAPPED_FLAG, True)
        module.megapixel_controls = megapixel_controls
    return added


class CinemaAspectRatiosPlugin(WAN2GPPlugin):
    def __init__(self):
        super().__init__()
        self.name = "Cinema Aspect Ratios"
        self.version = "0.1"
        self.description = "Adds cinema aspect ratios (1.85, IMAX, 2.39/2.40 Scope, Ultra Panavision...) to the Megapixels resolution picker, with optional snapping to standard sizes."
        self._ui_built = False
        settings.load()

    def setup_ui(self):
        self.request_component("resolution_mp")
        # Before the page is built, so the picker's first list already includes them
        try:
            from shared import resolutions
        except Exception as error:
            print(f"{LOG_PREFIX} Wan2GP's resolution module not found, no ratio added: {error!r}")
            return
        if not hasattr(resolutions, "ASPECT_RATIOS") or not hasattr(resolutions, "megapixel_controls"):
            print(f"{LOG_PREFIX} this Wan2GP version has no Megapixels resolution picker: no ratio added")
            return
        ratios = load_ratios()
        added = install(resolutions, ratios)
        print(f"{LOG_PREFIX} {len(ratios)} ratio(s) from aspect_ratios.json, {len(added)} added to the picker")

    def post_ui_setup(self, components):
        """A compact "Snap" checkbox in the slider's own row (no extra row or space)."""
        if self._ui_built or components.get("resolution_mp") is None:
            return {}
        self._ui_built = True

        def build_checkbox():
            snap = gr.Checkbox(value=settings.get()["snap"], label="Snap", scale=0, min_width=70,
                               elem_id="cinema_aspect_ratios_snap")
            snap.change(lambda value: settings.set_value("snap", bool(value)), inputs=snap, outputs=None,
                        show_progress="hidden", queue=False)
            return snap

        self.insert_after("resolution_mp", build_checkbox)
        return {}
