"""Default-view SH26 ablations against the unchanged cached RT reference."""
import argparse
import json
from pathlib import Path
import sys
import time
import numpy as np
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'sample'))
from renderer import LPVRenderer,create_device
from scene import Scene,demo_scene
from camera import Camera
from volume import VolumeSettings


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-cache',type=Path,default=ROOT/'sample/output/accuracy-moments')
    p.add_argument('--output',type=Path,default=ROOT/'sample/output/sh-radiance-ablation')
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    cached=json.loads((a.reference_cache/'report.json').read_text())
    if any(cached[k]!=v for k,v in [('width',640),('height',480),('reference_spp',16384),('material_orders',3)]):
        p.error('Reference does not match the fixed 640x480/16384 spp/3-order harness')
    reference=np.load(a.reference_cache/'default-reference.npy')
    device=create_device('metal' if sys.platform=='darwin' else 'd3d12' if sys.platform=='win32' else 'vulkan')
    r=LPVRenderer(device,Scene(device,demo_scene()),Camera(),settings=VolumeSettings(),width=640,height=480,spp=64)
    report={'metric':'full linear RGB NRMSE / mean(RT)','width':640,'height':480,'lpv_spp':2048,
        'reference_spp':16384,'reference_material_orders':3,'cases':{},'temporal_profile':[]}
    # Track transport convergence in SH coefficients, separately from RT error.
    snapshots=[]
    for step in range(1,2049):
        r.ensure_volume()
        if step in (1,8,32,64,128,256) or r.refinement.converged:
            device.wait()
            field=r.fields['accumulated'].to_numpy().view(np.float32).copy()
            snapshots.append((r.refinement.updates,field))
        if r.refinement.converged:break
    else:raise RuntimeError('SH warmup did not settle')
    stable=snapshots[-1][1]
    for step,field in snapshots:
        report['temporal_profile'].append({'update':step,'relative_sh_l2_to_settled':float(np.linalg.norm(field-stable)/max(np.linalg.norm(stable),1e-20))})
    variants=[('full',True,True,3),('no_geometry_occlusion',False,True,3),
              ('no_temporal_refinement',True,False,3),('one_material_order',True,True,1)]
    for name,occlusion,temporal,bounces in variants:
        r.settings.occlusion=occlusion;r.settings.temporal_refinement=temporal;r.settings.bounces=bounces
        start=time.perf_counter();updates=r.settle_volume()
        r._image_signature=None
        for _ in range(32):r.frame()
        image=r.linear_image()
        r.save(a.output/f'{name}.png',a.output/f'{name}.npy')
        report['cases'][name]={'nrmse':float(np.sqrt(np.mean((image-reference)**2))/reference.mean()),
            'mean_rgb':float(image.mean()),'refinement_updates':updates,'seconds':time.perf_counter()-start,
            'geometry_occlusion':occlusion,'temporal_refinement':temporal,'material_orders':bounces,
            'reference_note':'one_material_order still compares to 3-order RT to quantify omitted secondary energy'}
        print(name,report['cases'][name],flush=True)
    (a.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    canvas=Image.new('RGB',(640*3,480+32),(24,27,32));draw=ImageDraw.Draw(canvas)
    for i,(name,label) in enumerate([('no_temporal_refinement','SH26 / one frame / no history (same physical target)'),('full','SH26 + GV / settled history'),('reference','RT / 3 orders')]):
        path=a.reference_cache/'default-reference.png' if name=='reference' else a.output/f'{name}.png'
        with Image.open(path) as image:canvas.paste(image,(i*640,32))
        draw.text((i*640+10,10),label,fill=(230,235,240))
    canvas.save(a.output/'comparison.png')
    device.wait()


if __name__=='__main__':main()
