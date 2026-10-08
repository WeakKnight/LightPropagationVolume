# SH9 LPV: geometry volume, 26 neighbors, and temporal refinement

> The current default has been corrected to an RT-guided compact SH9 field. This page preserves the earlier kernel's history; see [SH_RADIANCE.md](SH_RADIANCE.md) for the latest analysis and low-error acceptance results.

2026-10-05, Metal / SlangPy 0.43.0. At this stage, default `sh` changed to geometry volume + 26 neighbors + persistent field history.
The original six-neighbor / five-face reprojection and short-ray geometry cache remain as `sh_legacy`; the directional extension remains as `directional`.

These three changes and directional SH reprojection improve the fixed cases, but do not provide high-accuracy GI: with the same 32³ preset, full linear-RGB errors drop from
49.75% / 74.67% / 41.31% to **30.41% / 39.62% / 28.04%**. This does not establish that UE4 itself has these errors.

## UE4.27 source actually checked

The following files were read through the user's signed-in Safari session. The entire engine was not downloaded, engine source files were not copied, and the Editor was not compiled.
The GitHub links below require Epic repository access.

- [LPVPropagate.usf](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Shaders/Private/LPVPropagate.usf): 26 neighbors, inverse squared distance, center retention, GV occlusion, and opacity³ secondary reflection.
- [LPVWriteCommon.ush](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Shaders/Private/LPVWriteCommon.ush): point-direction SH projection; propagation inputs use solidAngle=1.
- [LPVCommon.ush](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Shaders/Private/LPVCommon.ush): cosine-convolution queries, previous-frame weight 0.9, and final lighting factor 0.1.
- [LPVGeometryVolumeCommon.ush](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Shaders/Private/LPVGeometryVolumeCommon.ush): L1 occlusion SH and RGB geometry color.
- [LPVBuildGeometryVolume.usf](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Shaders/Private/LPVBuildGeometryVolume.usf): RSM geometry samples and empirical area weights.
- [LPVInject_AccumulateVplLists.usf](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Shaders/Private/LPVInject_AccumulateVplLists.usf): point-direction injection; UE uses the negative surface normal as the incident direction.
- [LightPropagationVolume.cpp](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Source/Runtime/Renderer/Private/LightPropagationVolume.cpp): 3 spatial propagation passes by default.

## Geometry volume

The light field remains RGB × 9 SH coefficients at cell centers. Geometry is stored at cell vertices, so a 32³ light field has a 33³ GV.
Each GV cell uses two float4 values, 32 bytes: four L1 directional-occlusion coefficients, RGB mean albedo, and area.

Area samples are binned to their nearest GV vertex. Occlusion coefficients are `Σ (A/h²) Y_lm(normal)`, and material color is `Σ A*rho / Σ A`.
The geometry field reads neither lighting nor the camera and traces no rays for propagation links. Light injection still tests sun visibility with RT.
Final consumption retains distance moments and Chebyshev weights. GV handles occlusion during propagation; these have separate roles.

Each propagation link averages GV vertices around its midpoint: 4 taps for a face neighbor, 2 for an edge, and 1 for a corner.
`opacity = saturate(cosine_lookup(GV, toward_neighbor) * secondary_occlusion)`.
Transmission multiplies by `1-opacity`; reflection uses mean albedo and `opacity³`.

This version uses true surface area and pure albedo. UE's GV comes from RSMs, includes projected area and empirical factors, and uses a VPL-derived color proxy.
The structure and equations are inspired by UE, but this cannot be called a bit-for-bit reproduction of UE output.

## 26-neighbor propagation and SH directions

This version stores light-travel direction; UE injection uses the opposite incident direction. Query and projection remain consistent after converting conventions.
For unit direction `u` from receiver to neighbor and squared distance `d²`:

