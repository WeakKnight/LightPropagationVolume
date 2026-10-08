# Light Propagation Volume / SlangPy

A SlangPy application for studying diffuse GI: capture surface lighting with RT, inject it into a grid, propagate it through the volume, and compute material reflections at new surfaces.
The default is an **RT-guided compact SH9 radiance field**: capture radiance at probes, evaluate the next reflection order at actual surfaces, and retain a geometry volume, 26-neighbor prediction, and temporal refinement.
`sh_ue4` preserves the earlier incremental SH26 kernel, `sh_legacy` the older six-neighbor kernel, and `directional` the high-memory directional comparison.

![Old incremental SH26 / new SH9 / RT](docs/sh-radiance-comparison.png)

At 32³ with three material-reflection orders, GI buffers occupy approximately **214 MB**, versus **3.72 GB** for the directional extension.
Using the same full 640×480 linear-RGB comparison at 2048 spp / RT 16384 spp, the default view, side view, and changed-sun errors are
**2.64% / 3.42% / 2.26%**, all below the original 5% threshold. The earlier incremental kernel produced 30.41% / 39.62% / 28.04%.
A diagnostic that keeps the accurate field and changes only final consumption to SH9 gives 2.57% / 3.32% / 2.25%, locating the previous large errors primarily in field generation and propagation.
The metric is `sqrt(mean((LPV-RT)²))/mean(RT)` over full, unexposed linear RGB, without cropping or gain fitting. It is not a per-pixel or all-scene error bound.

Dynamic capture is spread across frames at **4096 probes/frame** by default. In the same warmed benchmark, a full rebuild takes about **111 ms**;
budgeted capture takes about **10.89 ms** per active frame, peaking at **11.94 ms**, and completes all three orders in **24 frames**.
A sun-intensity step reaches 90% of the field change in about **32 frames** (approximately 0.53 seconds at 60 FPS).
GI buffers remain around 214 MB, and all three steady-state accuracy cases pass again. Budgeting reduces peaks at the cost of response latency; initial preparation is outside this budget.
See [docs/DYNAMIC_UPDATE.md](docs/DYNAMIC_UPDATE.md) for scheduling, dynamic response, and the same-run benchmark.
Batch rendering completes capture and history convergence before accumulating pixel samples.
See [docs/SH_RADIANCE.md](docs/SH_RADIANCE.md) for the root cause, UE5 source comparison, equations, ablations, and limitations.

UE5 uses Lumen, which replaced the old LPV. This project borrows its ray-capture, compact-field, and surface-feedback ideas
while retaining LPV spatial structure. It does not reproduce all of Lumen or claim that the old UE4 LPV achieves the same accuracy.

## Running

```bash
cd /Users/litianyu/Documents/GitHub/LightPropagationVolume
./run_local.sh
```

The script prefers the project's `.venv`, falling back to the adjacent `DSHARC/sample/.venv` and local `slangpy`.
The local environment is Python 3.13.12 / a local SlangPy 0.43.0 build / Metal. No dependencies were added and Unreal was not compiled.
Use `LPV_PYTHON` and `SLANGPY_LOCAL_DIR` to select an existing environment.

For an independent Windows / Linux environment:

```bash
python -m venv .venv
# After activating the environment:
python -m pip install -r requirements.txt
python sample/entry_point.py --backend d3d12  # Windows
python sample/entry_point.py --backend vulkan # Linux
```

The GPU runtime must support acceleration structures and inline ray queries. Only Metal has been validated locally.
The default allocates no per-direction radiance or irradiance maps. Distance moments and retained geometry-ray scratch occupy about 152 MB; other GI buffers occupy about 61 MB.
These are buffer payloads, excluding images, scene data, CPU arrays, and runtime memory; they are not total process memory.
In directional mode, `--angular-resolution 3` reduces the count to 290 directions to save memory.

## Views and interaction

| Mode | Meaning |
| --- | --- |
| LPV | Direct lighting + volume indirect diffuse lighting |
| DIRECT | Direct lighting only |
| INDIRECT | Volume indirect lighting only; retains the background |
| REFERENCE | Independent RT path integration with the same indirect material depth as LPV |
| INJECTION | Slice of the first-order field's SH9 directional average; the injection source in SH mode |
| VOLUME | Slice of the propagated field's SH9 directional average |

