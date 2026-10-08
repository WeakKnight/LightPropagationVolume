# Review and optimization validation

2026-10-03, local Python 3.13.12, locally built SlangPy 0.43.0, Metal.
The DSHARC environment was reused. Unreal Editor was not compiled and no dependencies were added.

```bash
./run_local.sh --test --backend metal --debug
./run_local.sh --test-download
./run_local.sh --compare
./run_local.sh --benchmark
./run_local.sh --window-frames 3 --width 960 --height 640
```

All 12 CPU/GPU checks and 7 offline downloader checks pass. The window starts and presents three frames successfully.
The existing Metal runtime prints `No supported shader model found, pretending to support sm_6_0`;
ray-query, SH-injection, and propagation checks actually execute successfully. GPU validation is not skipped.

## Three rounds of improvements

| Round | Change | Validation |
| --- | --- | --- |
| First | 4 → 9 RGB SH coefficients; add band-2 injection, directional queries, and diffuse convolution | Lambertian flux preserved; analytic SH direction and convolution checks pass; RMSE 27.59% in the same 320×240 test |
| Second | One cell-center ray → five-ray partial occlusion per direction | Full / partial occlusion and bidirectional consistency checks; RMSE 26.65% at the same size |
| Third | Separate surface-sampling, binning, occlusion, injection-source, and propagation caches; reference / direct modes skip LPV | Parameter-invalidation checks; source reuse remains bit-identical; synchronized update time decreases |

UE4's 26-neighbor stencil, temporal refinement, and empirical intensity constants were not directly copied.
This stage retains the original six-neighbor / five-face propagation, area weighting, and `Phi/pi` units. Nine-coefficient SH also retains the original light-travel direction convention.
The two implementations use different SH signs, so basis functions and convolution were checked individually; UE4 query expressions cannot simply be pasted in.

## Previous stage: single-reflection images and error

![Direct / LPV / one-bounce RT reference](comparison-single-bounce.png)

Identical 640×480 camera, 16³ / 24 steps / 131072 surface samples / decay=0.95.
Direct and LPV images use 64 spp. The RT reference uses one indirect material reflection and 1024 spp, without a denoiser.

| Unexposed linear-RGB metric | First version | Updated version |
| --- | ---: | ---: |
| Mean LPV total-lighting RGB | 0.120963 | 0.117587 |
| Mean RT reference RGB | 0.121958 | 0.121958 |
| Mean LPV indirect RGB where direct light is near zero | 0.009395 | 0.008431 |
| Mean RT indirect RGB in the same region | 0.024843 | 0.024843 |
| Normalized RGB RMSE | 29.95% | 26.65% |

Error is `sqrt(mean((LPV-reference)²))/mean(reference)`. The updated error is about 11% lower relative to the original,
but not every region improves: distant shadows remain too dark, and the coarse grid still limits detail and lighting range.
Similar average brightness does not establish quality, and these results do not imply path-tracing accuracy.

Nine additional injection-bias / read-bias / decay combinations were tested.
Increasing decay to 0.98 or 1 increases overall error in this scene. Reducing read bias improves one camera's RMSE
but makes shadow illumination darker. These results were not used to force-tune defaults or uniformly amplify indirect light.

## Cache-update timings

`--benchmark` simulates the old invalidation policy on the same SH9 and occlusion algorithm and compares it with the new caches.
16³ / 131072 samples, 24×24 / 1 spp, excluding compilation, scene creation, and warmup; median of nine changes.
CPU timing with `device.wait()` after each frame includes CPU and GPU completion, not isolated GPU kernel time.

| Change | Simulated old invalidation | Separate caches |
| --- | ---: | ---: |
| Injection bias | 26.13 ms | 15.91 ms |
| Propagation steps | 2.58 ms | 1.40 ms |
| Sun intensity | 2.48 ms | 2.44 ms |

The main gain comes from avoiding unnecessary sampling, occlusion work, and shadow rays, not LDS, packing, or thread-group micro-optimization.
These are measurements from one local run, not frame-rate predictions for other backends.

## Check coverage

- Surface-sample area, determinism, bin weights, and discarding out-of-bounds samples without renormalization.
- Lambertian direct light `rho*E/pi` and integrated injected flux `rho*E*area`.
- Doubling sample count leaves total injected flux unchanged; direct light and injection are zero beneath a black roof.
- Five-ray full occlusion, partial occlusion, and reverse-direction consistency.
- An isotropic single-cell source conserves integrated energy in one propagation step with decay=1, reaching only six neighbors.
- Analytic diffuse response of homogeneous SH, plus signs, rotated addition-theorem solutions, and convolution for a nine-coefficient cosine lobe.
- Positive shadow GI, clearing after lights off, deterministic reset, and image / linear-array export.
- Direct-light and RT-reference modes do not build LPV; camera / read changes do not rebuild the field; step changes reuse injection sources; bias changes retain sampling / occlusion.
- Downloader behavior with real Range, ignored Range, disconnects, prefix mismatches, expired authorization, complete-partial recovery, and safe selective LPV extraction, using offline simulations.

