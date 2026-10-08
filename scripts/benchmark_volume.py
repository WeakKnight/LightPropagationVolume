"""Compare compact SH and directional volumes with synchronized wall timings."""
import argparse
import gc
import json
from pathlib import Path
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sample'))
from renderer import LPVRenderer,create_device
from scene import Scene,demo_scene
from camera import Camera
from volume import VolumeSettings


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',default='metal' if sys.platform=='darwin' else 'd3d12' if sys.platform=='win32' else 'vulkan')
    parser.add_argument('--trials',type=int,default=11)
    parser.add_argument('--output',type=Path,default=ROOT/'sample/output/sh-radiance-benchmark.json')
    args=parser.parse_args()
    if args.trials<3:parser.error('Use at least 3 trials')
    device=create_device(args.backend);scene=Scene(device,demo_scene())
    report={'backend':args.backend,'width':640,'height':480,'spp':1,'grid':32,'material_orders':3,
            'probe_visibility':'moments','trials':args.trials,
            'note':'CPU wall clock + device.wait; render includes accumulation/tonemap, excludes UI/presentation. '
                   'SH target updates alternate sunlight and recapture finite material orders; directional alternates 40/41 steps. '
                   'Memory is GI buffer payload including retained depth scratch; excludes scene/images/runtime and CPU arrays.',
            'cases':{}}
    for transport in ('directional','sh'):
        renderer=LPVRenderer(device,scene,Camera(),settings=VolumeSettings(transport=transport,probes_per_frame=0),width=640,height=480,spp=1)
        start=time.perf_counter();renderer.frame();device.wait()
        cold=time.perf_counter()-start
        start=time.perf_counter();settled=renderer.settle_volume();settle_seconds=time.perf_counter()-start
        for _ in range(3):renderer.frame();device.wait()
        frames=[]
        for _ in range(args.trials):
            start=time.perf_counter();renderer.frame();device.wait();frames.append((time.perf_counter()-start)*1000)
        updates=[]
        for i in range(5):
            if transport=="sh":renderer.lighting.sun_intensity=3 if i%2 else 2.9
            else:renderer.settings.steps=41 if i%2==0 else 40
            start=time.perf_counter();renderer.frame();device.wait();updates.append((time.perf_counter()-start)*1000)
        refinement_frames=[]
        if transport=="sh":
            for i in range(args.trials):
                start=time.perf_counter();renderer.frame();device.wait()
                refinement_frames.append((time.perf_counter()-start)*1000)
        visibility=renderer.visibility if transport=='sh' else renderer.directional.visibility
        result={'cold_frame_seconds':cold,'settle_seconds':settle_seconds,'refinement_updates':settled,'steady_render_ms':statistics.median(frames),
                'update_ms':statistics.median(updates),'refinement_frame_ms':statistics.median(refinement_frames) if refinement_frames else None,
                'refinement_samples_ms':refinement_frames,'render_samples_ms':frames,'update_samples_ms':updates,
                'decay':renderer.settings.decay,'volume_memory':renderer.volume_memory(),
                'geometry_builds':renderer.link_builds,'source_builds':renderer.source_builds,'depth_builds':visibility.builds}
        report['cases'][transport]=result
        print(transport,'MB',result['volume_memory']['total_bytes']/1e6,'render ms',result['steady_render_ms'],'update ms',result['update_ms'],flush=True)
        del renderer;gc.collect();device.wait()
    report['memory_reduction_vs_current_directional']=report['cases']['directional']['volume_memory']['total_bytes']/report['cases']['sh']['volume_memory']['total_bytes']
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
