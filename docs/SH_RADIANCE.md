# Fixing the dark field: RT-guided compact SH9 radiance

2026-10-05, Metal / SlangPy 0.43.0. Default `sh` has been corrected to use RT radiance capture + actual surface reflections + GV-constrained 26-neighbor prediction + temporal refinement.
Under the same full-RGB metric, errors drop from **30.41% / 39.62% / 28.04%** to **2.64% / 3.42% / 2.26%**. All three cases pass the original 5% gate.

The previous attribution of large errors to insufficient SH9 representation was premature. Projecting the accurate field to SH9 gives only 2.57% / 3.32% / 2.25% error,
showing that the main problem was the old field generation and propagation. The correction does not change the reference, exposure, indirect-light strength, or error metric.

![Old incremental SH26 / new compact SH9 / same RT](sh-radiance-comparison.png)

## Checking UE5's actual approach

The local engine's `Engine/Build/Build.version` identifies UE5.8. Searches in Renderer, Engine/Shaders, and Runtime plugins found no old LPV implementation.
Epic's [UE5 migration guide](https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-engine-5-migration-guide)
explicitly states that Lumen replaces LPV and DFGI. The earlier Safari reading was of **UE4.27 LPV**, not UE5 LPV.

The following UE5 sources were inspected read-only:

- `Engine/Shaders/Private/Lumen/LumenScreenProbeTracing.usf`: ray-based lighting queries combined with the radiance cache.
- `ScreenProbeConvertToIrradianceCS` in `Engine/Shaders/Private/Lumen/LumenScreenProbeFiltering.usf`: constructs three-band SH from directional radiance and applies diffuse convolution.
- `Engine/Shaders/Private/Lumen/LumenRadianceCacheInterpolation.ush`: probe queries and spatial interpolation using depth / parallax information.

The new version follows the ideas of capturing useful radiance, compressing the field, reflecting at surface boundaries, and reusing information in space and time.
It is an **RT-guided SH radiance-field hybrid**, retaining the requested GV, 26-neighbor stencil, and history. It is neither complete Lumen nor a component-by-component replica of UE4's old incremental kernel.
It does not introduce Surface Cache, screen probes, screen traces, cascades, or UE scheduling optimizations. The engine was not compiled.

## Why the old implementation converged to a dark field

The old kernel repeatedly injected the entire field into a center-retaining incremental operator and decayed history:

```
C[t+1] = P³(0.9 C[t] + S)
visible = 0.1 C
```

Its steady state is therefore `0.1 (I - 0.9 P³)^(-1) P³ S`, not automatically the physical free-transport solution.
The factor 0.9 does more than filter temporal noise: it enters the steady-state spatial transport operator. More iterations solve that operator more accurately but cannot correct its spatial bias.

In a low-frequency approximation with homogeneous L0 and no geometry, the old 26-neighbor kernel gives:

```
a = 1 + (11/3) w
D = (13/12) w
P ≈ a I + D ∆
I - 0.9 P³ ≈ (1 - 0.9 a³) I - 0.9 * 3 a² D ∆
```

At `w=.008`, the mass term is about .01845 and the spatial term about .02479, giving a screening length of approximately **1.16 cells**.
This is only an L0 low-frequency analysis, not an exact distance bound for the full SH operator, but it explains the darkening of distant walls.
A fine grid converts this cell-scale loss into a very short world-space transport range.

Moreover, the old source was a `Phi/h²` proxy in surface cells, not the radiance actually arriving at each probe.
Reflections using opacity³ and coarse-cell average albedo also failed to reproduce accurate surface feedback.
Applying UE4's empirical presets to this project's RT area injection, fine grid, and finite material orders, then judging SH9 from the result, was not a valid comparison.
This is not reliably fixed by inserting a single pi factor or scaling all brightness uniformly.

## Separating SH representation error from transport error

The diagnostic first runs the existing accurate directional solver, then changes only final consumption to its exported SH9 field.
Geometry, lighting field, interpolation weights, and camera remain identical. This diagnostic is not the default implementation.

| Case | Directional-map consumption | SH9 consumption of the same field | Old incremental SH26 |
| --- | ---: | ---: | ---: |
| Default | 2.54% | **2.57%** | 30.41% |
| Side view | 3.30% | **3.32%** | 39.62% |
| Changed sun | 2.15% | **2.25%** | 28.04% |

