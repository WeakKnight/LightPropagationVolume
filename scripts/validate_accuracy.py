"""Fixed linear-RGB accuracy gate; no exposure fitting, crop or intensity scaling."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path
import sys
import time
import numpy as np
from PIL import Image, ImageDraw
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sample'))
from camera import Camera
from renderer import LPVRenderer, Lighting, create_device
from scene import Scene,demo_scene
from volume import VolumeSettings


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend',default='metal' if sys.platform=='darwin' else 'd3d12' if sys.platform=='win32' else 'vulkan')
    p.add_argument('--width',type=int,default=640);p.add_argument('--height',type=int,default=480)
    p.add_argument('--lpv-spp',type=int,default=2048);p.add_argument('--reference-spp',type=int,default=16384)
    p.add_argument('--extra-cases',action='store_true')
    p.add_argument('--transport',choices=('sh','sh_ue4','sh_legacy','directional'),default='sh')
    p.add_argument('--projected-sh',action='store_true',help='Diagnostic: directional solver with SH9 final consumption')
    p.add_argument('--reference-cache',type=Path,help='Reuse matched fixed-harness RT arrays and report; never regenerate or alter them')
    p.add_argument('--output',type=Path,default=ROOT/'sample/output/accuracy')
    a=p.parse_args()
    if a.lpv_spp%64 or a.reference_spp%128 or min(a.width,a.height,a.lpv_spp,a.reference_spp)<=0:
        p.error('Use positive dimensions, LPV spp divisible by 64, RT spp divisible by 128')
    a.output.mkdir(parents=True,exist_ok=True)
    dev=create_device(a.backend);scene=Scene(dev,demo_scene())
    candidate=LPVRenderer(dev,scene,Camera(),settings=VolumeSettings(transport=a.transport),width=a.width,height=a.height,spp=64)
    if a.projected_sh and a.transport!="directional":p.error("--projected-sh requires directional diagnostic")
    candidate.consume_projected_sh=a.projected_sh
    reference=LPVRenderer(dev,scene,Camera(),settings=VolumeSettings(),width=a.width,height=a.height,spp=64,mode='reference')
    cases=[('default',Camera(),Lighting())]
    if a.extra_cases:
        cases += [('side_view',Camera((3.8,1.8,4.6),(0,1.2,-.3),50),Lighting()),
                  ('sun_changed',Camera(),Lighting(-35,40,3))]
    cached_report=None
    if a.reference_cache:
        cached_report=json.loads((a.reference_cache/'report.json').read_text())
        for key,expected in [('width',a.width),('height',a.height),('reference_spp',a.reference_spp),('material_orders',3)]:
            if cached_report.get(key)!=expected:p.error(f'Cached reference mismatch: {key}')
        for name,_,_ in cases:
            if name not in cached_report['cases'] or not (a.reference_cache/f'{name}-reference.npy').is_file():
                p.error(f'Missing cached reference case: {name}')
        if a.reference_cache.resolve()==a.output.resolve():p.error('Use a separate output directory from the reference cache')
    report={'metric':'sqrt(mean((LPV-RT)^2))/mean(RT), full uncropped unexposed linear RGB',
            'target':.05,'width':a.width,'height':a.height,'lpv_spp':a.lpv_spp,'reference_spp':a.reference_spp,
            'transport':a.transport,'projected_sh_consumption':a.projected_sh,'grid':32,'angular_resolution':5 if a.transport=='directional' else None,'directions':1154 if a.transport=='directional' else 0,
            'probe_visibility':candidate.settings.probe_visibility,'visibility_bias':candidate.settings.visibility_bias,
            'distance_map_size':16,'distance_rays_per_probe':512,'irradiance_vertex_map_size':17 if a.transport=='directional' else None,
            'source_angular_samples':8 if a.transport=='directional' else None,'source_samples':candidate.settings.source_samples if a.transport in ('sh_ue4','sh_legacy') else 0,
            'geometry_samples':candidate.settings.source_samples if a.transport in ('sh','sh_ue4') else None,
            'first_material_order':'RT probe radiance -> SH9' if a.transport=='sh' else 'RT surface flux -> SH9' if a.transport!='directional' else 'ray-traced angular footprint average',
            'probes_per_frame':candidate.settings.probes_per_frame,'spatial_steps':candidate.settings.steps,'probe_rays':candidate.settings.probe_rays if a.transport=='sh' else None,
            'spatial_budget':'RT-anchored 26-neighbor predictor' if a.transport=='sh' else 'incremental passes per refinement update' if a.transport=='sh_ue4' else 'shared across material orders' if a.transport=='sh_legacy' else 'per secondary material order',
            'material_orders':3,'decay':candidate.settings.decay,'read_bias':candidate.settings.read_bias,'indirect_strength':1,
            'geometry_volume':a.transport in ('sh','sh_ue4'),'neighbors':26 if a.transport in ('sh','sh_ue4') else 6 if a.transport=='sh_legacy' else None,
            'temporal_refinement':a.transport in ('sh','sh_ue4'),'history_weight':candidate.settings.history_weight if a.transport in ('sh','sh_ue4') else None,
            'propagation_weight':candidate.settings.propagation_weight if a.transport in ('sh','sh_ue4') else None,
            'reference_cache':str(a.reference_cache.resolve()) if a.reference_cache else None,'cases':{}}
    for name,camera,light in cases:
        candidate.camera=reference.camera=camera
        candidate.lighting=reference.lighting=light
        candidate._image_signature=reference._image_signature=None
        start=time.perf_counter()
        settled=candidate.settle_volume()
        for _ in range(a.lpv_spp//64):candidate.frame()
        pred=candidate.linear_image();build_seconds=time.perf_counter()-start
        candidate.save(a.output/f'{name}-lpv.png',a.output/f'{name}-lpv.npy')
        if a.reference_cache:
            ref=np.load(a.reference_cache/f'{name}-reference.npy')
            if ref.shape!=(a.height,a.width,3) or not np.isfinite(ref).all():p.error(f'Invalid cached reference: {name}')
            rt_noise=cached_report['cases'][name]['reference_noise_estimate']
            for suffix in ('npy','png'):
                shutil.copyfile(a.reference_cache/f'{name}-reference.{suffix}',a.output/f'{name}-reference.{suffix}')
        else:
            half=None
            for i in range(a.reference_spp//64):
                reference.frame()
                if i+1==a.reference_spp//128:half=reference.linear_image()
                if (i+1)%32==0:dev.wait();print(name,'reference',reference.sample_count,flush=True)
            ref=reference.linear_image();reference.save(a.output/f'{name}-reference.png',a.output/f'{name}-reference.npy')
            rt_noise=float(np.sqrt(np.mean((ref-half)**2))/ref.mean())
        rmse=float(np.sqrt(np.mean((pred-ref)**2))/ref.mean())
        report['cases'][name]={'nrmse':rmse,'passes':rmse<=.05,'reference_noise_estimate':rt_noise,
            'mean_lpv':float(pred.mean()),'mean_reference':float(ref.mean()),'lpv_cold_or_update_seconds':build_seconds,
            'refinement_updates':settled,'refinement_residual':candidate.refinement.residual if candidate.refinement else None,
            'reference_sha256':hashlib.sha256((a.output/f'{name}-reference.npy').read_bytes()).hexdigest(),
            'accumulated_spp':candidate.sample_count,'geometry_builds':candidate.link_builds,'direct_source_builds':candidate.source_builds,
            'volume_memory':candidate.volume_memory()}
        print(name,report['cases'][name],flush=True)
    # Legacy SH parameter preset, using the current budgeted SH kernel.
    baseline=LPVRenderer(dev,scene,Camera(),settings=VolumeSettings(16,24,read_bias=.25,decay=.95,transport='sh_legacy',probe_visibility='none'),
                         width=a.width,height=a.height,spp=64)
    for _ in range(a.lpv_spp//64):baseline.frame()
    baseline.save(a.output/'baseline-sh.png',a.output/'baseline-sh.npy')
    ref=np.load(a.output/'default-reference.npy')
    report['baseline_sh_nrmse']=float(np.sqrt(np.mean((baseline.linear_image()-ref)**2))/ref.mean())
    report['baseline_note']='Legacy 16^3/24/.95/.25 preset with current SH flux budget; not the untouched historical shader'
    canvas=Image.new('RGB',(a.width*3,a.height+32),(24,27,32));draw=ImageDraw.Draw(canvas)
    for i,(filename,label) in enumerate([('baseline-sh.png','SH / legacy parameter preset'),
                                         ('default-lpv.png',f"{'SH9 / directional-field diagnostic' if a.projected_sh else a.transport.upper()+' VOLUME'} / {a.lpv_spp} spp"),
                                         ('default-reference.png',f'RT / matched 3 indirect hits / {a.reference_spp} spp')]):
        with Image.open(a.output/filename) as im:canvas.paste(im,(i*a.width,32))
        draw.text((i*a.width+10,10),label,fill=(230,235,240))
    canvas.save(a.output/'comparison.png')
    report['all_cases_pass']=all(v['passes'] for v in report['cases'].values())
    (a.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return 0 if report['all_cases_pass'] else 1


if __name__=='__main__':sys.exit(main())
