# Cinema Aspect Ratios for Wan2GP

Adds cinema aspect ratios to Wan2GP's **Megapixels** resolution picker (*Switch to Megapixels*, then *Aspect Ratio*):

| Ratio | Name |
|---|---|
| 1.375:1 | Academy |
| 1.43:1 | IMAX 70mm |
| 1.66:1 | European Widescreen |
| 1.85:1 | Flat Widescreen |
| 1.90:1 | IMAX Digital |
| 2.00:1 | Univisium |
| 2.20:1 | Todd-AO / Super Panavision 70 |
| 2.35:1 | CinemaScope |
| 2.39:1 | Scope (DCI) |
| 2.40:1 | Scope / Panavision Anamorphic |
| 2.55:1 | CinemaScope (1953) |
| 2.76:1 | Ultra Panavision 70 |

The ratio you pick stays selected as you move the size slider (Wan2GP rounds sizes to 16 pixels, which could
otherwise read as a neighbouring ratio).

## Snap

The size slider moves in steps of 0.05 megapixels over a wide range, so standard sizes like 1920x800 are hard to hit.
With **Snap** ticked (the checkbox next to the slider), positions within 5% of a standard size snap to it: for 2.40:1,
1.50 to 1.60 MP give 1920x800. Standard sizes are:

- standard frame widths (1280, 1920, 2048, 2560, 3840...);
- standard heights (480p, 720p, 768p, 1080p, 1440p, 2160p), rounded the way Wan2GP rounds them (720p 16:9 is
  1280x704, 768p 16:9 is 1344x768);
- Wan2GP's presets of that ratio, including your own `resolutions.json`.

With Snap on, the up/down arrows of the number box (and the Up/Down keys in it) jump straight to the next or
previous standard size. Untick Snap for Wan2GP's exact sizes and its 0.05 steps.

### Grid (two phases)

With two guidance phases, phase 1 runs at the output size / 2 (or / 1.5 with 2 Phases Plus' *Output / 1.5*), rounded
to 32 pixels. Unless both sides of the output are multiples of **64** (or **96**), that rounding gives phase 1 a
slightly different shape, which the latent upscaler stretches back: in sliding windows the picture can slide into
place at the start of each window. The **Grid** dropdown next to Snap limits snapping and the arrows to sizes where
phase 1 is exact:

- **Model**: the model's own 16 or 32 pixels (one phase).
- **64 · ÷2**: two phases (Wan2GP's default). 2.40:1 gives 1536x640, 2304x960...
- **96 · ÷1.5**: two phases with *Output / 1.5*. 2.40:1 gives 1152x480, 2304x960...

On a grid the sizes are within 2% of the ratio (2.40:1 is exact at 1536x640 and 2304x960).

## Your own ratios

Edit `aspect_ratios.json` in the plugin folder and restart Wan2GP. Ratios can be written `2.40:1`, `2.40` or `12:5`;
portrait versions as `1:2.40`.

## Install

In Wan2GP, open the **Plugins** tab, paste this repository's URL under *Install New Plugin*, install,
enable it and restart Wan2GP.

## License

MIT
