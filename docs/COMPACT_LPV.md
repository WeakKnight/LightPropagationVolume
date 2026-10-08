# 紧凑 SH LPV：结构与画质取舍

> 当前默认已修正为 RT 引导的紧凑 SH9 光场；本页保留此前核的历史记录，最新分析与低误差验收见 [SH_RADIANCE.md](SH_RADIANCE.md)。

> 当前默认已升级为 GV + 26 邻域 + 时间 refinement；本页为此前核/预设的记录，最新评估见 [SH_REFINEMENT.md](SH_REFINEMENT.md)。

2026-10-05，Metal / SlangPy 0.43.0。本次把默认模式从离散方向扩展恢复为 SH9 核心；没有把精度下降包装成等价优化。

## 默认路径

默认 32³、40 个空间步、三个间接材质阶数，`--transport sh --probe-visibility moments`。

1. 面积加权表面样本取得 normal、albedo、emission，用 RT 查询太阳遮挡。
   注入 `Phi = (rho*E_direct + pi*Le)*area`，把 `Phi/(pi*h²)` 的余弦瓣投影为 RGB×9 SH。
2. 静态几何阶段，每条六邻域 link 的五条短射线缓存覆盖比例、albedo 和朝向源侧的反射 SH 响应。
   这些是几何缓存构建射线，不是每个屏幕采样点的八探针可见性射线。
3. 传播使用六邻域/五面角度积分，按材质阶数保存当前 front；只有上一阶被阻挡的出射通量进入下一阶反射。
   不将 accumulated 反馈到反射，不因增加空间步而无限补出更多材质阶数。
4. 最终着色使用八面体距离矩和 Chebyshev 权重混合八邻域 SH，再做 SH 余弦卷积求 irradiance。
   不再需要 1154 方向的 radiance，也不需要 17×17 irradiance 图。

SH 默认恢复原有的每步 `decay=.95` 阻尼；这是低阶传播近似的工程预设，会损失能量，不能视为真空输运。
方向模式仍默认 `decay=1`。`--decay` 可显式覆盖，两条模式切换时保留用户已设置的值。

## 出射通量预算

SH 的负值截断会使正值查询的积分超过原有系数零阶积分。现在对每个源格子、每个颜色通道，
用与传播/反射相同的六面 quadrature 求和，并加预算：

\[
B=\max(\sqrt{4\pi}\,c_0,0),\qquad
Q=\sum_{i=0}^{5}\phi_i(C),\qquad
s=\min\left(1,\frac{B}{\max(Q,\epsilon)}\right).
\]

透射和反射查询共用 `s*C`；SHCell 本身不被原地改写。它限制该离散 stencil 制造能量，
不修复方向扩散、不证明物理正确，也不是从 reference 拟合的亮度倍率。
均匀场、封闭反射级数、部分遮挡预算和强 SH ringing 的多步预算均有实际 GPU 检查。

## 内存

通过 `renderer.volume_memory()` 读取实际分配 buffer 的 `.size`，包含保留的距离射线 scratch，
不包括场景/TLAS、图像、CPU 数组、程序缓存和运行库。不是进程 RSS 或硬件驻留显存测量。

| 当前 SH 默认 buffer | 大小（十进制 MB） |
| --- | ---: |
| source + accumulated | 7.34 |
| 三个材质阶数的 front + next | 22.02 |
| 六面反射 SH 缓存 | 22.02 |
| link、表面样本、分桶 | 8.78 |
| 距离矩图 | 84.93 |
| 距离射线 scratch | 67.11 |
| active、方向及过滤表 | 0.31 |
| **合计** | **212.51** |

这里先保留 FP32 来检查数值与算子，主要收益来自取消逐方向表示。距离矩和 scratch 已成为最大项，
仅把 SH 改成半精度也不会再取得几十倍收益。

方向对照模式也做了独立的无损内存缩减：moments 消费不分配完整累计角度数组，只绑定一个 16-byte 占位 buffer；
同时不分配无用途的经典 SH front/reflection/link。因此默认方向对照现为 **3.716 GB**。
按同样的 buffer 布局反推，上版约为 **4.366 GB**；与本次 SH 默认相比，GI payload 缩小约 **20.54 倍**。
与本次已缩减的方向模式相比约 **17.49 倍**。

