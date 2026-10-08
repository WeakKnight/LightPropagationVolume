# 墙面低频暗斑：定位与修复

> 当前默认已修正为 RT 引导的紧凑 SH9 光场；本页保留此前核的历史记录，最新分析与低误差验收见 [SH_RADIANCE.md](SH_RADIANCE.md)。

> 当前默认已升级为 GV + 26 邻域 + 时间 refinement；本页为此前核/预设的记录，最新评估见 [SH_REFINEMENT.md](SH_REFINEMENT.md)。

> 2026-10-05 更新：本文的达标数值和方向图消费描述属于 `--transport directional`。当前默认为紧凑 SH LPV，其内存、性能和明显的画质退步见 [COMPACT_LPV.md](COMPACT_LPV.md)。方向模式的累计角度缓存现已按需分配。

## 现象与原因

此前默认场虽然通过全图 5% NRMSE 验收，墙面和天花板仍有稳定的暗条纹。
固定角度集合的每个方向只取中心射线；射线遇到开口、受光区域或几何边缘时，贡献会在亮值与零之间切换。
这些不连续的方向覆盖经过网格插值后形成大块明暗起伏。提高最终图像 spp 只能收敛像素采样，不能消除这类稳定的光场混叠。

离散方向输运中的这种现象称为 ray effects；相关研究将首段自由传播单独处理，以减少方向离散的伪影。
见 [Christensen 等，JCP 2024](https://doi.org/10.1016/j.jcp.2024.113049)。
本文的修复只借鉴这一分解思想，没有复现该论文的守恒 surface-current 方法，也没有因此获得严格通量守恒证明。

本项目的定位证据是：保留网格、角度数量、反射深度、插值、读取偏移及间接强度，仅改变首段角度覆盖后，暗斑显著减少。
单独改变 read bias 无法达到相同效果。仍可能有有限网格/方向误差；这不是对所有暗斑成因的排除证明。

## 修复方式

在每个探针计算来自直接照明表面的首阶 radiance，将方向 bin 的中心值改为球面角度范围内的平均：

\[
\bar L_{0,i}(x)\approx \frac1S\sum_{k=1}^{S}
\left[L_e(y_k)+\frac{\rho(y_k)}\pi E_{\rm direct}(y_k)\right],
\qquad y_k=\operatorname{firstHit}(x,-\omega_{i,k}).
\]

未命中的射线贡献零。方向子样本取自实际球面 Voronoi bin 内的确定性 Fibonacci 点集，按 z 分层，反向 bin 使用镜像样本。
默认 S=8，S=1 使用方向中心以便观察消融；子样本生成不读取场景光照或参考图。
每个子样本独立读取命中表面的 albedo、emission、法线和太阳可见性。

首段由完整 RT 射线完成，结果直接作为已求解的第一个材质阶数，**不再沿网格重复传播或重复累加它**。
后续两个材质阶数仍通过原有表面反射与保留方向的网格传播求解。
因此这是目前方向体积方案的混合首段处理，不是传统 SH LPV 原封不动的实现，也不是每像素路径追踪。
最终消费仍为可见性过滤的八邻域线性插值与角度余弦积分，read bias 仍为 0。
没有修改相机着色 shader，也没有对输出画面执行平滑滤镜。

源码位于 `sample/directional_volume.py` 的 `angular_footprints/update` 和 `sample/shaders/directional.slang` 的 `direct_main/copy_direct_main`。
首阶缓存依赖场景/网格/方向格、光源、子样本数量和 decay；相机、读取偏移、反射深度及空间步数不会重建它。
非默认 decay 按射线距离/cell size 衰减，与后续方向传播的距离衰减一致。
`--steps 0` 仍可得到已经 RT 完成的第一阶，后续阶数为零。

代价是首次建立或换光源时的更多 RT 查询，默认约 3.03 亿条首段射线，另有命中后的太阳阴影查询。
角度子样本表约 148 KB；主体角度缓存仍约 3.8 GB，没有增加一整套方向场。
移动相机复用首阶和几何缓存。当前确定性采样不需要时间累积，也没有运动历史拖影。

## 数值与观察

640×480，LPV 2048 spp，独立 RT 16384 spp，32³ / 1154 方向 / 三个间接材质阶数。
使用同一参考图；前后 reference 数组逐值一致。没有修改曝光或拟合强度。

| 案例 | 修复前全图 NRMSE | 修复后全图 NRMSE |
| --- | ---: | ---: |
| 默认视角 | 2.76% | 1.79% |
| 侧视角 | 3.86% | 2.30% |
| 换太阳方向 | 2.35% | 1.73% |

![修复前 / 修复后；相同曝光](wall-comparison.png)

为了衡量墙面起伏而不是只看全图平均，另取默认视角三个固定、避开几何边缘的平面区域。
亮度定义为 RGB 平均，误差为 LPV minus RT。仅在**测量误差**时做固定 9×9 box 平均，
再计算空间标准差并除以该区域参考亮度平均值；这会压低参考 Monte Carlo 噪声，不改变渲染图像。
真实 GI 渐变保留在 reference 中，衡量的是偏离该渐变的空间起伏。
这些区域指标只用于诊断，不替代完整 RGB 验收。

| 区域 | 修复前残差标准差 | 修复后残差标准差 | 减少 |
| --- | ---: | ---: | ---: |
| 红墙 | 4.62% | 1.14% | 75.3% |
| 后墙 | 5.73% | 1.85% | 67.8% |
| 天花板 | 3.05% | 1.12% | 63.1% |

完整坐标和未滤波区域 RMSE/平均偏差见 [wall-artifacts-report.json](wall-artifacts-report.json)。
计算脚本是 `scripts/measure_wall_artifacts.py`，原始线性数组保留在本机 `sample/output/accuracy-final`（前）与 `accuracy-footprints`（后）。

单独控制当前实现的首段子样本数，512 spp、同一 16384 spp RT：S=1 为 2.77%，S=4 为 1.86%，S=8 为 1.83%。
这一消融的两个版本都使用完整首段 RT，因而 S=1 与历史网格首段版本并非逐值相同。
消融记录见 [wall-footprint-ablation.json](wall-footprint-ablation.json)。
8 样本进一步减少残留起伏，故选作默认；32 样本未作为必需优化加入。

```bash
./run_local.sh
# 比较方向中心采样与角度范围平均：
./run_local.sh --source-angular-samples 1
./run_local.sh --source-angular-samples 8
./run_local.sh --accuracy --extra-cases
./run_local.sh --test --backend metal --debug
```

24 项 CPU/GPU 检查通过，新增验证子方向落在所属 bin 内、单位化和反向对称，
首阶平面反射 radiance 保持正确，跨黑色/自发光边缘的方向 bin 得到部分覆盖平均，样本数/decay 改变只更新光源缓存；已有黑色吸收、关灯、空气方向保持及材质反射序列检查仍通过。
窗口三帧呈现通过。剩余的粗网格误差、次级反射方向混叠及有限角度积分误差没有被这次修复完全消除。