For default SH, `Propagation steps` is the number of 26-neighbor prediction passes during target rebuilding (default 3). In directional mode, full RT solves the first order, and later material orders use this budget.
`Indirect material bounces` is the material-reflection depth, default 3. Set it to 1 to see indirect light contributed only by directly lit surfaces.
`Angular resolution` controls the direction lattice density; `Source angular samples` controls first-flight angular-footprint sampling (default 8).
Hold RMB to look; use WASD to move, Q/E vertically, and Shift to accelerate. R rebuilds; F2 saves.
`RT probes / frame` controls the capture budget; 0 requests a full rebuild. The window displays order and probe progress.
Camera changes reset only the image. Light changes rebuild the RT target across frames, reusing GV and distance moments and retaining field history. Exposure changes preserve linear image history.

The default scene is a static room with red and green walls, blue objects, a roof opening, and thick boxes.
`--room dsharc` loads the larger room; `--scene file.glb` imports a small scene.
Only diffuse material factors / vertex colors are supported: no textures, transparency, specular materials, animation, or dynamic geometry rebuilding.
Both paths support emissive surface injection.

## Batch rendering and acceptance checks

```bash
./run_local.sh --headless --frames 16 --spp 32 --output sample/output/lpv.png
./run_local.sh --headless --mode reference --frames 256 --spp 64 --output sample/output/reference.png
./run_local.sh --compare
./run_local.sh --accuracy --extra-cases
./run_local.sh --test --backend metal --debug
./run_local.sh --test-download
./run_local.sh --benchmark
./run_local.sh --benchmark-visibility
./run_local.sh --benchmark-volume
./run_local.sh --benchmark-dynamic
./run_local.sh --evaluate-sh
```

`--accuracy` enforces the fixed full-RGB 5% gate, with LPV 2048 spp and RT 16384 spp by default. All three current default cases pass; exceeding the threshold still returns a nonzero exit code.
`--accuracy --transport directional --extra-cases` runs the accurate directional comparison.
`--extra-cases` also checks a side view and changed lighting; any failure returns nonzero.
The report includes independent reference sampling-noise estimates: currently 0.56% / 0.72% / 0.48% for the three cases.
`--compare` produces views of direct lighting, one indirect reflection, three indirect reflections, and RT with matched depth.
`--benchmark` retains the earlier classic-SH cache-invalidation timing comparison.

`--linear-output file.npy` saves unexposed RGB. PNGs apply exposure, a filmic curve, and sRGB, and are not used for numerical acceptance.
`--volume-output file.npz` saves the SH9 source / accumulated field with shape `(z,y,x,RGB,9)`, plus origin, cell size, transport, and bounces.
SH mode computes irradiance directly with SH cosine convolution. Directional-mode SH is a diagnostic projection; shading uses cached irradiance maps.
`--probe-visibility ray` enables exact short-ray filtering in both paths. Directional mode also allocates its accumulated directional array for on-demand integration.
Python's `renderer.angular_data()` reads directional data as `(z,y,x,direction,RGB)`. Requesting the accumulated array allocates it on demand and rebuilds the field once.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `--transport` | sh | RT-guided SH9; `sh_ue4`: old incremental kernel; `sh_legacy`: six-neighbor kernel; `directional`: directional comparison |
| `--grid` | 32 | Grid resolution, 8..64 |
| `--angular-resolution` | 5 | 1..5 selects 26 / 98 / 290 / 578 / 1154 directions |
| `--source-angular-samples` | 8 | Subsamples per first-flight direction bin, 1..32; 1 uses the center ray |
| `--steps` | SH/old incremental kernel 3 / others 40 | Prediction passes during default-SH target rebuilding; per-frame propagation passes for the old incremental kernel |
| `--probes-per-frame` | 4096 | Per-frame capture-task budget shared across material orders; 0 requests a full rebuild |
| `--probe-rays` | 1024 | Radiance-capture directions per probe in default SH; even values 32..4096 |
| `--bounces` | 3 | Indirect material-reflection depth, 1..8; also controls the RT reference |
| `--decay` | SH/directional 1 / legacy 0.95 | Per-step damping in SH; distance-based attenuation in directional mode |
| `--history-weight / --propagation-weight` | 0.9 / 0.008 | SH history weight and RT-anchored neighbor-prediction weight |
| `--secondary-occlusion / --secondary-bounce` | 1 / 1 | GV prediction occlusion and actual surface-reflection strength |
| `--no-refinement` | off | Disable SH temporal history while retaining the same physical target field |
| `--probe-visibility` | moments | Shared distance-moment filtering; ray: exact rays; none: no filtering |
| `--visibility-bias` | 0.25 | Normal offset for distance-moment queries in cells; leaves interpolation coordinates unchanged |
| `--read-bias` | 0 | Normal offset for the final query, in cells |
| `--indirect-strength` | 1 | Display strength of indirect light; fixed at 1 for acceptance |
| `--sun-azimuth / --sun-elevation` | -50 / 55 | Ideal directional-light orientation, in degrees |
| `--sun-intensity` | 3 | Irradiance received by a plane perpendicular to the light direction |
| `--width / --height` | 960 / 640 | Image resolution |
| `--frames / --spp` | 64 / 1 | Batch frame count / pixel samples per frame |

