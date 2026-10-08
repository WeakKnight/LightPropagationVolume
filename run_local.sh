#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
slangpy_dir="${SLANGPY_LOCAL_DIR:-$(dirname "$project_dir")/slangpy}"
python_bin="${LPV_PYTHON:-$project_dir/.venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then
    python_bin="$(dirname "$project_dir")/DSHARC/sample/.venv/bin/python"
fi
if [[ ! -x "$python_bin" ]]; then
    echo "Create .venv and install requirements.txt, or set LPV_PYTHON." >&2
    exit 1
fi
if [[ -f "$slangpy_dir/slangpy/__init__.py" ]]; then
    export PYTHONPATH="$slangpy_dir${PYTHONPATH:+:$PYTHONPATH}"
fi
entry_point="$project_dir/sample/entry_point.py"
case "${1:-}" in
    --test) entry_point="$project_dir/tests/test_lpv.py"; shift ;;
    --test-download) entry_point="$project_dir/tests/test_download.py"; shift ;;
    --compare) entry_point="$project_dir/scripts/render_comparison.py"; shift ;;
    --accuracy) entry_point="$project_dir/scripts/validate_accuracy.py"; shift ;;
    --benchmark-visibility) entry_point="$project_dir/scripts/benchmark_probe_visibility.py"; shift ;;
    --evaluate-sh) entry_point="$project_dir/scripts/evaluate_sh_refinement.py"; shift ;;
    --benchmark-dynamic) entry_point="$project_dir/scripts/benchmark_dynamic.py"; shift ;;
    --benchmark-volume) entry_point="$project_dir/scripts/benchmark_volume.py"; shift ;;
    --benchmark) entry_point="$project_dir/scripts/benchmark_cache.py"; shift ;;
esac
exec "$python_bin" "$entry_point" "$@"
