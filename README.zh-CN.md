# RK Edge Doctor

面向 Rockchip 板卡的离线体检工具，先支持 RK3588/RK3588S。用于检查 NPU
设备缺失、驱动不可读、内存不足和温度过高等问题。v0.1 只实现 `board`，
运行仅需 Python 3.9+ 标准库，无第三方运行依赖。

在仓库根目录直接运行，不需要安装包：

```sh
PYTHONPATH=src python3 -m rkdoctor board --json examples/report.json --html examples/report.html
```

完全离线，无遥测、注册、license 校验，不包含 Rockchip 专有二进制或库。
读不到的来源记为 skip 并说明原因；无法识别的版本记为 unknown。

> 示例截图占位：终端摘要与单文件 HTML 报告。

已附上本机真实运行的 [x86 JSON](examples/sample-report-x86.json)、
[x86 HTML](examples/sample-report-x86.html)、
[RK3588 假树 HTML](examples/sample-report-rk3588-fixture.html)，以及一台
**真实 RK3588 板卡**（Ubuntu 22.04、内核 6.1、rknpu 驱动 0.9.8，16 项检查
全部通过）的报告：[JSON](examples/sample-report-rk3588-rk2.json) /
[HTML](examples/sample-report-rk3588-rk2.html)。
[English README](README.md)

## 使用方式

```sh
PYTHONPATH=src python3 -m rkdoctor --version
PYTHONPATH=src python3 -m rkdoctor board
PYTHONPATH=src python3 -m rkdoctor board --root tests/fixtures/fake_rk3588 --html examples/fixture.html
PYTHONPATH=src python3 -m rkdoctor model
PYTHONPATH=src python3 -m rkdoctor rtsp
```

可选安装：`python3 -m pip install .`，安装后使用 `rkdoctor` 命令。
离线安装需要本地已有 setuptools 构建后端；源码运行不需要安装任何包。
输出文件的父目录需要事先存在。

`board` 完成报告返回 0，即使 verdict 为 CRITICAL；自动验收应读取 JSON
中的 verdict。写报告失败返回 1，参数错误返回 2。`model`/`rtsp` 分别预留
v0.2/v0.3 接口，仅打印英文一行、中文一行的 planned 说明，返回 3。

`--root DIR` 将 `/proc`、`/sys`、`/dev`、`/etc` 的探测重定向到 DIR；
`/usr`、`/lib` 中的运行库搜索也重定向，不调用开发机的 ldconfig。
允许快照根目录内部的相对软链接；越过根目录的链接会跳过，打包快照时请带上实际目标文件。

`uname`、hostname、Python 模块检查仍反映执行工具的主机；磁盘统计反映 DIR
所在文件系统，默认是 `/`，不能据此推断原板卡磁盘容量。因此在 x86 上跑
RK3588 假树时，架构检查仍然 warn；测试会替换这些实时值来验证确定性 verdict。

## 检查项

| 稳定 ID | 检查内容与阈值 |
| --- | --- |
| `soc` | device-tree 中的型号和 compatible；非 RK 为 warn |
| `os` | 发行版与实际架构；非 aarch64 为 warn |
| `kernel` | 实际内核版本，仅记录 |
| `cpu` | 核数、按 cluster 分组的当前频率 |
| `memory` | 总量/可用量；可用 <10% 为 warn |
| `disk` | 剩余 <10% 为 warn，<5% 为 fail |
| `npu_device` | `/dev/rknpu*` 或驱动证据（部分 BSP 仅经 DRM render 节点暴露 NPU）；RK 上两者皆缺失为 fail，非 RK 缺失为 skip |
| `npu_driver` | debugfs 驱动版本；不可读为 skip，提示 sudo 重跑 |
| `npu_load` | NPU 各核负载；不可读为 skip |
| `npu_freq` | 当前频率和 governor；省电 governor 不警告 |
| `rknn_runtime` | librknnrt.so 存在性与可识别版本；缺失为 warn |
| `thermal` | 任一区 >=75°C 为 warn，>=85°C 为 fail |
| `mpp` | `/dev/mpp_service`；缺失为 warn，视频场景才需要 |
| `rga` | `/dev/rga`；缺失为 warn |
| `video_devices` | `/dev/video*` 数量；无设备为 skip |
| `python_env` | Python 及 rknnlite/cv2 存在性、版本 |

有 fail → CRITICAL；否则有 warn → ATTENTION；否则 HEALTHY。
非 RK 由 soc 的 warn 表达，不改变 verdict 字符串。skip 表示证据不全，
不能把包含 skip 的 HEALTHY 当作所有硬件都已验收。

报告包含工具版本、带时区时间、hostname、SoC、发行版和内核，不采集板卡序列号。
HTML 是内联 CSS 的单文件，无外部资源或 JavaScript。逐项说明见 [docs/checks.md](docs/checks.md)。

## 开发与测试

本地已有 pytest 时运行：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
```

同一套测试也可用标准库执行：

```sh
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

三棵假树位于 `tests/fixtures/`；device-tree 使用实际 NUL 分隔，名为
`librknnrt.so` 的 fixture 是文本文件，不是二进制库。CI 在 Python 3.9/3.12 上运行 pytest。

## 路线图

- v0.1：board 体检与离线报告。
- v0.2：model 检查、CPU 回退算子清单、p50/p95、ONNX/RKNN 差异；
  预留 `NPU_ONLY / MIXED / CPU_HEAVY / UNKNOWN` 判定词。
- v0.3：RTSP 连接、实际 codec/分辨率/帧率、帧间隔抖动、冻结、重连、快照。

RK Edge Doctor 是与 Site AI Gateway 关联的开源项目：https://site-ai-gateway.com/open-source/rk-edge-doctor/

许可证：[Apache-2.0 全文](LICENSE)。