These checks do not prove strict energy conservation for every directional field at arbitrary iteration counts.
Clamping negative low-order SH reconstruction remains biased, and default propagation attenuation is retained. Material multiple reflections were not yet included at this stage; the next section records their validation. Cascades and a complete geometry volume were still unimplemented.

## UE4 source and download status

Five 4.27 files were actually read through the existing Safari sign-in:
`LPVCommon.ush`, `LPVWriteCommon.ush`, `LPVPropagate.usf`,
`LPVInject_GenerateVplLists.usf`, and `LPVFinalPass.ush`.
Checks covered 9 RGB SH coefficients, directional reprojection, diffuse convolution, injection-normal offsets, 26 neighbors, and temporal refinement.

The full ZIP download has not succeeded. An unauthenticated CLI returns 404. Temporary codeload URLs obtained through the browser work,
but both HTTP/2 and HTTP/1.1 disconnected, and expired temporary authorization returned 404.
Range requests returned 200 rather than 206. Safari window access failed at that time, preventing authorization refresh; source reading through Safari worked again on 2026-10-04.
Partial downloads were retained and a downloader that does not truncate progress was added. A partial ZIP is not a successfully obtained local engine reference.

The original shallow partial-clone / sparse-checkout script remains available, but CLI Git lacks usable Epic repository credentials.
Private engine source will not be included in project commits.

## 2026-10-04: adding material multiple reflections

All 16 CPU/GPU checks pass with Metal debug layers, and the window starts and presents three frames successfully.
New analytic checks: black materials do not reflect; with visibility 0.6 and occlusion 0.4, transmitted flux is `0.6*S`
and reflected flux is `0.4*rho*S`; a closed isotropic test gives `S*(1+rho+rho²)` without double counting in later steps.
Actual ray-query cached reflectance, coverage, and reflection-normal hemisphere agree with independent CPU SH equations.
Changing reflection depth retains source fields, samples, bins, and geometry caches; returning to depth 1 reproduces the old image bit-for-bit.

![Direct / LPV one bounce / LPV three bounces / RT three bounces](comparison-secondary.png)

Still 640×480, 16³ / 24 spatial steps, decay=0.95, LPV 64 spp, and RT 1024 spp.
The RT reference now uses three indirect material reflections. Both LPV variants are compared with that image, so their errors are not directly comparable to the single-reflection RT errors above.

| Linear-RGB metric | LPV one reflection | LPV three reflections | RT three reflections |
| --- | ---: | ---: | ---: |
| Mean RGB | 0.117587 | 0.125087 | 0.134915 |
| Mean indirect RGB where direct light is near zero | 0.008431 | 0.012442 | 0.037072 |
| Normalized RMSE against three-reflection RT | 29.13% | 30.12% | — |

Reflections restore some shadow energy, but full-image RMSE rises slightly. Coarse grids, spatial reprojection, SH truncation, finite spatial steps, and per-step attenuation
still produce bias; more material reflections also propagate existing spatial errors. These results do not support the claim that more reflections automatically approach ground truth.
Reflectance normalization is retained without fitting extra intensity to this camera. `--bounces 1` preserves a reproducible old-algorithm comparison.
See [SECONDARY_BOUNCES.md](SECONDARY_BOUNCES.md) for source checks and discrete operators.

## 2026-10-04: the 5% gate and directional propagation

The new default at this stage is 32³ / 1154 directions / 40 spatial steps per order / three material orders / decay=1 / read bias=0.
For full 640×480 linear RGB, LPV 2048 spp / RT 16384 spp, errors are 2.76% for the default view, 3.86% for the side view, and 2.35% for changed sun direction, all below 5%.
The original default SH configuration against the same high-sample RT reference gives 30.03%.
21 CPU/GPU regression checks pass, and the new default presents three window frames successfully.
See [ACCURACY.md](ACCURACY.md) for equations, diagnosis experiments, sampling noise, and resource costs, and [accuracy-report.json](accuracy-report.json) for machine-readable results.
This stage changes the angular representation and air-transport operator; the old SH algorithm was still available through `--transport sh` at that time.

## Fixing low-frequency wall patches

The default first flight becomes an average of eight angular-footprint probe-RT subsamples, retaining directional propagation and material reflections afterward.
24 CPU/GPU checks with Metal debug and three-frame window presentation pass.
Full three-case NRMSE is 1.79% / 2.30% / 1.73%, with reference arrays bit-identical to the previous version.
Low-frequency residual standard deviations in fixed red-wall / back-wall / ceiling regions fall by 75.3% / 67.8% / 63.1%.
See [WALL_ARTIFACTS.md](WALL_ARTIFACTS.md) for the metric, cache costs, and before/after images.

## 2026-10-05: DDGI distance moments for leak suppression

