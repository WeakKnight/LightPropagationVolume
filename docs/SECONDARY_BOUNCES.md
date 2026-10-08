# Material reflections and LPV secondary bounces

> The current default has been corrected to an RT-guided compact SH9 field. This page preserves the earlier kernel's history; see [SH_RADIANCE.md](SH_RADIANCE.md) for the latest analysis and low-error acceptance results.

> The default was subsequently upgraded to GV + 26 neighbors + temporal refinement. This page records the preceding kernel / preset; see [SH_REFINEMENT.md](SH_REFINEMENT.md) for that evaluation.

2026-10-04. UE4.27 source was read through the user's signed-in Safari session, followed by implementation and validation on this project's Metal backend.

## How UE4.27 handles it

- [LightPropagationVolume.h](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Source/Runtime/Renderer/Private/LightPropagationVolume.h): `LPV_MULTIPLE_BOUNCES` is 1, and the geometry volume has SH order 1.
- [LightPropagationVolume.cpp](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Source/Runtime/Renderer/Private/LightPropagationVolume.cpp): reflection strength comes from `LPVSecondaryBounceIntensity`; values above a threshold select the multiple-bounce shader permutation. The class constructor initializes it to 0. Having this mechanism in source does not mean it is enabled by default in every scene.
- [LPVGeometryVolumeCommon.ush](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Shaders/Private/LPVGeometryVolumeCommon.ush): the geometry volume stores directional occlusion SH and, when multiple bounces are enabled, additional RGB color.
- [LPVBuildGeometryVolume.usf](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Shaders/Private/LPVBuildGeometryVolume.usf): aggregates directional occlusion and color from geometry VPL lists. Color averages the lists' flux field multiplied by RSM area weights, making it a weighted color proxy. This investigation did not trace the raster material pass generating geometry VPLs, so it cannot be called unscaled raw albedo.
- [LPVPropagate.usf](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Shaders/Private/LPVPropagate.usf): reads light traveling from the current cell toward blockers, multiplies by geometry color, occlusion weight, and secondary-reflection strength, and adds it to propagation. This branch also requires secondary occlusion to be enabled.

The reflection term has the structure:

\[
\Delta C_{\mathrm{reflection}}=
 C_{\mathrm{outgoing}}\odot C_{\mathrm{geometry}}
 (1-w_{\mathrm{visibility}})^3\,s_{\mathrm{bounce}}.
\]

`C_outgoing` already includes cell / propagation weights. Geometry color, cubed opacity, and global bounce strength contain empirical factors.
UE4's temporal refinement and 26-neighbor stencil also differ from this project's spatial front accumulation. The borrowed structure is intercepted light multiplied by material color and re-emitted.

## Simplified implementation in this project

The existing five RT rays per cell link cache the nearest hit's **albedo and geometric normal** during static geometry preparation.
Misses contribute transmission and hits contribute reflection response, using the same coverage samples.
Reflection response is stored at the source cell, with the normal facing that cell; reflected light is still emitted from the source-side air cell.
This approximates actual surface positions with coarse voxels. It does not move light behind the blocker into the neighboring cell.

For link i, cache the RGB SH response:

\[
A_i(\omega)=\frac{1}{5}\sum_{j\in\mathrm{hit}(i)}
 \frac{\rho_j}{\pi}\max(n_j\cdot\omega,0),
\qquad v_i=\frac{N_{\mathrm{miss}}}{5}.
\]

The stored representation consists of its nine SH coefficients. At every propagation step, compute the full outgoing-flux proxy for the link:

\[
\phi_i=\Omega_m L(d_i)+
 \Omega_s\sum_{a\perp d_i}L(\operatorname{normalize}(d_i+0.5a)),
\]

