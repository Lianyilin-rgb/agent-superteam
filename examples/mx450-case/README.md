# 真实案例：MX450 移动 GPU 算力提升决策

> 完整的、未删改的实战记录。**包含团队真实吵架的过程。**
> 这是本仓库最有价值的部分——因为它是真的，包括错误。

---

## 一、项目背景

**目标**：提升联想昭阳 E4-ITL（机型 82F6）上 NVIDIA MX450（TU117, CC 7.5）的可用算力。

**硬约束**：绝不物理拆机。

**为什么用这个框架**：主 Agent 已经独自折腾了 2.5 小时，期间：
- 犯了 3 次"假设当事实"的错误（每次都被自己的后续实验推翻）
- 导致 2 次启动失败（1 次 KERNEL PANIC、1 次 initramfs shell）
- 让用户 2 次长按电源键强制断电

**主 Agent 已经陷入"自己验证自己"的循环，需要外部否证。**

---

## 二、Context Brief（节选）

### 已确证的事实

| 事实 | 测量方法 | 数值 |
|---|---|---|
| 空闲时钟 | `nvidia-smi --query-gpu=clocks.sm,pstate` | **1395 MHz / P0** |
| 负载时钟 | fp32 GEMM 2048²，0.4s 采样 × 15s 稳态 | **1290–1346 MHz** |
| Max Clocks | `nvidia-smi -q -d CLOCK` | 2100 MHz |
| 支持时钟档位 | `--query-supported-clocks` | **388 档，最高 2100** |
| 功率限制 | `-q -d POWER` | **5001.00 W**（哨兵值） |
| 功率读数 | 同上 | **N/A** |
| 温度阈值 | `-q -d TEMPERATURE` | **Target Temp = N/A** |
| 热降频 | 控温 A/B（63°C 同起点） | 65→77°C 时 clk 1320→1290 |
| throttle reasons | `clocks_throttle_reasons.active` | 偶发 **0x20**（SwThermalSlowdown） |

### 已排除的路径

| 路径 | 否证方法 | 结果 |
|---|---|---|
| **ACPI 改 TGPV** | 运行时把 `Name(TGPV, 0x2710)` 改成 `0x3A98`（+50%），`acpi_call` 读回确认生效，再做**控温 A/B 交替 4 轮** | **-0.09%**，驱动不读该值 |
| **ACPI 改 CTGP** | `0→1`，观察 `power.limit` | 仍 5001，无效 |
| **刷 BIOS 改 VBIOS** | 调研 Turing Falcon 签名验证 | 拒绝未签名固件 → 变砖 |

### 未知

- 实际功耗（无功率计）
- 是否支持 Dynamic Boost（未查 `/proc/driver/nvidia/gpus/*/power`）
- CPU 负载对 GPU 时钟的影响（未做对照）
- 空闲 1395 是否真是实时值（未用 `clock64()` 交叉验证）

### 已知隐患（非技术因素）

- ZFS 池曾有 `SUSPENDED`、5 个永久元数据错误
- **`systemctl reboot` 会卡在卸载该池**（实测 15 分钟）
- 历史：2 次启动失败、2 次强制断电

---

## 三、第 1 轮：三专家并行（关键发现）

### 专家 A（EC/PMU 方向）

**核心结论**：
- TGP 由 VBIOS 定义，GPU PMU 取"最保守策略"
- **NVPCF/cTGP 通道只在 Ampere+ 且 SBIOS 支持 Dynamic Boost 时成立** → Turing 不适用
- 没找到任何 MX450 改 TGP 成功的案例
- `Average Power Draw = N/A` 在 Turing 上是**官方预期**（文档：仅支持 Ampere+）
- EC 风扇收益仅 0–3%，且 NBFC 官方要求"取出电池"才安全（与不拆机冲突）

**红队后来指出**：结论对，但**论据错** —— Dynamic Boost 实际是 Turing RTX 20 Super 首发，
不是 Ampere+。

### 专家 B（VBIOS 方向）

**核心结论**：
- Vix Editor Linux loader 需**开源模块 + 615.x + GSP 启用**，三条全不满足 → 不可行
- `EnableGpuFirmware=0x11` = "GSP 开 + 允许回退"（**纠正了主 Agent 的错误假设**）
- **★★ VBIOS dump 偏移 4 字节**：option ROM 校验和 `shift=0` 时 sum=0x8F（非法），
  `shift=4` 时 sum=0x00（合法），且 PCIR 指针精确指向 0x170
- **BIT 记录格式是 6 字节** `{u8 id; u8 ver; u16 len; u16 off}`，17 条完美连续
- nouveau 官方解析器在这份 ROM 上也读不到功率表

