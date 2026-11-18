# TB710FU 修复目录

此目录供修复助手读取。`catalog.json` 中每条问题包括标题、说明、设备、状态、脚本相对路径、SHA-256、是否需要管理员权限及风险。`contributions/` 中的社区修复须按各自适用范围、验证边界和风险单独审核。

清单第一项为唯一的 [TB710FU 日期内核升级](contributions/kernel-offline-upgrade/attachments/KERNEL-README.md)。20261118 接受无日期旧版和早于目标的标准日期版（包含 20261117）。升级器兼容已知 A/B 优先级、重试次数及成功位差异，并保留布局、固件、启动镜像和关键属性校验。以后发布的标准日期必须严格递增。

原第二项 Snap 用户态独立修复/验证与第三项 snap1 网络模块修复已从目录及当前仓库移除，相关功能统一由当前日期版内核入口提供。

内核更新统一使用目录第一项，默认只读 check；`TB710FU_KERNEL_ACTION=install` 执行备份、安装和 boot 写入。客户端须下载并核验全部新日期版附件，通过 `TB710FU_ATTACHMENTS_DIR` 传给唯一 Bash 入口。安装前须展示风险并取得用户授权；不自动重启，成功后保存工作并手动重启。

客户端接入前应固定可信仓库及修订版本，对清单进行格式校验，限制相对路径，验证脚本哈希，展示完整脚本及风险，并在用户确认后执行。哈希仅保证与所取清单一致，不能证明清单本身可信；远程内容不得自动执行。

提交真实修复应说明适用设备、系统版本、问题症状、依赖、风险、验证证据和回退方式，并经人工审核。不得提交设备备份、凭据、设备唯一标识、私人资料或已退役的前摄实验。涉及设备写入时必须先核实当前固件和 GPT；历史分区坐标不能直接使用。保留已工作的后摄适配。

## 中文候选区与退格修复

- `ime-preedit-backspace`：[中文拼音候选区与退格修复](contributions/ime-preedit-backspace/attachments/README.md)。适用于已有 TB710FU 候选区的 Ubuntu 26.04 / GNOME 50；核心来自历史实机修复，新安装包装尚待实机复核。
- [完整源码包](packages/ime-preedit-backspace-20261116.zip)及[包校验值](packages/SHA256SUMS)可供下载审查。仓库内的真实修复仍须逐项审查依赖、风险、哈希和适用版本，不能自动执行。

## 2026-11-16 代码审核

[PR #8–#11 的修正、测试和回退边界](docs/REVIEW-20261116.md)。

## Wi-Fi 与麦克风补充审核

[PR #12、#14 的代码修正、测试及恢复边界](docs/REVIEW-20261116-network-microphone.md)。

## 当前内核覆盖范围与目录归并

已对照发布的七个分片重组包、启动镜像设备树、ath12k 模块、安装器文件清单及各独立脚本核对。以下旧入口及其 contributions 文件已从当前仓库删除，历史版本仍可通过 Git 记录查看：

| 已删除的旧入口 | 当前处理方式 |
| --- | --- |
| `microphone-snap1`（原第 7 项） | 当前日期版 boot 已含 WCD/SoundWire DAI 1 采集链路；不再提供旧 snap1 镜像刷写入口。 |
| `ath12k-stats-timeout-recovery`（原第 6 项） | 当前内核已含对应 vdev 计数补丁；移除旧版临时驱动重载入口，避免与正式内核修复重复。短期实测不等于全部 Wi-Fi 故障已解决。 |

内核升级会保留现有麦克风 ALSA 初始化服务及 WirePlumber 路由，不新建这些用户态文件；不能把“镜像已含采集链路”理解为在任意原始系统上自动完成全部音频配置。

目录现有 6 项，顺序为：日期内核升级、中文候选区与退格、电源键、窗口触控、DroidLoom 按需启动、DroidLoom 扬声器音频。后五项可独立应用，继续保留。内核包也包含已配置的电源键服务的短按锁屏调整。

已有相机和手电筒补充会随内核升级迁移到 20261118。首次安装补充仍使用第一项的 `camera-install` 或 `flash-install`。新版相机保留全分辨率，以连续预览和后台编码缩短拍照等待。[相机与手电筒](contributions/kernel-offline-upgrade/attachments/flash-camera/README.md)

[内核日期及构建规则](contributions/kernel-offline-upgrade/attachments/KERNEL-VERSIONING.md)

## USB-C 直连 SSD 测试候选（目标 SSD 尚未实测）

[Issue #19 的测试说明与恢复步骤](contributions/usb-c-ucsi-host-candidate/attachments/README.md)。提供保持 UCSI 绑定的单次 host 协商候选、只读采样和可并行的独立 UCSI tracing。已复核软件逻辑；macOS 与 Linux ARM64 隔离环境各 32 项测试、平板只读检查通过。**目标 SSD 尚未实测，真实 host/trace 管理员路径也未验证，不宣称故障已修复。**

[预发布测试包 v20261117.1](https://github.com/fakerUZI/tb710fu-repair-catalog/releases/tag/usb-c-ucsi-host-test-20261117.1)、[仓库内可导入包](packages/usb-c-ucsi-host-candidate-20261117.tbfix)及[SHA-256](packages/usb-c-ucsi-host-candidate-20261117.tbfix.sha256)。本候选不加入正式 `catalog.json`，现有六个条目不变；issue #19 保持开启，待目标硬件验收。
