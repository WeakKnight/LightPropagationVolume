# Light Propagation Volume / SlangPy

一个用于学习漫反射 GI 的 SlangPy 应用：RT 捕获表面光照，注入网格，在体积中传播，并在新的表面计算材质反射。
默认采用**RT 引导的紧凑 SH9 光场**：捕获探针处的 radiance，在实际表面计算下一阶反射，保留 geometry volume、26 邻域预测和时间 refinement。
`sh_ue4` 保留此前的增量 SH26 核，`sh_legacy` 为更早六邻域核，`directional` 为高内存方向对照。

![旧增量 SH26 / 新 SH9 / RT](docs/sh-radiance-comparison.png)

32³ / 三阶材质反射的 GI buffer 数据量约 **214 MB**，方向扩展约 **3.72 GB**。
同一批 640×480、2048 spp / RT 16384 spp 的完整线性 RGB 对照，默认、侧视角和换太阳的误差为
**2.64% / 3.42% / 2.26%**，均通过原来的 5% 门槛；此前增量核为 30.41% / 39.62% / 28.04%。
准确光场仅切换 SH9 消费的诊断为 2.57% / 3.32% / 2.25%，说明此前的大误差主要来自光场生成和传播。
误差口径是 `sqrt(mean((LPV-RT)²))/mean(RT)`，完整未曝光线性 RGB、不裁剪、不拟合增益；不是每像素或所有场景的误差界。

动态捕获默认按 **4096 probes/frame** 分帧。同轮预热测试中，完整重建约 **111 ms**；
分帧捕获期间每帧约 **10.89 ms**、峰值 **11.94 ms**，完整三阶目标需要 **24 帧**。
太阳强度阶跃测试约 **32 帧**完成 90% 光场变化（按 60 FPS 换算约 0.53 秒）。
GI buffer 仍为约 214 MB，三个稳态精度案例重新通过。预算减少峰值，以响应延迟为代价，首次准备不在此预算内。
调度、动态响应和同轮性能见 [docs/DYNAMIC_UPDATE.md](docs/DYNAMIC_UPDATE.md)。
批量渲染先完成捕获和历史收敛，再累积像素样本。
根因、UE5 源码对照、实现公式、消融和限制见 [docs/SH_RADIANCE.md](docs/SH_RADIANCE.md)。

UE5 的实际 GI 方案是 Lumen，旧 LPV 已被替代。本项目参考其 ray capture、紧凑场和表面反馈思路，
是保留 LPV 空间结构的混合方案；没有复刻完整 Lumen 或宣称旧 UE4 LPV 达到同样精度。

## 运行

```bash
cd /Users/litianyu/Documents/GitHub/LightPropagationVolume
./run_local.sh
```

脚本优先使用项目 `.venv`，没有时复用旁边 `DSHARC/sample/.venv` 和本地 `slangpy`。
本机为 Python 3.13.12 / SlangPy 0.43.0 本地构建 / Metal；没有新增依赖或编译 Unreal。
`LPV_PYTHON`、`SLANGPY_LOCAL_DIR` 可指定现有环境。

Windows / Linux 的独立环境：

```bash
python -m venv .venv
# 激活环境后：
python -m pip install -r requirements.txt
python sample/entry_point.py --backend d3d12  # Windows
python sample/entry_point.py --backend vulkan # Linux
```

需要支持 acceleration structure / inline ray query 的 GPU runtime。本机只验证 Metal。
默认不分配逐方向 radiance 或 irradiance 图；距离矩与保留的几何射线 scratch 占约 152 MB，其余 GI buffer 约 61 MB。
以上是 buffer 数据量，不包括图像、场景、CPU 数组和运行库，不能当作进程总内存。
方向模式可用 `--angular-resolution 3` 降至 290 方向以节省内存。

## 观察与交互

| 模式 | 含义 |
| --- | --- |
| LPV | 直接光 + 体积间接漫反射 |
| DIRECT | 只显示直接光 |
| INDIRECT | 只显示体积间接光；背景仍保留 |
| REFERENCE | 独立 RT 路径积分，间接材质深度与 LPV 相同 |
| INJECTION | 首阶 radiance 场的 SH9 方向平均值切片；SH 模式为注入源 |
| VOLUME | 完成传播的场的 SH9 方向平均值切片 |

