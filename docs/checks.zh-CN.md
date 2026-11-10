# 板卡检查 — schema 1 / v0.1

检查项 ID 是永久锚点。每项检查固定返回
`{id, title, status, value, detail, hint}`。`status` 取值为 `pass`、`warn`、`fail` 或
`skip`；通过（pass）时 `hint` 为 null，其余情况为一句可执行的处理建议。返回值
保留带明确单位的结构化测量数据。缺失的测量值为
null，无法识别的版本记为 `unknown`。

数据源按原样只读读取，不做任何修改。缺失或不可访问的测量项记为
skip；明确的设备或运行库缺失则遵循下文各检查项的具体规则。
部分可读的 CPU、频率或温度数据会保留已读部分并记为 skip，但
已知的温度告警或失败优先于不可读的温区。

## soc

数据源：`/proc/device-tree/model`、`/proc/device-tree/compatible`。后者
是实际以 NUL 分隔的列表。识别为 Rockchip 需要这些数据源中包含 `rockchip`；
明确的 `rkNNNN` 标识符会保留后缀（如 `rk3588s`）。
SoC 名称优先取自 compatible 列表。当标识符不可用时，
不会猜测具体的 SoC 型号。

返回值：`{soc, rockchip, model}`。Rockchip 板卡记为 pass；可读的非 Rockchip
设备树记为 warn。在 x86 主机上，缺失设备树会根据当前运行架构给出非 Rockchip
的 warn。若设备树数据与运行架构均无法确认板卡
身份，则记为 skip 且 `rockchip: null`。请在具有可读 device-tree
文件的板卡上重跑。不会从 model/compatible 中提取板卡序列号。

## os

数据源：`/etc/os-release` 以及通过 Python 的 platform 模块执行 `uname -m`。
返回值：`{distribution, architecture}`。发行版名称优先取 PRETTY_NAME，其次取 NAME。
除 `aarch64` 外的任何已知架构均记为 warn。缺失发行版或架构信息时
记为 skip，除非已知架构本身已触发 warn。请使用 aarch64 操作系统并
检查 os-release 的读取权限。`--root` 仅重定向发行版数据源。

## kernel

数据源：通过 platform 模块获取当前运行的 `uname -r`。返回值为内核
版本字符串。由于各厂商内核版本差异较大，只要能读到版本号即记为 pass，
不做版本高低评判。无法获取版本号时记为 skip。`--root` 不会改变该项结果。

## cpu

数据源：`/proc/cpuinfo` 与 `/sys/devices/system/cpu/cpu*/cpufreq/`。
统计 `processor` 记录数；将 `scaling_cur_freq` 按 `related_cpus` 分组，
若缺少 cluster 归属信息则退回按单核标识分组。返回值：`{cores, clusters}`；
每个 cluster 包含 `cpus` 和 `current_khz`。缺失核数或频率时记为 skip
并保留已读到的部分数据。不设频率阈值。请核查 cpuinfo
及 CPU 频率相关 sysfs 节点的支持情况与读取权限。

## memory

数据源：`/proc/meminfo` 中的 MemTotal 和 MemAvailable 字段，单位为 KiB。返回值：
`{total_kib, available_kib, available_percent}`。可用内存 **<10%** 记为 warn；
恰好 10% 记为 pass。字段无效、缺失或前后不一致时记为 skip。不会用
MemFree 拼凑回退估算。内存不足时请停止未使用的负载。

## disk

数据源：`shutil.disk_usage('/')`；指定 `--root` 时，查询包含
DIR 的文件系统。返回值：`{total_bytes, free_bytes, free_percent}`。剩余空间 **<5%** 记为 fail；
其余情况下 **<10%** 记为 warn。恰好 5% 记为 warn；恰好 10% 记为 pass。查询失败时
记为 skip。空间不足时请清理被测文件系统的可用空间。文件系统快照不包含
原板卡的磁盘容量数据；此项测量的是执行主机的文件系统。

## npu_device

数据源：`/dev/rknpu*`。返回值：匹配到的设备路径列表，记为 skip 时为 null。检测到
设备即记为 pass。部分 BSP（例如内核 6.1 搭配 rknpu 驱动 0.9.8）完全不暴露
`/dev/rknpu*` 节点——NPU 经由 DRM render 节点
（`/dev/dri/renderD*`）访问，同时驱动仍通过 debugfs
和 devfreq 上报版本与负载；在此情况下，只要存在有效的驱动证据（可读的
`/sys/kernel/debug/rknpu/version` 或 NPU devfreq 节点）也记为 pass，
返回值为 `{dev_nodes: [], render_nodes: [...], access: "drm_render/debugfs"}`。
在已确认为 RK 的板卡上，若设备节点与驱动证据同时明确缺失则
记为 fail；在已确认为非 RK 的主机上缺失则记为 skip。设备枚举不可访问或
未知 SoC 时记为 skip。需要时请启用板卡的 rknpu 内核驱动并确认设备
节点已创建。

## npu_driver

数据源：`/sys/kernel/debug/rknpu/version`。数据源可读时记为 pass 并记录
其版本号；无法识别的格式记为 `unknown`。数据源缺失、为空或不可读时
记为 skip，并给出提示 “用 sudo 重跑可读 / Rerun with sudo to read debugfs.”
本工具不会自行挂载 debugfs 或修改权限。

## npu_load

