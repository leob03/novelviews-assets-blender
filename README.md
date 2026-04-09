# NovelViews Assets — Blender Add-on

> **Blender tools for Hunyuan3D-compatible camera rigs, animated tours, mesh normalization, and depth/normal rendering — all from a single sidebar panel.**

<img src="assets/mutlicam_blender_viewport.png" width="600"/>

---

## What it does

| Tool | Description |
|---|---|
| **Camera Setup** | Creates the 8 orthographic cameras that exactly match `Hy3DRenderMultiView` viewpoints in ComfyUI |
| **Camera Spherical Tour** | Animated camera that visits every Hunyuan3D viewpoint in a smooth spherical trajectory |
| **Camera 360 Tour** | Clean 360° horizontal orbit around the asset — configurable elevation, seamlessly looping |
| **Normalize Mesh** | Centers and scales your mesh to match Hunyuan3D's preprocessing (bounding sphere = 1.15) |
| **Render Passes** | Renders depth maps and surface normals as image sequences or MP4 video, with inversion and background masking |

---

## Install

**Option A — from zip (recommended)**
```
./package_addon.sh          # generates novelviews_assets.zip
```
Then in Blender: **Edit → Preferences → Add-ons → Install…** → select the zip → enable **"3D View: NovelViews Assets"**

**Option B — directly from the repo**

Symlink or copy `blender_addon/` into your Blender add-ons folder:
```
~/.config/blender/<version>/scripts/addons/novelviews_assets/
```

Open the **N-panel** in the 3D Viewport → **NovelViews** tab.

---

## Panels

### Camera Setup
Reproduces the exact viewpoints used by Hunyuan3D's multi-view renderer so renders in Blender match ComfyUI output 1:1.

- **8 orthographic cameras** (top, right, bottom, left, back, front, front-left, front-right)
- Configurable distance, ortho scale, and render resolution
- Track-To constraint option for easy post-hoc adjustment

### Camera Spherical Tour
A single camera animated along a spherical path through all 8 viewpoints — useful for previewing how a model looks from every angle.

- Configurable travel duration and pause time at each waypoint
- Parallel-transported up vector (no roll flips)
- Linear interpolation throughout

### Camera 360 Tour
Smooth 360° orbit around the world Z axis — the most natural "turntable" view.

- Configurable elevation and revolution duration
- Pure yaw rotation, no roll
- Keyframes get a **Cycles modifier** for seamless timeline looping

### Normalize Mesh
Matches Hunyuan3D's internal mesh preprocessing before inference.

- Centers mesh at origin
- Scales uniformly so the bounding sphere diameter equals the target value (default 1.15)

### Render Passes

Renders depth or surface normal maps directly from any camera in the scene.

| Option | Details |
|---|---|
| **Pass** | Depth (grayscale) or Surface Normal (RGB) |
| **Invert Depth** | Near = white, far = black |
| **Mask Background** | Multiplies by alpha — background pixels become 0 |
| **Output Format** | PNG image sequence or H.264 MP4 |
| **Preview Frame** | Renders just the current frame so you can verify before a full render |

<img src="blender_render/depth/depth_0120.png" width="300"/>
<img src="blender_render/normal/normal_0001.png" width="300"/>

---

## Compatibility

- Blender **3.0+** (tested on 4.4)
- Works with any mesh — not limited to Hunyuan3D assets

---

## License

[MIT](LICENSE)
