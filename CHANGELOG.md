# Changelog

## 0.3 (2026-10-21)

- **Grid** dropdown next to Snap, for two-phase generation: snapping and the arrows only use sizes that are multiples
  of 64 (phase 1 = output / 2) or 96 (2 Phases Plus' Output / 1.5), so phase 1 is exact and the picture doesn't slide
  at the start of each sliding window. Changing it re-snaps the current size.

## 0.2 (2026-10-18)

- With Snap on, the number box arrows (and Up/Down keys) step to the next or previous standard size.
- Standard sizes now also include standard heights (720p, 768p, 1080p...) and Wan2GP's presets (including
  `resolutions.json`), so sizes like 1280x704 and 1344x768 can be reached.

## 0.1 (2026-10-17)

- First release: 12 cinema aspect ratios in the Megapixels resolution picker, editable in `aspect_ratios.json`.
- Snap the size slider to standard frame widths (1920x800 for 2.40:1...), with a Snap checkbox next to the slider.