```
w = propagation_weight / d²
incoming = irradiance(neighbor_same_order, -u) * w * (1-opacity)
reflection = irradiance(center_previous_order, u) * w * albedo * opacity³ * secondary_bounce
next_same_order = center_same_order + Σ project_direction(incoming + reflection, -u)
```

Default `propagation_weight=.008`, with `decay=1` after each propagation pass.
Point-direction projection uses `Y_lm(u)` rather than applying cosine-convolution band factors at every cell transition.
Queries still use SH cosine convolution and clamp negative RGB. The old outgoing-flux budget remains only in `sh_legacy`; it is not mixed into this UE-style kernel.

Source flux is `(rho * E_direct + pi * Le) * A`. The new kernel point-projects `Phi/h²` along the surface normal.
This preserves the angular integral of the old `Phi/(pi*h²)` cosine-lobe projection but changes higher-order coefficients.

To retain comparison with an RT reference having three indirect hits, each material order is stored separately.
Air propagation preserves order; reflection reads only the previous material order. Propagation iteration count is not treated as reflection depth.
UE mixes reflection contributions within one field; finite-order separation here is another explicit difference. Outside-grid boundaries use a zero field, while UE clamps neighbor coordinates.

## Persistent temporal refinement

Each update decays the previous raw SH field, injects current sources, performs 3 spatial passes, and finally scales and combines material orders:

```
raw[t+1] = P³(0.9 * raw[t] + source[t])
visible[t+1] = 0.1 * Σ raw_order[t+1]
```

The 0.1 factor is paired history normalization, not brightness fitted to RT. Light changes preserve history and converge over frames. R rebuilds, or changes to grid/material depth/
propagation settings, clear history. Camera and exposure do not change the field. Source samples are fixed, so refinement solves the field;
it does not accumulate more accurate surface injection through randomized resampling.

This is not an ordinary image EMA of a fixed field. Field changes reset pixel accumulation to avoid mixing old GI into the new image; stable fields accumulate pixel spp normally.
Every 32 updates, the maximum change in raw SH coefficients is checked. Static updates pause when relative change is below 1e-5 or absolute change below 1e-7.
Lighting changes resume updates. Checks include a GPU wait and CPU readback, so they are not pure GPU timings.
Batch rendering, `--accuracy`, and `--compare` call `settle_volume()` before accumulating the required pixel spp.

The default scene initially needs **416 updates**, and a light change needs **352**. These measure initialization from an empty field and solving with retained history respectively;
the latter is not a cold-start convergence rate. At one update per display frame, 416 frames at 60 Hz take about 6.9 seconds, producing noticeable response lag.

In a uniform field without geometry, the 26 `1/d²` weights sum to 44/3, and the L0 point-projection / cosine-query product is 1/4.
The uniform-field per-pass gain is therefore `g = decay * (1 + (11/3)*weight)`. A necessary temporal-stability condition is `history_weight * g^steps < 1`.
The default is about 0.982. Four passes at the same weights exceed 1, so the old 40-step default cannot be reused directly.
Parameter validation rejects combinations already unstable in a uniform field. This is not a complete stability proof for arbitrary geometry and SH clamping.

`--no-refinement` provides a single-frame ablation: history becomes zero, final scaling becomes 1, and propagation weight becomes .08, matching UE's non-refinement preset.

## Quality evaluation with the same metric

640×480, LPV 2048 spp / RT 16384 spp, 32³, three material-reflection orders, moments consumption.
The metric is `sqrt(mean((LPV-RT)^2))/mean(RT)` over complete unexposed linear RGB, without cropping or gain fitting.
Existing reference arrays are reused byte-for-byte, with SHA256 recorded. RT noise estimates are 0.56% / 0.72% / 0.48%.

| Case | Old six-neighbor SH | New SH26 + GV + refinement | Existing directional extension |
| --- | ---: | ---: | ---: |
| Default | 49.75% | **30.41%** | 2.54% |
| Side view | 74.67% | **39.62%** | 3.30% |
| Changed sun | 41.31% | **28.04%** | 2.15% |