SH9 can therefore achieve low error in these diffuse scenes; SH truncation does not explain the old errors of tens of percentage points.
The diagnostic does allocate the directional solver's 3.72 GB, but the new default does not depend on that solver. Memory is reported separately.
Report: [sh-projection-diagnostic.json](sh-projection-diagnostic.json).

## New field generation and actual surface reflections

Each probe uses 1024 fixed, equal-weight, antipodally paired spherical Fibonacci directions. Every ray queries only the nearest hit:

- First order: evaluate `Lo = Le + rho * E_direct / pi` at the hit.
- Later orders: read previous-material-order SH irradiance at the hit and evaluate `Lo[q] = rho * E[q-1] / pi`.
- Project to RGB × 9 SH using `Σ Lo(omega_i) Y_lm(omega_i) * 4pi/N`.

This project's SH stores light-travel direction, so the ray query and projection directions are opposite; diffuse consumption queries `-normal`.
Reading the previous order uses the same eight-probe interpolation, distance-moment / Chebyshev visibility, and normal offset as final consumption.
Materials and normals come from **actual hit surfaces**, replacing average color times opacity³ as the primary reflection mechanism.

Emission and direct sunlight enter only the first order. Later orders read only the previous order, avoiding repeated source injection or treating solver history as material-reflection depth.
Each order's source uses one ray hit plus a cache query. It does not call the multi-hit RT reference in `render.slang`, read reference arrays, or depend on the camera.
Fixed-ray projection still has angular-discretization error. The 1024-direction budget is the default validated for these three cases, not an accuracy guarantee for arbitrary scenes.

## The roles of GV and the 26-neighbor stencil

The 33³ vertex GV is retained, storing L1 occlusion SH, mean albedo, and surface area.
The new kernel uses directional occlusion to constrain exchange between cells; actual material reflections use albedo at RT hits.
Face / edge / corner links still average 4/2/1 GV vertices respectively.

The 26-neighbor stencil is now a radiance predictor constrained by the RT-captured field, rather than repeatedly adding neighbor energy:

```
anchor = captured[cell]
prediction = Σ Lebedev_weight[d] * project(
    (1-visibility) * evaluate(anchor, d)
    + visibility * evaluate(neighbor_previous_prediction, d), d)
next_prediction = (1-beta) * anchor + beta * prediction
```

`beta=.008`, with 3 prediction passes by default. Lebedev weights for the 26 face / edge / corner directions are
`4pi/21`, `4pi*4/105`, and `4pi*9/280`. Degree-7 quadrature preserves reprojection of arbitrary homogeneous SH9 fields.
Air prediction uses point radiance queries and signed SH, without repeated cosine convolution or early clamping of negative reconstructions.
Invalid grid neighbors fall back to the local captured field, as do contributions blocked by the GV.

GV primarily prevents spatial prediction from crossing boundaries here. Rays capture arriving radiance over long distances;
the 26-neighbor stencil does not handle all free transport by itself. The low error cannot be described as pure parameter tuning of the old LPV stencil.
Each order first constructs its own RT anchor and applies prediction before serving the next order's surface-reflection queries.

## Temporal refinement independent of steady-state energy

After generating the correct target field `T`, history updates as:

```
C[t+1] = h C[t] + (1-h) T
visible = Σ C_order
```

Default `h=.9`. There is no additional multiplication by 0.1, and history no longer passes every frame through the spatially lossy incremental operator.
For a fixed scene, history and no-history modes have the same steady state. History affects only the approach to the target.

The target is rebuilt when lighting, geometry, ray budget, or propagation settings change. Camera movement reuses it; exposure affects only tone mapping.
Light changes retain old history; grid, order-count, propagation-setting changes, and explicit rebuilds clear it.
Raw SH relative change is checked every 32 updates, and updates stop when stable. Batch images converge the field before accumulating the required pixel spp.

The default cold-start check confirms stability at **160 updates**. Ideal EMA residual error is `0.9^t`:
about 3.43% after 32 updates, .118% after 64, and .000139% after 128. These are distances to the target SH field, not RT-image NRMSE.
Updating once per displayed frame can still produce latency; dense offscreen warmup is not the same as waiting for 160 display frames.
Fixed-direction capture does not add rays over time, and temporal history does not eliminate systematic error from fixed quadrature.

## Full quality acceptance

