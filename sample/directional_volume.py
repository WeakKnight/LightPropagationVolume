"""Direction-preserving LPV extension with ray-traced surface boundaries.

The first flight uses ray-traced angular footprints; subsequent orders move
radiance unchanged through air. Only a material boundary reflects light.
The classic SH/cosine-stencil implementation remains available for comparison.
"""
from __future__ import annotations
from dataclasses import astuple
import itertools
import math
import numpy as np
import slangpy as spy


def angular_grid(extent: int):
    offsets = np.array([(*v, 0) for v in itertools.product(range(-extent, extent+1), repeat=3)
                        if any(v) and math.gcd(*v) == 1], np.int32)
    directions = offsets[:, :3] / np.linalg.norm(offsets[:, :3], axis=1)[:, None]
    if extent == 1:
        # Lebedev 26-point quadrature, exact for spherical polynomials to degree 7.
        squared = np.square(offsets[:, :3]).sum(axis=1)
        weights = np.select([squared == 1, squared == 2, squared == 3], [1/21, 4/105, 9/280])*4*math.pi
    else:
        # Estimate spherical Voronoi solid angles with a deterministic Fibonacci
        # sphere. Keep all weights positive; no lighting/reference image enters.
        size = 262144
        counts = np.zeros(len(directions), np.int64)
        for start in range(0, size, 4096):
            ids = np.arange(start, min(start+4096, size))
            z = 1-2*(ids+.5)/size
            phi = ids*(math.pi*(3-math.sqrt(5)))
            sphere = np.column_stack([np.sqrt(1-z*z)*np.cos(phi), np.sqrt(1-z*z)*np.sin(phi), z])
            counts += np.bincount(np.argmax(sphere @ directions.T, axis=1), minlength=len(directions))
        # Enforce antipodal symmetry, eliminating the small sample asymmetry.
        lookup = {tuple(v[:3]): i for i, v in enumerate(offsets)}
        counts = np.array([(counts[i]+counts[lookup[tuple(-v[:3])]])*.5 for i,v in enumerate(offsets)])
        weights = counts*(4*math.pi/size)
    return offsets, np.column_stack([directions, weights]).astype(np.float32)


def angular_footprints(directions, sample_count):
    """Deterministic subdirections within the actual spherical Voronoi bins.

    Stratify in z using a dense Fibonacci sphere; antipodal bins use mirrored
    samples. This averages visibility/source coverage, not screen-space pixels.
    """
    if sample_count == 1:
        return directions[:, None, :].copy()
    count = len(directions)
    size = 262144
    groups = [[] for _ in range(count)]
    axes = directions[:, :3].astype(np.float64)
    for start in range(0, size, 4096):
        ids = np.arange(start, min(start+4096, size))
        z = 1-2*(ids+.5)/size
        phi = ids*(math.pi*(3-math.sqrt(5)))
        sphere = np.column_stack([np.sqrt(1-z*z)*np.cos(phi), np.sqrt(1-z*z)*np.sin(phi), z])
        nearest = np.argmax(sphere @ axes.T, axis=1)
        for index in np.unique(nearest):
            groups[index].append(sphere[nearest == index])
    result = np.zeros((count, sample_count, 4), np.float32)
    filled = set()
    opposite = np.argmin(axes @ axes.T, axis=1)
    for i in range(count):
        if i in filled:
            continue
        points = np.concatenate(groups[i])
        indices = np.minimum(((np.arange(sample_count)+.5)*len(points)/sample_count).astype(int), len(points)-1)
        result[i, :, :3] = points[indices]
        result[opposite[i], :, :3] = -points[indices]
        filled.update((i, int(opposite[i])))
    return result