`--probe-visibility ray/none` 需要方向数组用于现场积分，会按需分配完整 `total`。
Python `renderer.angular_data('total')` 也会显式启用累计方向诊断，并重建光场；后续继续保留这个诊断数组。
从方向模式切回 SH 时释放方向对象；从 SH 切到方向模式时释放 SH 专用资源和距离矩对象。
网格变化复用缓存对象并重建相应资源，相机或光源变化不会重建距离矩。

## UE4.27 对照

本次通过已登录 Safari 核对：

- [LPVCommon.ush](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Shaders/Private/LPVCommon.ush) 将 RGB×9 系数和 AO 紧密打包到七张 RGBA 体纹理。
- [LightPropagationVolume.cpp](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Source/Runtime/Renderer/Private/LightPropagationVolume.cpp) 使用 32³、`PF_FloatRGBA`（RGBA16F）和两套交替缓冲。
  主光照体积理论 payload 是 `32³*7*8*2=3.5 MiB`，不是整个 LPV 系统的总内存。
  另外还有 geometry volume、AO、VPL 链表、RSM 等资源。

本项目采用同样的低阶角度表示思想，仍用 FP32 buffer、显式材质阶数和六邻域 stencil。
UE 的 26 邻域、geometry volume packing、时间 refinement 与这里不同；没有声称逐项复刻 UE4。

## 性能与精度

性能：640×480 / 1 spp，编译与预热后，CPU wall clock + `device.wait()`；包含渲染/累积/tone mapping，
不含 UI/呈现。更新在 40/41 空间步之间切换，保持几何、距离矩和直接源缓存。两模式使用各自上述 decay 预设。

| 模式 | 稳定渲染 | 传播更新 | GI buffer |
| --- | ---: | ---: | ---: |
| 方向模式，moments | 0.619 ms | 1409.61 ms | 3716.47 MB |
| **紧凑 SH，moments** | **0.613 ms** | **13.86 ms** | **212.51 MB** |

传播更新约快 101.68 倍；最终消费已经都有便宜的 irradiance 查询，因此帧渲染速度相近。
报告见 [compact-volume-benchmark.json](compact-volume-benchmark.json)。

质量使用同一批完整 640×480 线性 RGB，LPV 2048 spp / RT 16384 spp，三个间接材质阶数：

| 案例 | 方向模式 | 紧凑 SH 默认 |
| --- | ---: | ---: |
| 默认视角 | 2.54% | **49.75%** |
| 侧视角 | 3.30% | **74.67%** |
| 换太阳方向 | 2.15% | **41.31%** |

**SH 默认未通过 5% 门槛。** 方向扩散、局部源附近的光照集中、粗表面位置和几何覆盖近似仍带来很大的空间偏差。
新通量预算只解决 stencil 的能量增益，不能使低阶重投影成为方向保持输运。
两个模式的平均亮度接近也不能说明画质接近。未缩放间接光强、未调整曝光或裁剪图像。
旧方向模式删缓存后的三个案例 RGB 数组与上版逐值一致；其质量没有因本次内存优化下降。

![方向模式 / SH 默认 / 同一 RT](compact-lpv-comparison.png)

报告见 [compact-lpv-accuracy.json](compact-lpv-accuracy.json)，方向对照见 [directional-compact-cache-accuracy.json](directional-compact-cache-accuracy.json)。
`--accuracy` 仍保留 5% 门槛；SH 不达标时返回非零，不能把退出码当作 GPU 回归测试失败。

```bash
./run_local.sh
./run_local.sh --transport directional
./run_local.sh --benchmark-volume
./run_local.sh --accuracy --extra-cases
./run_local.sh --accuracy --transport directional --extra-cases
```

34 项 CPU/Metal debug GPU 检查涵盖材质反射、能量约束、可选方向数组、资源切换、SH 距离矩薄墙检查、
距离图接缝/缓存、原有方向输运和自发光注入。测试并不等同于 5% 画质验收。
