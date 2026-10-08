# 材质反射与 LPV 次级反弹

> 当前默认已修正为 RT 引导的紧凑 SH9 光场；本页保留此前核的历史记录，最新分析与低误差验收见 [SH_RADIANCE.md](SH_RADIANCE.md)。

> 当前默认已升级为 GV + 26 邻域 + 时间 refinement；本页为此前核/预设的记录，最新评估见 [SH_REFINEMENT.md](SH_REFINEMENT.md)。

2026-10-04，通过用户登录的 Safari 实际阅读 UE4.27 源码，随后在本项目 Metal 后端实现并验证。

## UE4.27 怎样处理

- [LightPropagationVolume.h](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Source/Runtime/Renderer/Private/LightPropagationVolume.h)：`LPV_MULTIPLE_BOUNCES` 为 1，geometry volume 的 SH order 为 1。
- [LightPropagationVolume.cpp](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Source/Runtime/Renderer/Private/LightPropagationVolume.cpp)：从 `LPVSecondaryBounceIntensity` 读取反射强度，超过阈值时选用 multiple-bounce shader permutation。类构造值为 0，源码存在该机制不表示所有场景默认启用。
- [LPVGeometryVolumeCommon.ush](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Shaders/Private/LPVGeometryVolumeCommon.ush)：geometry volume 保存方向遮挡的 SH，并在启用多次反射时额外保存 RGB color。
- [LPVBuildGeometryVolume.usf](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Shaders/Private/LPVBuildGeometryVolume.usf)：从几何 VPL 列表汇聚方向遮挡和颜色；color 由列表的 flux 字段乘 RSM 面积权重后平均，属于带权颜色代理。本次没有继续追踪生成几何 VPL 的 raster material pass，不能将它宣称为未经缩放的原始 albedo。
- [LPVPropagate.usf](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Shaders/Private/LPVPropagate.usf)：读取当前格子射向阻挡物的光照，再乘 geometry color、遮挡权重和次级反射强度，加入传播结果。该分支要求 secondary occlusion 同时启用。

其反射项的结构是：

\[
\Delta C_{\mathrm{reflection}}=
 C_{\mathrm{outgoing}}\odot C_{\mathrm{geometry}}
 (1-w_{\mathrm{visibility}})^3\,s_{\mathrm{bounce}}.
\]

`C_outgoing` 已包含 cell / propagation 的权重。几何颜色与 opacity 的三次方、全局 bounce strength 都有经验成分。
UE4 的时间 refinement、26 邻域与本项目的空间 front 累积也不同，因此参考其“被截获的光乘材质颜色并重新发射”的结构。

## 本项目的简化实现

沿用每条 cell link 的五条 RT 射线，在静态几何缓存阶段取得最近命中的 **albedo 与几何法线**。
同一条射线的 miss 计入透射，hit 计入反射响应，二者使用同一份覆盖采样。
反射响应保存在源 cell，法线朝向源 cell，反射光仍从源侧的空气格子发射。
这是对实际表面位置的粗体素近似；没有把光搬到阻挡物背后的相邻格子。

对 link i，缓存 RGB SH 响应为：

\[
A_i(\omega)=\frac{1}{5}\sum_{j\in\mathrm{hit}(i)}
 \frac{\rho_j}{\pi}\max(n_j\cdot\omega,0),
\qquad v_i=\frac{N_{\mathrm{miss}}}{5}.
\]

这里实际存储 `A_i` 的九个 SH 系数。每个空间传播步计算该 link 的完整出射通量代理：

\[
\phi_i=\Omega_m L(d_i)+
 \Omega_s\sum_{a\perp d_i}L(\operatorname{normalize}(d_i+0.5a)),
\]

其中主面立体角为 0.4006696846，四个侧面各为 0.4234313544。
通量代理与场使用相同的 cell 面积归一化；物理通量需再乘 cell_size²。
透射使用 `v_i`，新增反射为 `phi_i * A_i`（RGB 逐通道乘法）。
对相同法线、相同 albedo 的完全命中表面，其积分反射通量恰为 `rho * phi_i`，因余弦瓣积分为 π。
没有额外调节 secondary bounce 强度，黑色材质自然吸收。
不同命中法线/材质的平均、五射线覆盖和五面角度积分仍然是近似。