Directional mode requires surface boundaries; `--no-occlusion` is used only for classic-SH ablation.
For default SH, `--source-samples` controls GV geometry samples and `--probe-rays` controls light capture. `--injection-bias` applies only to the old surface-flux kernels.
Increasing grid resolution does not guarantee better accuracy. History and propagation settings in `sh_ue4` must be paired; the six-neighbor kernel's 40 steps cannot be applied directly.

## The two transport paths

The directional comparison traces 8 full rays within each probe direction bin, angularly averages first-order `Lo = Le + rho*E_direct/pi`, and obtains the first-order field directly.
Later material orders cache local surface boundary conditions and solve them through the grid.
Air cells read the upstream cell along the same direction, preserving radiance. Only material surfaces multiply previous-order illumination by albedo and re-emit it.
Each solved order is accumulated once; intermediate steady-state estimates are not accumulated. Per-order irradiance maps serve final shading and subsequent reflection queries. Distance-moment / Chebyshev weights reduce sampling through walls.
Angular weights, geometry, and materials are camera-independent and do not read the RT reference's indirect-light results.

Default SH builds a vertex GV from true surface areas. Each probe captures first-order radiance with RT and projects it into SH9.
Later orders read previous-order SH irradiance at actual hits, multiply by albedo/pi, and project again.
The 26-neighbor predictor estimates directional radiance under GV constraints, anchored to the captured field. Degree-7 Lebedev weights preserve homogeneous SH9 fields.
Temporal history independently tracks the final target, preventing history decay from changing the steady-state spatial transport range.
Final shading blends SH using distance-moment weights and applies cosine convolution. See [docs/SH_RADIANCE.md](docs/SH_RADIANCE.md) for equations and the scope of the UE5 comparison.
The old incremental kernel is documented in [docs/SH_REFINEMENT.md](docs/SH_REFINEMENT.md), and the six-neighbor kernel in [docs/COMPACT_LPV.md](docs/COMPACT_LPV.md).
To reproduce the old default configuration:

```bash
./run_local.sh --transport sh_legacy --grid 16 --steps 24 --decay .95 --read-bias .25
```

| File | Responsibility |
| --- | --- |
| `sample/sh_radiance.py` | Default-SH RT target cache, material recursion, and history scheduling |
| `sample/shaders/radiance_capture / radiance_predict_26 / radiance_resolve.slang` | Default capture, 26-neighbor prediction, and history update |
| `sample/sh_refinement.py` | Shared GV construction / convergence checks and old incremental-kernel scheduling |
| `sample/shaders/build_geometry / propagate_26 / refine_*.slang` | GV construction and the old incremental SH26 kernel |
| `sample/directional_volume.py` | Direction lattice, resource / boundary / source caches, and per-order scheduling |
| `sample/shaders/directional.slang` | Boundary capture, actual surface injection, directional advection, and order accumulation |
| `sample/shaders/render_directional.slang` | Receiver irradiance from angular radiance |
| `sample/shaders/render.slang` | Shared camera / direct lighting, independent RT reference, and classic-SH queries |
| `sample/shaders/lpv.slang` | Shared grid, directional light, and diagnostic SH |
| `sample/shaders/inject / propagate / occlusion.slang` | Classic-SH injection, propagation, and reflection cache |
| `sample/renderer.py` | SlangPy scheduling, mode switching, and image history |
| `scripts/validate_accuracy.py` | Three-case 5% acceptance with a fixed error metric |
| `scripts/measure_wall_artifacts.py` | Spatial residual variation in fixed planar regions |
| `sample/probe_visibility.py` / `sample/shaders/probe_visibility.slang` | Octahedral distance-moment cache and shared Chebyshev queries |
| `scripts/benchmark_probe_visibility.py` | Directional-mode timing: short rays / on-demand integration versus moments / cached irradiance |
| `scripts/benchmark_volume.py` | Actual SH/directional buffer sizes and synchronized timings |
| `tests/test_lpv.py` | CPU and actual-GPU checks for energy, reflections, visibility, and caching |

