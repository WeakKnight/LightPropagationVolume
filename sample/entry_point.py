"""python sample/entry_point.py, or ./run_local.sh with the local Metal build."""
from __future__ import annotations
import argparse
import math
from pathlib import Path
import sys


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Minimal SlangPy LPV with ray-traced light injection")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--scene", type=Path, help="Optional GLB/OBJ; diffuse factors only")
    parser.add_argument("--room", choices=("lpv", "dsharc"), default="lpv")
    parser.add_argument("--scene-scale", type=float, default=1)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=640)
    parser.add_argument("--frames", type=int, default=64)
    parser.add_argument("--spp", type=int, default=1)
    parser.add_argument("--mode", choices=("lpv", "direct", "indirect", "reference", "injection", "volume"), default="lpv")
    parser.add_argument("--grid", type=int, default=32)
    parser.add_argument("--steps", type=int, default=None, help="SH target predictor passes 3; sh_ue4 per-frame passes 3; legacy/directional 40")
    parser.add_argument("--bounces",type=int,default=3,help="Maximum indirect material bounces, 1..8; 1 disables secondary reflection")
    parser.add_argument("--transport",choices=("sh","sh_ue4","sh_legacy","directional"),default="sh",help="Compact SH9 LPV (default) or direction-preserving comparison")
    parser.add_argument("--probe-rays",type=int,default=1024,help="RT directions per SH probe; even 32..4096")
    parser.add_argument("--probes-per-frame",type=int,default=4096,help="SH capture probe budget per frame across material orders; 0 performs full capture")
    parser.add_argument("--no-refinement",action="store_true",help="Disable temporal history; retain the SH radiance target")
    parser.add_argument("--history-weight",type=float,default=.9)
    parser.add_argument("--propagation-weight",type=float,default=.008)
    parser.add_argument("--secondary-occlusion",type=float,default=1)
    parser.add_argument("--secondary-bounce",type=float,default=1)
    parser.add_argument("--angular-resolution",type=int,default=5,help="Integer direction lattice extent 1..5 (26/98/290/578/1154 directions)")
    parser.add_argument("--source-angular-samples",type=int,default=8,help="First-flight angular footprint samples, 1..32; 1 uses bin centers")
    parser.add_argument("--probe-visibility",choices=("moments","ray","none"),default="moments",help="Probe interpolation visibility for both transports; ray is the exact comparison")
    parser.add_argument("--visibility-bias",type=float,default=.25,help="Moments-only surface normal bias in cell units; leaves trilinear coordinates unchanged")
    parser.add_argument("--source-samples", type=int, default=131072, help="Area samples for GV / legacy flux injection")
    parser.add_argument("--injection-bias", type=float, default=.5, help="Legacy surface-flux injection offset in cell units")
    parser.add_argument("--read-bias", type=float, default=0.0, help="Receiver offset in cell units")
    parser.add_argument("--decay", type=float, default=None, help="Energy damping, 0..1; defaults: SH/directional 1, legacy .95")
    parser.add_argument("--no-occlusion", action="store_true", help="SH only: disable GV predictor gating / legacy link blockers")
    parser.add_argument("--indirect-strength", type=float, default=1)
    parser.add_argument("--slice", type=float, default=.35, help="XZ debug slice normalized height, 0..1")
    parser.add_argument("--sun-azimuth", type=float, default=-50)
    parser.add_argument("--sun-elevation", type=float, default=55)
    parser.add_argument("--sun-intensity", type=float, default=3)
    parser.add_argument("--exposure", type=float, default=1)
    parser.add_argument("--fov", type=float, default=50)
    parser.add_argument("--camera-position", type=float, nargs=3)
    parser.add_argument("--camera-target", type=float, nargs=3)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--backend", choices=("metal", "d3d12", "vulkan"),
        default="metal" if sys.platform == "darwin" else "d3d12" if sys.platform == "win32" else "vulkan")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "output" / "lpv.png")
    parser.add_argument("--linear-output", type=Path)
    parser.add_argument("--volume-output", type=Path, help="Save source/accumulated SH as .npz")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--vsync", action="store_true")
    parser.add_argument("--window-frames", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    from volume import VolumeSettings
    try:
        VolumeSettings(args.grid, args.steps, args.source_samples, args.injection_bias,
            args.read_bias, args.decay, not args.no_occlusion,args.bounces,args.transport,args.angular_resolution,args.source_angular_samples,args.probe_visibility,args.visibility_bias,
            not args.no_refinement,args.history_weight,args.propagation_weight,args.secondary_occlusion,args.secondary_bounce,args.probe_rays,args.probes_per_frame).validate()
    except ValueError as error:
        parser.error(str(error))
    for name in ("scene_scale", "sun_azimuth", "sun_elevation", "sun_intensity", "exposure", "fov", "indirect_strength", "slice"):
        if not math.isfinite(getattr(args, name)):
            parser.error(f"--{name.replace('_', '-')} must be finite")
    if not 1 <= args.width <= 8192 or not 1 <= args.height <= 8192:
        parser.error("Render dimensions must be 1..8192")
    if args.frames < 1 or not 1 <= args.spp <= 64 or args.window_frames < 0:
        parser.error("Expected positive frames, 1..64 spp, and nonnegative window frames")
    if args.scene_scale <= 0 or args.sun_intensity < 0 or args.indirect_strength < 0:
        parser.error("Scale must be positive; lighting strengths must be nonnegative")
    if not 1 <= args.fov < 179 or not -90 <= args.sun_elevation <= 90 or not 0 <= args.slice <= 1:
        parser.error("Expected FOV 1..179, sun elevation -90..90, slice 0..1")
    if not -20 <= args.exposure <= 20 or not 0 <= args.seed < 2**32:
        parser.error("Expected exposure -20..20 and uint32 seed")
    if bool(args.camera_position) != bool(args.camera_target):
        parser.error("Supply both camera position and target")
    if args.camera_position and (not all(math.isfinite(x) for x in args.camera_position + args.camera_target)
            or args.camera_position == args.camera_target):
        parser.error("Camera position and target must be finite and distinct")
    return args


def main():
    args = parse_args()
    from app import App
    App(args).run()


if __name__ == "__main__":
    main()
