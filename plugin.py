"""Cinema Aspect Ratios: add film aspect ratios to Wan2GP's Megapixels resolution picker.

Wan2GP's picker offers a fixed list of ratios (shared.resolutions.ASPECT_RATIOS). This plugin, without modifying any
Wan2GP file:
* adds the ratios of aspect_ratios.json to that list (the picker reads it each time it is refreshed);
* shows them with their cinema names in the dropdown;
* keeps the ratio you chose: Wan2GP rounds sizes to blocks of 16 pixels and then works out the ratio again from the
  rounded size, which can land on a close neighbour (a 2.40:1 size can read as 2.35:1). The ratio that produced each
  size is remembered and used instead;
* optionally snaps the size slider to standard sizes (see standard_sizes): the slider moves in steps of
  0.05 megapixels over a wide range, so a size like 1920x800 (1.536 MP) is otherwise hard or impossible to hit. A
  standard size is used when the slider is within SNAP_WINDOW (5%) of it (the nearest one when several are).
  A "Snap" checkbox next to the slider switches it on and off. With it on, the arrows of the slider's number box step
  to the next or previous standard size;
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
STANDARD_HEIGHTS = (480, 540, 576, 720, 768, 1080, 1440, 2160)
SHAPE_TOLERANCE = 0.03   # a generated size counts for a ratio when its shape is within 3% of it
SAME_SIZE = 0.03         # sizes less than 3% apart in pixels count as one step
PRESET_TOLERANCE = 0.012  # a preset counts for a ratio when its shape is within 1.2% of it (presets are exact sizes)
GRID_TOLERANCE = 0.02     # on a two-phase grid, sizes within 2% of the ratio (the grid is coarse)
GRID_CHOICES = [("Model", "model"), ("64 · ÷2", "64"), ("96 · ÷1.5", "96")]
MAX_LONG_SIDE = 4096
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


def _preset_sizes(model_def):
    """Wan2GP's preset sizes for this model (built-in list plus resolutions.json), already aligned to its block."""
    try:
        import sys
        choices, _ = sys.modules["__main__"].get_resolution_choices(None, model_def)
        return [value for _, value in choices]
    except Exception:
        return []


def snap_grid():
    """The two-phase grid chosen for snapping (64 or 96), or None."""
    value = str(settings.get().get("grid", "model"))
    return int(value) if value in ("64", "96") else None


def snap_block(block):
    """The block snapped sizes are multiples of: the model's own, or the two-phase grid when one is chosen. With
    two phases, phase 1 is the output / 2 (or / 1.5) rounded to 32 pixels: it is exactly that fraction, with the
    same shape as the output, only when both sides are multiples of 64 (or 96). Otherwise phase 1 is slightly
    stretched and, in sliding windows, the picture slides into place over the first frames of each window."""
    grid = snap_grid()
    return block if grid is None else block * grid // math.gcd(block, grid)


def grid_sizes(aspect_ratio, block, model_def=None, presets=None):
    """[(pixels, "WxH")] on the block (a two-phase grid), within GRID_TOLERANCE of the ratio, smallest first: presets
    on the grid first, then the closest grid sizes for every short side. Sizes less than SAME_SIZE apart count once
    (the one closest to the ratio)."""
    rw, rh = map(int, aspect_ratio.split(":"))
    ratio = rw / rh
    model_def = model_def or {}
    max_w, max_h = model_def.get("max_width"), model_def.get("max_height")
    if presets is None:
        presets = _preset_sizes(model_def)
    error = lambda w, h: abs((w / h) / ratio - 1.0)
    candidates = []
    for value in presets:
        try:
            width, height = map(int, str(value).lower().split("x"))
        except ValueError:
            continue
        if width % block == 0 and height % block == 0 and error(width, height) <= PRESET_TOLERANCE:
            candidates.append((-1.0, width, height))  # presets first
    long_ratio = max(ratio, 1 / ratio)
    for short_side in range(block, MAX_LONG_SIDE + 1, block):
        exact_long = short_side * long_ratio
        for long_side in {max(block, math.floor(exact_long / block) * block), max(block, math.ceil(exact_long / block) * block)}:
            if long_side > MAX_LONG_SIDE:
                continue
            width, height = (long_side, short_side) if ratio >= 1 else (short_side, long_side)
            candidates.append((error(width, height), width, height))
    sizes = []
    for err, width, height in sorted(candidates):
        if (max_w is not None and width > max_w) or (max_h is not None and height > max_h):
            continue
        if err > GRID_TOLERANCE:
            continue
        pixels = width * height
        if any(abs(pixels / other - 1.0) < SAME_SIZE for other, _ in sizes):
            continue
        sizes.append((pixels, f"{width}x{height}"))
    return sorted(sizes)


