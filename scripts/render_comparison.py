"""Render direct / single-bounce LPV / LPV / matched-depth RT locally."""
from pathlib import Path
import argparse
import json
import sys
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sample"))
from camera import Camera
from renderer import LPVRenderer, Lighting, create_device
from scene import Scene, demo_scene
from volume import VolumeSettings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="metal" if sys.platform == "darwin" else "d3d12" if sys.platform == "win32" else "vulkan")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--lpv-spp",type=int,default=512)
    parser.add_argument("--reference-spp",type=int,default=8192)
    parser.add_argument("--bounces", type=int, choices=range(1,9), default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "sample/output/comparison")
    args = parser.parse_args()
    if args.lpv_spp<64 or args.reference_spp<64 or args.lpv_spp%64 or args.reference_spp%64:
        parser.error("Samples must be positive multiples of 64")
    args.output.mkdir(parents=True, exist_ok=True)
    device = create_device(args.backend)
    renderer = LPVRenderer(device, Scene(device, demo_scene()), Camera(), Lighting(), VolumeSettings(bounces=args.bounces),
        width=args.width, height=args.height, spp=4)
    images = {}
    passes = [("direct", "direct",args.lpv_spp//64,64,args.bounces)]
    if args.bounces>1:
        passes.append(("lpv_one","lpv",args.lpv_spp//64,64,1))
    passes += [("lpv","lpv",args.lpv_spp//64,64,args.bounces), ("reference","reference",args.reference_spp//64,64,args.bounces)]
    for name, mode, frames, spp, bounces in passes:
        renderer.mode, renderer.spp, renderer.settings.bounces = mode, spp, bounces
        if mode in ("lpv","indirect"):renderer.settle_volume()
        for _ in range(frames):
            renderer.frame()
        renderer.save(args.output/f"{name}.png", args.output/f"{name}.npy")
        images[name] = renderer.linear_image()
    shadow = images["direct"].mean(axis=2) < .001
    def rmse(image):
        return float(np.sqrt(np.mean((image-images["reference"])**2))/max(images["reference"].mean(),1e-9))
    stats = {"backend": args.backend, "grid": renderer.settings.resolution, "steps": renderer.settings.steps,
        "transport": renderer.settings.transport,"directions":renderer.directional.direction_count if renderer.directional else 0,
        "probe_visibility":renderer.settings.probe_visibility,"visibility_bias":renderer.settings.visibility_bias,
        "source_samples": renderer.settings.source_samples if renderer.settings.transport=='sh' else 0,"source_angular_samples":renderer.settings.source_angular_samples if renderer.directional else 0, "indirect_material_bounces": args.bounces,
        "mean_linear_RGB": {mode: float(image.mean()) for mode,image in images.items()},
        "shadow_indirect_lpv": float((images["lpv"]-images["direct"])[shadow].mean()),
        "shadow_indirect_reference": float((images["reference"]-images["direct"])[shadow].mean()),
        "normalized_linear_rmse_vs_reference": rmse(images["lpv"]),
        "note": f"Finite-grid {renderer.settings.transport} volume; RT reference has {args.bounces} indirect material bounces."}
    if "lpv_one" in images:
        stats["single_bounce_lpv_rmse_vs_matched_reference"] = rmse(images["lpv_one"])
        stats["shadow_indirect_lpv_one"] = float((images["lpv_one"]-images["direct"])[shadow].mean())
    (args.output/"report.json").write_text(json.dumps(stats, indent=2)+"\n")
    labels = {"direct":f"DIRECT / {args.lpv_spp} spp", "lpv_one":f"LPV / 1 indirect bounce / {args.lpv_spp} spp",
        "lpv":f"LPV / {args.bounces} indirect bounces / {args.lpv_spp} spp",
        "reference":f"RT / {args.bounces} indirect bounces / {args.reference_spp} spp"}
    canvas = Image.new("RGB", (args.width*len(images), args.height+34), (24,27,32))
    draw = ImageDraw.Draw(canvas)
    for i,name in enumerate(images):
        with Image.open(args.output/f"{name}.png") as image:
            canvas.paste(image, (i*args.width,34))
        draw.text((i*args.width+12,10),labels[name],fill=(230,235,240))
    canvas.save(args.output/"comparison.png")
    print(json.dumps(stats, indent=2))
    print(f"Saved {args.output/'comparison.png'}")
    device.wait()


if __name__ == "__main__":
    main()
