"""Small CPU checks plus actual GPU energy/visibility/SH transport tests."""
from __future__ import annotations
import argparse
import contextlib
import io
import math
from pathlib import Path
import sys
import unittest
import numpy as np
import slangpy as spy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sample"))
from camera import Camera
from entry_point import parse_args
from renderer import LPVRenderer, Lighting, create_device
from scene import Scene, SceneData, SceneBuilder, demo_scene
from volume import Grid, VolumeSettings, sample_surfaces, bin_samples

BACKEND = "metal" if sys.platform == "darwin" else "d3d12" if sys.platform == "win32" else "vulkan"
DEBUG = False


def plane(albedo=(.5, .5, .5)):
    vertices = np.array([[-1,0,-1], [-1,0,1], [1,0,1], [1,0,-1]], np.float32)
    return SceneData(vertices, np.tile([0,1,0], (4,1)), np.array([[0,1,2], [0,2,3]]),
        np.tile([*albedo,1], (2,1)), np.zeros((2,4)))


class CPUChecks(unittest.TestCase):
    def test_surface_area_and_barycentric_sampling(self):
        data = plane()
        samples = sample_surfaces(data, 16384, 5)
        self.assertAlmostEqual(float(samples[:,0,3].sum()), 4, places=5)
        np.testing.assert_array_equal(samples[:,0,1], 0)
        self.assertLessEqual(float(np.abs(samples[:,0,[0,2]]).max()), 1)
        np.testing.assert_allclose(samples[:,1,:3], np.tile([0,1,0], (len(samples),1)))
        np.testing.assert_array_equal(samples, sample_surfaces(data, 16384, 5))

    def test_grid_binning_preserves_area(self):
        data = demo_scene()
        grid = Grid.around_scene(data, 32)
        original = sample_surfaces(data, 8192, 1)
        selected, ranges = bin_samples(original, grid, .5)
        self.assertEqual(len(selected), len(original))
        self.assertEqual(int(ranges[:,1].sum()), len(original))
        self.assertAlmostEqual(float(selected[:,0,3].sum()), float(original[:,0,3].sum()), places=4)
        # A deliberately displaced volume discards samples, without brightening survivors.
        outside = Grid(grid.origin + 100, grid.cell_size, grid.resolution)
        discarded, empty = bin_samples(original, outside, .5)
        self.assertEqual(len(discarded), 0)
        self.assertEqual(int(empty[:,1].sum()), 0)

    def test_cli_invalid_settings(self):
        for arguments in (["--probes-per-frame", "-1"], ["--grid", "4"], ["--steps", "-1"], ["--decay", "1.01"],
                          ["--sun-intensity", "nan"], ["--bounces","0"], ["--bounces","9"], ["--spp", "0"], ["--angular-resolution","0"], ["--source-angular-samples","0"], ["--source-angular-samples","33"], ["--visibility-bias","nan"], ["--visibility-bias","-1"], ["--transport","directional","--no-occlusion"], ["--camera-position", "0", "1", "2"]):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args(arguments)

    def test_refinement_defaults_and_isotropic_stability(self):
        s=VolumeSettings();s.validate()
        self.assertEqual((s.steps,s.decay),(3,1))
        self.assertEqual(VolumeSettings(transport="sh_legacy").steps,40)
        with self.assertRaises(ValueError):VolumeSettings(transport="sh_ue4",steps=4).validate()
        VolumeSettings(steps=40,propagation_weight=.0006).validate()
        for args in (["--history-weight","1"],["--propagation-weight","nan"],["--transport","sh_ue4","--steps","40"]):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):parse_args(args)

    def test_directional_quadrature(self):
        from directional_volume import angular_grid
        for extent, count in [(1,26),(2,98),(3,290),(4,578),(5,1154)]:
            offsets, directions=angular_grid(extent)
            self.assertEqual(len(offsets),count)
            self.assertTrue(np.all(directions[:,3]>0))
            np.testing.assert_allclose(np.linalg.norm(directions[:,:3],axis=1),1,rtol=2e-7)
            self.assertAlmostEqual(float(directions[:,3].sum()),4*math.pi,places=5)
            np.testing.assert_allclose((directions[:,:3]*directions[:,3,None]).sum(0),0,atol=2e-6)


    def test_angular_footprints_cover_bins_and_preserve_symmetry(self):
        from directional_volume import angular_grid, angular_footprints
        offsets,directions=angular_grid(2)
        np.testing.assert_array_equal(angular_footprints(directions,1),directions[:,None,:])
        samples=angular_footprints(directions,8)[:,:,:3]
        np.testing.assert_allclose(np.linalg.norm(samples,axis=2),1,rtol=2e-7)
        nearest=np.argmax(samples@directions[:,:3].T,axis=2)
        np.testing.assert_array_equal(nearest,np.broadcast_to(np.arange(len(directions))[:,None],nearest.shape))
        lookup={tuple(v[:3]):i for i,v in enumerate(offsets)}
        for i,offset in enumerate(offsets):
            np.testing.assert_array_equal(samples[i],-samples[lookup[tuple(-offset[:3])]])


    def test_distance_filter_normalization_and_octahedral_borders(self):
        from probe_visibility import distance_layout,DISTANCE_SIZE,DISTANCE_RAYS
        rays,indices,weights=distance_layout()
        np.testing.assert_allclose(np.linalg.norm(rays[:,:3],axis=1),1,rtol=2e-7)
        np.testing.assert_allclose(weights.sum(1),1,rtol=2e-7)
        self.assertTrue(np.all(weights>=0))
        self.assertTrue(np.all(indices<DISTANCE_RAYS))
        side=DISTANCE_SIZE+2
        combined=np.concatenate((indices.astype(np.float32),weights),axis=1).reshape(side,side,-1)
        np.testing.assert_array_equal(combined[0,1:-1],combined[1,-2:0:-1])
        np.testing.assert_array_equal(combined[-1,1:-1],combined[-2,-2:0:-1])
        np.testing.assert_array_equal(combined[1:-1,0],combined[-2:0:-1,1])
        np.testing.assert_array_equal(combined[1:-1,-1],combined[-2:0:-1,-2])
        np.testing.assert_array_equal(combined[0,0],combined[-2,-2])
        np.testing.assert_array_equal(combined[-1,-1],combined[1,1])



class GPUChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.device = create_device(BACKEND, DEBUG)

    @classmethod
    def tearDownClass(cls):
        cls.device.wait()

    def renderer(self, data=None, samples=16384, steps=6, mode="lpv"):
        scene = Scene(self.device, data or plane())
        camera = Camera((0,2,0), (0,0,0), 30)
        lighting = Lighting(0, 90, 1, (1,1,1))
        return LPVRenderer(self.device, scene, camera, lighting,
            VolumeSettings(16, steps, samples,read_bias=.25,decay=.95,bounces=1,transport="sh_legacy",probe_visibility="none"), width=24, height=24, spp=4, mode=mode)

    def image(self, renderer, frames=1):
        for _ in range(frames):
            renderer.frame()
        image = renderer.linear_image()
        self.assertTrue(np.isfinite(image).all())
        self.assertGreaterEqual(float(image.min()), 0)
        return image

    def test_lambert_direct_energy_and_injection_flux(self):
        renderer = self.renderer(plane((.7,.2,.1)), steps=0, mode="direct")
        image = self.image(renderer)
        expected = np.broadcast_to(np.array([.7,.2,.1])/math.pi, image.shape)
        np.testing.assert_allclose(image, expected, rtol=2e-6)
        source = renderer.volume_data("source")
        # Integral of reflected intensity over all directions, after undoing h^2.
        reflected_flux = source[...,0].sum(axis=(0,1,2)) * math.sqrt(4*math.pi) * renderer.grid.cell_size**2
        np.testing.assert_allclose(reflected_flux, np.array([.7,.2,.1])*4, rtol=2e-5)

    def test_sample_count_does_not_change_brightness(self):
        a, b = self.renderer(samples=16384), self.renderer(samples=32768)
        self.image(a)
        self.image(b)
        np.testing.assert_allclose(a.volume_data("source")[...,0].sum(axis=(0,1,2)),
            b.volume_data("source")[...,0].sum(axis=(0,1,2)), rtol=2e-5)

    def test_SH_emissive_surface_injection_flux(self):
        data=plane((0,0,0));data.emissions[:,:3]=[2,1,.5]
        renderer=self.renderer(data,steps=0)
        renderer.lighting.sun_intensity=0
        self.image(renderer)
        source=renderer.volume_data('source')
        emitted=source[...,0].sum(axis=(0,1,2))*math.sqrt(4*math.pi)*renderer.grid.cell_size**2
        np.testing.assert_allclose(emitted,np.array([2,1,.5])*math.pi*4,rtol=2e-5)

    def test_sun_shadow_and_cell_link_blocker(self):
        data = plane()
        builder = SceneBuilder()
        builder.box((4,.1,4), (0,.75,0), (0,0,0))
        roof = builder.finish()
        count = len(data.positions)
        blocked = SceneData(np.concatenate((data.positions, roof.positions)),
            np.concatenate((data.normals, roof.normals)),
            np.concatenate((data.triangles, roof.triangles + count)),
            np.concatenate((data.albedos, roof.albedos)), np.concatenate((data.emissions, roof.emissions)))
        renderer = self.renderer(blocked)
        renderer.camera = Camera((0,.3,0), (0,0,0), 30)
        np.testing.assert_array_equal(self.image(renderer), 0)
        np.testing.assert_array_equal(renderer.volume_data("source"), 0)
        links = renderer.links.to_numpy().view(np.uint32).reshape(16,16,16)
        # The upward link crossing the plane must be blocked; its reciprocal too.
        lower = int(math.floor((0-renderer.grid.origin[1])/renderer.grid.cell_size - .5))
        self.assertEqual((int(links[8,lower,8]) >> 9) & 7, 0)
        self.assertEqual((int(links[8,lower+1,8]) >> 6) & 7, 0)

    def test_isotropic_propagation_conserves_integrated_flux(self):
        renderer = self.renderer(steps=0)
        self.image(renderer)
        field = np.zeros((16,16,16,3,9), np.float32)
        field[8,8,8,:,0] = math.sqrt(4*math.pi) # Constant intensity one in one cell.
        renderer.upload_volume("front",field)
        renderer.upload_volume("accumulated",np.zeros_like(field))
        encoder = self.device.create_command_encoder()
        def bind(cursor):
            cursor.g_front = renderer.fields["front"]
            cursor.g_next = renderer.fields["next"]
            cursor.g_accumulated = renderer.fields["accumulated"]
            cursor.g_links = renderer.links
            cursor.g_reflection = renderer.reflection
            cursor.g_bounces = renderer.settings.bounces
            cursor.g_use_occlusion = False
            cursor.g_decay = 1.0
        renderer._dispatch(encoder, "propagate", [16,16,16], bind)
        self.device.submit_command_buffer(encoder.finish())
        propagated = renderer.volume_data("next")
        np.testing.assert_allclose(propagated[...,0].sum(axis=(0,1,2)),
            np.full(3, math.sqrt(4*math.pi)), rtol=2e-6)
        self.assertEqual(np.count_nonzero(propagated[...,0,0]), 6)
        np.testing.assert_array_equal(propagated[8,8,8], 0)

    def test_SH_convolution_and_light_travel_direction(self):
        renderer = self.renderer(mode="indirect")
        renderer.lighting.sun_intensity = 0
        self.image(renderer)
        field = np.zeros((16,16,16,3,9), np.float32)
        field[...,0] = math.sqrt(4*math.pi)
        renderer.upload_volume("accumulated",field)
        renderer._image_signature = None
        np.testing.assert_allclose(self.image(renderer), .5, rtol=2e-6)
        # Cosine travel lobe +Y: an upward-facing receiver sees its back (zero),
        # while the underside sees its front. This catches a common sign reversal.
        field[...] = 0
        field[..., :4] = np.array([math.sqrt(math.pi)/2, -math.sqrt(math.pi/3), 0, 0])
        field[..., 6] = -math.sqrt(5*math.pi)/16
        field[..., 8] = -math.sqrt(15*math.pi)/16
        renderer.upload_volume("accumulated",field)
        renderer._image_signature = None
        np.testing.assert_array_equal(self.image(renderer), 0)
        renderer.camera = Camera((0,-2,0), (0,0,0), 30)
        np.testing.assert_allclose(self.image(renderer), 127/384, rtol=3e-6)

    def test_SH9_rotated_cosine_convolution(self):
        data=plane()
        normal=np.ones(3)/math.sqrt(3)
        up=np.array([0.,1.,0.])
        axis=np.cross(up,normal)
        skew=np.array([[0,-axis[2],axis[1]],[axis[2],0,-axis[0]],[-axis[1],axis[0],0]])
        rotation=np.eye(3)+skew+skew@skew/(1+up@normal)
        data.positions=(data.positions@rotation.T).astype(np.float32)
        data.normals=(data.normals@rotation.T).astype(np.float32)
        renderer=self.renderer(data,mode="indirect")
        renderer.camera=Camera(2*normal,np.zeros(3),30)
        renderer.lighting.sun_intensity=0
        self.image(renderer)
        for travel in [np.array([.3,-.8,.5]),np.array([-1.,-2.,-3.]),np.array([1.,-1.,0.])]:
            travel=travel/np.linalg.norm(travel)
            x,y,z=travel
            basis=np.array([1/math.sqrt(4*math.pi),-math.sqrt(3/(4*math.pi))*y,
                math.sqrt(3/(4*math.pi))*z,-math.sqrt(3/(4*math.pi))*x,
                math.sqrt(15/(4*math.pi))*x*y,-math.sqrt(15/(4*math.pi))*y*z,
                math.sqrt(5/(16*math.pi))*(3*z*z-1),-math.sqrt(15/(4*math.pi))*x*z,
                math.sqrt(15/(16*math.pi))*(x*x-y*y)])
            coefficients=basis*np.array([math.pi,*[2*math.pi/3]*3,*[math.pi/4]*5])
            field=np.broadcast_to(coefficients,(16,16,16,3,9)).copy()
            renderer.upload_volume("accumulated",field)
            renderer._image_signature=None
            mu=float(travel@(-normal))
            # Addition theorem, not the shader's term-by-term expression.
            expected=.5*max(.25+mu/3+(5/64)*(.5*(3*mu*mu-1)),0)
            np.testing.assert_allclose(self.image(renderer),expected,rtol=2e-5,atol=2e-7)

    def test_cache_invalidation_and_lazy_modes(self):
        renderer = self.renderer(mode="direct")
        self.image(renderer)
        renderer.mode = "reference"
        self.image(renderer)
        self.assertEqual((renderer.volume_builds,renderer.source_builds,renderer.surface_builds,renderer.link_builds),(0,0,0,0))
        renderer.mode = "lpv"
        renderer.settings.occlusion = False
        self.image(renderer)
        self.assertEqual(renderer.link_builds,0)
        self.assertEqual((renderer.source_builds,renderer.surface_builds,renderer.bin_builds),(1,1,1))
        source = renderer.volume_data("source")
        renderer.settings.steps += 1
        self.image(renderer)
        np.testing.assert_array_equal(renderer.volume_data("source"),source)
        self.assertEqual(renderer.source_builds,1)
        renderer.settings.occlusion = True
        self.image(renderer)
        self.assertEqual(renderer.link_builds,1)
        renderer.settings.injection_bias = .7
        self.image(renderer)
        self.assertEqual((renderer.source_builds,renderer.surface_builds,renderer.bin_builds,renderer.link_builds),(2,1,2,1))
        renderer.settings.source_samples *= 2
        self.image(renderer)
        self.assertEqual((renderer.surface_builds,renderer.bin_builds,renderer.link_builds),(2,3,1))
        renderer.lighting.sun_intensity = 0
        self.image(renderer)
        np.testing.assert_array_equal(renderer.volume_data(),0)
        self.assertEqual((renderer.surface_builds,renderer.link_builds),(2,1))

    def test_partial_occlusion_and_reciprocity(self):
        renderer = self.renderer(demo_scene())
        self.image(renderer)
        links = renderer.links.to_numpy().view(np.uint32).reshape(16,16,16)
        axes = [(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)]
        fractional = 0
        for i,(dx,dy,dz) in enumerate(axes):
            counts = (links >> (3*i)) & 7
            self.assertLessEqual(int(counts.max()),5)
            fractional += np.count_nonzero((counts>0)&(counts<5))
            for z in range(1,15):
                for y in range(1,15):
                    for x in range(1,15):
                        reverse = (int(links[z-dz,y-dy,x-dx]) >> (3*(i^1))) & 7
                        self.assertEqual(int(counts[z,y,x]),reverse)
        self.assertGreater(fractional,0)

    def directional_renderer(self, bounces=3):
        return LPVRenderer(self.device,Scene(self.device,plane((.7,.2,.1))),
            Camera((0,2,0),(0,0,0),30),Lighting(0,90,1,(1,1,1)),
            VolumeSettings(16,20,angular_resolution=1,bounces=bounces,transport="directional"),width=24,height=24,spp=4)

    def test_directional_plane_has_no_self_bounce(self):
        renderer=self.directional_renderer()
        expected=np.broadcast_to(np.array([.7,.2,.1])/math.pi,(24,24,3))
        np.testing.assert_allclose(self.image(renderer),expected,rtol=2e-6)
        # Nonzero outgoing light, zero incoming light on the same flat surface.
        self.assertGreater(float(renderer.angular_data().max()),.01)
        renderer.mode="indirect"
        np.testing.assert_array_equal(self.image(renderer),0)

    def test_directional_first_flight_energy_and_cache(self):
        renderer=self.directional_renderer(1);renderer.settings.steps=0
        self.image(renderer)
        dv=renderer.directional
        offsets=dv.offsets.to_numpy().view(np.int32).reshape(-1,4)
        direction=int(np.flatnonzero(np.all(offsets[:,:3]==(0,1,0),axis=1))[0])
        expected=np.array([.7,.2,.1])/math.pi
        np.testing.assert_allclose(renderer.angular_data("direct")[8,12,8,direction],expected,rtol=2e-6)
        geometry,source=renderer.link_builds,renderer.source_builds
        renderer.settings.source_angular_samples=4;self.image(renderer)
        self.assertEqual((renderer.link_builds,renderer.source_builds),(geometry,source+1))
        np.testing.assert_allclose(renderer.angular_data("direct")[8,12,8,direction],expected,rtol=2e-6)
        undamped=renderer.angular_data("direct").copy()
        renderer.settings.decay=.8;self.image(renderer)
        self.assertEqual((renderer.link_builds,renderer.source_builds),(geometry,source+2))
        self.assertTrue(np.all(renderer.angular_data("direct")<=undamped+1e-7))
        self.assertLess(float(renderer.angular_data("direct").sum()),float(undamped.sum()))

    def test_angular_footprint_averages_partial_emissive_coverage(self):
        # Two coplanar halves: black and unit emission. A bin spans their edge.
        vertices=np.array([[-1,0,-1],[-1,0,1],[0,0,1],[0,0,-1],
                           [0,0,-1],[0,0,1],[1,0,1],[1,0,-1]],np.float32)
        triangles=np.array([[0,1,2],[0,2,3],[4,5,6],[4,6,7]])
        emission=np.zeros((4,4),np.float32);emission[2:,:3]=1
        data=SceneData(vertices,np.tile([0,1,0],(8,1)),triangles,np.zeros((4,4)),emission)
        renderer=LPVRenderer(self.device,Scene(self.device,data),Camera((0,2,0),(0,0,0),30),
            Lighting(0,90,0),VolumeSettings(16,0,bounces=1,angular_resolution=1,transport="directional"),width=24,height=24,spp=4)
        self.image(renderer);dv=renderer.directional
        offsets=dv.offsets.to_numpy().view(np.int32).reshape(-1,4)
        direction=int(np.flatnonzero(np.all(offsets[:,:3]==(0,1,0),axis=1))[0])
        # Center of the +Y bin hits the emissive half, but some subrays hit black.
        value=renderer.angular_data("direct")[8,12,8,direction]
        self.assertGreater(float(value[0]),0)
        self.assertLess(float(value[0]),1)
        np.testing.assert_array_equal(value,np.full(3,value[0]))
        renderer.settings.source_angular_samples=1;self.image(renderer)
        np.testing.assert_array_equal(renderer.angular_data("direct")[8,12,8,direction],1)

    def test_chebyshev_visibility_analytic_cases(self):
        rows=np.array([[1,2,5,0],[3,2,5,0],[4,2,5,0],[3,2,4,0],[2,2,4,0]],np.float32)
        expected=np.array([1,1/8,1/125,0,1],np.float32)
        usage=spy.BufferUsage.shader_resource|spy.BufferUsage.unordered_access
        cases=self.device.create_buffer(data=rows,usage=usage)
        result=self.device.create_buffer(data=np.zeros(len(rows),np.float32),usage=usage)
        path=Path(__file__).parent/'shaders/probe_visibility_checks.slang'
        kernel=self.device.create_compute_kernel(self.device.load_program(str(path),['formula_main']))
        kernel.dispatch(thread_count=[len(rows),1,1],vars={'g_cases':cases,'g_results':result,'g_count':len(rows)})
        self.device.wait()
        np.testing.assert_allclose(result.to_numpy().view(np.float32),expected,rtol=2e-6)

    def test_distance_moments_and_static_cache(self):
        renderer=self.directional_renderer(1);self.image(renderer)
        cache=renderer.directional.visibility
        from probe_visibility import DISTANCE_SIZE
        side=DISTANCE_SIZE+2
        m=cache.moments.to_numpy().view(np.float32).reshape(16,16,16,side,side,2)
        self.assertTrue(np.isfinite(m).all())
        self.assertTrue(np.all(m[...,1]>=m[...,0]**2-2e-7))
        np.testing.assert_array_equal(m[...,0,1:-1,:],m[...,1,-2:0:-1,:])
        active=cache.active.to_numpy().view(np.uint32).reshape(16,16,16)
        self.assertEqual(int(active[8,8,8]),1)
        self.assertEqual(int(active[8,7,8]),0)
        # Camera, lighting, quadrature and query bias do not change geometry depth.
        renderer.camera=Camera((.1,2,0),(0,0,0),30);self.image(renderer)
        renderer.lighting.sun_intensity=2;self.image(renderer)
        renderer.settings.angular_resolution=2;self.image(renderer)
        renderer.settings.visibility_bias=.1;self.image(renderer)
        self.assertEqual(cache.builds,1)
        renderer.settings.resolution=8;self.image(renderer)
        self.assertEqual(cache.builds,2)

    def test_cached_irradiance_matches_axis_quadrature_and_constant_radiance(self):
        renderer=self.directional_renderer(1);self.image(renderer);dv=renderer.directional
        from probe_visibility import octahedral_directions,IRRADIANCE_SIZE
        normals=octahedral_directions(IRRADIANCE_SIZE,vertices=True)
        dirs=dv.directions.to_numpy().view(np.float32).reshape(-1,4)
        for values in (np.ones(len(dirs)),1+.2*dirs[:,0]+.3*dirs[:,1]**2):
            field=np.zeros((16**3,len(dirs),4),np.float32);field[...,:3]=values[None,:,None]
            dv.front.copy_from_numpy(field)
            encoder=self.device.create_command_encoder()
            dv._dispatch(encoder,'irradiance_main',dv.irradiance_slots,renderer.grid,{
                'g_irradiance_size':IRRADIANCE_SIZE,'g_irradiance_filter':dv.irradiance_filter,
                'g_front':dv.front,'g_previous_irradiance':dv.previous_irradiance,
                'g_total_irradiance':dv.total_irradiance,'g_bounce':0})
            self.device.submit_command_buffer(encoder.finish());self.device.wait()
            result=dv.previous_irradiance.to_numpy().view(np.float32).reshape(16**3,-1,4)
            for normal in ((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)):
                cosine=np.maximum(-dirs[:,:3]@normal,0)*dirs[:,3]
                expected=math.pi*np.dot(values,cosine)/cosine.sum()
                indices=np.flatnonzero(np.all(normals==normal,axis=1))
                self.assertGreater(len(indices),0)
                np.testing.assert_allclose(result[:,indices,:3],expected,rtol=2e-6)
            if np.all(values==1):np.testing.assert_allclose(result[...,:3],math.pi,rtol=2e-6)

    def test_moments_reject_bright_probe_across_thin_wall(self):
        builder=SceneBuilder();builder.box((2,.001,2),(0,0,0),(1,1,1))
        renderer=LPVRenderer(self.device,Scene(self.device,builder.finish()),Camera((0,2,0),(0,0,0),30),
            Lighting(0,90,0),VolumeSettings(16,0,bounces=1,angular_resolution=1,transport="directional"),width=24,height=24,spp=4,mode='indirect')
        self.image(renderer)
        count=renderer.directional.direction_count
        field=np.zeros((16,16,16,count,4),np.float32)
        field[:,:8,:,:,:3]=1 # Bright probes below the wall; above-side probes are black.
        for mode in ('none','ray','moments'):
            renderer.settings.probe_visibility=mode;self.image(renderer)
            if mode!='moments':renderer.directional.total.copy_from_numpy(field)
            if mode=='moments':
                from probe_visibility import IRRADIANCE_SIZE
                irradiance=np.zeros((16,16,16,(IRRADIANCE_SIZE+2)**2,4),np.float32)
                irradiance[:,:8,:,:,:3]=math.pi
                renderer.directional.total_irradiance.copy_from_numpy(irradiance)
            renderer._image_signature=None
            image=self.image(renderer)
            if mode=='none':self.assertGreater(float(image.mean()),.05)
            elif mode=='ray':np.testing.assert_array_equal(image,0)
            else:self.assertLess(float(image.max()),.001)

    def test_SH_moments_reject_bright_probe_across_thin_wall(self):
        builder=SceneBuilder();builder.box((2,.001,2),(0,0,0),(1,1,1))
        renderer=LPVRenderer(self.device,Scene(self.device,builder.finish()),Camera((0,2,0),(0,0,0),30),
            Lighting(0,90,0),VolumeSettings(16,0,bounces=1,transport="sh_ue4"),width=24,height=24,spp=4,mode='indirect')
        self.image(renderer)
        field=np.zeros((16,16,16,3,9),np.float32)
        field[:,:8,:,:,0]=math.sqrt(4*math.pi)
        renderer.upload_volume('accumulated',field)
        if renderer.refinement is not None: renderer.refinement.converged=True
        for mode in ('none','ray','moments'):
            renderer.settings.probe_visibility=mode
            image=self.image(renderer)
            if mode=='none':self.assertGreater(float(image.mean()),.05)
            elif mode=='ray':np.testing.assert_array_equal(image,0)
            else:self.assertLess(float(image.max()),.001)

    def radiance_renderer(self, data=None, bounces=1, steps=3):
        return LPVRenderer(self.device,Scene(self.device,data or plane()),Camera((0,2,0),(0,0,0),30),
            Lighting(0,90,0),VolumeSettings(8,steps,1024,bounces=bounces,probe_rays=256,probes_per_frame=0,
                temporal_refinement=False,probe_visibility="moments"),width=8,height=8)

    def test_radiance_predictor_preserves_homogeneous_SH9(self):
        renderer=self.radiance_renderer();renderer.ensure_volume();self.device.wait()
        value=np.array([2,.2,-.3,.1,.1,.04,-.15,-.09,.08],np.float32)
        field=np.broadcast_to(value,(8,8,8,3,9)).copy()
        renderer.upload_volume('captured',field);renderer.upload_volume('next',field)
        encoder=self.device.create_command_encoder()
        def bind(c):
            c.g_stage=0;c.g_weight=.75;c.g_occlusion_strength=1;c.g_use_occlusion=False
            c.g_geometry=renderer.refinement.geometry;c.g_captured=renderer.fields['captured']
            c.g_input,c.g_output=renderer.fields['next'],renderer.fields['target']
        renderer._dispatch(encoder,'radiance_predict_26',[8,8,8],bind)
        self.device.submit_command_buffer(encoder.finish());self.device.wait()
        result=renderer.fields['target'].to_numpy().view(np.float32)
        expected=renderer.fields['captured'].to_numpy().view(np.float32)
        np.testing.assert_allclose(result,expected,rtol=3e-6,atol=3e-6)

    def test_radiance_surface_reflection_matches_closed_emissive_room(self):
        for rho in (.4,0):
            builder=SceneBuilder();builder.box((4,4,4),(0,0,0),(rho,rho,rho))
            data=builder.finish();data.emissions[:,:3]=1
            data.triangles=data.triangles[:,[0,2,1]].copy();data.normals*=-1
            renderer=self.radiance_renderer(data,bounces=3,steps=0)
            renderer.settings.probe_visibility="ray" # Isolate recurrence from moments approximation at room corners.
            renderer.camera=Camera((0,0,0),(0,0,-1),30)
            image=self.image(renderer)
            # Independent path reference is constant at every ray direction.
            expected=1+rho+rho*rho+rho**3
            np.testing.assert_allclose(image,expected,rtol=.003,atol=.001)
            stages=renderer.fields['captured'].to_numpy().view(np.float32).reshape(3,8,8,8,28)
            for order in range(3):
                np.testing.assert_allclose(stages[order,3:5,3:5,3:5,0],math.sqrt(4*math.pi)*rho**order,
                    rtol=.003,atol=.001)
            if rho==0:np.testing.assert_array_equal(stages[1:],0)

    def test_radiance_history_and_target_cache(self):
        renderer=self.radiance_renderer();renderer.lighting.sun_intensity=1
        renderer.settings.temporal_refinement=True
        renderer.ensure_volume();self.device.wait()
        target=renderer.refinement.target.to_numpy().view(np.float32).copy()
        first=renderer.fields['front'].to_numpy().view(np.float32).copy()
        np.testing.assert_allclose(first,target*.1,rtol=2e-6,atol=1e-7)
        renderer.ensure_volume();self.device.wait()
        second=renderer.fields['front'].to_numpy().view(np.float32).copy()
        np.testing.assert_allclose(second,target*.19,rtol=2e-6,atol=1e-7)
        self.assertEqual(renderer.refinement.target_builds,1)
        renderer.camera.rotate(20,0);renderer.frame();self.device.wait()
        self.assertEqual(renderer.refinement.target_builds,1)
        old=renderer.fields['front'].to_numpy().view(np.float32).copy()
        renderer.lighting.sun_intensity=0;renderer.ensure_volume();self.device.wait()
        np.testing.assert_allclose(renderer.fields['front'].to_numpy().view(np.float32),old*.9,rtol=2e-6,atol=1e-7)
        self.assertEqual(renderer.refinement.target_builds,2)
        self.assertEqual(renderer.refinement.geometry_builds,1)

    def test_budgeted_capture_matches_full_and_never_converges_early(self):
        for steps in (0,2,3):
            full=self.radiance_renderer(demo_scene(),bounces=3,steps=steps)
            full.lighting=Lighting();full.ensure_volume();self.device.wait()
            expected=full.refinement.target.to_numpy().copy()
            partial=self.radiance_renderer(demo_scene(),bounces=3,steps=steps)
            partial.lighting=Lighting();partial.settings.probes_per_frame=133
            frames=0
            while True:
                partial.ensure_volume();self.device.wait();frames+=1
                self.assertLessEqual(partial.refinement.last_capture_probes,133)
                if partial.refinement.converged:break
                self.assertLess(frames,20)
            self.assertEqual(frames,math.ceil(512*3/133))
            self.assertEqual(partial.refinement.target_builds,1)
            np.testing.assert_array_equal(partial.refinement.target.to_numpy(),expected)

    def test_budgeted_continuous_light_changes_finish_then_catch_up(self):
        r=self.radiance_renderer(demo_scene(),bounces=3)
        r.settings.probes_per_frame=128;r.lighting=Lighting()
        for frame in range(12):
            r.lighting.sun_intensity=3+frame*.1
            r.ensure_volume();self.device.wait()
        self.assertEqual(r.refinement.target_builds,1)
        frozen=self.radiance_renderer(demo_scene(),bounces=3)
        frozen.lighting=Lighting();frozen.ensure_volume();self.device.wait()
        np.testing.assert_array_equal(r.refinement.target.to_numpy(),frozen.refinement.target.to_numpy())
        r.lighting.sun_intensity=0
        r.settle_volume()
        self.assertEqual(r.refinement.target_builds,2)
        np.testing.assert_array_equal(r.refinement.target.to_numpy().view(np.float32),0)
        self.assertEqual(r.refinement.geometry_builds,1)

    def test_budgeted_structural_change_restarts_in_flight_capture(self):
        r=self.radiance_renderer(demo_scene(),bounces=3)
        r.lighting=Lighting();r.settings.probes_per_frame=133
        r.ensure_volume();self.device.wait()
        r.settings.bounces=2;r.settings.steps=2
        r.settle_volume()
        full=self.radiance_renderer(demo_scene(),bounces=2,steps=2)
        full.lighting=Lighting();full.ensure_volume();self.device.wait()
        np.testing.assert_array_equal(r.refinement.target.to_numpy(),full.refinement.target.to_numpy())
        self.assertEqual(r.refinement.target_builds,1)

    def test_directional_projected_SH_consumption_diagnostic(self):
        renderer=self.directional_renderer(1);self.image(renderer)
        builds=renderer.volume_builds
        field=np.zeros((16,16,16,3,9),np.float32);field[...,0]=math.sqrt(4*math.pi)
        renderer.upload_volume('accumulated',field);renderer.consume_projected_sh=True
        image=self.image(renderer)
        expected=np.broadcast_to(np.array([.7,.2,.1])*(1+1/math.pi),image.shape)
        np.testing.assert_allclose(image,expected,rtol=2e-6)
        self.assertEqual(renderer.volume_builds,builds)

    def refined_renderer(self, steps=0, bounces=1, temporal=True, occlusion=False):
        renderer=LPVRenderer(self.device,Scene(self.device,plane()),Camera(),Lighting(0,90,0),
            VolumeSettings(8,steps,1024,bounces=bounces,occlusion=occlusion,
                transport="sh_ue4",temporal_refinement=temporal,probe_visibility="none"),width=8,height=8)
        renderer.ensure_volume();self.device.wait()
        return renderer

    def refine_once(self, renderer):
        ref=renderer.refinement
        ref.converged=False
        encoder=self.device.create_command_encoder()
        ref.update(encoder,renderer._source_signature)
        self.device.submit_command_buffer(encoder.finish());self.device.wait()
        return renderer.fields['accumulated'].to_numpy().view(np.float32).reshape(8,8,8,28)

    def test_refinement_26_neighbors_and_center_retention(self):
        renderer=self.refined_renderer(1,temporal=False)
        field=np.zeros((8,8,8,3,9),np.float32)
        field[4,4,4,:,0]=math.sqrt(4*math.pi)
        renderer.upload_volume('source',field)
        out=self.refine_once(renderer)
        self.assertEqual(np.count_nonzero(out[...,0]),27)
        self.assertAlmostEqual(float(out[4,4,4,0]),math.sqrt(4*math.pi),places=5)
        for z in (-1,0,1):
            for y in (-1,0,1):
                for x in (-1,0,1):
                    d2=x*x+y*y+z*z
                    if not d2:continue
                    expected=math.pi*.08/math.sqrt(4*math.pi)/d2
                    self.assertAlmostEqual(float(out[4+z,4+y,4+x,0]),expected,places=6)
                    # L1 must point away from the source, in our travel basis.
                    direction=np.array([x,y,z])/math.sqrt(d2)
                    np.testing.assert_allclose(out[4+z,4+y,4+x,1:4]/expected,
                        math.sqrt(3)*np.array([-direction[1],direction[2],-direction[0]]),atol=2e-6)

    def test_geometry_volume_true_area_and_albedo(self):
        renderer=self.refined_renderer()
        cells=renderer.refinement.geometry.to_numpy().view(np.float32).reshape(-1,8)
        h=renderer.grid.cell_size
        self.assertAlmostEqual(float(cells[:,0].sum())*h*h*math.sqrt(4*math.pi),4,places=5)
        np.testing.assert_allclose(cells[:,1].sum()/cells[:,0].sum(),-math.sqrt(3),rtol=2e-6)
        self.assertAlmostEqual(float(cells[:,7].sum()),4,places=5)
        np.testing.assert_allclose(cells[cells[:,7]>0,4:7],.5,rtol=2e-6)
        renderer.lighting.sun_intensity=1;renderer.ensure_volume();self.device.wait()
        self.assertEqual(renderer.refinement.geometry_builds,1)
        np.testing.assert_array_equal(cells,renderer.refinement.geometry.to_numpy().view(np.float32).reshape(-1,8))

    def test_geometry_face_edge_corner_tap_averages(self):
        renderer=self.refined_renderer(1,temporal=False,occlusion=True)
        geometry=np.zeros((9,9,9,8),np.float32)
        z,y,x=np.indices((9,9,9))
        geometry[...,0]=(.01*x+.02*y+.03*z)/(math.pi/math.sqrt(4*math.pi))
        renderer.refinement.geometry.copy_from_numpy(geometry)
        field=np.zeros((8,8,8,3,9),np.float32);field[4,4,4,:,0]=math.sqrt(4*math.pi)
        renderer.upload_volume('source',field)
        out=self.refine_once(renderer)
        # Midpoints of face/edge/corner crossings have respectively 4/2/1
        # surrounding corner values. Their linear scalar field average is exact.
        for offset,taps in [((1,0,0),4),((1,1,0),2),((1,1,1),1)]:
            x,y,z=offset
            midpoint=np.array([4.5,4.5,4.5])+.5*np.array(offset)
            opacity=float(midpoint@np.array([.01,.02,.03]))
            expected=math.pi*.08*(1-opacity)/(x*x+y*y+z*z)/math.sqrt(4*math.pi)
            self.assertAlmostEqual(float(out[4+z,4+y,4+x,0]),expected,places=6)

    def test_refinement_settles_and_light_changes_preserve_history(self):
        renderer=self.refined_renderer()
        renderer.lighting.sun_intensity=1
        renderer.settle_volume()
        self.assertTrue(renderer.refinement.converged)
        renderer.frame();renderer.frame();self.device.wait()
        self.assertEqual(renderer.sample_count,2)
        revisions=renderer.refinement.revision
        renderer.camera.rotate(10,0);renderer.frame();self.device.wait()
        self.assertEqual(renderer.refinement.revision,revisions)
        source=renderer.fields['source'].to_numpy().view(np.float32).copy()
        total=renderer.fields['accumulated'].to_numpy().view(np.float32).copy()
        np.testing.assert_allclose(total,source,rtol=2e-5,atol=1e-7)
        # New source is zero, but history fades by .9 rather than vanishing.
        renderer.lighting.sun_intensity=0;renderer.frame();self.device.wait()
        faded=renderer.fields['accumulated'].to_numpy().view(np.float32)
        np.testing.assert_allclose(faded,total*.9,rtol=2e-6,atol=1e-7)
        self.assertEqual(renderer.refinement.geometry_builds,1)
        self.assertEqual(renderer.sample_count,1)

    def test_refinement_history_recurrence_and_reset(self):
        renderer=self.refined_renderer()
        field=np.zeros((8,8,8,3,9),np.float32);field[4,4,4,:,0]=1
        renderer.upload_volume('source',field)
        first=self.refine_once(renderer)
        second=self.refine_once(renderer)
        self.assertAlmostEqual(float(first[4,4,4,0]),.1,places=6)
        self.assertAlmostEqual(float(second[4,4,4,0]),.19,places=6)
        renderer.upload_volume('source',np.zeros_like(field))
        faded=self.refine_once(renderer)
        self.assertAlmostEqual(float(faded[4,4,4,0]),.171,places=6)
        renderer.reset();renderer.ensure_volume();self.device.wait()
        np.testing.assert_array_equal(renderer.fields['accumulated'].to_numpy(),0)

    def test_geometry_reflection_opacity_cube_and_material_orders(self):
        renderer=self.refined_renderer(1,bounces=2,temporal=False,occlusion=True)
        geometry=np.zeros((9**3,8),np.float32)
        geometry[:,0]=.5/(math.pi/math.sqrt(4*math.pi))
        geometry[:,4:7]=[.8,.2,0]
        renderer.refinement.geometry.copy_from_numpy(geometry)
        field=np.zeros((8,8,8,3,9),np.float32);field[4,4,4,:,0]=math.sqrt(4*math.pi)
        renderer.upload_volume('source',field)
        self.refine_once(renderer)
        orders=renderer.fields['front'].to_numpy().view(np.float32).reshape(2,8,8,8,28)
        expected=math.pi*.08*(44/3)*(.5**3)/math.sqrt(4*math.pi)*np.array([.8,.2,0])
        np.testing.assert_allclose(orders[1,4,4,4,[0,4,8]],expected,rtol=2e-6,atol=1e-7)
        self.assertEqual(np.count_nonzero(orders[1,...,0]),1)
        # Transmission of the first order is independently attenuated by .5.
        expected_transmit=math.pi*.08*.5/math.sqrt(4*math.pi)
        self.assertAlmostEqual(float(orders[0,4,4,5,0]),expected_transmit,places=6)

    def test_compact_default_and_visibility_cache(self):
        self.assertEqual(parse_args([]).transport,'sh')
        renderer=LPVRenderer(self.device,Scene(self.device,demo_scene()),Camera(),
            settings=VolumeSettings(probe_rays=64,source_samples=1024,probes_per_frame=0),width=24,height=24,spp=4)
        self.image(renderer)
        self.assertIsNone(renderer.directional)
        self.assertLess(renderer.volume_memory()['total_bytes'],230_000_000)
        self.assertIsNone(renderer.links)
        self.assertIsNone(renderer.reflection)
        self.assertEqual(renderer.refinement.geometry.size,33**3*32)
        counters=(renderer.visibility.builds,renderer.link_builds,renderer.source_builds)
        renderer.camera.rotate(10,0);self.image(renderer)
        renderer.settings.probe_visibility='ray';self.image(renderer)
        renderer.settings.probe_visibility='moments';self.image(renderer)
        self.assertEqual(counters[:2],(renderer.visibility.builds,renderer.link_builds))
        self.assertEqual(renderer.source_builds,counters[2]+2)
        renderer.lighting.sun_intensity=0;self.image(renderer)
        renderer.settle_volume()
        self.assertLess(float(np.abs(renderer.volume_data()).max()),1e-7)
        self.assertEqual(renderer.visibility.builds,1)

    def test_directional_total_is_optional_and_transport_releases_resources(self):
        renderer=self.directional_renderer()
        before=self.image(renderer).copy()
        self.assertFalse(renderer.directional.has_angular_total)
        self.assertEqual(renderer.directional.total.size,16)
        renderer.angular_data()
        self.assertTrue(renderer.directional.has_angular_total)
        np.testing.assert_array_equal(self.image(renderer),before)
        renderer.settings.transport='sh_legacy';self.image(renderer)
        self.assertIsNone(renderer.directional)
        renderer.settings.transport='directional';self.image(renderer)
        self.assertIsNone(renderer.visibility)
        self.assertIsNone(renderer.reflection)
        self.assertEqual(set(renderer.fields),{'source','accumulated'})

    def test_directional_air_preserves_angular_distribution(self):
        renderer=self.directional_renderer(1);self.image(renderer)
        dv=renderer.directional;n=16;count=dv.direction_count
        dirs=dv.directions.to_numpy().view(np.float32).reshape(count,4)
        offsets=dv.offsets.to_numpy().view(np.int32).reshape(count,4)[:,:3]
        z,y,x=np.indices((n,n,n));coords=np.column_stack([x.ravel(),y.ravel(),z.ravel()])
        neighbors=coords[:,None,:]-offsets[None,:,:]
        inside=np.all((neighbors>=0)&(neighbors<n),axis=-1)
        hits=np.zeros((n**3,count,4),np.float32);hits[...,3]=np.where(inside,0,-1)
        dv.hits.copy_from_numpy(hits)
        values=1+.2*dirs[:,0]+.3*dirs[:,1]**2
        angular=np.zeros((n**3,count,4),np.float32);angular[...,:3]=values[None,:,None]
        dv.front.copy_from_numpy(angular)
        encoder=self.device.create_command_encoder()
        dv._dispatch(encoder,"step_main",dv.slots,renderer.grid,{"g_offsets":dv.offsets,"g_hits":dv.hits,
            "g_boundary":dv.boundary,"g_front":dv.front,"g_next":dv.next,"g_decay":1.0})
        self.device.submit_command_buffer(encoder.finish());self.device.wait()
        result=dv.next.to_numpy().view(np.float32).reshape(n,n,n,count,4)
        np.testing.assert_array_equal(result[8,8,8,:,:3],angular[0,:,:3])

    def test_directional_reflection_series_not_sweep_history(self):
        renderer=self.directional_renderer();self.image(renderer)
        dv=renderer.directional;count=dv.direction_count
        dirs=dv.directions.to_numpy().view(np.float32).reshape(count,4)
        hits=np.zeros((16**3,count,4),np.float32);hits[...,:3]=dirs[None,:,:3]
        hits[...,3]=renderer.grid.cell_size*.1
        dv.hits.copy_from_numpy(hits);dv.material.copy_from_numpy(np.zeros(dv.slots,np.uint32))
        source=np.ones((dv.slots,4),np.float32);source[:,3]=0;dv.direct.copy_from_numpy(source)
        rho=np.array([.7,.2,.1]);expected=1+rho+rho*rho
        for steps in (1,20):
            renderer.settings.steps=steps;renderer._volume_signature=None
            self.image(renderer)
            np.testing.assert_allclose(renderer.angular_data()[8,12,8],np.broadcast_to(expected,(count,3)),rtol=2e-6)

    def test_directional_cache_and_transport_switch(self):
        renderer=self.directional_renderer();self.image(renderer);initial=renderer.angular_data()
        counters=(renderer.link_builds,renderer.source_builds)
        renderer.camera=Camera((.2,2,0),(0,0,0),30);self.image(renderer)
        renderer.settings.bounces=2;self.image(renderer)
        self.assertEqual((renderer.link_builds,renderer.source_builds),counters)
        renderer.settings.transport="sh_legacy";self.image(renderer)
        renderer.settings.transport="directional";renderer.settings.bounces=3;self.image(renderer)
        np.testing.assert_array_equal(renderer.angular_data(),initial)
        geometry=renderer.link_builds
        renderer.lighting.sun_intensity=0;self.image(renderer)
        np.testing.assert_array_equal(renderer.angular_data(),0)
        self.assertEqual(renderer.link_builds,geometry)

    def synthetic_reflector(self, albedo, blocked_fraction=1.0, bounces=3):
        """Closed unit-cell material or partially covered faces; exact flux budget."""
        renderer=self.renderer(steps=0)
        renderer.settings.bounces=bounces
        self.image(renderer)
        n=16
        field=np.zeros((n,n,n,3,9),np.float32)
        field[8,8,8,:,0]=math.sqrt(4*math.pi)
        renderer.fields["front"].copy_from_numpy(np.zeros((bounces,n**3,28),np.float32))
        renderer.upload_volume("front",field)
        renderer.upload_volume("accumulated",field)
        visible=int(round(5*(1-blocked_fraction)))
        mask=sum(visible << (3*i) for i in range(6))
        renderer.links.copy_from_numpy(np.full(n**3,mask,np.uint32))
        geometry=np.zeros((n**3,6,28),np.float32)
        index=8+n*(8+n*8)
        for i,normal in enumerate(((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1))):
            x,y,z=normal
            basis=np.array([1/math.sqrt(4*math.pi),-math.sqrt(3/(4*math.pi))*y,
                math.sqrt(3/(4*math.pi))*z,-math.sqrt(3/(4*math.pi))*x,
                math.sqrt(15/(4*math.pi))*x*y,-math.sqrt(15/(4*math.pi))*y*z,
                math.sqrt(5/(16*math.pi))*(3*z*z-1),-math.sqrt(15/(4*math.pi))*x*z,
                math.sqrt(15/(16*math.pi))*(x*x-y*y)])
            lobe=basis*np.array([math.pi,*[2*math.pi/3]*3,*[math.pi/4]*5])
            color=np.asarray(albedo)*blocked_fraction/math.pi
            coeff=color[:,None]*lobe
            geometry[index,i,:12]=coeff[:,:4].reshape(12)
            geometry[index,i,12:24]=coeff[:,4:8].reshape(12)
            geometry[index,i,24:27]=coeff[:,8]
        renderer.reflection.copy_from_numpy(geometry)
        return renderer

    def propagate_once(self, renderer):
        encoder=self.device.create_command_encoder()
        def bind(cursor):
            cursor.g_front=renderer.fields["front"]
            cursor.g_next=renderer.fields["next"]
            cursor.g_accumulated=renderer.fields["accumulated"]
            cursor.g_links=renderer.links
            cursor.g_reflection=renderer.reflection
            cursor.g_bounces=renderer.settings.bounces
            cursor.g_use_occlusion=True
            cursor.g_decay=1.0
        renderer._dispatch(encoder,"propagate",[16,16,16],bind)
        self.device.submit_command_buffer(encoder.finish())
        self.device.wait()
        renderer.fields["front"],renderer.fields["next"]=renderer.fields["next"],renderer.fields["front"]

    def test_material_bounce_series_and_no_history_feedback(self):
        rho=np.array([.7,.2,.1])
        renderer=self.synthetic_reflector(rho,bounces=3)
        for step in range(1,6):
            self.propagate_once(renderer)
            maximum=min(step,2)
            expected=math.sqrt(4*math.pi)*sum(rho**k for k in range(maximum+1))
            observed=renderer.volume_data()[...,0].sum(axis=(0,1,2))
            np.testing.assert_allclose(observed,expected,rtol=3e-6)
            if step<=2:
                np.testing.assert_allclose(renderer.volume_data("front",bounce=step)[...,0].sum(axis=(0,1,2)),
                    math.sqrt(4*math.pi)*rho**step,rtol=3e-6)
            else:
                for bounce in range(3):np.testing.assert_array_equal(renderer.volume_data("front",bounce=bounce),0)
        self.assertTrue(np.all(observed<math.sqrt(4*math.pi)/(1-rho)))

    def test_anisotropic_SH_front_does_not_amplify_energy(self):
        renderer=self.renderer(steps=0)
        self.image(renderer)
        # Nonnegative physical budget, with strong SH ringing in angular queries.
        field=np.zeros((16,16,16,3,9),np.float32)
        field[8,8,8,:,0]=1
        field[8,8,8,:,1]=3
        renderer.upload_volume('front',field)
        renderer.upload_volume('accumulated',np.zeros_like(field))
        # Empty links transmit all flux; the geometry responses are zero.
        renderer.links.copy_from_numpy(np.full(16**3,sum(5<<(3*i) for i in range(6)),np.uint32))
        renderer.reflection.copy_from_numpy(np.zeros((16**3*6,28),np.float32))
        for _ in range(8):
            self.propagate_once(renderer)
            integral=renderer.volume_data('front')[...,0].sum(axis=(0,1,2))
            self.assertTrue(np.all(integral<=1+2e-6))

    def test_partial_reflector_flux_budget_and_black_absorption(self):
        for rho in [(0,0,0),(.7,.2,.1)]:
            renderer=self.synthetic_reflector(rho,blocked_fraction=.4,bounces=2)
            self.propagate_once(renderer)
            transmitted=renderer.volume_data("front",bounce=0)[...,0].sum(axis=(0,1,2))
            reflected=renderer.volume_data("front",bounce=1)[...,0].sum(axis=(0,1,2))
            unit=math.sqrt(4*math.pi)
            np.testing.assert_allclose(transmitted,np.full(3,.6*unit),rtol=2e-6)
            np.testing.assert_allclose(reflected,np.array(rho)*.4*unit,rtol=3e-6,atol=1e-7)
            self.assertTrue(np.all(transmitted+reflected<=unit+1e-6))

    def test_RT_reflector_cache_has_material_and_correct_hemisphere(self):
        rho=np.array([.7,.2,.1])
        renderer=self.renderer(plane(rho))
        self.image(renderer)
        links=renderer.links.to_numpy().view(np.uint32).reshape(16,16,16)
        geometry=renderer.reflection.to_numpy().view(np.float32).reshape(16,16,16,6,28)
        for i in range(6):
            counts=(links[1:15,1:15,1:15]>>(3*i))&7
            reflected_integral=geometry[1:15,1:15,1:15,i][...,[0,4,8]]*math.sqrt(4*math.pi)
            np.testing.assert_allclose(reflected_integral,(1-counts[...,None]/5)*rho,rtol=3e-6,atol=2e-7)
        # Above the plane, +Y-emitting reflection has negative Y1-1 coefficient.
        upper=int(math.floor((0-renderer.grid.origin[1])/renderer.grid.cell_size+.5))
        response=geometry[8,upper,8,2]
        self.assertGreater(float(response[0]),0)
        self.assertLess(float(response[1]),0)

    def test_secondary_scene_and_bounce_cache(self):
        renderer=self.renderer(demo_scene(),samples=32768,steps=24)
        renderer.camera=Camera()
        renderer.lighting=Lighting()
        once=self.image(renderer,4)
        source=renderer.volume_data("source")
        counts=(renderer.surface_builds,renderer.bin_builds,renderer.link_builds,renderer.source_builds)
        renderer.settings.bounces=3
        multiple=self.image(renderer,4)
        self.assertGreater(float((multiple-once).mean()),.001)
        np.testing.assert_array_equal(renderer.volume_data("source"),source)
        self.assertEqual(counts,(renderer.surface_builds,renderer.bin_builds,renderer.link_builds,renderer.source_builds))
        renderer.lighting.sun_intensity=0
        self.image(renderer)
        np.testing.assert_array_equal(renderer.volume_data(),0)
        renderer.settings.bounces=1
        renderer.lighting=Lighting()
        np.testing.assert_array_equal(self.image(renderer,4),once)

    def test_indirect_light_reset_and_export(self):
        renderer = self.renderer(demo_scene(), samples=32768, steps=12)
        renderer.camera = Camera()
        renderer.lighting = Lighting()
        renderer.mode = "direct"
        direct = self.image(renderer, 4)
        renderer.mode = "lpv"
        combined = self.image(renderer, 4)
        self.assertGreater(float((combined-direct).mean()), .005)
        self.assertGreaterEqual(float((combined-direct).min()), -1e-6)
        self.assertGreater(float((combined-direct)[direct.mean(axis=2)<.001].mean()), .0001)
        builds = renderer.volume_builds
        renderer.camera.rotate(10, 0)
        self.image(renderer)
        self.assertEqual(renderer.volume_builds, builds)
        self.assertEqual(renderer.sample_count, 4)
        renderer.lighting.sun_intensity = 0
        self.image(renderer)
        np.testing.assert_array_equal(renderer.volume_data(), 0)
        renderer.lighting.sun_intensity = 3
        renderer.mode = "lpv"
        renderer.reset()
        expected = self.image(renderer)
        renderer.reset()
        np.testing.assert_array_equal(self.image(renderer), expected)
        output = Path(__file__).resolve().parents[1] / "sample/output/test"
        renderer.save(output/"lpv.png", output/"lpv.npy")
        np.testing.assert_array_equal(np.load(output/"lpv.npy"), expected)
        self.assertTrue((output/"lpv.png").is_file())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="metal" if sys.platform == "darwin" else "d3d12" if sys.platform == "win32" else "vulkan")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--cpu-only", action="store_true")
    options = parser.parse_args()
    BACKEND, DEBUG = options.backend, options.debug
    classes = (CPUChecks,) if options.cpu_only else (CPUChecks, GPUChecks)
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in classes)
    sys.exit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