`Propagation steps` 在默认 SH 中是目标重建时的 26 邻域预测修正次数（默认 3）；方向模式的首阶由完整 RT 完成，后续材质阶数使用这个预算。
`Indirect material bounces` 是材质反射深度，默认 3；设为 1 可以观察只有直接照明表面贡献的间接光。
`Angular resolution` 控制方向格密度，`Source angular samples` 控制首段角度范围采样数（默认 8）。
右键看向，WASD 移动，Q/E 上下，Shift 加速；R 重建，F2 保存。
`RT probes / frame` 控制捕获预算，0 为整场重建；窗口显示阶数和探针进度。
相机只重置图像，光源变化分帧重建 RT 目标、复用 GV 和距离矩并保留光场历史；曝光保留线性图像历史。

默认是有红绿墙、蓝色物体、屋顶开口和厚盒体的静态房间。
`--room dsharc` 可加载大房间，`--scene file.glb` 可导入小场景。
只处理漫反射材质因子/顶点色，没有贴图、透明、镜面、动画或动态几何重建。
两条路径均支持自发光表面注入。

## 批量渲染与验收

```bash
./run_local.sh --headless --frames 16 --spp 32 --output sample/output/lpv.png
./run_local.sh --headless --mode reference --frames 256 --spp 64 --output sample/output/reference.png
./run_local.sh --compare
./run_local.sh --accuracy --extra-cases
./run_local.sh --test --backend metal --debug
./run_local.sh --test-download
./run_local.sh --benchmark
./run_local.sh --benchmark-visibility
./run_local.sh --benchmark-volume
./run_local.sh --benchmark-dynamic
./run_local.sh --evaluate-sh
```

`--accuracy` 是固定的完整 RGB 5% 验收，默认 LPV 2048 spp、RT 16384 spp；当前默认三组通过；超出门槛仍返回非零。
`--accuracy --transport directional --extra-cases` 运行高精度方向对照。
`--extra-cases` 同时检查侧视角与不同光源；任一超标返回非零。
报告含独立 reference 采样噪声估计，当前三个案例为 0.56% / 0.72% / 0.48%。
`--compare` 生成直接光、单次间接反射、三次间接反射和匹配深度 RT 的观察图。
`--benchmark` 保留之前经典 SH 缓存失效策略的耗时比较。

`--linear-output file.npy` 保存未曝光的 RGB，PNG 使用曝光、filmic 曲线与 sRGB，不用于数值验收。
`--volume-output file.npz` 保存 SH9 源场/累积场，形状 `(z,y,x,RGB,9)`，以及 origin、cell size、transport 和 bounces。
SH 模式直接用 SH 余弦卷积求 irradiance；方向模式的 SH 是诊断投影，消费缓存 irradiance 图。
`--probe-visibility ray` 在两条路径中都启用精确短射线过滤；方向模式还会分配累计方向数组用于现场积分。
Python 的 `renderer.angular_data()` 可读取方向模式的 `(z,y,x,direction,RGB)`；请求累计数组会按需分配并重建一次光场。

| 参数 | 默认 | 含义 |
| --- | --- | --- |
| `--transport` | sh | RT 引导 SH9；`sh_ue4` 旧增量核；`sh_legacy` 六邻域；`directional` 方向对照 |
| `--grid` | 32 | 网格分辨率，8..64 |
| `--angular-resolution` | 5 | 1..5 对应 26 / 98 / 290 / 578 / 1154 个方向 |
| `--source-angular-samples` | 8 | 首段方向 bin 的子样本数，1..32；1 为中心射线 |
| `--steps` | SH/旧增量核 3 / 其他 40 | 默认 SH 是目标重建时的预测修正次数，旧增量核是每帧传播次数 |
| `--probes-per-frame` | 4096 | 每帧捕获探针任务预算，跨材质阶数共享；0 为完整重建 |
| `--probe-rays` | 1024 | 默认 SH 每探针的 radiance 捕获方向数，偶数 32..4096 |
| `--bounces` | 3 | 间接材质反射深度，1..8，也控制 RT reference |
| `--decay` | SH/directional 1 / legacy 0.95 | SH 为每步阻尼，方向模式为按距离衰减 |
| `--history-weight / --propagation-weight` | 0.9 / 0.008 | SH 历史权重和有 RT anchor 的邻格预测权重 |
| `--secondary-occlusion / --secondary-bounce` | 1 / 1 | GV 预测遮挡和实际表面反射强度 |
| `--no-refinement` | 关闭 | 禁用 SH 时间历史，使用同一物理目标场 |
| `--probe-visibility` | moments | 两条路径共用距离矩过滤；ray 精确射线，none 无过滤 |
| `--visibility-bias` | 0.25 | 距离矩查询点的法线偏移（cell），不改变插值坐标 |
| `--read-bias` | 0 | 最终查询沿法线的偏移，单位为 cell |
| `--indirect-strength` | 1 | 间接光显示强度；验收固定为 1 |
| `--sun-azimuth / --sun-elevation` | -50 / 55 | 理想方向光朝向，角度制 |
| `--sun-intensity` | 3 | 垂直平面接收的辐照度 |
| `--width / --height` | 960 / 640 | 图像分辨率 |
| `--frames / --spp` | 64 / 1 | 批量帧数 / 每帧像素样本数 |

