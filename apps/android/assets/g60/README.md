# G60 model and native image assets

Original procedural visual approximation of the Zhiyun MOLUS G60, made for the
Lighttime Android redesign on 2026-10-25. This is not dimensionally exact product
CAD, a manufacturer model, or a photograph. No AI image generation was used.

## Files

| File | Intended use |
| --- | --- |
| `hero.png` | Transparent 960 × 720 front three-quarter studio render. |
| `compact.png` | Transparent 480 × 360 version with the same framing. |
| `glow.png` | Transparent 256 × 256 radial ruby/burgundy glow, smoothly fading to zero alpha. |
| `hero-ruby.png` | Transparent 960 × 720 ruby-preset render: pale key, scarlet/coral rim and edge light, reflective ring, soft contact shadow. |
| `compact-ruby.png` | Transparent 480 × 360 version of the ruby render with the same framing. |
| `g60.glb` | Reusable glTF 2.0 model with PBR materials and embedded procedural textures. |
| `showcase.html` | Generated offline viewer, copied into Android APK assets during prebuild. |
| `Three-MIT.txt` | Complete license for the renderer bundled in the offline showcase. |

Use the PNGs with a native `Image` static `require`, for example
`require('../assets/g60/hero.png')` from `src/`. Render the glow as a separate
native image behind the hero, using a ruby/dark backdrop and normal alpha
compositing. Its opacity and scale can be tuned without changing the model.
The image is a decorative preview; the warm COB appearance and example rear
display do not represent live output or device readback.

The normal native screens use the still PNGs. Tapping their **3D** badge opens
an offline WebView with the actual embedded GLB and a bundled Three.js renderer.
The native view opens the bundled `lighttime-showcase.html` asset directly; the
large document is not sent through React Native view properties.
The renderer loads only on opening and is released on closing. Three.js remains
in the model-tool package rather than the app's JavaScript dependencies; its
viewer code and complete MIT license are included in the APK.

## Model and art direction

The model has a bevelled, almost-cubic matte-black metal body, a silver circular
front mount with machined grooves and knurling, three bayonet catches, four
screws, a gold carrier and warm COB phosphor array. It includes a perforated
diagonal top grille and internal fan, side cooling louvres, original side
typography, rear display, separate DIM/CCT knobs, power input details, and a
folded stand bracket. The matte-black treatment and warm/ruby studio lighting
are art direction, rather than a claim about a particular production finish.

The model is approximately 101 × 100 × 110 mm including protrusions, uses metres
in the GLB, faces +Z and has +Y up. The origin is at the chassis centre. There
are no external geometry, texture, font or network references in the GLB.
The rear screen is an illustrative texture labelled in the source, not live data.

## Rebuild

All editable source and the build-only dependency live in
[`scripts/model-g60/`](../../../../scripts/model-g60/). From the repository root:

```sh
cd scripts/model-g60
bun install --frozen-lockfile
bun run build
bun run build:showcase
bun run studio
```

Open `http://127.0.0.1:9036/?capture=1` in a Chromium browser with WebGL 2.
The page renders at 2× and downsamples into the exact native image sizes, writes
the PNGs to this directory, exports the actual model to `g60.glb`, and loads the
export back through `GLTFLoader`. The “Rebuild assets” button does the same.
Wait for “Assets saved” before closing the browser. Run `bun run verify` to
check PNG alpha/dimensions, uncropped bounds, and the self-contained GLB.

`?preset=ruby&capture=1` renders only `hero-ruby.png`, `compact-ruby.png` and
`ruby-render-metrics.json`; it never writes the default images or the GLB.
`ruby.ts` holds that preset's lights, reflection panels, perspective camera
and contact shadow. The model geometry and materials are shared; only the COB
emission is raised for that render. The ruby images stand level on their
shadow, so they are framed differently from `hero.png`.

`model.ts` contains the geometry, procedural Canvas textures and materials;
`studio.ts` contains the camera, softboxes, warm key and ruby rim, PNG export,
glow generation and GLB export. The generator has no random inputs. Raster font
metrics and WebGL output can vary slightly between browser/OS versions.

`showcase.ts` provides the interactive turntable, tilt, zoom, reduced-motion
handling and lifecycle controls. `showcase-build.ts` embeds the GLB and minified
renderer into `apps/android/src/showcase/document.ts`; rebuild it after model,
lighting or viewer changes. Then run `bun run prebuild` in `apps/android` to
refresh the native asset before compiling. The generated document needs no remote assets.
The showcase blanks the exported study's illustrative rear LCD to keep sample
numbers separate from actual output. The source GLB and stills retain their
original provenance.

The studio server binds `0.0.0.0:9036`; use it on a trusted private network while
working and stop with Ctrl-C. `G60_STUDIO_HOST=127.0.0.1` keeps it on loopback
and `G60_STUDIO_PORT` changes the port. Its write endpoint accepts only the
named generated files. The built self-contained `studio.html`, alternate rear render,
reference evidence and verification report are under
`artifacts/redesign-20261025/model/`. No application code is generated or changed.

## Reference / provenance

Primary manufacturer sources were reviewed on 2026-10-25:

- [Official G60 product page](https://www.zhiyun-tech.com/en/product/detail/757): compact body, side graphic plate, top cooling, frame/bracket and control layout.
- [Official G60 user guide](https://zhiyun-website-shenzhen.oss-cn-shenzhen.aliyuncs.com/instruction/20231229/1703837987498-n25TCbCZ.pdf), printed page 5 / PDF page 9: front accessory mount and COB, fan and air outlet, display, brightness and temperature knobs, power ports and bracket.
- [Official product image](https://bucket.zhiyun-tech.com/detail/g60/MolusG60-Home-02.png), [cooling image](https://bucket.zhiyun-tech.com/detail/g60/MolusG60-HeatDissipation-Cover.jpg), and [controls image](https://bucket.zhiyun-tech.com/detail/g60/MolusG60-Switch-Cover.jpg).

The local Behance reference boards were viewed only as stylistic references for
luminous ruby falloff, scarlet/coral rim light and warm edge contrast. No pixels
from those or the manufacturer images are included in the app textures, hero,
ruby renders, glow or GLB. Reference downloads remain in the dated artifact directory only.
All geometry and graphic maps were constructed in source; Three.js 0.180.0 and
its exporter/loader/helpers are MIT-licensed build tooling.