class DirectionalVolume:
    def __init__(self, device):
        self.device = device
        self.kernels = {name: device.create_compute_kernel(device.load_program("directional.slang", [name]))
                        for name in ("cache_main", "direct_main", "copy_direct_main", "inject_main", "clear_main", "step_main", "project_main", "irradiance_main")}
        self.resource_signature = self.source_signature = None
        self.quadratures = {}
        self.footprints = {}
        self.sample_signature = None
        self.geometry_builds = self.source_builds = 0
        self.visibility = None
        self.irradiance_signature = None
        self.irradiance_filters = {}
        self.has_angular_total = False

    def _buffer(self, data, label):
        return self.device.create_buffer(data=np.ascontiguousarray(data),
            usage=spy.BufferUsage.shader_resource | spy.BufferUsage.unordered_access, label=f"LPV.directional.{label}")

    def ensure_resources(self, scene, grid, extent):
        signature = (id(scene), grid.resolution, extent)
        if signature == self.resource_signature:
            return False
        self.device.wait()
        if extent not in self.quadratures:
            self.quadratures[extent] = angular_grid(extent)
        offsets, directions = self.quadratures[extent]
        self.direction_count = len(offsets)
        self.cells = grid.resolution**3
        self.slots = self.cells*self.direction_count
        self.offsets = self._buffer(offsets, "offsets")
        self.directions = self._buffer(directions, "directions")
        self.hits = self._buffer(np.zeros((self.slots,4),np.float32), "hits")
        self.material = self._buffer(np.zeros(self.slots,np.uint32), "material")
        for name in ("direct", "boundary", "front", "next"):
            setattr(self,name,self._buffer(np.zeros((self.slots,4),np.float32),name))
        # Shader binding placeholder; the full array is needed only by ray/none
        # consumers or explicitly requested angular diagnostics.
        self.total = self._buffer(np.zeros((1,4),np.float32), "total_unused")
        self.has_angular_total = False
        self.resource_signature = signature
        self.source_signature = None
        return True

    def ensure_irradiance(self, extent):
        if self.irradiance_signature == self.resource_signature:
            return
        from probe_visibility import octahedral_directions, IRRADIANCE_SIZE
        if extent not in self.irradiance_filters:
            normals=octahedral_directions(IRRADIANCE_SIZE,vertices=True)
            directions=self.quadratures[extent][1]
            weights=np.maximum(normals@(-directions[:,:3]).T,0)*directions[:,3]
            weights*=math.pi/weights.sum(1)[:,None]
            self.irradiance_filters[extent]=weights.astype(np.float32)
        self.irradiance_filter=self._buffer(self.irradiance_filters[extent],"irradiance_filter")
        self.irradiance_slots=self.cells*(IRRADIANCE_SIZE+2)**2
        for name in ("previous_irradiance","total_irradiance"):
            setattr(self,name,self._buffer(np.zeros((self.irradiance_slots,4),np.float32),name))
        self.irradiance_signature=self.resource_signature

    def _dispatch(self, encoder, entry, size, grid, values):
        self.kernels[entry].dispatch(thread_count=[size,1,1], vars={
            "g_grid": grid.shader_values(), "g_direction_count": self.direction_count, **values}, command_encoder=encoder)
        encoder.global_barrier()

    @staticmethod
    def scene_values(scene):
        return {"tlas": scene.tlas, **scene.buffers, "ray_epsilon": scene.epsilon}

    def update(self, encoder, scene, grid, lighting, sun, settings, fields, angular_diagnostics=False):
        geometry_changed = self.ensure_resources(scene, grid, settings.angular_resolution)
        keep_total = angular_diagnostics or settings.probe_visibility != "moments"
        if keep_total != self.has_angular_total:
            self.device.wait()
            self.total = self._buffer(np.zeros((self.slots if keep_total else 1,4),np.float32), "total")
            self.has_angular_total = keep_total
        if settings.probe_visibility == "moments":
            if self.visibility is None:
                from probe_visibility import ProbeVisibility
                self.visibility = ProbeVisibility(self.device)
            self.visibility.update(encoder, scene, grid)
            self.ensure_irradiance(settings.angular_resolution)
        visibility_values = {"g_probe_visibility": ("moments","ray","none").index(settings.probe_visibility),"g_visibility_bias":settings.visibility_bias}
        if settings.probe_visibility == "moments":
            visibility_values.update(self.visibility.shader_values())
            from probe_visibility import IRRADIANCE_SIZE
            visibility_values["g_irradiance_size"]=IRRADIANCE_SIZE
            visibility_values["g_previous_irradiance_read"]=self.previous_irradiance
        cached = {"g_offsets":self.offsets,"g_directions":self.directions,"g_hits":self.hits,
                  "g_material":self.material,"g_scene":self.scene_values(scene)}
        if geometry_changed:
            self._dispatch(encoder,"cache_main",self.slots,grid,cached)
            self.geometry_builds += 1
        sample_signature=(settings.angular_resolution,settings.source_angular_samples)
        if sample_signature!=self.sample_signature:
            if sample_signature not in self.footprints:
                directions=self.quadratures[settings.angular_resolution][1]
                self.footprints[sample_signature]=angular_footprints(directions,settings.source_angular_samples)
            self.angular_samples=self._buffer(self.footprints[sample_signature],"angular_footprints")
            self.sample_signature=sample_signature
        source_signature = (self.resource_signature, astuple(lighting),sample_signature,settings.decay)
        if source_signature != self.source_signature:
            self._dispatch(encoder,"direct_main",self.slots,grid,{**cached,"g_sun":sun,"g_direct":self.direct,
                "g_angular_samples":self.angular_samples,"g_source_samples":settings.source_angular_samples,"g_decay":settings.decay})
            self.source_signature = source_signature
            self.source_builds += 1
        for bounce in range(settings.bounces):
            if bounce==0:
                # The first free flight is integrated by ray tracing at each
                # probe. Do not advect/sum that already-completed radiance again.
                self._dispatch(encoder,"copy_direct_main",self.slots,grid,{"g_direct":self.direct,"g_next":self.front})
            else:
                self._dispatch(encoder,"inject_main",self.slots,grid,{**cached,"g_direct":self.direct,
                    "g_bounce":bounce,"g_previous_angular":self.front,"g_boundary":self.boundary,**visibility_values})
                # Injection reads the completed previous material order before this
                # order starts from zero. Never retain old order values at low steps.
                self._dispatch(encoder,"clear_main",self.slots,grid,{"g_next":self.front})
                for _ in range(settings.steps):
                    self._dispatch(encoder,"step_main",self.slots,grid,{"g_offsets":self.offsets,"g_hits":self.hits,
                        "g_boundary":self.boundary,"g_front":self.front,"g_next":self.next,"g_decay":settings.decay})
                    self.front,self.next = self.next,self.front
            self._dispatch(encoder,"project_main",self.cells,grid,{"g_directions":self.directions,
                "g_front":self.front,"g_direct":self.direct,"g_total":fields["accumulated"],
                "g_source_projected":fields["source"],"g_angular_total":self.total,
                "g_store_angular_total":keep_total,"g_bounce":bounce})
            if settings.probe_visibility=="moments":
                self._dispatch(encoder,"irradiance_main",self.irradiance_slots,grid,{
                    "g_irradiance_size":IRRADIANCE_SIZE,
                    "g_irradiance_filter":self.irradiance_filter,"g_front":self.front,
                    "g_previous_irradiance":self.previous_irradiance,"g_total_irradiance":self.total_irradiance,"g_bounce":bounce})