方向模式必须有表面边界，`--no-occlusion` 只用于经典 SH 的消融。
默认 SH 的 `--source-samples` 控制 GV 几何样本数，`--probe-rays` 控制光照捕获；`--injection-bias` 仅用于旧表面 flux 核。
提高 grid 不保证更准确；旧增量核 `sh_ue4` 的历史/传播参数必须配套，不能直接套用六邻域核的 40 步。

## 两条传播路径

方向对照路径从探针沿每个方向 bin 内的 8 条完整射线计算首阶 `Lo = Le + rho*E_direct/pi` 的角度平均，直接得到首阶场。
后续材质阶数缓存局部表面边界条件并通过网格求解。
在空气中沿同一方向向上游 cell 取值，保留 radiance；只在材质表面把上一阶照明乘 albedo 后重新发射。
每阶求解完成后累加一次，不累加各轮稳态求解的估计。每阶的 irradiance 方向图用于消费与下一次反射，距离矩/Chebyshev 权重降低穿墙取样的贡献。
角度权重、几何和材质数据与相机无关，没有读取 RT reference 的间接光结果。

默认 SH 用真实表面积构建顶点 GV；每个探针 RT 捕获首阶 radiance 并投影 SH9。
后续阶在实际命中处读取上一阶 SH irradiance，乘 albedo/pi 后重新投影。
26 邻域在 GV 约束下预测方向 radiance，以捕获场为 anchor；degree-7 Lebedev 权重保持均匀 SH9 场。
时间历史独立跟踪最终目标，避免旧历史衰减改变稳态空间传光距离。
最终用距离矩加权混合 SH 并做余弦卷积。公式和 UE5 的对应范围见 [docs/SH_RADIANCE.md](docs/SH_RADIANCE.md)。
旧增量核的记录见 [docs/SH_REFINEMENT.md](docs/SH_REFINEMENT.md)，六邻域核见 [docs/COMPACT_LPV.md](docs/COMPACT_LPV.md)。
复现旧默认配置：

```bash
./run_local.sh --transport sh_legacy --grid 16 --steps 24 --decay .95 --read-bias .25
```

| 文件 | 职责 |
| --- | --- |
| `sample/sh_radiance.py` | 默认 SH 的 RT 目标缓存、材质递归与历史调度 |
| `sample/shaders/radiance_capture / radiance_predict_26 / radiance_resolve.slang` | 默认捕获、26 邻域预测与历史更新 |
| `sample/sh_refinement.py` | 共用 GV 构建/收敛检查与旧增量核调度 |
| `sample/shaders/build_geometry / propagate_26 / refine_*.slang` | GV 构建与旧增量 SH26 核 |
| `sample/directional_volume.py` | 方向格、资源/边界/光源缓存与逐阶调度 |
| `sample/shaders/directional.slang` | 边界捕获、实际表面注入、方向平流与阶数累积 |
| `sample/shaders/render_directional.slang` | 从角度 radiance 计算接收面 irradiance |
| `sample/shaders/render.slang` | 共同的相机/直接光、独立 RT reference 与经典 SH 查询 |
| `sample/shaders/lpv.slang` | 共同的网格、方向光与诊断 SH |
| `sample/shaders/inject / propagate / occlusion.slang` | 经典 SH 注入、传播与反射缓存 |
| `sample/renderer.py` | SlangPy 调度、模式切换和图像历史 |
| `scripts/validate_accuracy.py` | 固定误差口径的三案例 5% 验收 |
| `scripts/measure_wall_artifacts.py` | 固定平面区域的空间误差起伏测量 |
| `sample/probe_visibility.py` / `sample/shaders/probe_visibility.slang` | 八面体距离矩缓存及共用 Chebyshev 查询 |
| `scripts/benchmark_probe_visibility.py` | 方向模式的短射线/现场积分与矩图/irradiance 缓存计时 |
| `scripts/benchmark_volume.py` | SH/方向模式的实际 buffer 大小和同步耗时 |
| `tests/test_lpv.py` | CPU 与实际 GPU 的能量、反射、可见性和缓存检查 |