640×480, 32³, three indirect material orders; SH images at 2048 spp, RT at 16384 spp.
The metric is `sqrt(mean((LPV-RT)^2))/mean(RT)` over complete unexposed linear RGB, including direct light, without cropping or intensity fitting.
RT references remain byte-identical and the report records SHA256. Original RT noise estimates are .56% / .72% / .48%.

| Case | Old incremental SH26 | New RT-guided SH9 | Original directional extension |
| --- | ---: | ---: | ---: |
| Default | 30.41% | **2.64%** | 2.54% |
| Side view | 39.62% | **3.42%** | 3.30% |
| Changed sun | 28.04% | **2.26%** | 2.15% |

All three cases pass the original 5% gate. This establishes acceptance only for these fixed cases, not an all-scene or per-pixel error bound.
Report: [sh-radiance-accuracy.json](sh-radiance-accuracy.json).

Default-view ablation, still compared against the same three-order RT:

| Configuration | NRMSE |
| --- | ---: |
| Complete new kernel | 2.64054% |
| No GV constraint on spatial prediction | 2.63664% |
| No history, same physical target | 2.64054% |
| First material order only | **13.25%** |

History no longer changes final energy. Actual surface reflections restore higher-order contributions missing from the old kernel.
GV has little effect on the full-image metric. The main improvement comes from correct radiance capture and surface feedback, not a full-image error reduction from GV.
See [sh-radiance-ablation.json](sh-radiance-ablation.json) for ablation and temporal profiles.

## Memory, performance, and limitations

Default GI buffer payload is **213.57 MB**, excluding scene data, images, CPU arrays, and runtime memory.
Distance moments and retained depth scratch account for about 152.35 MB, SH/history/target/capture for 51.38 MB, and GV plus construction samples for 9.83 MB.
No per-direction ray-hit, material, accumulated-radiance, or dense irradiance maps are allocated. Ray streams exist only temporarily inside the capture kernel.
GPU scratch/cache, compilation runtime, and driver overhead are outside this payload.

Local CPU wall clock + `device.wait()`, 640×480 / 1 spp, including rendering / accumulation / tone mapping, excluding the window/UI/presentation:

| Operation | Time |
| --- | ---: |
| Steady consumption | **0.600 ms** |
| One history-refinement frame + rendering with a cached target | **0.929 ms** |
| Light change: full three-order RT target rebuild + rendering | **112.920 ms** |
| First frame, including cold preparation | 1.164 seconds |
| Dense remaining history warmup after the first frame | .0315 seconds |

These are full-rebuild timings from 2026-10-05. Budgeted capture at 4096 probes/frame was added on 2026-10-06;
see [DYNAMIC_UPDATE.md](DYNAMIC_UPDATE.md) for updated same-run performance and response measurements. The 0.929 ms includes only history processing and cannot represent total dynamic GI update cost.
Only the existing static diffuse scenes and sun / emissive changes are supported. Performance report: [sh-radiance-benchmark.json](sh-radiance-benchmark.json).

45 CPU/actual Metal debug GPU checks pass. New coverage includes homogeneous SH9 invariance under 26-neighbor prediction, RT surface feedback, black-material absorption,
source/target/camera caches, and temporal recurrence. A closed emissive room uses exact ray visibility to isolate Chebyshev approximation,
verifying displayed radiance `1+rho+rho²+rho³`. The final effect of approximate distance moments is covered separately by the three full-image acceptance cases.

## Reproduction

```bash
./run_local.sh
./run_local.sh --headless --frames 32 --spp 64 --output sample/output/sh-radiance.png
./run_local.sh --accuracy --extra-cases --reference-cache sample/output/accuracy-moments --output sample/output/accuracy-sh-radiance
./run_local.sh --evaluate-sh
./run_local.sh --benchmark-volume
./run_local.sh --test --debug
# High-memory diagnostic isolating representation error:
./run_local.sh --accuracy --transport directional --projected-sh --extra-cases --reference-cache sample/output/accuracy-moments --output sample/output/accuracy-sh-projection
# Earlier incremental kernel that produced a dark field / older six-neighbor kernel:
./run_local.sh --transport sh_ue4
./run_local.sh --transport sh_legacy
```

`sh_ue4` labels only the old UE4-inspired incremental kernel. Its injection, GV, and material-order handling still differ from UE4, so its quality does not represent the original UE4 implementation.