## 怎样避免重复注入

`front[q][cell]` 分别保存不同材质反射阶数的**本步新增光**；`q=0` 是直接照明表面的注入源。
下一步概念上写为：

\[
f_{t+1,q}=T(f_{t,q})+R(f_{t,q-1}),\qquad f_{t,-1}=0.
\]

`T` 是带遮挡的空间传播，`R` 是截获通量乘材质反射响应。
每步只把新 front 加到 accumulated 一次，绝不把 accumulated 再送进 `R`。

默认 `--bounces 3` 保存 q=0、1、2 三层，RT reference 同样追踪最多三个间接表面命中，
最后可见表面的 albedo 在着色时另乘一次。`--bounces 1` 保留旧算法，仅让直接照明表面的源在空间中传播。
`--steps 24` 是所有阶数共享的空间预算，并不是每个阶数各传播 24 步。
没有 geometry occlusion 时关闭次级反射，和 UE4 的 shader 条件一致。

在封闭的各向同性、无衰减解析测试里，这给出 `S*(1+rho+rho²)`；更多空间步骤不会凭空继续增加该和。
一般场并非精确物理 Neumann 级数：低阶 SH 的方向查询含负值截断，`T/R` 也因此不严格线性，
还存在空间重投影和几何离散误差。补充材质反射解决了缺少反射机制的问题，并没有证明 LPV 会收敛到 ground truth。

## 数据与运行

- `sample/shaders/occlusion.slang`：静态 ray query、透射覆盖和材质反射 SH 缓存。
- `sample/shaders/propagate.slang`：出射通量积分、逐阶透射/反射与一次累积。
- `sample/shaders/prepare.slang`：第一层从 source 初始化，其他层清零。
- `sample/renderer.py`：front 分层 buffer 与缓存失效；调整反射深度保留注入源与几何缓存。
- `sample/shaders/render.slang`：匹配反射深度的独立 diffuse RT reference。

```bash
./run_local.sh --bounces 3
./run_local.sh --headless --bounces 1 --output sample/output/one.png
./run_local.sh --compare --bounces 3
./run_local.sh --test --backend metal --debug
```

`source` 与 `accumulated` 仍是每格 RGB × 9 SH 的场，最终消费仍通过 SH cosine convolution 得到 irradiance。
反射响应也采用 SH9；front/next 有额外反射阶数维度。静态缓存使传播阶段无需额外 ray query。

## 验证及限制

16 项 CPU/GPU 检查通过。其中新增：

1. 封闭各向同性源，RGB 反射率各异，依次得到 rho、rho²，累积为 `S*(1+rho+rho²)`；后续步不重复累积。
2. 部分遮挡为 0.4 时，透射为 `0.6*S`，反射为 `0.4*rho*S`；黑色反射为零。
3. 实际 RT 平面的缓存覆盖与 albedo、反射方向半球符合独立 CPU SH 公式。
4. 反射深度变化只重建传播；关灯无残留，返回深度 1 与旧图逐值一致。

三次反射对照三次 RT 的画面与误差见 [VALIDATION.md](VALIDATION.md)。
新增反射增加了阴影中的间接光，但全图 RMSE 略升，不能把它包装成普遍质量提升。
这些检查证明特定测试的反射率、预算和阶数正确，没有证明任意 SH 场严格守恒。
粗网格、SH9、有限空间步、每步 decay、细几何覆盖遗漏仍是限制。
本节记录 2026-10-04 的实现，当时只补直接光源引出的 diffuse 材质反射。
2026-10-05 的紧凑默认版已补上 SH 自发光表面注入，通量为 `pi*Le*area`；当前结果见 [COMPACT_LPV.md](COMPACT_LPV.md)。
