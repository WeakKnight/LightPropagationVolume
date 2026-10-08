#!/usr/bin/env bash
# Private Epic reference stays outside the distributable source tree.
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
reference_dir="${1:-$project_dir/references/UnrealEngine-4.27}"
if [[ ! -d "$reference_dir/.git" ]]; then
    mkdir -p "$(dirname "$reference_dir")"
    GIT_TERMINAL_PROMPT=0 git -c http.connectTimeout=15 \
        -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=30 clone \
        --filter=blob:none --depth=1 --single-branch --branch=4.27 \
        --no-checkout https://github.com/EpicGames/UnrealEngine.git "$reference_dir"
fi
# Non-cone mode is intentional: cone mode also checks out unrelated ancestor files.
git -C "$reference_dir" sparse-checkout init --no-cone
git -C "$reference_dir" sparse-checkout set --no-cone \
    '/Engine/Shaders/Private/LPV*' \
    '/Engine/Shaders/Private/LightPropagationVolume*' \
    '/Engine/Source/Runtime/Renderer/Private/LightPropagationVolume*' \
    '/Engine/Source/Runtime/Renderer/Private/PostProcess/*[Ll][Pp][Vv]*' \
    '/Engine/Source/Runtime/Renderer/Public/LightPropagationVolume*' \
    '/Engine/Plugins/Runtime/LightPropagationVolume/Source/' \
    '/Engine/Plugins/Runtime/LightPropagationVolume/Shaders/'
git -C "$reference_dir" checkout 4.27
git -C "$reference_dir" ls-files | while IFS= read -r path; do
    if [[ -f "$reference_dir/$path" ]]; then printf '%s\n' "$path"; fi
done
du -sh "$reference_dir"
