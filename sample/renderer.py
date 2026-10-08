"""SlangPy compute passes: surface injection -> SH propagation -> shading."""
from __future__ import annotations

from dataclasses import dataclass, astuple
import math
from pathlib import Path
import numpy as np
from PIL import Image
import slangpy as spy

from volume import Grid, VolumeSettings, sample_surfaces, bin_samples

SHADER_DIR = Path(__file__).resolve().parent / "shaders"
MODES = ("lpv", "direct", "indirect", "reference", "injection", "volume")


@dataclass
class Lighting:
    sun_azimuth: float = -50.0
    sun_elevation: float = 55.0
    sun_intensity: float = 3.0
    sun_color: tuple = (1.0, .95, .88)

    def shader_values(self):
        if not all(math.isfinite(x) for x in (self.sun_azimuth, self.sun_elevation, self.sun_intensity, *self.sun_color)):
            raise ValueError("Sun parameters must be finite")
        if self.sun_intensity < 0 or min(self.sun_color) < 0:
            raise ValueError("Sun energy must be nonnegative")
        az, el = math.radians(self.sun_azimuth), math.radians(self.sun_elevation)
        return {"direction": spy.float3(math.sin(az) * math.cos(el), math.sin(el), math.cos(az) * math.cos(el)),
                "color": spy.float3(*self.sun_color), "intensity": self.sun_intensity}


def create_device(backend="metal", debug=False):
    options = {"include_paths": [SHADER_DIR]}
    if backend == "metal":
        options["capabilities"] = ["metallib_4_0"]
    return spy.Device(type=getattr(spy.DeviceType, backend), enable_debug_layers=debug, compiler_options=options)


