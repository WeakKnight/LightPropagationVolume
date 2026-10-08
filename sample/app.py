from __future__ import annotations

from pathlib import Path
import time

import numpy as np
import slangpy as spy

from camera import Camera, DEFAULT_POSITION, DEFAULT_TARGET
from renderer import Lighting, LPVRenderer, create_device, MODES
from volume import VolumeSettings
from scene import Scene, demo_scene, dsharc_room, load_scene


class App:
    def __init__(self, options):
        self.options = options
        self.device = create_device(options.backend, options.debug)
        data = load_scene(options.scene) if options.scene else (dsharc_room() if options.room == "dsharc" else demo_scene())
        data.positions *= options.scene_scale
        self.scene = Scene(self.device, data)
        if options.camera_position:
            camera = Camera(options.camera_position, options.camera_target, options.fov)
        elif options.scene:
            low, high = data.bounds
            center = (low + high) * .5
            radius = max(float(np.linalg.norm(high - low)), .01)
            camera = Camera(center + radius * np.array([.65, .4, .85]), center, options.fov)
        elif options.room == "dsharc":
            camera = Camera(np.array((2.8, 1.8, 3.25)) * options.scene_scale,
                np.array((-.65, 1.35, -1.25)) * options.scene_scale, options.fov)
        else:
            camera = Camera(np.array(DEFAULT_POSITION) * options.scene_scale,
                np.array(DEFAULT_TARGET) * options.scene_scale, options.fov)
        lighting = Lighting(options.sun_azimuth, options.sun_elevation, options.sun_intensity)
        settings = VolumeSettings(options.grid, options.steps, options.source_samples,
            options.injection_bias, options.read_bias, options.decay, not options.no_occlusion,options.bounces,options.transport,options.angular_resolution,options.source_angular_samples,options.probe_visibility,options.visibility_bias,
            not options.no_refinement,options.history_weight,options.propagation_weight,options.secondary_occlusion,options.secondary_bounce,options.probe_rays,options.probes_per_frame)
        self.renderer = LPVRenderer(self.device, self.scene, camera, lighting, settings,
            options.width, options.height, options.spp, options.seed, options.mode)
        self.renderer.exposure = options.exposure
        self.renderer.indirect_strength = options.indirect_strength
        self.renderer.slice = options.slice
        self.keys = set()
        self.dragging = False
        self.last_mouse = None
        self.capture_requested = False
        low, high = data.bounds
        self.move_speed = max(float(np.linalg.norm(high - low)) * .10, .01)
        print(f"Scene: {len(data.triangles):,} triangles | {options.backend} | "
              f"{options.width} x {options.height}", flush=True)

    def run(self):
        try:
            if self.options.headless:
                self.run_headless()
            else:
                self.run_interactive()
        finally:
            self.device.wait()

    def run_headless(self):
        start = time.perf_counter()
        if self.renderer.settings.transport in ("sh","sh_ue4") and self.renderer.mode not in ("direct","reference"):
            updates=self.renderer.settle_volume()
            print(f"Refinement settled in {updates} updates",flush=True)
        for frame in range(self.options.frames):
            self.renderer.frame()
            # Bound command submission backlog in long batch renders.
            if (frame + 1) % 32 == 0:
                self.device.wait()
            if (frame + 1) % max(self.options.frames // 8, 1) == 0:
                print(f"Render {frame + 1}/{self.options.frames} | {self.renderer.sample_count} spp", flush=True)
        self.renderer.save(self.options.output, self.options.linear_output)
        image = self.renderer.linear_image()
        if not np.isfinite(image).all() or np.any(image < 0):
            raise RuntimeError("Nonfinite or negative linear radiance")
        print(f"Saved {self.options.output.resolve()} | {self.renderer.sample_count} spp | "
              f"{time.perf_counter() - start:.2f}s | mean linear RGB {image.mean():.5f}", flush=True)
        memory=self.renderer.volume_memory()
        print(f"GI buffer payload: {memory['total_bytes']/1e6:.1f} MB | {self.renderer.settings.transport}",flush=True)
        if self.options.volume_output:
            self.options.volume_output.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(self.options.volume_output, source=self.renderer.volume_data("source"),
                accumulated=self.renderer.volume_data(), origin=self.renderer.grid.origin,
                cell_size=self.renderer.grid.cell_size,transport=self.renderer.settings.transport,bounces=self.renderer.settings.bounces)
        if self.renderer.source_builds and self.renderer.settings.transport=="directional":
            print(f"LPV directional: {self.renderer.settings.resolution}^3 | {self.renderer.directional.direction_count} directions | "
                f"{self.renderer.settings.steps} steps/order | {self.renderer.settings.bounces} material orders | "
                f"visibility={self.renderer.settings.probe_visibility}",flush=True)
        elif self.renderer.source_builds and self.renderer.settings.transport=="sh":
            print(f"SH radiance: {self.renderer.settings.resolution}^3 | {self.renderer.settings.probe_rays} rays/probe | "
                  f"{self.renderer.refinement.target_builds} target capture(s) | {self.renderer.refinement.updates} temporal updates",flush=True)
        elif self.renderer.source_builds:
            print(f"LPV: {self.renderer.settings.resolution}^3 | {self.renderer.settings.steps} propagation steps | "
                  f"{self.renderer.retained_samples:,} surface samples | {self.renderer.volume_builds} volume build(s)", flush=True)
        else:
            print("LPV construction skipped for this rendering mode",flush=True)

    def _slider(self, parent, name, attribute, minimum, maximum, target):
        return spy.ui.SliderFloat(parent, name, min=minimum, max=maximum,
            value=getattr(target, attribute), callback=lambda value: setattr(target, attribute, value))

    def setup_ui(self):
        self.ui = spy.ui.Context(self.device)
        panel = spy.ui.Window(self.ui.screen, "Light Propagation Volume", spy.float2(12, 12), spy.float2(310, 625))
        spy.ui.Text(panel, f"RT light capture / {self.renderer.settings.transport} transport")
        self.stats = spy.ui.Text(panel, "Starting...")
        for mode in MODES:
            label = "REFERENCE (matched material depth)" if mode == "reference" else mode.upper()
            spy.ui.Button(panel, label,
                callback=lambda mode=mode: setattr(self.renderer, "mode", mode))
        self._slider(panel, "Sun azimuth", "sun_azimuth", -180, 180, self.renderer.lighting)
        self._slider(panel, "Sun elevation", "sun_elevation", -10, 90, self.renderer.lighting)
        self._slider(panel, "Sun intensity", "sun_intensity", 0, 12, self.renderer.lighting)
        self._slider(panel, "Indirect strength", "indirect_strength", 0, 4, self.renderer)
        if self.renderer.settings.transport in ("sh_ue4","sh_legacy"):
            self._slider(panel, "Injection bias (cells)", "injection_bias", 0, 2, self.renderer.settings)
        self._slider(panel, "Read bias (cells)", "read_bias", 0, 2, self.renderer.settings)
        self._slider(panel, "Propagation decay", "decay", 0, 1, self.renderer.settings)
        self._slider(panel, "Slice height (XZ)", "slice", 0, 1, self.renderer)
        self._slider(panel, "Exposure (EV)", "exposure", -5, 5, self.renderer)
        if self.renderer.settings.transport=="sh":
            spy.ui.SliderInt(panel,"RT probes / frame (0 = full)",min=0,max=16384,value=self.renderer.settings.probes_per_frame,
                callback=lambda value:setattr(self.renderer.settings,"probes_per_frame",value))
        spy.ui.SliderInt(panel, "Propagation steps", min=0, max=3 if self.renderer.settings.transport=="sh_ue4" and self.renderer.settings.temporal_refinement else 256, value=self.renderer.settings.steps,
            callback=lambda value: setattr(self.renderer.settings, "steps", value))
        spy.ui.SliderInt(panel,"Indirect material bounces",min=1,max=8,value=self.renderer.settings.bounces,
            callback=lambda value:setattr(self.renderer.settings,"bounces",value))
        if self.renderer.settings.transport!="directional":
            spy.ui.Button(panel, "Toggle propagation occlusion",
                callback=lambda: setattr(self.renderer.settings, "occlusion", not self.renderer.settings.occlusion))
        else:
            spy.ui.SliderInt(panel,"Angular resolution",min=1,max=5,value=self.renderer.settings.angular_resolution,
                callback=lambda value:setattr(self.renderer.settings,"angular_resolution",value))
            spy.ui.SliderInt(panel,"Source angular samples",min=1,max=32,value=self.renderer.settings.source_angular_samples,
                callback=lambda value:setattr(self.renderer.settings,"source_angular_samples",value))
        spy.ui.Button(panel, "Rebuild volume + reset image", callback=self.renderer.reset)
        spy.ui.Button(panel, "Save PNG", callback=lambda: setattr(self, "capture_requested", True))
        spy.ui.Text(panel, "RMB: look | WASD: move | Q/E: down/up")
        spy.ui.Text(panel, "Shift: faster | R: reset | F2: save | Esc: quit")

    def keyboard_event(self, event):
        consumed = self.ui.handle_keyboard_event(event)
        # Always release keys, including when a UI widget has acquired focus.
        if event.is_key_release():
            self.keys.discard(event.key)
        if event.is_key_press() and not consumed:
            if event.key == spy.KeyCode.escape:
                self.window.close()
            elif event.key == spy.KeyCode.r:
                self.renderer.reset()
            elif event.key == spy.KeyCode.f2:
                self.capture_requested = True
            self.keys.add(event.key)

    def mouse_event(self, event):
        consumed = self.ui.handle_mouse_event(event)
        if event.is_button_up() and event.button == spy.MouseButton.right:
            self.dragging = False
        if event.is_button_down() and event.button == spy.MouseButton.right and not consumed:
            self.dragging = True
        position = (event.pos.x, event.pos.y)
        if event.is_move() and self.dragging and self.last_mouse is not None:
            self.renderer.camera.rotate(position[0] - self.last_mouse[0], position[1] - self.last_mouse[1])
        self.last_mouse = position

    def resize(self, width, height):
        self.device.wait()
        if width > 0 and height > 0:
            self.surface.configure(width=width, height=height,
                format=self.surface_format, vsync=self.options.vsync)
            self.renderer.resize(width, height)
        else:
            self.surface.unconfigure()

    def run_interactive(self):
        mode = spy.WindowMode.minimized if self.options.window_frames else spy.WindowMode.normal
        self.window = spy.Window(width=self.options.width, height=self.options.height,
            title="LPV | SlangPy / ray tracing injection", resizable=True, mode=mode)
        self.surface = self.device.create_surface(self.window)
        self.surface_format = next((format for format in (spy.Format.rgba8_unorm, spy.Format.bgra8_unorm)
            if format in self.surface.info.formats), None)
        if self.surface_format is None:
            raise RuntimeError("This sample requires an 8-bit UNORM presentation surface")
        self.surface.configure(width=self.options.width, height=self.options.height,
            format=self.surface_format, vsync=self.options.vsync)
        self.setup_ui()
        self.window.on_keyboard_event = self.keyboard_event
        self.window.on_mouse_event = self.mouse_event
        self.window.on_resize = self.resize
        previous = time.perf_counter()
        frames = 0
        while not self.window.should_close():
            now = time.perf_counter()
            dt, previous = min(now - previous, .1), now
            self.window.process_events()
            k = spy.KeyCode
            self.renderer.camera.move(int(k.d in self.keys) - int(k.a in self.keys),
                int(k.e in self.keys) - int(k.q in self.keys),
                int(k.w in self.keys) - int(k.s in self.keys),
                dt * self.move_speed * (4 if k.left_shift in self.keys else 1))
            if not self.surface.config:
                time.sleep(.02)
                continue
            texture = self.surface.acquire_next_image()
            if texture is None:
                time.sleep(.01)
                continue
            self.renderer.resize(texture.width, texture.height)
            encoder = self.device.create_command_encoder()
            self.renderer.render(encoder)
            # The target is UNORM, not sRGB: display already contains encoded RGB.
            encoder.blit(texture, self.renderer.display)
            self.stats.text = (f"{self.renderer.mode} | {self.renderer.sample_count} spp | {dt * 1000:.1f} ms\n"
                f"{self.renderer.settings.resolution}^3 | {self.renderer.settings.steps} steps | {self.renderer.settings.bounces} bounces | "
                f"occlusion {'ON' if self.renderer.settings.occlusion else 'OFF'} | {self.renderer.settings.probe_visibility}")
            if self.renderer.settings.transport=="sh" and self.renderer.refinement is not None:
                job=self.renderer.refinement.job
                progress=f"order {job['stage']+1}/{self.renderer.settings.bounces}, probe {job['offset']}/{self.renderer.settings.resolution**3}" if job else "capture complete"
                self.stats.text+=f"\nRT budget {self.renderer.settings.probes_per_frame or 'full'} probes/frame | {progress}"
            self.ui.begin_frame(self.window.width, self.window.height)
            self.ui.end_frame(texture, encoder)
            self.device.submit_command_buffer(encoder.finish())
            del texture
            self.surface.present()
            if self.capture_requested:
                self.renderer.save(self.options.output, self.options.linear_output)
                print(f"Saved {self.options.output.resolve()}", flush=True)
                self.capture_requested = False
            frames += 1
            if self.options.window_frames and frames >= self.options.window_frames:
                self.window.close()
        self.device.wait()
        self.surface.unconfigure()
