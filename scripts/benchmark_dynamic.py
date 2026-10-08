"""Synchronized dynamic SH capture latency and field-response measurements."""
import argparse
import json
from pathlib import Path
import sys
import time
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sample'))
from renderer import LPVRenderer,create_device
from scene import Scene,demo_scene
from camera import Camera
from volume import VolumeSettings


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=ROOT/'sample/output/sh-dynamic-benchmark.json')
    p.add_argument('--backend',default='metal')
    a=p.parse_args()
    device=create_device(a.backend);scene=Scene(device,demo_scene())
    r=LPVRenderer(device,scene,Camera(),settings=VolumeSettings(probes_per_frame=0),width=640,height=480,spp=1)
    r.settle_volume()
    for _ in range(3):r.frame();device.wait() # Warm render pipelines before timing any budget.
    exact=r.refinement.target.to_numpy().view(np.float32).copy()
    def summary(values):
        return dict(median_ms=float(np.median(values)),p95_ms=float(np.percentile(values,95)),max_ms=max(values))
    report=dict(backend=a.backend,grid=32,probe_rays=1024,material_orders=3,width=640,height=480,spp=1,
        note='CPU wall clock + device.wait; includes render/accumulation/tonemap, excludes UI/presentation and initialization. '
        'Sun intensity jumps 2.4 to 3.0; steady target compared to full capture. Field response is SH coefficient L2 relative to initial-to-final field change.',cases={})
    for budget in (0,1024,2048,4096):
        r.settings.probes_per_frame=0;r.lighting.sun_intensity=2.4;r.settle_volume()
        initial=r.fields['front'].to_numpy().view(np.float32).copy()
        scale=np.linalg.norm(initial-exact)
        r.settings.probes_per_frame=budget;r.lighting.sun_intensity=3
        samples=[];capture=[];response=[];complete=None;frames90=None
        builds=r.refinement.target_builds
        for frame in range(1,350):
            start=time.perf_counter();r.frame();device.wait();ms=(time.perf_counter()-start)*1000
            samples.append(ms)
            if r.refinement.last_capture_probes:capture.append(ms)
            if complete is None and r.refinement.target_builds>builds:complete=frame
            if frame%4==0 or frame==1:
                front=r.fields['front'].to_numpy().view(np.float32)
                error=float(np.linalg.norm(front-exact)/scale)
                response.append(dict(frame=frame,relative_remaining_change=error))
                if frames90 is None and error<=.1:frames90=frame
            if r.refinement.converged:break
        else:raise RuntimeError('Dynamic update failed to converge')
        target=r.refinement.target.to_numpy().view(np.float32)
        equal=np.array_equal(target,exact)
        assert equal,'Budget changed the steady radiance target'
        result=dict(capture_frames=complete,frames_to_90_percent_response_sampled_every_4=frames90,
            settled_frames=frame,capture=summary(capture),all_update_frames=summary(samples),
            target_bit_identical=equal,samples_ms=samples,response=response)
        report['cases'][str(budget)]=result
        print(budget,'capture',complete,'90%',frames90,result['capture'],flush=True)
    # Continuous slider motion must not starve completed captures.
    r.settings.probes_per_frame=4096;r.settle_volume()
    builds=r.refinement.target_builds;samples=[]
    for frame in range(96):
        r.lighting.sun_intensity=2.5+.5*np.sin(frame*.07)
        start=time.perf_counter();r.frame();device.wait();samples.append((time.perf_counter()-start)*1000)
    report['continuous_light_motion']=dict(frames=96,completed_cycles=r.refinement.target_builds-builds,**summary(samples))
    assert report['continuous_light_motion']['completed_cycles']==4
    r.lighting.sun_intensity=3;r.settle_volume()
    assert np.array_equal(r.refinement.target.to_numpy().view(np.float32),exact)
    report['continuous_light_motion']['final_target_bit_identical']=True
    # Repeat the single-frame baseline under the same warmed conditions.
    r.settings.probes_per_frame=0
    full=[]
    for i in range(5):
        r.lighting.sun_intensity=2.4 if i%2==0 else 3
        start=time.perf_counter();r.frame();device.wait();full.append((time.perf_counter()-start)*1000)
    report['full_capture_repeated']=dict(samples_ms=full,**summary(full))
    report['volume_memory']=r.volume_memory()
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,indent=2)+'\n')
    print('Saved',a.output,flush=True)


if __name__=='__main__':main()