## UE4 references and minimal download

The following 4.27 files were read through the user's signed-in Safari session: `LPVCommon.ush`, `LPVWriteCommon.ush`, `LPVPropagate.usf`,
`LPVInject_GenerateVplLists.usf`, and `LPVFinalPass.ush`. `LPVGeometryVolumeCommon.ush`,
`LPVBuildGeometryVolume.usf`, and the renderer `.cpp/.h` were also checked.
The earlier incremental kernel drew on UE4's SH9, 26-neighbor stencil, temporal refinement, and geometry volume.
The default is now an RT-guided hybrid field; see [SH_RADIANCE.md](docs/SH_RADIANCE.md). The old kernel remains for comparison; engine code and packing/LDS optimizations were not copied into the implementation.

```bash
./scripts/checkout_ue4_lpv.sh
```

The script uses **depth 1 + single branch + blob:none + no-checkout + non-cone sparse checkout**,
checking out only LPV shaders, renderer files, and the corresponding plugin source. It does not call Setup.sh, download engine dependencies, or build the Editor.
A shallow clone still needs branch-commit and tree metadata; sparse checkout alone does not eliminate metadata overhead.
Private engine references default to the Git-ignored `references/UnrealEngine-4.27` directory.

CLI Git currently lacks usable Epic repository credentials. The attempted script run failed during authentication; **the engine was not successfully cloned**.
Browser sign-in permits reading pages but does not automatically authenticate Git HTTPS. Once existing Git credentials are configured, rerun the script above.

ZIP downloading uses `scripts/download_ue4.py`. The full archive has not yet downloaded successfully; existing `.part` files must not be treated as engine source.
Temporary GitHub codeload authorization comes from the signed-in browser's normal download flow and is passed through stdin only, never saved to project files or logs:

```bash
# Interactively paste the browser's temporary codeload URL, then press Enter:
python scripts/download_ue4.py
# If a complete ZIP is already available, extract only LPV files:
python scripts/download_ue4.py --extract-only --target /absolute/path/UnrealEngine-4.27.zip
```

The downloader uses HTTP/1.1, timeouts, bounded retries, and a default 4 MiB/s limit. It first attempts Range requests.
**codeload currently ignores Range**. In that case, it rereads and verifies the saved prefix, appending only new bytes so ordinary retries do not truncate progress.
This preserves disk progress but cannot avoid retransmission bandwidth. An ETag or prefix mismatch stops the download to avoid mixing revisions.
Expired authorization preserves `.part` and stops; fresh browser download authorization is required to continue.
Completion requires a readable ZIP central directory and records SHA256. Only LPV files are extracted, with their CRCs verified; the entire engine is not unpacked.
ZIPs and private reference files remain under Git-ignored `references/`.

References:

- [Kaplanyan / Dachsbacher, I3D 2010 LPV paper](https://cg.ivd.kit.edu/publications/p2010/CLPVFRII_Kaplanyan_2010/CLPVFRII_Kaplanyan_2010.pdf)
- [Andreas Kirsch's LPV equations and solid-angle corrections](https://data.blog.blackhc.net/2010/07/lpv-annotations.pdf)
- [UE4.27 LPV documentation](https://dev.epicgames.com/documentation/en-us/unreal-engine/light-propagation-volumes?application_version=4.27)
- [UE4 LPVCommon.ush](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Shaders/Private/LPVCommon.ush)
- [UE4 LPVPropagate.usf](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Shaders/Private/LPVPropagate.usf)
- [DSHARC](https://github.com/WeakKnight/DSHARC): scene/camera, inline ray queries, accumulation, tone mapping, and window code from local revision `590c281` provided the starting point.

Validation covers area weights, Lambertian energy, RT shadows, cell-link occlusion, isotropic propagation energy, SH directions / convolution, indirect-light contribution,
camera and light rebuilding, deterministic reset, black absorption, partial-reflection flux budgets, material-reflection series, and PNG / linear-array export. See [docs/VALIDATION.md](docs/VALIDATION.md) for local results.