def standard_sizes(aspect_ratio, block, model_def=None, presets=None):
    """[(pixels, "WxH")] of the standard sizes for a ratio, smallest first, within the model's maximum width and height:
    * Wan2GP's presets (and your resolutions.json) whose shape is within PRESET_TOLERANCE of the ratio;
    * standard frame widths (1280, 1920, 2048...), the other side from the ratio rounded to the nearest block;
    * standard heights (480p, 720p, 768p, 1080p...): the height to the nearest block (exact halves down, so 720p is
      704 at 32-pixel blocks), the width rounded down to the block as Wan2GP does (768p 16:9 is 1344x768).
    Sizes less than SAME_SIZE (3%) apart in pixels count once (presets first, then widths, then heights).
    With a two-phase grid chosen, the sizes come from grid_sizes instead."""
    if snap_grid() is not None:
        return grid_sizes(aspect_ratio, snap_block(block), model_def, presets)
    rw, rh = map(int, aspect_ratio.split(":"))
    ratio = rw / rh
    model_def = model_def or {}
    max_w, max_h = model_def.get("max_width"), model_def.get("max_height")
    if presets is None:
        presets = _preset_sizes(model_def)
    down = lambda value: max(block, int(value) // block * block)
    nearest = lambda value: max(block, math.ceil(value / block - 0.5) * block)  # exact halves go down: 720 -> 704
    candidates = []
    for value in presets:
        try:
            width, height = map(int, str(value).lower().split("x"))
        except ValueError:
            continue
        if width % block == 0 and height % block == 0 and abs((width / height) / ratio - 1.0) <= PRESET_TOLERANCE:
            candidates.append((width, height))
    for long_side in STANDARD_WIDTHS:
        if long_side % block == 0:
            short_side = max(block, round(long_side / max(ratio, 1 / ratio) / block) * block)
            candidates.append((long_side, short_side) if ratio >= 1 else (short_side, long_side))
    for short_side in STANDARD_HEIGHTS:
        short_side = nearest(short_side)
        long_side = down(short_side * max(ratio, 1 / ratio))
        candidates.append((long_side, short_side) if ratio >= 1 else (short_side, long_side))
    sizes = []
    for width, height in candidates:
        if (max_w is not None and width > max_w) or (max_h is not None and height > max_h):
            continue
        if abs((width / height) / ratio - 1.0) > SHAPE_TOLERANCE:
            continue
        pixels = width * height
        if any(abs(pixels / other - 1.0) < SAME_SIZE for other, _ in sizes):
            continue
        sizes.append((pixels, f"{width}x{height}"))
    return sorted(sizes)


def snapped_size(megapixels, aspect_ratio, block, model_def=None):
    """The standard size within half a slider step or SNAP_WINDOW of megapixels (the nearest one), or None. With a
    two-phase grid, always the nearest grid size: no size off the grid is ever used."""
    best = None
    on_grid = snap_grid() is not None
    for pixels, resolution in standard_sizes(aspect_ratio, block, model_def):
        size_mp = pixels / 1_000_000
        distance = abs(size_mp - float(megapixels))
        if (on_grid or distance <= SLIDER_STEP / 2 + 1e-9 or distance <= SNAP_WINDOW * size_mp) and (best is None or distance < best[0]):
            best = (distance, resolution)
    return None if best is None else best[1]


def next_standard_size(megapixels, aspect_ratio, block, model_def, direction, minimum=None, maximum=None):
    """The standard size just above (direction > 0) or below (direction < 0) the size the slider is on, or None."""
    from shared import resolutions
    width, height = map(int, resolutions.megapixels_to_resolution(float(megapixels), aspect_ratio, block, model_def).split("x"))
    current = width * height
    sizes = [(pixels, resolution) for pixels, resolution in standard_sizes(aspect_ratio, block, model_def)
             if (minimum is None or pixels / 1_000_000 >= minimum - 1e-9) and (maximum is None or pixels / 1_000_000 <= maximum + 1e-9)]
    if direction > 0:
        return next(((pixels, resolution) for pixels, resolution in sizes if pixels > current), None)
    return next(((pixels, resolution) for pixels, resolution in reversed(sizes) if pixels < current), None)


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


def step_updates(state, megapixels, ratio, direction):
    """Updates for (resolution, resolution_group, resolution_mp) that move the picker to the next (direction > 0) or
    previous standard size, applied exactly like releasing the slider there. Nothing changes at either end."""
    unchanged = (gr.update(), gr.update(), gr.update())
    try:
        import sys
        from shared import resolutions
        main = sys.modules["__main__"]
        model_def = main.get_model_def(main.get_state_model_type(state))
        if not ratio or megapixels is None or model_def.get("lock_resolutions"):
            return unchanged
        block = resolutions.normalize_block_size(model_def.get("vae_block_size", 16))
        current = resolutions.megapixels_to_resolution(float(megapixels), ratio, block, model_def)
        choices, _ = main.get_resolution_choices(current, model_def)
        limits = resolutions.megapixel_controls(current, choices, model_def, main.server_config.get("enable_4k_resolutions", 0) == 1)
        target = next_standard_size(megapixels, ratio, block, model_def, direction, limits["minimum"], limits["maximum"])
        if target is None:
            return unchanged
        return main.commit_resolution_mp(state, target[0] / 1_000_000, ratio)
    except Exception as error:
        print(f"{LOG_PREFIX} could not step to the {'next' if direction > 0 else 'previous'} size: {error!r}")
        return unchanged


def resnap_updates(state, megapixels, ratio):
    """Updates for (resolution, resolution_group, resolution_mp) that apply the snapping settings to the current size
    right away (after the grid or Snap changes), the way releasing the slider does."""
    unchanged = (gr.update(), gr.update(), gr.update())
    if not settings.get()["snap"] or not ratio or megapixels is None:
        return unchanged
    try:
        import sys
        main = sys.modules["__main__"]
        if main.get_model_def(main.get_state_model_type(state)).get("lock_resolutions"):
            return unchanged
        return main.commit_resolution_mp(state, float(megapixels), ratio)
    except Exception as error:
        print(f"{LOG_PREFIX} could not apply the snapping settings to the current size: {error!r}")
        return unchanged


# With Snap on, the arrows of the slider's number box (and the Up/Down keys or mouse wheel in it) move to the next or
# previous standard size instead of 0.05 MP: the step is caught before Gradio sees it, undone, and replaced by a click
# on one of two hidden buttons. Typing a value in the box is unaffected.
_STEP_JS = r"""
(function () {
    if (window.__cinemaAspectRatiosSteps) return;
    window.__cinemaAspectRatiosSteps = true;
    const root = () => { const app = document.querySelector("gradio-app"); return (app && app.shadowRoot) || document; };
    const before = new WeakMap();
    let suppressRelease = 0;
    function numberBox(event) {
        const target = event.composedPath ? event.composedPath()[0] : event.target;
        if (!target || target.tagName !== "INPUT" || target.type !== "number") return null;
        return target.closest(".cinema-ar-mp") ? target : null;
    }
    function snapOn() {
        const box = root().querySelector("#cinema_aspect_ratios_snap input[type=checkbox]");
        return !!box && box.checked;
    }
    const remember = (event) => { const box = numberBox(event); if (box) before.set(box, box.value); };
    ["pointerdown", "keydown", "wheel", "focusin"].forEach((name) => document.addEventListener(name, remember, true));
    document.addEventListener("input", (event) => {
        const box = numberBox(event);
        if (!box || event.inputType || !snapOn()) return;  // typed or pasted values have an inputType
        const previous = parseFloat(before.has(box) ? before.get(box) : box.defaultValue);
        const value = parseFloat(box.value);
        if (!isFinite(previous) || !isFinite(value) || value === previous) return;
        event.stopImmediatePropagation();
        box.value = before.get(box);
        suppressRelease = Date.now();
        const button = root().querySelector(value > previous ? "#cinema_aspect_ratios_next" : "#cinema_aspect_ratios_previous");
        if (button) button.click();
    }, true);
    document.addEventListener("pointerup", (event) => {
        // The arrow's mouse release would otherwise apply the old value over the new size
        if (suppressRelease && numberBox(event) && Date.now() - suppressRelease < 10000) {
            suppressRelease = 0;
            event.stopImmediatePropagation();
        }
    }, true);
})();
"""


class CinemaAspectRatiosPlugin(WAN2GPPlugin):
    def __init__(self):
        super().__init__()
        self.name = "Cinema Aspect Ratios"
        self.version = "0.3"
        self.description = "Adds cinema aspect ratios (1.85, IMAX, 2.39/2.40 Scope, Ultra Panavision...) to the Megapixels resolution picker, with optional snapping to standard sizes."
        self._ui_built = False
        settings.load()

    def setup_ui(self):
        for component in ("resolution_mp", "resolution_ratio", "resolution", "resolution_group", "state"):
            self.request_component(component)
        self.add_custom_js(_STEP_JS)
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
        """A compact "Snap" checkbox and "Grid" dropdown in the slider's own row (no extra row), and the hidden
        buttons the number box arrows use to step between standard sizes."""
        slider = components.get("resolution_mp")
        if self._ui_built or slider is None:
            return {}
        self._ui_built = True
        step_inputs = [components.get(name) for name in ("state", "resolution_mp", "resolution_ratio")]
        step_outputs = [components.get(name) for name in ("resolution", "resolution_group", "resolution_mp")]
        can_step = all(component is not None for component in step_inputs + step_outputs)
        if can_step:
            slider.elem_classes = [*(slider.elem_classes or []), "cinema-ar-mp"]
        else:
            print(f"{LOG_PREFIX} this Wan2GP version doesn't expose the picker's components: arrow stepping is off")

        def build_checkbox():
            if can_step:
                for elem_id, direction in (("cinema_aspect_ratios_previous", -1), ("cinema_aspect_ratios_next", 1)):
                    button = gr.Button(visible=False, elem_id=elem_id)
                    button.click(lambda state, mp, ratio, direction=direction: step_updates(state, mp, ratio, direction),
                                 inputs=step_inputs, outputs=step_outputs, show_progress="hidden", queue=False)
            grid = gr.Dropdown(choices=GRID_CHOICES, value=str(settings.get().get("grid", "model")), label="Grid",
                               scale=0, min_width=140, interactive=settings.get()["snap"], elem_id="cinema_aspect_ratios_grid",
                               info=None)
            snap = gr.Checkbox(value=settings.get()["snap"], label="Snap", scale=0, min_width=70,
                               elem_id="cinema_aspect_ratios_snap")

            def store_snap(value):
                settings.set_value("snap", bool(value))
                return gr.update(interactive=bool(value))

            def store_grid(value):
                settings.set_value("grid", str(value) if str(value) in ("64", "96") else "model")
                print(f"{LOG_PREFIX} snap grid: {dict((v, k) for k, v in GRID_CHOICES).get(str(value), value)}")

            snap_event = snap.change(store_snap, inputs=snap, outputs=grid, show_progress="hidden", queue=False)
            grid_event = grid.change(store_grid, inputs=grid, outputs=None, show_progress="hidden", queue=False)
            if can_step:  # the current size follows the new setting straight away
                for event in (snap_event, grid_event):
                    event.then(resnap_updates, inputs=step_inputs, outputs=step_outputs, show_progress="hidden", queue=False)
            return snap  # created last: it is the one placed next to the slider (the grid follows it)

        self.insert_after("resolution_mp", build_checkbox)
        return {}
