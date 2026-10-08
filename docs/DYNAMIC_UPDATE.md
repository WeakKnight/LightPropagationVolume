# Budgeted dynamic SH field updates

2026-10-06, Metal / SlangPy, 32³ / 1024 rays per probe / three material-reflection orders.

This iteration changes RT capture scheduling without changing the validated radiance projection, actual surface reflections, or GV/26-neighbor prediction equations. The default processes at most **4096 probe-material-order tasks per frame**, replacing a full three-order target rebuild in one frame whenever lighting changes.

## Scheduling

1. Freeze the current lighting parameters when a capture cycle starts. Each frame uses a fixed probe budget and writes directly to the existing captured buffer; no per-direction ray data is stored.
2. Once all probes of a material order are captured, run that order's GV/26-neighbor prediction and publish the complete order as a new target. The next order reads this completed previous-order target.
3. Each frame blends the displayed field toward the currently published target. Unfinished higher orders retain the previous cycle's results. On first use, unfinished fields are zero. With steps=0, captured is the target, allowing publication per probe.
4. Continuous lighting changes do not interrupt the current cycle. When it finishes, capture the latest lighting, coalescing intermediate states to avoid endless restarts. Geometry, resolution, or transport-parameter changes cancel old work and reinitialize instead.
5. Convergence cannot be declared while capture is unfinished or the latest lighting remains unprocessed. History residuals are checked only after a cycle completes. Camera movement does not restart field capture.

The budget is shared across material orders. For example, the default takes 32³×3 / 4096 = 24 frames; the first order is published on frame 8. A budget of 0 retains the full-rebuild path for accuracy and performance comparison.

This iteration uses **capture spread across frames and material orders**. It does not introduce infinite-order feedback from the previous frame's total field, rotating random directions, fewer final angular samples per frame, or prioritization by lighting influence. It retains the same 1024-direction integration, allowing strict validation that the target field is independent of the budget.

## Same-run performance and response

Timings use CPU wall clock + device.wait at 640×480 / 1 spp, including field updates, rendering, accumulation, and tone mapping, but excluding the window/UI/presentation. After warming the scene, GV, distance moments, and rendering pipelines, sun intensity jumps from 2.4 to 3.0. Statistics below cover active capture frames; they are not pure GPU kernel timings.

| Probe budget per frame | Frames to complete capture | Median capture frame | P95 | Maximum | Frames to complete 90% of the field change |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0 (full field) | 1 | 110.84 ms | 110.84 ms | 110.84 ms | 24 |
| 1024 | 96 | 10.44 ms | 11.61 ms | 11.79 ms | 72 |
| 2048 | 48 | 10.63 ms | 11.74 ms | 11.85 ms | 44 |
| **4096 (default)** | **24** | **10.89 ms** | **11.88 ms** | **11.94 ms** | **32** |

Five additional full-field runs have a median of 111.17 ms and a maximum of 111.79 ms. On this machine, 4096 barely increases the peak over 2048 while substantially shortening response time, so it is the default. Per-frame cost does not decrease linearly with the budget; milliseconds cannot be predicted from the probe-count ratio.

The 90% response uses the L2 difference of material-order SH coefficients, normalized by the total change from the old steady state to the new target, sampled every 4 frames. It is not image error relative to RT. At 60 FPS, 24 frames correspond to 0.40 seconds and 32 frames to about 0.53 seconds. These are frame-count conversions, not measurements with a frame-rate cap. The default test confirms strict steady state on frame 152.

With lighting changing continuously for 96 frames, 4 cycles complete, with a median of 10.90 ms, P95 of 11.90 ms, and maximum of 12.05 ms. Once changes stop, the field catches up to the latest lighting, and its final target is bit-identical to a full rebuild.

**Budgeting reduces peaks, not necessarily total work.** Lighting latency, temporary mixing of orders from different cycles, and synchronous initialization / structural-change costs remain. The 4096 budget counts tasks; it is not a hard millisecond limit. Rays per task are controlled by probe-rays, and whole-order prediction is not further split across frames. Dynamic geometry / animation support has not been added.

Raw timing and response samples: [sh-dynamic-benchmark.json](sh-dynamic-benchmark.json).

## Accuracy, memory, and validation

Default 4096 budget, the original full 640×480 linear-RGB metric, LPV 2048 spp / RT 16384 spp, without cropping or exposure / brightness fitting:

| Case | NRMSE |
| --- | ---: |
| Default view | 2.64054% |
| Side view | 3.41705% |
| Changed sun direction | 2.25872% |

All three cases still pass the 5% gate and reuse the same RT reference cache. Completed target fields are bit-identical across budgets. Display history stops at a residual threshold, so different stopping times can produce tiny numerical image differences. Report: [sh-budgeted-accuracy.json](sh-budgeted-accuracy.json).

GI buffer payload remains 213,571,368 bytes (213.57 MB), without additional directional caches or target-field copies.

All 48 CPU/Metal debug GPU checks pass. New coverage includes non-divisible tail batches; identical complete target fields with steps=0/2/3; no premature convergence during capture; progress under continuous lighting changes; convergence to a zero field after lights are turned off; and cancellation of old work when reflection depth / propagation settings change mid-capture. Existing energy, occlusion, SH, and cache tests remain.

## Usage

```bash
./run_local.sh                           # Default: 4096 probes/frame
./run_local.sh --probes-per-frame 2048  # Smaller task budget
./run_local.sh --probes-per-frame 0     # Full-rebuild comparison
./run_local.sh --benchmark-dynamic
./run_local.sh --accuracy --extra-cases --reference-cache sample/output/accuracy-moments --output sample/output/accuracy-sh-budgeted
./run_local.sh --test --debug
```

The window provides an RT probes/frame slider and capture-order / probe progress. Batch rendering still calls settle_volume before pixel sampling, so unfinished updates do not enter steady-state accuracy reports.