The main-face solid angle is 0.4006696846 and each of the four side-face solid angles is 0.4234313544.
The flux proxy and field share cell-area normalization; physical flux additionally requires cell_size².
Transmission multiplies by `v_i`; added reflection is `phi_i * A_i`, channel by channel.
For complete hits with identical normals and albedo, integrated reflected flux is exactly `rho * phi_i`, because a cosine lobe integrates to π.
No extra secondary-bounce intensity adjustment is applied; black materials naturally absorb light.
Averaged hit normals / materials, five-ray coverage, and five-ray angular integration remain approximations.

## Avoiding repeated injection

`front[q][cell]` stores the **new light at the current step** for each material-reflection order. `q=0` is the source injected from directly lit surfaces.
Conceptually, the next step is:

\[
f_{t+1,q}=T(f_{t,q})+R(f_{t,q-1}),\qquad f_{t,-1}=0.
\]

`T` is occluded spatial propagation; `R` is intercepted flux multiplied by the material-reflection response.
Each step adds the new front to accumulated exactly once. Accumulated is never fed back into `R`.

Default `--bounces 3` stores q=0,1,2, while the RT reference likewise traces at most three indirect surface hits.
The final visible surface's albedo is multiplied separately during shading. `--bounces 1` preserves the old algorithm, propagating only sources from directly lit surfaces.
`--steps 24` is the shared spatial budget for all orders, not 24 separate propagation steps per order.
Secondary reflection is disabled without geometry occlusion, matching UE4's shader condition.

In a closed, isotropic analytic test without attenuation, this produces `S*(1+rho+rho²)`; more spatial steps do not keep increasing that sum.
The general field is not an exact physical Neumann series: low-order SH queries clamp negative RGB, so `T/R` are not strictly linear.
Spatial reprojection and geometry discretization add further error. Adding material reflections supplies the missing mechanism; it does not prove convergence to ground truth.

## Data and running

- `sample/shaders/occlusion.slang`: static ray queries and cached transmission coverage / reflection SH.
- `sample/shaders/propagate.slang`: outgoing-flux integration, per-order transmission / reflection, and one-time accumulation.
- `sample/shaders/prepare.slang`: initializes the first layer from source and zeros other layers.
- `sample/renderer.py`: layered front buffers and cache invalidation; reflection-depth changes retain injection sources and geometry caches.
- `sample/shaders/render.slang`: independent diffuse RT reference with matching reflection depth.

```bash
./run_local.sh --bounces 3
./run_local.sh --headless --bounces 1 --output sample/output/one.png
./run_local.sh --compare --bounces 3
./run_local.sh --test --backend metal --debug
```

`source` and `accumulated` remain RGB × 9 SH per cell; final consumption obtains irradiance by SH cosine convolution.
Reflection responses also use SH9; front/next add a material-order dimension. Static caching eliminates additional ray queries during propagation.

## Validation and limitations

16 CPU/GPU checks pass, including new checks:

1. Closed isotropic source with per-channel rho: obtains `S*(1+rho+rho²)` and does not repeatedly accumulate later steps.
2. With occlusion 0.4, transmission is `0.6*S` and reflection is `0.4*rho*S`; black materials reflect zero.
3. Actual RT-plane cached albedo, coverage, and reflection-normal hemisphere match independent CPU SH equations.
4. Reflection-depth changes do not rebuild propagation geometry; lights off leaves no residual, and returning to depth 1 reproduces the old image bit-for-bit.

See [VALIDATION.md](VALIDATION.md) for images and errors comparing three reflections against three-reflection RT.
The added reflections brighten shadows, but full-image RMSE rises slightly; this is not presented as a universal quality improvement.
These checks validate particular reflectances, budgets, and orders. They do not prove strict conservation for arbitrary SH fields.
Coarse grids, SH9, finite spatial steps, per-step decay, and missed fine-geometry coverage remain limitations.
This section records the 2026-10-04 implementation, which added only diffuse material reflections originating from direct light.
The 2026-10-05 compact default also adds SH emissive-surface injection with flux `pi*Le*area`; see [COMPACT_LPV.md](COMPACT_LPV.md) for that stage's results.