## UE4 参考与最小下载

已通过用户登录的 Safari 阅读 4.27 分支的 `LPVCommon.ush`、`LPVWriteCommon.ush`、`LPVPropagate.usf`、
`LPVInject_GenerateVplLists.usf`、`LPVFinalPass.ush`，并核对了 `LPVGeometryVolumeCommon.ush`、
`LPVBuildGeometryVolume.usf` 与 renderer 的 `.cpp/.h`。
此前增量核参考 UE4 的 SH9、26 邻域、时间 refinement 和 geometry volume。
默认已改为 RT 引导的混合光场，详见 [SH_RADIANCE.md](docs/SH_RADIANCE.md)。保留旧核作对照，没有复制引擎代码或 packing/LDS 优化。

```bash
./scripts/checkout_ue4_lpv.sh
```

脚本使用 **depth 1 + single branch + blob:none + no-checkout + non-cone sparse checkout**，
只检出 LPV shader、renderer 和对应 plugin source。不会调用 Setup.sh、下载引擎依赖或构建 Editor。
浅克隆仍需要分支提交和目录树的元数据；sparse checkout 本身不能保证零元数据开销。
私人引擎参考默认放在被 Git 忽略的 `references/UnrealEngine-4.27`。

当前 CLI Git 没有可用的 Epic 仓库凭据，脚本实际尝试在认证阶段失败，**未成功克隆引擎**。
浏览器的登录允许页面阅读，但不自动为 Git HTTPS 提供认证。配置好现有 Git 凭据后可直接重新运行上述脚本。

ZIP 下载使用 `scripts/download_ue4.py`；当前整包尚未成功下载，不能将已有 `.part` 当作引擎源码。
GitHub codeload 的临时授权来自已登录浏览器的正常下载流程，只通过 stdin 传入，不保存到项目或日志：

```bash
# 以交互输入方式粘贴浏览器取得的临时 codeload 地址，再按 Enter：
python scripts/download_ue4.py
# 已有完整 ZIP 时，只提取 LPV 文件：
python scripts/download_ue4.py --extract-only --target /absolute/path/UnrealEngine-4.27.zip
```

下载器用 HTTP/1.1、超时、有限重试与默认 4 MiB/s 上限；先尝试 Range。
**codeload 当前忽略 Range**，这种情况下会重读并核对已保存前缀，再只追加新字节，避免普通重试截断进度。
这只能保住磁盘进度，不能免除重复传输的带宽。ETag 或前缀不一致时停止，避免混入不同版本。
认证过期会保留 `.part` 并停止，需要新的浏览器下载授权继续。
完成后要求 ZIP 中央目录可读，记录 SHA256，只提取 LPV 文件并校验这些文件的 CRC；不解压整个引擎。
ZIP 与私有参考文件全部保留在 Git 忽略的 `references/` 中。

参考：

- [Kaplanyan / Dachsbacher, I3D 2010 LPV paper](https://cg.ivd.kit.edu/publications/p2010/CLPVFRII_Kaplanyan_2010/CLPVFRII_Kaplanyan_2010.pdf)
- [Andreas Kirsch 的 LPV 公式与立体角校正](https://data.blog.blackhc.net/2010/07/lpv-annotations.pdf)
- [UE4.27 LPV documentation](https://dev.epicgames.com/documentation/en-us/unreal-engine/light-propagation-volumes?application_version=4.27)
- [UE4 LPVCommon.ush](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Shaders/Private/LPVCommon.ush)
- [UE4 LPVPropagate.usf](https://github.com/EpicGames/UnrealEngine/blob/4.27/Engine/Shaders/Private/LPVPropagate.usf)
- [DSHARC](https://github.com/WeakKnight/DSHARC)：本地 `590c281` 的 scene/camera、inline ray query、accumulator、tonemap 与窗口代码作为起点。

验证覆盖面积权重、Lambert 能量、RT 阴影、cell-link 遮挡、各向同性传播能量、SH 方向/卷积、间接光贡献、
相机与光照重建、确定性 reset、黑色吸收、部分反射通量预算、材质反射序列及 PNG/线性数组导出。本机结果见 [docs/VALIDATION.md](docs/VALIDATION.md)。
