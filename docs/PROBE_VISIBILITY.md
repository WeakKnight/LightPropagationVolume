# 距离矩与 Chebyshev 探针可见性

> 当前默认已修正为 RT 引导的紧凑 SH9 光场；本页保留此前核的历史记录，最新分析与低误差验收见 [SH_RADIANCE.md](SH_RADIANCE.md)。

> 当前默认已升级为 GV + 26 邻域 + 时间 refinement；本页为此前核/预设的记录，最新评估见 [SH_REFINEMENT.md](SH_REFINEMENT.md)。

> 2026-10-05 更新：本文的达标数值和方向图消费描述属于 `--transport directional`。当前默认为紧凑 SH LPV，其内存、性能和明显的画质退步见 [COMPACT_LPV.md](COMPACT_LPV.md)。方向模式的累计角度缓存现已按需分配。

2026-10-05，参考本机 `Downloads/RTXGI-DDGI-main/rtxgi-sdk` 的以下源码：

- `shaders/ddgi/ProbeBlendingCS.hlsl`：距离及距离平方的角度过滤、`cos^probeDistanceExponent`、局部距离上限。
- `shaders/ddgi/Irradiance.hlsl`：Chebyshev 权重、三次方压漏光、wrap-normal 权重、弱权重压缩及八探针归一化。
- `shaders/ddgi/include/ProbeOctahedral.hlsl`：球面方向的八面体编码。
- `shaders/ddgi/ProbeClassificationCS.hlsl`：背面命中比例用于排除实体内的探针。