数据源：`/sys/kernel/debug/rknpu/load`。将 `Core0: 0%, Core1: 15%, ...` 解析为
`{core0: 0.0, core1: 15.0, ...}`。同时也兼容单核的 `NPU load: 25%`
格式。百分比数值必须介于 0 到 100 之间。数据可读且合法时记为 pass；
数据缺失或不可读时记为 skip 并附带同上的 sudo 提示；无法识别的数据格式记为 skip
并提示检查文件格式。不设负载利用率阈值。

## npu_freq

数据源：`/sys/class/devfreq/*npu*/cur_freq` 与 `governor`。返回值：
`{device, current_hz, governor}` 列表。数值可读即记为 pass。包括
`simple_ondemand` 和 `powersave` 在内的各种 governor 仅作记录，不触发警告。数值缺失或
无效时保留已读部分数据并记为 skip；请检查 sysfs 读取权限。

## rknn_runtime

数据源：实机环境下执行 `ldconfig -p`，外加 `/usr/lib`、
`/usr/local/lib`、`/lib`（含一层架构子目录）以及
`/usr/lib64` 下的常见路径。查找 `librknnrt.so` 及带版本号的文件名。使用 `--root` 时，所有
运行库路径均重定向至快照目录，且禁用宿主机的实时 ldconfig。

返回值：存在时为 `{present, version, paths}`，否则为 `{present, version}`。
存在即记为 pass。每个库文件最多扫描前 16 MiB，提取明确的 `librknnrt version`
或 `RKNN [Runtime] version` 版本标记。不动态加载库文件，也不从文件名或
无关字符串推断版本号；无法识别版本时记为 `unknown`。
未找到运行库时记为 warn，因为可能是有意采用静态 C API 部署或未安装
运行时。搜索路径不可访问时记为 skip。如需动态加载 RKNN，请另行安装
板卡厂商提供的对应运行时库。

## thermal

数据源：`/sys/class/thermal/thermal_zone*/{type,temp}`。将毫摄氏度
整数换算为 °C。返回值：`{zone, type, temperature_c}` 列表。
任一温区 **>=85°C** 记为 fail；其余情况下任一温区 **>=75°C** 记为 warn。温度更低且可读的
温区记为 pass。数据缺失或格式异常时记为 skip，除非已知温区已
触发 warn/fail。请加强散热或降低负载，并检查 sysfs 访问权限。

## mpp

数据源：`/dev/mpp_service`。返回值：`{present}`。存在记为 pass，明确缺失
记为 warn，元数据不可访问记为 skip。仅相关视频编解码场景需要 MPP；
按需启用板卡的 MPP 驱动即可。检查过程不执行任何编解码操作。

## rga

数据源：`/dev/rga`。返回值：`{present}`。存在记为 pass，缺失记为 warn，
元数据不可访问记为 skip。若图像或视频负载需要 RGA，请启用对应驱动。
检查过程不运行硬件加速基准测试。

## video_devices

数据源：`/dev/video*`。返回值：找到设备时为 `{count, paths}`；无设备时为 `{count: 0}`；
枚举失败时为 `{count: null}`。至少存在一个设备时记为 pass；无设备或
数据源不可读时记为 skip。如有需要请连接视频采集设备并检查其驱动。
设备节点存在并不代表具备实际采集能力或视频流可正常打开。

## python_env

数据源：当前运行的 `sys.version`，并使用同一解释器在子进程中尝试导入 `rknnlite` 和 `cv2`
模块。返回值：`{python, modules}`；每个模块
记录 `present`、`version` 以及相关的失败原因。可选
模块缺失仅作信息记录，状态记为 pass。导入失败、缺少传递
依赖、进程崩溃或超时记为 skip，且 `present: null`。每次导入尝试设有
五秒超时，且不写入 Python 字节码。无法获取
`__version__` 时记为 `unknown`。该项始终探测当前执行环境，而非
快照中的 Python 安装。

## 报告契约

```text
{
  schema: 1,
  tool_version: string,
  generated_at: ISO8601 string with timezone,
  host: {hostname, soc, os, kernel},
  summary: {pass, warn, fail, skip},
  verdict: CRITICAL | ATTENTION | HEALTHY,
  checks: [{id, title, status, value, detail, hint}, ...]
}
```

各项计数均为整数。fail 优先于 warn；skip 不改变
最终 verdict。非 Rockchip 主机通过 soc 检查项的 warn 表达，不会在
verdict 枚举中额外增加文本。阈值判定使用未四舍五入的原始值；展示的
百分比保留两位小数。hostname 始终为执行工具所在主机的
hostname（含快照模式）。不采集任何序列号字段。

终端输出中每项检查占一行，格式为“符号 + ID + 值”（`+` 表示 pass，`!` 表示 warn，
`X` 表示 fail，`-` 表示 skip），最后输出 verdict 与报告文件路径。JSON 报告保留详细原因与
处理建议；HTML 报告将它们展示在汇总卡片下方的检查项表格中。HTML 会转义
所有数据源内容，采用内联 CSS，无 JS 依赖且不引用任何外部资源。
其固定页脚为：

```text
Generated by RK Edge Doctor · https://site-ai-gateway.com/open-source/rk-edge-doctor/
```

报告仅记录观测事实，不运行推理负载或性能基准测试。
`board` 在写完所请求的报告后返回 0，即使结果为 CRITICAL 也是如此；调用方
应检查 verdict 字段。报告写入失败返回 1，参数用法错误返回 2，规划中的
model/rtsp 命令返回 3。
