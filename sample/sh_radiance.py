"""RT-guided SH radiance volume with GV/26-neighbor prediction and history.

This deliberately differs from the old additive UE4 LPV stencil. The compact
field and material recursion are independent of the directional diagnostic.
"""
from copy import deepcopy
import numpy as np
import slangpy as spy
from sh_refinement import SHRefinement


def radiance_rays(count):
    # Antipodal equal-area Fibonacci pairs: odd bands cancel exactly for a
    # constant sphere, hemisphere/flat-plane symmetry is substantially better.
    ids=np.arange(count//2,dtype=np.float64)
    z=1-2*(ids+.5)/(count//2)
    phi=ids*np.pi*(3-np.sqrt(5))
    xyz=np.column_stack((np.sqrt(1-z*z)*np.cos(phi),np.sqrt(1-z*z)*np.sin(phi),z))
    result=np.empty((count,4),np.float32)
    result[::2,:3]=xyz;result[1::2,:3]=-xyz
    result[:,3]=4*np.pi/count
    return result


class SHRadiance(SHRefinement):
    def __init__(self,renderer):
        super().__init__(renderer)
        for name in ('radiance_capture','radiance_predict_26','radiance_resolve'):
            program=self.device.load_program(name+'.slang',['compute_main'])
            renderer.programs[name]=program
            renderer.pipelines[name]=self.device.create_compute_pipeline(program)
        self.target_signature=None
        self.rays=None
        self.ray_count=0
        self.target_builds=0
        self.target=None

    def update(self,encoder,source_signature):
        r=self.renderer;s=r.settings
        parameters=(s.steps,s.decay,s.occlusion,s.bounces,s.probe_rays,s.propagation_weight,
                    s.secondary_occlusion,s.secondary_bounce,s.probe_visibility,s.visibility_bias)
        requested=(source_signature,parameters,self.geometry_signature)
        if s.probe_visibility=='moments':
            if r.visibility is None:
                from probe_visibility import ProbeVisibility
                r.visibility=ProbeVisibility(self.device)
            r.visibility.update(encoder,r.scene,r.grid)
        n=s.resolution;cells=n**3;count=cells*s.bounces
        resources=(n,parameters,self.geometry_signature)
        if getattr(self,'resources',None)!=resources or self.clear_history:
            # Structural changes invalidate in-flight work. Lighting-only changes
            # keep both GPU resources and the displayed history alive.
            self.device.wait()
            layout=spy.ReflectionCursor(r.programs['inject'])
            for name in ('captured','target'):
                r.fields[name]=self.device.create_buffer(resource_type_layout=layout.g_source.type_layout,
                    element_count=count,data=np.zeros((count,28),np.float32),
                    usage=spy.BufferUsage.shader_resource|spy.BufferUsage.unordered_access,label='LPV.'+name)
            if self.ray_count!=s.probe_rays:
                self.rays=self.device.create_buffer(data=radiance_rays(s.probe_rays),usage=spy.BufferUsage.shader_resource)
                self.ray_count=s.probe_rays
            self.clear_history=True
            self.resources=resources
            self.job=None
            self.target_signature=None
            self.target=r.fields['target'] if s.steps else r.fields['captured']
            self.changed()
        if getattr(self,'job',None) is None and requested!=self.target_signature:
            # Freeze the sun for a whole capture cycle. Do not restart each time
            # a slider moves: finish this cycle, then pick up the latest request.
            self.job={'signature':requested,'sun':deepcopy(r.lighting).shader_values(),'stage':0,'offset':0}
            self.changed()
        self.last_capture_probes=0
        budget=s.probes_per_frame or cells*s.bounces
        while self.job is not None and budget>0:
            job=self.job;stage=job['stage'];offset=job['offset']
            batch=min(budget,cells-offset)
            def capture(c):
                r.scene.bind(c.g_scene)
                c.g_sun=job['sun']
                c.g_stage=stage;c.g_decay=s.decay;c.g_bounce_strength=s.secondary_bounce
                c.g_ray_count=s.probe_rays;c.g_rays=self.rays
                c.g_probe_offset=offset;c.g_probe_count=batch
                c.g_previous=self.target
                c.g_captured=r.fields['captured'];c.g_source=r.fields['source']
                c.g_probe_visibility=('moments','ray','none').index(s.probe_visibility)
                c.g_visibility_bias=s.visibility_bias
                if r.visibility is not None and s.probe_visibility=='moments':
                    for name,value in r.visibility.shader_values().items():setattr(c,name,value)
            r._dispatch(encoder,'radiance_capture',[batch,1,1],capture)
            budget-=batch;job['offset']+=batch;self.last_capture_probes+=batch
            if job['offset']!=cells:break
            current=r.fields['captured']
            for iteration in range(s.steps):
                # Always finish in target, including an even predictor count.
                output=r.fields['target'] if (s.steps-iteration)%2 else r.fields['next']
                def predict(c):
                    c.g_stage=stage;c.g_weight=s.propagation_weight
                    c.g_occlusion_strength=s.secondary_occlusion;c.g_use_occlusion=s.occlusion
                    c.g_geometry=self.geometry;c.g_captured=r.fields['captured']
                    c.g_input,c.g_output=current,output
                r._dispatch(encoder,'radiance_predict_26',[n,n,n],predict)
                current=output
            self.target=current
            job['stage']+=1;job['offset']=0
            # A complete material order is published. Later orders still hold
            # the previous cycle until their own capture/prediction completes.
            if job['stage']==s.bounces:
                self.target_signature=job['signature'];self.target_builds+=1
                self.job=None
                self.changed()
        target_signature=requested
        configuration=(target_signature,s.temporal_refinement,s.history_weight)
        if self.configuration!=configuration:
            if self.configuration is not None and self.configuration[1:]!=configuration[1:]:self.clear_history=True
            self.configuration=configuration
            self.changed()
        if self.job is None and self.target_signature==requested:
            self.check_convergence()
        if self.converged:return False
        def resolve(c):
            c.g_bounces=s.bounces
            c.g_history_weight=s.history_weight if s.temporal_refinement else 0
            c.g_reset=self.clear_history
            c.g_target=self.target;c.g_front=r.fields['front'];c.g_accumulated=r.fields['accumulated']
        r._dispatch(encoder,'radiance_resolve',[s.resolution]*3,resolve)
        self.clear_history=False
        self.updates+=1;self.revision+=1
        if not s.temporal_refinement:
            self.converged=self.job is None and self.target_signature==requested
        return True