**红队后来指出**：`shift=4` 的发现**是本轮最扎实的技术发现**，
但它推出的"移动版 VBIOS 无功率墙"结论**近乎零证据**
（用未修正偏移的指针链解析失败是预期结果，不是证据）。

### 专家 C（性能调优方向）

**核心结论**：
- MX450 四档：`12W(720–930)` / `25W(1395–1575)` / `28.5W(1395–1575)`
- **同芯片对照**：ThinkBook 15 2021（Lenovo, GDDR6 MX450）15W / 69°C 稳 **1575 MHz**
  → 我们 1290–1320 @ 65–77°C **低 16–18% 且温度更低**
- HP Pavilion 14（同 CPU+GPU）：温度 >75°C 从 20W+ 掉到 12W，**所有 Windows 工具无效**
- 收益上限：A(0~3%) / B(冷却+时钟 +8~12%) / D(功耗配额 +17~22%，无先例)
- 建议 NVML VF offset 路线

**红队后来指出**：
- `1575 上限`与实测 `SUPPORTED_CLOCKS` 有 2100 表面矛盾（C 应说明"表里有 ≠ 可达"）
- `冷却 +8~12%` 与主 Agent 实测 0.19%/°C（12°C 只换 2%）**直接冲突 → C 错**
- "ThinkBook 对照"证据强度**弱**（若是游戏实测则不可比，游戏常吃不装满功耗墙）

---

## 四、第 2 轮：红队审核（最有价值的一轮）

### 红队指出的委托人（主 Agent）错误

**★★★ 错误 1：`-lgc 2100` 这个实验按设计就不可能有结果**

```
如果 -lgc 单值语义是"只设 max"，
而实际运行频率本来（1320）就低于 2100，
那这个设置是恒等 no-op —— 看不到变化是必然的，不是"驱动不支持"。

正确做法：用 -lgc 300,900（如果真锁住，频率必须掉到 ≤900）
```

**这个错误主 Agent 自己完全没看出来。**

**★★★ 错误 2：`5001 W` 与显存时钟 `5001 MHz` 数值完全相同，但未验证**

```
高度疑似 VBIOS 解析偏移错位读到了显存字段。
但这是假设，不是结论 —— 必须用"修正偏移后重解析"来验证。
```

### 红队指出的专家错误

| 专家 | 错误 | 影响 |
|---|---|---|
| A | 结论对但论据错（Dynamic Boost 起源） | 会导出"Turing 永远不可能"的过强结论 |
| B | `shift=4` 发现后推出"无功率墙"，证据近乎为零 | 可能误导放弃 VBIOS 路线 |
| B | 建议 `OverrideMaxPerf=0x1` 无出处 | 未文档化键会被静默忽略 → 伪阴性 |
| C | "冷却 +8~12%" 与实测冲突 | 会误导资源投入方向 |

### ★★★ 三方共同遗漏的路径（红队最大贡献）

1. **`nvidia-powerd` / `/proc/driver/nvidia/gpus/*/power`**
   —— Linux 上"笔记本 GPU 卡在 base TGP"是已知系统性问题，标准成因是 powerd 未运行
2. **`-q -d POWER_PROFILES`**（新驱动可直接读 base/boost 功率档）
3. **适配器识别可能影响功率预算**（换 90W 适配器复测，成本极低）
4. **整机功率预算共享**（RAPL 读 CPU 封装功耗 + CPU 压到最低对照）
5. **`clock64()` 微基准量真实 SM 时钟**（一次钉死"读数是否可信"）
6. **VBIOS 镜像清洁度与完整性**（剥 4 字节前缀后重跑解析）

### 红队最可能的真相判断

> **这是一堵"配置性低功率墙"，不是故障，也不是单纯的热限。**
>
> 最硬的单一证据：**负载时钟（1290–1346）低于本 SKU 的 base（1395）**
> —— 行业语义里"低于 base 运行" = 平台没有兑现额定 TGP。
> 等效持续预算大约落在 **13–18W 量级**（估算，需功率计落实）。
>
> 软件侧抬墙概率很低；当前预算内提效（VF offset 降压）是唯一便宜的正期望动作。

---

## 五、激进派 vs 稳健派（真实对立）

### 激进派

**病理诊断**：
- `Target Temp = N/A` → **驱动的温度约束表根本没加载**
- `NVreg_EnableGpuFirmware=0` 在 Turing 上有已知 bug
  （`open-gpu-kernel-modules#1083`：OEM thermal 设置被忽略、Target Temp 被压到 76–80°C）
- **这就是我们 65°C 就开始降频的原因**（正常 Turing 87–95°C 才降）

**预测**：2.20–2.60 TFLOPS（**+6~25%**），置信度 70%

