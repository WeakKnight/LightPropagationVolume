"""Synchronized update latency, same SH9/visibility/render code for both policies.

The old policy resamples and reallocates on injection-bias changes, and traces
injection again on propagation changes. Simulate those invalidations explicitly.
Compilation, scene build and warmup are excluded; CPU and GPU work are included.
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sample'))
from camera import Camera
from renderer import LPVRenderer,create_device
from scene import Scene,demo_scene
from volume import VolumeSettings


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',default='metal' if sys.platform=='darwin' else 'd3d12' if sys.platform=='win32' else 'vulkan')
    parser.add_argument('--output',type=Path,default=ROOT/'sample/output/cache-benchmark.json')
    args=parser.parse_args()
    device=create_device(args.backend)
    scene=Scene(device,demo_scene())
    results={}
    for policy in ('previous_invalidation','split_cache'):
        renderer=LPVRenderer(device,scene,Camera(),settings=VolumeSettings(16,24,read_bias=.25,decay=.95,bounces=1,transport="sh_legacy"),width=24,height=24,spp=1)
        for _ in range(4):renderer.frame();device.wait()
        stages={}
        for attribute,values in [('injection_bias',(.5,.6)),('steps',(23,24)),('sun_intensity',(2.9,3))]:
            samples=[]
            for i in range(9):
                target=renderer.lighting if attribute=='sun_intensity' else renderer.settings
                setattr(target,attribute,values[i%2])
                if policy=='previous_invalidation':
                    if attribute=='injection_bias':
                        renderer._resource_signature=renderer._surface_signature=renderer._bin_signature=None
                    elif attribute=='steps':renderer._source_signature=None
                start=time.perf_counter()
                renderer.frame();device.wait()
                samples.append((time.perf_counter()-start)*1000)
            stages[attribute]=statistics.median(samples)
        results[policy]=stages
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2))


if __name__=='__main__':main()
