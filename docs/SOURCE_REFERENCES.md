# Local source reference index

2026-10-05: frequently consulted source files are stored under `references/source-cache/`. Search local files first. Visit the web only for missing files or updates, and cache the complete original text immediately after obtaining it.

## Cached files

There are 46 files totaling 870,558 bytes (approximately 0.87 MB), verified byte-for-byte after copying. Original repository-relative paths are preserved. Absolute source paths, file sizes, and SHA-256 hashes are recorded in `../references/source-cache/manifest.json`. These are source subsets for reading, not independently buildable projects.

### Local UE5 version: 28 files

Source: `/Users/litianyu/Perforce/hermitli_litianyudeMacBook-Pro_PBR`. The version is recorded in the copied `Engine/Build/Build.version`. Copies come from the current working directory and do not represent a pristine official commit.

Cache directory: `../references/source-cache/UE5-local/`.

- `Engine/Shaders/Private/Lumen/LumenScreenProbeFiltering.usf`: radiance-to-SH projection and filtering.
- `Engine/Shaders/Private/Lumen/LumenScreenProbeTracing.usf`: probe tracing and lighting queries.
- `Engine/Shaders/Private/Lumen/LumenScreenProbeGatherTemporal.usf`: temporal processing.
- `Engine/Shaders/Private/Lumen/LumenRadianceCacheInterpolation.ush`: radiance-cache interpolation and depth-related processing.
- Other `LumenScreenProbe*` and `LumenRadianceCache*` shaders in the same directory: related declarations and update flow.
- `Engine/Source/Runtime/Renderer/Private/Lumen/LumenScreenProbeGather.cpp/.h` and `LumenRadianceCache.cpp/.h`: CPU scheduling.
- `Engine/Shaders/Private/SHCommon.ush`: SH basis functions and operations.

### RTXGI DDGI: 18 files

Source: `/Users/litianyu/Downloads/RTXGI-DDGI-main`.

Cache directory: `../references/source-cache/RTXGI-DDGI-local/rtxgi-sdk/shaders/`, containing all files currently in the shader directory.

- `ddgi/Irradiance.hlsl`: final probe sampling and visibility weights.
- `ddgi/ProbeBlendingCS.hlsl`: irradiance, distance moments, and history blending.
- `ddgi/ProbeRelocationCS.hlsl` and `ddgi/ProbeClassificationCS.hlsl`: probe positioning and classification.
- `ddgi/include/`: indexing, octahedral mapping, and common functions.

## UE4.27 LPV: not yet cached

The 10 core files previously read on the web are listed in the manifest's `ue4_27.files`: propagation, injection, GV, final consumption, and renderer `.cpp/.h` files. This inventory is not a source copy.

During this attempt, the GitHub connector returned 404 for `EpicGames/UnrealEngine@4.27`. The Safari window interface returned `cgWindowNotFound`, and an AppleScript read did not return and was terminated. Existing archives are incomplete downloads. Failed responses, page summaries, and incomplete archives have not been marked as source files.

When browser access is available, save the complete original text of these 10 files to `../references/source-cache/UE4-4.27/`, preserving the `Engine/...` paths, and record file sizes, hashes, and an available commit revision. Alternatively, once Git HTTPS authentication works, use the existing `scripts/checkout_ue4_lpv.sh` for sparse checkout.

## Quick searches

Run from the project root:

```bash
rg -n 'Chebyshev|variance|visibility' references/source-cache/RTXGI-DDGI-local
rg -n 'SHBasisFunction3|MulSH3' references/source-cache/UE5-local
rg -n 'History|Temporal' references/source-cache/UE5-local/Engine/Shaders/Private/Lumen
```

The entire `references/` directory is excluded by `.gitignore`. The cache stays local; project documentation records the index and provenance.