Default probe consumption and secondary-reflection queries use octahedral distance moments + Chebyshev weights, with additional cached irradiance normal-direction maps.
29 CPU/GPU checks pass, including analytic probabilities, moments / variance, seams, axis-normal irradiance, no planar self-lighting, a 1 mm thin wall, caching, and existing reflection checks.
Three-frame window presentation passes. Full three-case NRMSE is 2.54% / 3.30% / 2.15%.
A small approximation error buys performance: steady offscreen frames at 640×480 / 1 spp drop from 9.06 ms to 0.62 ms; secondary updates drop from 2.37 s to 1.46 s.
See [PROBE_VISIBILITY.md](PROBE_VISIBILITY.md) for raw performance reports, all quality comparisons, and limitations.

## 2026-10-05: compact SH default

The default becomes SH9 + RT surface injection + material reflections + distance-moment filtering. All 34 CPU/Metal debug GPU checks and three-frame window startup pass.
GI buffer payload at 32³ is 212.51 MB; steady offscreen frames take 0.613 ms, and propagation updates 13.86 ms.
Full three-case NRMSE is 49.75% / 74.67% / 41.31%, failing the 5% gate.
The old directional extension remains available through `--transport directional`. After redundant caches are removed, its three-case RGB arrays remain bit-identical to the previous version and still pass 5%.
See [COMPACT_LPV.md](COMPACT_LPV.md) for the complete memory, performance, and quality tradeoffs.

## 2026-10-05: GV / SH26 / temporal refinement

- `./run_local.sh --test --debug`: 41 CPU and actual Metal GPU checks pass.
- New coverage includes 26-neighbor support, center retention and SH directions, true surface areas / mean albedo, 4/2/1-tap face/edge/corner GV queries,
  opacity³ material reflection and order separation, 0.9 history recurrence, clearing on rebuild, retaining history on light changes, static convergence, and camera independence.
- Existing SH / directional-extension, Chebyshev thin-wall, cache-invalidation, and resource-switching checks still pass.
- `./run_local.sh --window-frames 3 --width 320 --height 240 --grid 16 --source-samples 4096 --debug`: three window/UI frames pass.
- Full 640×480 / 2048 spp comparisons retain the original 16384 spp RT references byte-for-byte.
  New-SH errors of 30.41% / 39.62% / 28.04% fail the 5% quality gate. The acceptance program returns 1, distinct from GPU-test failure.
- See [SH_REFINEMENT.md](SH_REFINEMENT.md) for synchronized timings, ablations, and visualizations. Unreal was not compiled.

## 2026-10-05: correcting the RT-guided SH field

- Changing only consumption of the same accurate directional field to SH9 gives 2.57% / 3.32% / 2.25%. This diagnostic corrects the previous attribution of large errors to SH9.
- The new default constructs a compact SH field directly, reading previous-material-order irradiance at actual RT hit surfaces and retaining GV/26-neighbor prediction. History updates do not change steady-state energy.
- Full 640×480, 2048 spp / original 16384 spp references: **2.64% / 3.42% / 2.26%**, all passing the 5% gate.
  RT reference arrays are unchanged. Reports store reference SHA256 hashes, and the rerendered default view is bit-identical to the full-kernel ablation.
- `./run_local.sh --test --debug`: 45 checks pass. After the capture-order branch was changed to an explicit if, the four new Metal debug GPU checks passed again.
- Homogeneous SH9 remains invariant under 26-neighbor prediction. A closed emissive room uses exact ray visibility to verify `1+rho+rho²+rho³`; later orders are zero for black materials.
- `./run_local.sh --window-frames 3 --width 320 --height 240 --grid 16 --source-samples 4096 --probe-rays 128 --debug`: three window/UI frames pass.
- GI buffer payload is 213.57 MB; steady consumption .600 ms; history-only update .929 ms; full RT target rebuilding 112.920 ms.
  Rebuilding was not yet spread across frames, so .929 ms is not total dynamic GI cost. See [SH_RADIANCE.md](SH_RADIANCE.md).
- Local UE5.8 shader source was inspected read-only; the Editor was not compiled. The default hybrid is explicitly distinguished from complete Lumen and original UE4 LPV.

## 2026-10-06: budgeted dynamic capture

Default 4096 probes/frame; all 48 CPU/Metal debug GPU checks pass. New checks cover identical targets with non-divisible batches / even prediction-pass counts, convergence protection during unfinished capture, progress under continuous light changes, and restart after mid-capture structural changes.

Full 640×480 / 2048 spp against the same RT 16384 spp references gives NRMSE of 2.64054% / 3.41705% / 2.25872%. Target fields are bit-identical to full capture; different history-convergence stopping times produce only tiny floating-point image differences.

Same-run warmed measurements: five full rebuilds have a median of 111.17 ms; default capture frames have a median of 10.89 ms and maximum of 11.94 ms. Complete target capture takes 24 frames, and an intensity step reaches 90% of the SH field change in about 32 frames. Total ray count is not reduced, and image error below 5% is not guaranteed on every frame during dynamic updates.

Reports and reproduction: [DYNAMIC_UPDATE.md](DYNAMIC_UPDATE.md), [sh-dynamic-benchmark.json](sh-dynamic-benchmark.json), and [sh-budgeted-accuracy.json](sh-budgeted-accuracy.json).
