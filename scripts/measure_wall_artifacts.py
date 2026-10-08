"""Compare fixed planar patches without hiding the full-frame accuracy metric.

Only measurement averages residuals; the displayed/rendered images are unchanged.
The patches apply to the 640x480 default room/camera, away from geometry edges.
"""
from pathlib import Path
import argparse
import json
import numpy as np

PATCHES = {
    "red_wall": (20, 140, 110, 300),
    "back_wall": (190, 140, 230, 235),
    "ceiling": (410, 20, 460, 60),
}


def box_mean(values, size=9):
    # Fixed 9x9 measurement window suppresses reference Monte Carlo noise.
    integral = np.pad(values.astype(np.float64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    return (integral[size:, size:] - integral[:-size, size:]
            - integral[size:, :-size] + integral[:-size, :-size]) / (size*size)


def measure(image, reference):
    results = {"full_frame_nrmse": float(np.sqrt(np.mean((image-reference)**2))/reference.mean()),
               "patches": {}}
    for name, (x0, y0, x1, y1) in PATCHES.items():
        truth = reference[y0:y1, x0:x1].mean(axis=2)
        error = image[y0:y1, x0:x1].mean(axis=2) - truth
        filtered_error = box_mean(error)
        scale = float(truth.mean())
        results["patches"][name] = {
            "xyxy": [x0, y0, x1, y1],
            "mean_reference_rgb": scale,
            "normalized_brightness_rmse": float(np.sqrt(np.mean(error**2))/scale),
            "normalized_mean_error": float(error.mean()/scale),
            "normalized_residual_std_9x9": float(filtered_error.std()/scale),
        }
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--before", type=Path, required=True)
    p.add_argument("--after", type=Path, required=True)
    p.add_argument("--reference", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    arrays = [np.load(path) for path in (args.before, args.after, args.reference)]
    if any(a.shape != (480, 640, 3) or not np.isfinite(a).all() for a in arrays):
        p.error("Expected finite 640x480 linear RGB images from the default camera")
    before, after, reference = arrays
    report = {
        "note": "Fixed wall/ceiling patch diagnostic; full-frame RGB gate remains unchanged. "
                "Brightness is mean(R,G,B). Residual is image-reference. A fixed 9x9 box mean "
                "is applied only to residual measurements, then std is divided by patch reference mean. "
                "Lower residual std means less spatially varying error, not necessarily less physical GI variation.",
        "before_file": str(args.before), "after_file": str(args.after), "reference_file": str(args.reference),
        "before": measure(before, reference), "after": measure(after, reference),
    }
    report["residual_std_reduction"] = {
        name: 1 - report["after"]["patches"][name]["normalized_residual_std_9x9"] /
                  report["before"]["patches"][name]["normalized_residual_std_9x9"]
        for name in PATCHES
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