class LPVRenderer:
    def __init__(self, device, scene, camera, lighting=None, settings=None,
                 width=960, height=640, spp=1, seed=1, mode="lpv"):
        self.device, self.scene, self.camera = device, scene, camera
        self.lighting, self.settings = lighting or Lighting(), settings or VolumeSettings()
        self.spp, self.seed, self.mode = spp, seed, mode
        self.exposure, self.indirect_strength, self.slice = 1.0, 1.0, .35
        self.directional = None
        self.refinement = None
        self.visibility = None
        self._angular_diagnostics = False
        self.visibility_filter = False
        self.consume_projected_sh = False # Diagnostic: same directional field, SH9 final query.
        self.sample_count = 0
        self.retained_samples = 0
        self.volume_builds = 0
        self.surface_builds = self.bin_builds = self.link_builds = self.source_builds = 0
        self._surface_signature = self._bin_signature = self._source_signature = None
        self._resource_signature = self._volume_signature = self._image_signature = None
        self._bounce_capacity = 0
        self.programs = {name: device.load_program(f"{name}.slang", ["compute_main"])
                         for name in ("inject", "prepare", "occlusion", "propagate", "render")}
        self.pipelines = {name: device.create_compute_pipeline(program) for name, program in self.programs.items()}
        self.accumulate = device.create_compute_kernel(device.load_program("accumulator.slang", ["compute_main"]))
        self.tonemap = device.create_compute_kernel(device.load_program("tone_mapper.slang", ["compute_main"]))
        self.width = self.height = 0
        self.resize(width, height)

    def resize(self, width, height):
        if min(width, height) < 1:
            raise ValueError("Render dimensions must be positive")
        if (width, height) == (self.width, self.height):
            return
        self.device.wait()
        self.width, self.height = width, height
        for name in ("sample", "history", "display"):
            setattr(self, name, self.device.create_texture(format=spy.Format.rgba32_float,
                width=width, height=height,
                usage=spy.TextureUsage.shader_resource | spy.TextureUsage.unordered_access, label=f"LPV.{name}"))
        self._image_signature = None
        self.sample_count = 0

    def reset(self):
        self._source_signature = self._volume_signature = self._image_signature = None
        self.sample_count = 0
        if self.refinement is not None: self.refinement.restart()

    def _ensure_resources(self):
        self.settings.validate()
        signature = (id(self.scene), self.settings.resolution, self.settings.transport)
        if signature == self._resource_signature:
            if self.settings.transport != "directional" and self._bounce_capacity != self.settings.bounces:
                self._allocate_fronts()
                self._volume_signature = None
                if self.refinement is not None: self.refinement.restart()
            return
        self.device.wait()
        # Mode changes must not keep the multi-GB comparison resources alive.
        if self._resource_signature is not None and self._resource_signature[2] != self.settings.transport:
            self.directional = None
            self.visibility = None
        if self.refinement is not None: self.refinement.restart()
        self.grid = Grid.around_scene(self.scene.data, self.settings.resolution)
        layout = spy.ReflectionCursor(self.programs["inject"])
        count = self.settings.resolution**3
        self.fields = {name: self.device.create_buffer(resource_type_layout=layout.g_source.type_layout,
            element_count=count, data=np.zeros((count,28), np.float32),
            usage=spy.BufferUsage.shader_resource | spy.BufferUsage.unordered_access, label=f"LPV.{name}")
            for name in ("source", "accumulated")}
        self.links = self.reflection = None
        if self.settings.transport == "sh_legacy":
            links_layout = spy.ReflectionCursor(self.programs["occlusion"])
            self.links = self.device.create_buffer(resource_type_layout=links_layout.g_links.type_layout,
                element_count=count, data=np.zeros(count,np.uint32),
                usage=spy.BufferUsage.shader_resource | spy.BufferUsage.unordered_access, label="LPV.links")
            self.reflection = self.device.create_buffer(resource_type_layout=links_layout.g_reflection.type_layout,
                element_count=count*6,data=np.zeros((count*6,28),np.float32),
                usage=spy.BufferUsage.shader_resource | spy.BufferUsage.unordered_access,label="LPV.reflectors")
            self._allocate_fronts()
        elif self.settings.transport in ("sh","sh_ue4"):
            self._allocate_fronts()
        else:
            self.refinement = None
            self._bounce_capacity = 0
            self.samples = self.ranges = None
            self._surface_samples = None
            self._surface_signature = self._bin_signature = None
        if self.settings.transport == "sh_legacy": self.refinement = None
        if self.fields["source"].size != count * 112 or (self.reflection is not None and self.reflection.size != count*6*112):
            raise RuntimeError("Unexpected GPU SH buffer stride")
        self._resource_signature = signature
        self._source_signature = self._volume_signature = None
        self._links_ready = False

    def _allocate_fronts(self):
        self.device.wait()
        layout = spy.ReflectionCursor(self.programs["inject"])
        count = self.settings.resolution**3*self.settings.bounces
        for name in ("front","next","captured","target"):
            self.fields[name] = self.device.create_buffer(resource_type_layout=layout.g_source.type_layout,
                element_count=count,data=np.zeros((count,28),np.float32),
                usage=spy.BufferUsage.shader_resource | spy.BufferUsage.unordered_access,label=f"LPV.{name}")
        self._bounce_capacity = self.settings.bounces

    def _ensure_samples(self):
        surface_signature = (id(self.scene), self.settings.source_samples, self.seed)
        if surface_signature != self._surface_signature:
            self._surface_samples = sample_surfaces(self.scene.data, self.settings.source_samples, self.seed)
            self._surface_signature = surface_signature
            self.surface_builds += 1
        if self.settings.transport=="sh":
            # The RT radiance path only needs area samples for geometry volume.
            # No surface-flux binning/injection buffer belongs to this path.
            self.samples=self.ranges=None
            self._bin_signature=None
            self.retained_samples=len(self._surface_samples)
            return
        signature = (surface_signature, self._resource_signature, self.settings.injection_bias)
        if signature == self._bin_signature:
            return
        self.device.wait()
        samples, ranges = bin_samples(self._surface_samples, self.grid, self.settings.injection_bias)
        self.retained_samples = len(samples)
        layout = spy.ReflectionCursor(self.programs["inject"])
        self.samples = self.device.create_buffer(resource_type_layout=layout.g_samples.type_layout,
            element_count=max(len(samples),1), usage=spy.BufferUsage.shader_resource,
            data=samples if len(samples) else np.zeros((1,4,4),np.float32))
        self.ranges = self.device.create_buffer(resource_type_layout=layout.g_ranges.type_layout,
            element_count=len(ranges), usage=spy.BufferUsage.shader_resource, data=ranges)
        if self.samples.size != max(len(samples),1)*64:
            raise RuntimeError("Unexpected GPU sample buffer stride")
        self._bin_signature = signature
        self.bin_builds += 1

    def _dispatch(self, encoder, name, size, bind):
        with encoder.begin_compute_pass() as compute:
            cursor = spy.ShaderCursor(compute.bind_pipeline(self.pipelines[name]))
            cursor.g_grid = self.grid.shader_values()
            bind(cursor)
            compute.dispatch(thread_count=size)
        # Explicit and readable pass ordering; no barriers hidden in clever helpers.
        encoder.global_barrier()

    def _update_volume(self, encoder, sun):
        if self.settings.transport == "directional":
            signature = ("directional",id(self.scene),self.settings.resolution,self.settings.angular_resolution,self.settings.source_angular_samples,
                         astuple(self.lighting),self.settings.steps,self.settings.decay,self.settings.bounces,self.settings.probe_visibility,self.settings.visibility_bias,self._angular_diagnostics)
            if signature == self._volume_signature:
                return
            if self.directional is None:
                from directional_volume import DirectionalVolume
                self.directional = DirectionalVolume(self.device)
            geometry_before,source_before = self.directional.geometry_builds,self.directional.source_builds
            self.directional.update(encoder,self.scene,self.grid,self.lighting,sun,self.settings,self.fields,self._angular_diagnostics)
            self.link_builds += self.directional.geometry_builds-geometry_before
            self.source_builds += self.directional.source_builds-source_before
            self._source_signature = None # SH source export belongs to the directional path now.
            self._volume_signature = signature
            self.volume_builds += 1
            return
        n = self.settings.resolution
        size = [n, n, n]
        self._ensure_samples()
        if self.settings.transport=="sh_legacy" and self.settings.occlusion and self.settings.steps > 0 and not self._links_ready:
            def bind_links(cursor):
                self.scene.bind(cursor.g_scene)
                cursor.g_links = self.links
                cursor.g_reflection = self.reflection
            self._dispatch(encoder, "occlusion", size, bind_links)
            self._links_ready = True
            self.link_builds += 1
        source_geometry=(self._surface_signature,self._resource_signature) if self.settings.transport=="sh" else self._bin_signature
        source_signature = (source_geometry, astuple(self.lighting),self.settings.transport)
        signature = ("sh",source_signature, self.settings.steps, self.settings.decay, self.settings.occlusion, self.settings.bounces)
        if self.settings.transport=="sh_legacy" and signature == self._volume_signature:
            return
        def bind_injection(cursor):
            self.scene.bind(cursor.g_scene)
            cursor.g_sun = sun
            cursor.g_point_projection = self.settings.transport=="sh_ue4"
            cursor.g_samples, cursor.g_ranges = self.samples, self.ranges
            cursor.g_source = self.fields["source"]
        if self.settings.transport!="sh" and source_signature != self._source_signature:
            self._dispatch(encoder, "inject", size, bind_injection)
            self._source_signature = source_signature
            self.source_builds += 1
        if self.settings.transport in ("sh","sh_ue4"):
            expected="SHRadiance" if self.settings.transport=="sh" else "SHRefinement"
            if self.refinement is None or type(self.refinement).__name__!=expected:
                if self.settings.transport=="sh":
                    from sh_radiance import SHRadiance
                    self.refinement=SHRadiance(self)
                else:
                    from sh_refinement import SHRefinement
                    self.refinement=SHRefinement(self)
            before=self.refinement.geometry_builds
            self.refinement.build_geometry(encoder)
            self.link_builds+=self.refinement.geometry_builds-before
            before_targets=getattr(self.refinement,"target_builds",0)
            if self.refinement.update(encoder,source_signature):
                self.volume_builds+=1
            if self.settings.transport=="sh":
                self.source_builds+=self.refinement.target_builds-before_targets
                self._source_signature=source_signature
            self._volume_signature=("sh26",self.refinement.configuration,self.refinement.revision)
            return
        def bind_prepare(cursor):
            cursor.g_source = self.fields["source"]
            cursor.g_front = self.fields["front"]
            cursor.g_accumulated = self.fields["accumulated"]
            cursor.g_bounces = self.settings.bounces
        self._dispatch(encoder,"prepare",size,bind_prepare)
        front, next_field = self.fields["front"], self.fields["next"]
        for _ in range(self.settings.steps):
            def bind_propagation(cursor):
                cursor.g_front, cursor.g_next = front, next_field
                cursor.g_accumulated, cursor.g_links = self.fields["accumulated"], self.links
                cursor.g_decay, cursor.g_use_occlusion = self.settings.decay, self.settings.occlusion
                cursor.g_bounces, cursor.g_reflection = self.settings.bounces, self.reflection
            self._dispatch(encoder, "propagate", size, bind_propagation)
            front, next_field = next_field, front
        self.fields["front"], self.fields["next"] = front, next_field
        self._volume_signature = signature
        self.volume_builds += 1

    def render(self, encoder):
        if self.mode not in MODES or not 1 <= self.spp <= 64:
            raise ValueError("Invalid rendering mode or samples per frame")
        if not math.isfinite(self.indirect_strength) or self.indirect_strength < 0:
            raise ValueError("Indirect strength must be finite and nonnegative")
        if not math.isfinite(self.slice) or not 0 <= self.slice <= 1:
            raise ValueError("Slice must be in [0,1]")
        self._ensure_resources()
        sun = self.lighting.shader_values()
        if self.mode not in ("direct", "reference"):
            self._update_volume(encoder, sun)
        if self.settings.transport != "directional" and self.mode in ("lpv", "indirect") and self.settings.probe_visibility == "moments":
            if self.visibility is None:
                from probe_visibility import ProbeVisibility
                self.visibility = ProbeVisibility(self.device)
            self.visibility.update(encoder,self.scene,self.grid)
        signature = (self._volume_signature, astuple(self.lighting), self.camera.signature(), self.settings.read_bias,
                     self.mode, self.indirect_strength, self.slice, self.spp, self.settings.bounces, self.visibility_filter,
                     self.settings.probe_visibility,self.settings.visibility_bias,self.consume_projected_sh)
        if signature != self._image_signature or self.sample_count + self.spp >= 2**24:
            self.sample_count = 0
            self._image_signature = signature
        def bind_render(cursor):
            self.scene.bind(cursor.g_scene)
            cursor.g_camera, cursor.g_sun = self.camera.shader_values(), sun
            cursor.g_mode, cursor.g_seed = MODES.index(self.mode), self.seed
            cursor.g_sample_base, cursor.g_samples_per_frame = self.sample_count, self.spp
            cursor.g_read_bias, cursor.g_indirect_strength = self.settings.read_bias, self.indirect_strength
            cursor.g_slice = self.slice
            cursor.g_bounces = self.settings.bounces
            cursor.g_visibility_filter = self.visibility_filter
            cursor.g_volume, cursor.g_source = self.fields["accumulated"], self.fields["source"]
            visibility_mode = self.settings.probe_visibility
            if self.settings.transport != "directional" and self.visibility_filter:
                visibility_mode = "ray" # Historical diagnostic switch.
            cursor.g_probe_visibility=("moments","ray","none").index(visibility_mode)
            cursor.g_visibility_bias=self.settings.visibility_bias
            if self.settings.transport != "directional" and self.visibility is not None and visibility_mode == "moments":
                for name,value in self.visibility.shader_values().items():
                    setattr(cursor,name,value)
            if self.settings.transport=="directional" and self.mode in ("lpv","indirect"):
                if not self.consume_projected_sh:
                    cursor.g_direction_count=self.directional.direction_count
                    cursor.g_directions=self.directional.directions
                    cursor.g_angular_total=self.directional.total
                cursor.g_visibility_bias=self.settings.visibility_bias
                cursor.g_probe_visibility=("moments","ray","none").index(self.settings.probe_visibility)
                if self.settings.probe_visibility=="moments":
                    from probe_visibility import IRRADIANCE_SIZE
                    if not self.consume_projected_sh:
                        cursor.g_irradiance_size=IRRADIANCE_SIZE
                        cursor.g_probe_irradiance=self.directional.total_irradiance
                    for name,value in self.directional.visibility.shader_values().items():
                        setattr(cursor,name,value)
            cursor.g_output = self.sample
        render_pass="render"
        if self.settings.transport=="directional" and self.mode in ("lpv","indirect") and not self.consume_projected_sh:
            render_pass="render_directional"
            if render_pass not in self.pipelines:
                self.pipelines[render_pass]=self.device.create_compute_pipeline(
                    self.device.load_program("render_directional.slang",["compute_main"]))
        self._dispatch(encoder, render_pass, [self.width, self.height, 1], bind_render)
        self.accumulate.dispatch(thread_count=[self.width, self.height, 1], vars={"g_sample": self.sample,
            "g_history": self.history, "g_previous_samples": self.sample_count, "g_new_samples": self.spp},
            command_encoder=encoder)
        self.tonemap.dispatch(thread_count=[self.width, self.height, 1], vars={"g_input": self.history,
            "g_output": self.display, "g_exposure_ev": self.exposure}, command_encoder=encoder)
        self.sample_count += self.spp

    def frame(self):
        encoder = self.device.create_command_encoder()
        self.render(encoder)
        self.device.submit_command_buffer(encoder.finish())

    def linear_image(self):
        self.device.wait()
        return np.array(self.history.to_numpy()[..., :3], copy=True)

    def ensure_volume(self):
        """Explicitly build diagnostics even if the current mode does not use LPV."""
        self._ensure_resources()
        encoder = self.device.create_command_encoder()
        self._update_volume(encoder,self.lighting.shader_values())
        self.device.submit_command_buffer(encoder.finish())

    def settle_volume(self, max_updates=2048):
        """Warm up temporal transport before accumulating a fixed-scene image."""
        for _ in range(max_updates):
            self.ensure_volume()
            if self.refinement is None or self.refinement.converged:
                self.device.wait()
                return 0 if self.refinement is None else self.refinement.updates
        self.device.wait()
        raise RuntimeError(f"SH refinement did not converge after {max_updates} updates")

    def volume_data(self, name="accumulated", bounce=0):
        self.ensure_volume()
        self.device.wait()
        if self.settings.transport=="directional" and name in ("front","next","captured","target"):
            raise ValueError("Directional front is angular radiance; use angular_data()")
        n = self.settings.resolution
        # Z/Y/X/RGB/coefficient; X is the fastest grid coordinate.
        if not 0 <= bounce < self.settings.bounces:
            raise ValueError("Invalid material bounce index")
        packed = self.fields[name].to_numpy().view(np.float32).reshape(-1,n,n,n,28)[bounce if name in ("front","next","captured","target") else 0]
        lo = packed[..., :12].reshape(n,n,n,3,4)
        hi = packed[..., 12:24].reshape(n,n,n,3,4)
        return np.concatenate((lo, hi, packed[...,24:27,None]), axis=-1).copy()

    def angular_data(self, name="total"):
        """Directional radiance in Z/Y/X/direction/RGB layout (diagnostics)."""
        if self.settings.transport!="directional" or name not in ("total","front","direct","boundary"):
            raise ValueError("Expected a directional angular field")
        if name == "total":
            self._angular_diagnostics = True
        self.ensure_volume()
        self.device.wait()
        n=self.settings.resolution
        return getattr(self.directional,name).to_numpy().view(np.float32).reshape(
            n,n,n,self.directional.direction_count,4)[...,:3].copy()

    def volume_memory(self):
        """Allocated GI buffer payload; excludes scene, images, programs/runtime."""
        buffers = {"sh."+name: value for name,value in getattr(self,"fields",{}).items()}
        for name in ("links","reflection","samples","ranges"):
            value = getattr(self,name,None)
            if value is not None: buffers["sh."+name] = value
        if self.directional is not None:
            for name in ("offsets","directions","hits","material","direct","boundary","front","next","total",
                         "angular_samples","irradiance_filter","previous_irradiance","total_irradiance"):
                value = getattr(self.directional,name,None)
                if value is not None: buffers["directional."+name] = value
        if self.refinement is not None:
            for name in ("geometry","samples","ranges","rays"):
                value=getattr(self.refinement,name,None)
                if value is not None: buffers["geometry."+name]=value
        visibility = self.visibility if self.settings.transport != "directional" else getattr(self.directional,"visibility",None)
        if visibility is not None:
            for name in ("rays","indices","weights","depths","moments","active"):
                value = getattr(visibility,name,None)
                if value is not None: buffers["distance."+name] = value
        sizes = {name:int(value.size) for name,value in buffers.items()}
        return {"buffers_bytes":sizes,"total_bytes":sum(sizes.values())}

    def upload_volume(self, name, coefficients, bounce=0):
        """Diagnostic upload in the same Z/Y/X/RGB/9 layout as volume_data()."""
        n = self.settings.resolution
        c = np.asarray(coefficients, dtype=np.float32).reshape(n,n,n,3,9)
        packed = np.zeros((n,n,n,28), np.float32)
        packed[..., :12] = c[..., :4].reshape(n,n,n,12)
        packed[..., 12:24] = c[..., 4:8].reshape(n,n,n,12)
        packed[..., 24:27] = c[..., 8]
        if name in ("front","next","captured","target"):
            levels = self.fields[name].to_numpy().view(np.float32).reshape(self.settings.bounces,n,n,n,28).copy()
            levels[bounce] = packed
            packed = levels
        self.fields[name].copy_from_numpy(packed)

    def save(self, path, linear_path=None):
        self.device.wait()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        display = self.display.to_numpy()[..., :3]
        if not np.isfinite(display).all():
            raise RuntimeError("Nonfinite render output")
        Image.fromarray(np.uint8(np.clip(display * 255 + .5, 0, 255))).save(path)
        if linear_path is not None:
            linear_path = Path(linear_path)
            linear_path.parent.mkdir(parents=True, exist_ok=True)
            with linear_path.open("wb") as stream:
                np.save(stream, self.linear_image())
