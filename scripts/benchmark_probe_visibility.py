"""Synchronized latency of warmed probe consumers; ray/moments share the source."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sample'))
from renderer import LPVRenderer,create_device
from scene import Scene,demo_scene
from volume import VolumeSettings
from camera import Camera


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend',choices=('metal','d3d12','vulkan'),default='metal' if sys.platform=='darwin' else 'd3d12' if sys.platform=='win32' else 'vulkan')
    p.add_argument('--trials',type=int,default=11)
    p.add_argument('--output',type=Path,default=ROOT/'sample/output/probe-visibility-benchmark.json')
    a=p.parse_args()
    if a.trials<3:p.error('At least 3 trials')
    dev=create_device(a.backend)
    r=LPVRenderer(dev,Scene(dev,demo_scene()),Camera(),settings=VolumeSettings(transport='directional'),width=640,height=480,spp=1)
    start=time.perf_counter();r.frame();dev.wait()
    report={'backend':a.backend,'width':640,'height':480,'spp':1,'grid':32,'directions':1154,
        'material_orders':3,'source_angular_samples':8,'trials':a.trials,
        'distance_map_size':16,'distance_rays':512,'irradiance_vertex_map_size':17,'visibility_bias':r.settings.visibility_bias,
        'initial_moments_frame_seconds':time.perf_counter()-start,
        'note':'CPU wall clock + device.wait; includes rendering/accumulation/tonemap, not isolated shader GPU timestamps. '
               'Steady render excludes volume build; reinjection timings alternate steps 40/41, reuse geometry/direct source. '
               'Each stage uses the same scene and camera; no other GPU benchmark runs concurrently.', 'cases':{}}
    for mode in ('ray','moments'):
        r.settings.probe_visibility=mode;r.settings.steps=40
        for _ in range(3):r.frame();dev.wait()
        render=[]
        for _ in range(a.trials):
            start=time.perf_counter();r.frame();dev.wait();render.append((time.perf_counter()-start)*1000)
        updates=[]
        for i in range(5):
            r.settings.steps=41 if i%2==0 else 40
            start=time.perf_counter();r.frame();dev.wait();updates.append((time.perf_counter()-start)*1000)
        report['cases'][mode]={'steady_render_ms':statistics.median(render),'secondary_update_ms':statistics.median(updates),
            'render_samples_ms':render,'secondary_update_samples_ms':updates,'depth_builds':r.directional.visibility.builds,
            'geometry_builds':r.directional.geometry_builds,'source_builds':r.directional.source_builds}
        print(mode,report['cases'][mode]['steady_render_ms'],report['cases'][mode]['secondary_update_ms'],flush=True)
    report['steady_render_speedup']=report['cases']['ray']['steady_render_ms']/report['cases']['moments']['steady_render_ms']
    report['secondary_update_speedup']=report['cases']['ray']['secondary_update_ms']/report['cases']['moments']['secondary_update_ms']
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