![Old SH / new SH / same RT](sh-refinement-comparison.png)

The new kernel still fails the 5% gate, with substantial missing indirect light on the ceiling, shadowed walls, and around boxes.
Old-kernel numbers use the same default 32³ preset, not the separately displayed 16³ legacy baseline in the `validate_accuracy.py` comparison.
That baseline's default-view error is about 29.82%, also demonstrating that greater density or a different kernel does not guarantee higher accuracy in every configuration.
Full report: [sh-refinement-accuracy.json](sh-refinement-accuracy.json).

Default-view ablations, using the same reference and 2048 spp:

| Configuration | Full-RGB error |
| --- | ---: |
| Complete new kernel | 30.41% |
| No GV occlusion or associated reflection | 29.62% |
| No temporal refinement, single-frame 3-pass propagation | 34.64% |
| First material order only, still compared with three-order RT | 30.45% |

This does not prove GV is useless: full-image metrics include substantial identical direct lighting that can mask local leaks. However, the improvement cannot be attributed entirely to GV.
The opacity³ reflection contributes little to this case's full-image metric. The higher-order material mechanism exists but still fails to reproduce the RT indirect-energy distribution.
Ablations and SH-distance-to-final profiles are recorded in [sh-refinement-ablation.json](sh-refinement-ablation.json).

![Single frame / temporal refinement / RT](sh-refinement-temporal.png)

> The conclusion below applies only to the incremental transport kernel at this stage. The subsequent SH9 representation diagnostic and RT-guided correction show that the same nine coefficients can achieve low error; see [SH_RADIANCE.md](SH_RADIANCE.md).

The conclusion at this stage was that this incremental kernel is unsuitable as a convergent substitute for ground truth.
Temporal refinement removes incomplete-iteration error; it cannot recover information lost by low-order SH, GV, and the local propagation operator.
This version did not measure actual UE4 rendering error.

## Memory and performance

32³, three material orders. Values are actual allocated GI buffer payloads, excluding images, scene data, CPU arrays, and runtime memory.

| Data | Decimal MB |
| --- | ---: |
| source + final SH | 7.34 |
| Three-order history double buffer | 22.02 |
| Injection samples and ranges | 8.65 |
| 33³ GV | 1.15 |
| GV construction samples and ranges | 8.68 |
| Distance moments, retained depth scratch, and indices | 152.34 |
| Total | **200.19** |

GV construction samples are retained for geometry-cache rebuilding. No half-precision packing or scratch-release micro-optimizations were applied.

Local synchronized CPU wall clock, including rendering / accumulation / tone mapping, excluding the window/UI/presentation:

| Configuration | Steady consumption | One update + rendering |
| --- | ---: | ---: |
| New SH, one 3-pass refinement update | 0.629 ms | **2.377 ms** |
| Directional extension, full 40/41-step update | 0.596 ms | 1420.683 ms |

The update workloads and routes to a stable field differ; these values cannot be interpreted directly as an equal-quality speedup.
The new SH first frame takes about 0.804 seconds, including cold preparation, followed by about 0.750 seconds for dense remaining refinement.
Performance report: [sh-refinement-benchmark.json](sh-refinement-benchmark.json).

## Reproduction

```bash
./run_local.sh
./run_local.sh --transport sh_ue4 --headless --frames 32 --spp 64 --output sample/output/sh26.png
./run_local.sh --accuracy --transport sh_ue4 --extra-cases --reference-cache sample/output/accuracy-moments --output sample/output/accuracy-sh-refinement
./run_local.sh --evaluate-sh
./run_local.sh --benchmark-volume
./run_local.sh --test --debug
# Original six-neighbor kernel:
./run_local.sh --transport sh_legacy
```

`--accuracy` still returns 1 when error exceeds 5%. Here that means a failed quality gate, not a Slang compilation or GPU execution error.
