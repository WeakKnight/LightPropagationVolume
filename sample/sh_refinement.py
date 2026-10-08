"""Compact geometry SH + incremental 26-neighbor light SH with frame history.

Independent implementation informed by UE4.27 LPV. Geometry comes from true
surface area (not RSM projected area); material orders are kept separate so the
RT reference's finite diffuse depth remains comparable.
"""
from dataclasses import astuple
import numpy as np
import slangpy as spy
from volume import Grid, bin_samples


class SHRefinement:
    def __init__(self, renderer):
        self.renderer=renderer
        self.device=renderer.device
        for name in ('build_geometry','refine_prepare','propagate_26','refine_resolve'):
            program=self.device.load_program(name+'.slang',['compute_main'])
            renderer.programs[name]=program
            renderer.pipelines[name]=self.device.create_compute_pipeline(program)
        self.geometry_signature=self.configuration=None
        self.geometry=self.samples=self.ranges=None
        self.geometry_builds=0
        self.revision=0
        self.restart()

    def restart(self):
        self.clear_history=True
        self.configuration=None
        self.changed()

    def changed(self):
        self.updates=0
        self.converged=False
        self.residual=None
        self._snapshot=None
        self._checked_at=0

    def build_geometry(self, encoder):
        r=self.renderer
        signature=(r._surface_signature,r.settings.resolution)
        if signature==self.geometry_signature: return
        self.device.wait()
        n=r.settings.resolution+1
        corner_grid=Grid(r.grid.origin-.5*r.grid.cell_size,r.grid.cell_size,n)
        samples,ranges=bin_samples(r._surface_samples,corner_grid,0)
        layout=spy.ReflectionCursor(r.programs['build_geometry'])
        self.samples=self.device.create_buffer(resource_type_layout=layout.g_samples.type_layout,
            element_count=max(len(samples),1),data=samples if len(samples) else np.zeros((1,4,4),np.float32),usage=spy.BufferUsage.shader_resource)
        self.ranges=self.device.create_buffer(resource_type_layout=layout.g_ranges.type_layout,
            element_count=len(ranges),data=ranges,usage=spy.BufferUsage.shader_resource)
        self.geometry=self.device.create_buffer(resource_type_layout=layout.g_geometry.type_layout,
            element_count=n**3,data=np.zeros((n**3,8),np.float32),
            usage=spy.BufferUsage.shader_resource|spy.BufferUsage.unordered_access)
        def bind(c):
            c.g_samples,c.g_ranges,c.g_geometry=self.samples,self.ranges,self.geometry
        r._dispatch(encoder,'build_geometry',[n,n,n],bind)
        self.geometry_signature=signature
        self.geometry_builds+=1
        self.clear_history=True

    def check_convergence(self):
        # A readback only every 32 updates; ordinary refinement has no CPU/GPU
        # sync per frame. Two full intervals compare the raw material-order SH.
        if self.updates==0 or self.updates-self._checked_at<32: return
        self.device.wait()
        field=self.renderer.fields['front'].to_numpy().view(np.float32).copy()
        if not np.isfinite(field).all():
            raise RuntimeError('Nonfinite SH refinement; reduce propagation/history weights')
        if self._snapshot is not None:
            delta=float(np.max(np.abs(field-self._snapshot)))
            scale=float(np.max(np.abs(field)))
            self.residual=delta/max(scale,1e-20)
            self.converged=delta<=1e-7 or self.residual<=1e-5
        self._snapshot=field
        self._checked_at=self.updates

    def update(self,encoder,source_signature):
        r=self.renderer; s=r.settings
        parameters=(s.steps,s.decay,s.occlusion,s.bounces,s.temporal_refinement,
                    s.history_weight,s.propagation_weight,s.secondary_occlusion,s.secondary_bounce)
        configuration=(source_signature,parameters,self.geometry_signature)
        if configuration!=self.configuration:
            if self.configuration is not None and self.configuration[1]!=parameters:
                self.clear_history=True
            self.configuration=configuration
            self.changed()
        self.check_convergence()
        if self.converged: return False
        n=s.resolution; size=[n,n,n]
        history=s.history_weight if s.temporal_refinement else 0
        def prepare(c):
            c.g_bounces=s.bounces
            c.g_history_weight=history
            c.g_reset=self.clear_history or not s.temporal_refinement
            c.g_source,c.g_front=r.fields['source'],r.fields['front']
        r._dispatch(encoder,'refine_prepare',size,prepare)
        self.clear_history=False
        front,next_field=r.fields['front'],r.fields['next']
        for _ in range(s.steps):
            def propagate(c):
                c.g_bounces=s.bounces
                c.g_weight=s.propagation_weight if s.temporal_refinement else s.propagation_weight*10
                c.g_decay=s.decay
                c.g_use_occlusion=s.occlusion
                c.g_occlusion_strength=s.secondary_occlusion
                c.g_bounce_strength=s.secondary_bounce
                c.g_geometry=self.geometry
                c.g_front,c.g_next=front,next_field
            r._dispatch(encoder,'propagate_26',size,propagate)
            front,next_field=next_field,front
        r.fields['front'],r.fields['next']=front,next_field
        def resolve(c):
            c.g_bounces=s.bounces
            c.g_lighting_scale=1-history
            c.g_front,c.g_accumulated=front,r.fields['accumulated']
        r._dispatch(encoder,'refine_resolve',size,resolve)
        self.updates+=1
        self.revision+=1
        if not s.temporal_refinement: self.converged=True
        return True