源码位置可从 [NVIDIA RTXGI-DDGI](https://github.com/NVIDIAGameWorks/RTXGI-DDGI/tree/main/rtxgi-sdk/shaders/ddgi) 核对。
这是本项目的独立实现，采用上述算法结构；没有搬入 SDK 或 UE 插件。

## 距离矩图

每个探针生成 512 条固定 Fibonacci 方向的几何射线，仅在场景/网格改变时更新。
距离截断为 `1.5*sqrt(3)*cell_size`，与 SDK 的 `1.5*length(probeSpacing)` 一致。
未命中的方向存上限；背面命中用负距离编码，过滤距离时取绝对值。
超过 25% 的射线命中三角形背面的探针禁用，防止实体内探针参与插值。没有实现 probe relocation。
这依赖闭合物体正确的三角形朝向；开放/双面网格有固有歧义。

对每个 16×16 八面体 texel，以 `max(dot(texel_direction,ray_direction),0)^50` 过滤：

\[
\mu=\frac{\sum_j w_jd_j}{\sum_jw_j},\qquad
m_2=\frac{\sum_jw_jd_j^2}{\sum_jw_j}.
\]

预计算每个 texel 最邻近的 64 条射线及归一化过滤权重，略去 cos^50 的极小尾部。
距离矩存 float2，包含镜像的单 texel 边框，共 18×18 texel/探针；查询手动做四点双线性采样。
这在逻辑上是每探针的八面体 depth/moment map，使用线性 buffer 保存图块，不是相机屏幕 depth buffer。
本实现存完整的均值和二阶矩；SDK 的 0.5 存储/2.0 解码约定不需要照搬。

## 可见性权重

从探针指向待着色点，查询该方向的两个距离矩。令查询距离为 t：

\[
\sigma^2=\max(m_2-\mu^2,0),\qquad
P=\begin{cases}1,&t\le\mu,\\
\dfrac{\sigma^2}{\sigma^2+(t-\mu)^2},&t>\mu.
\end{cases}
\]

这是单侧 Chebyshev/Cantelli 上界，作为近似可见性权重使用，不是精确遮挡证明。
遵循 SDK：使用 `P³` 增强遮挡对比；与 `(wrap²+0.2)` 相乘，保留 0.05 的可见性下限，
对小于 0.2 的权重再连续压缩，然后乘三线性权重，最后归一化八个探针。
方差的微小负数截为零，避免浮点误差产生负概率。

仅用于距离矩查询的点沿几何法线偏移 0.25 cell，另加原有 ray epsilon。
`--visibility-bias` 控制这个偏移；它不改变插值网格坐标、irradiance 查询法线或光照采样位置。
`--read-bias` 仍为 0，是另一个控制插值位置的参数。
矩过滤会让平面距离均值稍大；薄墙情况下，过小的可见性偏移容易把墙另一侧误判为可见。
默认偏移通过 1 mm 薄墙回归检查，不根据 reference 图像拟合。

最终着色和次级材质反射共用 `sample/shaders/probe_visibility.slang`。
在默认 moments 路径，这两处不再发射八邻域的短可见性射线。
相机首命中、太阳阴影、光照注入及静态几何缓存仍使用 RT。

## 同时缓存 irradiance

仅换 Chebyshev 查询仍会在每个消费点循环积分 1154 个方向，并且软可见性会让更多探针参与。
本机初测“距离矩 + 现场方向积分”略慢，因此按 DDGI 的消费结构进一步缓存 irradiance：

- 每个材质阶数完成后，把方向 radiance 余弦积分成一个法线方向图。
- 当前阶 irradiance 图供下一次反射查询；另一个图只累加各材质阶数一次，供最终着色。
- 最终/次级查询只读取距离矩和法线方向的 irradiance，再做八探针加权。

irradiance 为线性 RGB float4，采用 17×17 八面体**顶点**采样，包含边框共 19×19。
17 是奇数且包含边界与中心，六个坐标轴法线都精确落在采样点上。
这防止普通偶数、texel-center 图在法线插值时把略倾斜法线的辐照度混入平面法线，产生错误自照明。
其他法线做双线性近似；均匀 radiance 保持 `E=pi*L`。
这部分改变消费/反射的缓存表示，保留 1154 方向的空气输运和材质阶数分解，未改为 SH 空间扩散。

32³ 默认新增距离矩约 85 MB、距离射线 scratch 约 67 MB、两张 irradiance 图合计约 379 MB。
总计约增加 0.53 GB；主体角度缓存仍约 3.8 GB，另有 SH 诊断及运行库。
距离矩不因相机、光源、反射次数、角度密度或可见性偏移改变而重建。
irradiance 图随光场更新；移动相机时直接复用。

## 运行与对照

```bash
./run_local.sh                              # moments 默认路径
./run_local.sh --probe-visibility ray       # 旧的精确短射线 + 现场角度积分
./run_local.sh --probe-visibility none      # 无过滤的诊断对照
./run_local.sh --visibility-bias .25
./run_local.sh --benchmark-visibility
./run_local.sh --accuracy --extra-cases
./run_local.sh --test --backend metal --debug
```

源码：`sample/probe_visibility.py`、`sample/shaders/probe_distance.slang`、`probe_visibility.slang`，
以及 `directional.slang` 的 `irradiance_main`。
经典 `--transport sh` 保留原有路径；此修改适用于默认 directional 体积。

## 验证结果

29 项 CPU/GPU 检查通过，包括：Chebyshev 的解析概率、两个距离矩/方差、八面体接缝、
六个轴向法线的 irradiance 与原角度积分一致、均匀 radiance、平面无自照明/反弹、
1 mm 薄墙两侧明暗探针、黑色吸收、关灯、材质反射序列和模式切换。
薄墙测试人为设置墙下所有探针亮、墙上探针黑；无过滤时明显漏光，ray 对照为零，moments 最大线性 RGB 小于 0.001。
这不是任意薄几何和任意探针布局下的零漏光保证。

性能用 640×480 / 1 spp / 32³ / 1154 方向 / 三个材质阶数，编译与预热后测试：

| 项目 | 原 ray + 现场积分 | moments + 缓存 irradiance |
| --- | ---: | ---: |
| 稳定离屏 render，中位数 11 次 | 9.06 ms | 0.62 ms |
| 次级反射/传播更新，中位数 5 次 | 2.37 s | 1.46 s |

计时用 CPU wall clock 加 `device.wait()`，包括 render、累积、tone mapping；不含窗口/UI/呈现。
更新测试在 40/41 空间步间切换，保持几何、距离矩和直接光源缓存；没有同时运行其他 GPU 任务。
14.6 倍的消费收益来自**距离矩查询加缓存 irradiance 的整套替换**，不能归因于单独的 Chebyshev 算术。
性能报告见 [probe-visibility-benchmark.json](probe-visibility-benchmark.json)。

质量保持原来的完整 640×480 线性 RGB 口径，LPV 2048 spp / RT 16384 spp：

| 案例 | 旧 ray + 现场积分 | 新 moments + 缓存 irradiance |
| --- | ---: | ---: |
| 默认视角 | 1.79% | 2.54% |
| 侧视角 | 2.30% | 3.30% |
| 换太阳方向 | 1.73% | 2.15% |

三个案例仍低于 5%，但新近似的误差比精确 ray 消费大。reference 数组前后逐值一致。
完整报告见 [accuracy-report.json](accuracy-report.json)。

![旧 ray / 新 moments / independent RT](probe-visibility-comparison.png)

固定红墙/后墙/天花板的低频残差标准差，由 1.14% / 1.85% / 1.12%
变为 1.17% / 1.89% / 1.13%，维持前一轮角度抗混叠后的平滑度，略有误差增加。
测量口径与完整数据见 [probe-visibility-wall-report.json](probe-visibility-wall-report.json)。
窗口三帧呈现检查通过。

有限角度矩图、irradiance 插值、表面偏移及软权重会带来近似误差。
Chebyshev 是上界，全部候选探针都被遮挡时的归一化 fallback 仍可能漏光。
保留 ray 模式可用于定位难处理的几何；当前结果不证明所有场景都满足 5% 或严格不漏光。
