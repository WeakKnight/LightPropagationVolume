"""Fixed LPV grid and deterministic, area-weighted surface samples.

The CPU only samples/bins geometry. Visibility, lighting, SH injection and all
propagation run on the GPU. Binning lets each cell own its writes: no float atomics.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import numpy as np
import slangpy as spy


@dataclass
class VolumeSettings:
    resolution: int = 32
    steps: int | None = None
    source_samples: int = 131072
    injection_bias: float = .5
    read_bias: float = 0.0
    decay: float | None = None
    occlusion: bool = True
    bounces: int = 3
    transport: str = "sh"
    angular_resolution: int = 5
    source_angular_samples: int = 8
    probe_visibility: str = "moments"
    visibility_bias: float = .25

    temporal_refinement: bool = True
    history_weight: float = .9
    propagation_weight: float = .008
    secondary_occlusion: float = 1.0
    secondary_bounce: float = 1.0
    probe_rays: int = 1024
    probes_per_frame: int = 4096

    def __post_init__(self):
        if self.steps is None:
            self.steps = 3 if self.transport in ("sh","sh_ue4") else 40
        if self.decay is None:
            self.decay = .95 if self.transport == "sh_legacy" else 1.0

    def validate(self):
        if not isinstance(self.probes_per_frame,int) or self.probes_per_frame<0:
            raise ValueError("Expected nonnegative probes per frame; 0 means full capture")
        if not isinstance(self.probe_rays,int) or self.probe_rays%2 or not 32<=self.probe_rays<=4096:
            raise ValueError("Expected even probe ray count 32..4096")
        if self.transport=="sh" and not 0<=self.propagation_weight<1:
            raise ValueError("Expected SH radiance predictor weight 0..1 (exclusive)")
        values=(self.history_weight,self.propagation_weight,self.secondary_occlusion,self.secondary_bounce)
        if not all(math.isfinite(x) for x in values) or not 0 <= self.history_weight < 1 or min(values[1:]) < 0:
            raise ValueError("Expected finite nonnegative refinement weights and history weight < 1")
        if not 8 <= self.resolution <= 64 or not 0 <= self.steps <= 256:
            raise ValueError("Expected grid resolution 8..64 and propagation steps 0..256")
        if self.transport not in ("sh", "sh_ue4", "sh_legacy", "directional") or not 1 <= self.angular_resolution <= 5:
            raise ValueError("Expected transport sh/sh_ue4/sh_legacy/directional and angular resolution 1..5")
        if not isinstance(self.source_angular_samples,int) or not 1 <= self.source_angular_samples <= 32:
            raise ValueError("Expected 1..32 angular footprint samples")
        if self.probe_visibility not in ("moments","ray","none"):
            raise ValueError("Expected probe visibility moments/ray/none")
        if not math.isfinite(self.visibility_bias) or not 0 <= self.visibility_bias <= 1:
            raise ValueError("Expected visibility normal bias 0..1 cells")
        if self.transport=="directional" and not self.occlusion:
            raise ValueError("Directional transport requires surface boundaries; use sh for no-occlusion comparison")
        if not isinstance(self.bounces,int) or not 1 <= self.bounces <= 8:
            raise ValueError("Expected 1..8 indirect material bounces")
        if not 1024 <= self.source_samples <= 1048576:
            raise ValueError("Expected 1024..1048576 surface samples")
        if not all(math.isfinite(x) for x in (self.injection_bias, self.read_bias, self.decay)):
            raise ValueError("LPV settings must be finite")
        if not 0 <= self.injection_bias <= 2 or not 0 <= self.read_bias <= 2 or not 0 <= self.decay <= 1:
            raise ValueError("Expected bias 0..2 cells and propagation decay 0..1")
        if self.transport == "sh_ue4" and self.temporal_refinement:
            # Necessary isotropic stability check: 26 inverse-distance-squared
            # weights sum to 44/3; point SH -> cosine lookup contributes 1/4.
            gain=self.decay*(1+(11/3)*self.propagation_weight)
            if self.history_weight>0 and gain>0 and (math.log(self.history_weight)+self.steps*math.log(gain)) >= 0:
                raise ValueError("Unstable isotropic refinement: reduce steps/propagation weight/history weight")


@dataclass
class Grid:
    origin: np.ndarray
    cell_size: float
    resolution: int

    @classmethod
    def around_scene(cls, data, resolution):
        low, high = data.bounds
        # Two-cell padding on each side also accommodates injection/read bias.
        cell = max(float(np.max(high - low)), 1e-3) / (resolution - 4)
        origin = (low + high) * .5 - .5 * cell * resolution
        return cls(np.asarray(origin, dtype=np.float32), cell, resolution)

    def shader_values(self):
        return {"origin": spy.float3(*self.origin), "cell_size": self.cell_size,
                "resolution": self.resolution}


def sample_surfaces(data, count, seed):
    """p_A=1/total_area; each sample carries total_area/count, never 1/count alone."""
    vertices = data.positions[data.triangles]
    cross = np.cross(vertices[:, 1] - vertices[:, 0], vertices[:, 2] - vertices[:, 0])
    twice_area = np.linalg.norm(cross, axis=1)
    area = twice_area.astype(np.float64) * .5
    total = float(area.sum())
    if not total > 0:
        raise ValueError("Scene has no nondegenerate surface area")
    rng = np.random.default_rng(seed)
    # Stratified area selection reduces random concentration on small triangles.
    selection = (np.arange(count) + rng.random(count)) * (total / count)
    triangle = np.searchsorted(np.cumsum(area), selection, side="right")
    a, b, c = np.moveaxis(vertices[triangle], 1, 0)
    uv = rng.random((count, 2))
    root = np.sqrt(uv[:, 0])
    bary = np.column_stack((1 - root, root * (1 - uv[:, 1]), root * uv[:, 1]))
    samples = np.zeros((count, 4, 4), dtype=np.float32)
    samples[:, 0, :3] = a * bary[:, 0, None] + b * bary[:, 1, None] + c * bary[:, 2, None]
    samples[:, 0, 3] = total / count
    # Geometric normals keep injection and shadow origins consistent. Smooth
    # shading normals are used only for final surface shading in this first version.
    samples[:, 1, :3] = cross[triangle] / twice_area[triangle, None]
    samples[:, 2, :3] = data.albedos[triangle, :3]
    samples[:, 3, :3] = data.emissions[triangle, :3]
    return samples


def bin_samples(samples, grid, injection_bias):
    positions = samples[:, 0, :3] + samples[:, 1, :3] * (injection_bias * grid.cell_size)
    cells = np.floor((positions - grid.origin) / grid.cell_size).astype(np.int64)
    keep = np.all((cells >= 0) & (cells < grid.resolution), axis=1)
    cells, selected = cells[keep], samples[keep]
    n = grid.resolution
    indices = cells[:, 0] + n * (cells[:, 1] + n * cells[:, 2])
    order = np.argsort(indices, kind="stable")
    counts = np.bincount(indices, minlength=n**3)
    starts = np.cumsum(counts) - counts
    ranges = np.column_stack((starts, counts)).astype(np.uint32)
    # Dropped samples are not renormalized: light outside this finite grid is lost.
    return np.ascontiguousarray(selected[order]), ranges