**三件事**：① 诊断（`-lgc 300,900` 判别）② 开 GSP 修 Target Temp ③ NVML 降压

### 稳健派

**★★ 挖到两条所有专家都漏掉的情报**：

**情报 1：USB 桥的 UAS 挂起是已知 bug，有免刷固件解法**

```
Linux USB 邮件列表（2026-03）：RTL9210B（0bda:9210）在重 I/O 下
出现 uas_eh_abort_handler → cannot reset (err=-71) → 设备 offlined

修复：usb-storage.quirks=0bda:9210:u（禁 UAS，回退 BOT）
代价：顺序读降到约 153 MB/s
```

**情报 2："退到 initramfs shell"的机制**

```
zpool.cache 被烘进 initramfs → zfs-import-cache.service 早期启动就导入
→ USB 桥尚未枚举时导入失败 → 掉进 initramfs shell
```

**定量权衡**：

```
E[单次实验周期损失] ≈ ¥425（最坏 ¥7,600）
年化收益 ≈ ¥0–1,000
回本周期 = 1.3 – ∞ 年
```

**建议**：**对 GPU 收手，把投资转向存储链**

**但有一个事实错误**：它假设该 GPU 只用于视频转码（`NVENC` 独立时钟域 → 收益 0）。
实际用途是 **CUDA 训练/推理**，SM 时钟直接决定吞吐 → 收益不为 0。

---

## 六、最终裁决

### 各方错误

| 方 | 错误 | 影响 |
|---|---|---|
| **稳健派** | 假设 GPU 只用于转码 → 收益为 0 | 会误判整个项目价值 |
| **激进派** | 把 "CTGP=0 解释 5001W" 当结论（实测改 CTGP 后仍 5001） | 会浪费一轮实验 |
| **红队** | 指出 5001 巧合后未标注"假设 vs 已验证" | 可能被下游当结论 |
| **专家 A** | 结论对，论据错（Dynamic Boost 起源） | 导出过强结论 |
| **专家 C** | "冷却 +8~12%" 与实测 0.19%/°C 冲突 | 误导资源投入 |

### 分档执行

**第 0 档（零重启零风险，必做）**

```bash
# ① 判别锁是否生效（红队指出这是决定性实验）
nvidia-smi -pm 1 && nvidia-smi -rgc
nvidia-smi -lgc 300,900          # 跑负载，看频率是否真掉到 ≤900

# ② 三方共同遗漏的 10 秒检查
cat /proc/driver/nvidia/gpus/*/power        # Dynamic Boost 支持？
nvidia-smi -q -d POWER_PROFILES             # 真实功率档
nvidia-smi -q -d VOLTAGE                    # 负载电压
```

**第 1 档（一次重启，可能拿回 16–18%）**
- 开 GSP：`NVreg_EnableGpuFirmware=1` → 看 `Target Temp` 是否从 N/A 变成 87°C
- **同时合并**：`usb-storage.quirks=0bda:9210:u`（修 UAS bug，这是两次启动失败的根因）
- **重启前必须 `zpool export`**（导出后断电无害）

**第 2 档（零重启，+5~12%）**
- NVML `GpcClkVfOffset` **正 offset = 降压**（功率受限 GPU 的正解）

### 停止线

```
若 `-lgc 300,900` 完全无效（频率不变）
  AND 开 GSP 后 Target Temp 仍是 N/A
→ 接受当前性能为天花板，收手。
```

---

## 七、这个案例教了什么

### 1. 红队能发现主 Agent 看不见的实验设计缺陷

`-lgc 2100` 是恒等 no-op —— 主 Agent 做了 2 次都没意识到。

### 2. 稳健派能发现所有人忽略的非技术因素和既有 bug

RTL9210B 的 UAS 问题、initramfs 机制，**三份专家报告零提及**。

### 3. 激进派能发现被别人当作"不可修"的病灶

`Target Temp = N/A` 这个现象，两位专家都看到了，但只有激进派把它和
`open-gpu-kernel-modules#1083` 的已知 bug 联系起来。

### 4. 三方的结论都有事实错误 —— 所以裁决不能是"折中"

稳健派错在前提（用途假设），激进派错在因果链（CTGP），红队错在标注（假设 vs 结论）。

**如果和稀泥，会把三个错误一起带进执行。**

### 5. 主 Agent 自己也犯过多层错误

本案例中主 Agent 造成的实际事故：
- `cpio ... 2>&1` 把 `18 blocks` 灌进归档 → 启动 panic
- `find kernel -type f` 丢掉三个目录条目 → 退到 initramfs shell
- QEMU 用 `rdinit=/bin/sh` 跳过真实路径 → 未发现上述问题

**这三条全部记在 `docs/ANTI-PATTERNS.md` 里。**
