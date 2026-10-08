# 本地源码参考索引

2026-10-05：高频参考源码统一放在 `references/source-cache/`。后续先搜索本地文件；仅在缺失或需要更新时访问网页，取得完整原文后立即缓存。

## 已缓存

共 46 个文件、870,558 字节（约 0.87 MB），复制后已逐字节核对。保留原仓库相对路径，来源绝对路径、文件大小和 SHA-256 记录在 `../references/source-cache/manifest.json`。这些是阅读用源码子集，不能单独编译。

### UE5 本地版本：28 个文件

来源：`/Users/litianyu/Perforce/hermitli_litianyudeMacBook-Pro_PBR`，版本以复制的 `Engine/Build/Build.version` 为准。副本来自当前工作目录，不代表干净的官方提交。

缓存目录：`../references/source-cache/UE5-local/`。

- `Engine/Shaders/Private/Lumen/LumenScreenProbeFiltering.usf`：辐射投影 SH、滤波。
- `Engine/Shaders/Private/Lumen/LumenScreenProbeTracing.usf`：探针追踪与光照查询。
- `Engine/Shaders/Private/Lumen/LumenScreenProbeGatherTemporal.usf`：时间处理。
- `Engine/Shaders/Private/Lumen/LumenRadianceCacheInterpolation.ush`：辐射缓存插值、深度相关处理。
- 同目录的其他 `LumenScreenProbe*`、`LumenRadianceCache*` shader：相关声明与更新流程。
- `Engine/Source/Runtime/Renderer/Private/Lumen/LumenScreenProbeGather.cpp/.h`、`LumenRadianceCache.cpp/.h`：CPU 调度。
- `Engine/Shaders/Private/SHCommon.ush`：SH 基函数与运算。

### RTXGI DDGI：18 个文件

来源：`/Users/litianyu/Downloads/RTXGI-DDGI-main`。

缓存目录：`../references/source-cache/RTXGI-DDGI-local/rtxgi-sdk/shaders/`，包括 shader 目录全部现有文件。

- `ddgi/Irradiance.hlsl`：最终探针采样与可见性权重。
- `ddgi/ProbeBlendingCS.hlsl`：辐照度、距离矩和历史融合。
- `ddgi/ProbeRelocationCS.hlsl`、`ddgi/ProbeClassificationCS.hlsl`：探针定位与分类。
- `ddgi/include/`：索引、八面体映射及公共函数。

## UE4.27 LPV：尚未缓存

此前网页阅读过的 10 个核心文件已列入 manifest 的 `ue4_27.files`，包含传播、注入、GV、最终消费和 renderer 的 `.cpp/.h`。这份清单不是源码副本。

本次 GitHub 连接器读取 `EpicGames/UnrealEngine@4.27` 返回 404；Safari 窗口接口返回 `cgWindowNotFound`，AppleScript 读取也未返回，已终止该读取。现有压缩包均为未完成下载。没有将失败响应、网页摘要或不完整压缩包标记为源码。

浏览器可访问后，应将这 10 个文件的完整原文保存至 `../references/source-cache/UE4-4.27/`，保留 `Engine/...` 路径，再补齐文件大小、哈希和可获取的提交版本。也可在 Git HTTPS 认证可用时使用现有 `scripts/checkout_ue4_lpv.sh` 进行 sparse checkout。

## 快速搜索

从项目根目录执行：

```bash
rg -n 'Chebyshev|variance|visibility' references/source-cache/RTXGI-DDGI-local
rg -n 'SHBasisFunction3|MulSH3' references/source-cache/UE5-local
rg -n 'History|Temporal' references/source-cache/UE5-local/Engine/Shaders/Private/Lumen
```

整个 `references/` 已由 `.gitignore` 排除。缓存只保留在本机，项目文档记录索引与来源。
