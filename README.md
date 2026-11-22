# TB710FU 修复目录

此目录供修复助手读取。`catalog.json` 中每条问题包括标题、说明、设备、状态、脚本相对路径、SHA-256、是否需要管理员权限及风险。`contributions/` 中的社区修复须按各自适用范围、验证边界和风险单独审核。

清单第 01 项为「一键升级」。修复助手 0.10.12 起可直接点击「一键升级」。0.10.11 可先下载第 01 项再运行。

一键升级会先刷新仓库并清理失效脚本缓存。它依据修复助手保存的安装记录和版本指纹跳过已安装且未更新的条目。确认本次清单后，所选修复会按顺序下载、校验和应用，成功项逐条保存记录。手动安装的项目可在确认页取消勾选。各修复的回退方式见条目说明。

客户端接入前应固定可信仓库及修订版本，对清单进行格式校验，限制相对路径，验证脚本哈希，展示完整脚本及风险，并在用户确认后执行。哈希仅保证与所取清单一致，不能证明清单本身可信；远程内容不得自动执行。

提交真实修复应说明适用设备、系统版本、问题症状、依赖、风险、验证证据和回退方式，并经人工审核。不得提交设备备份、凭据、设备唯一标识、私人资料或已退役的前摄实验。涉及设备写入时必须先核实当前固件和 GPT；历史分区坐标不能直接使用。保留已工作的后摄适配。

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
| `power-key-single-press` | 20261118 已包含单次按键亮屏和短按锁屏，沿用 ROM 中启用的电源键用户服务。原独立入口已退役，已安装独立修复的用户仍可从本机用 `tb-power-key-off` 回退对应的用户级安装。 |
| `ime-preedit-backspace`（原第 02 项） | 当前 TB710FU 版本已集成中文拼音候选区与退格修复，独立条目和下载包已下架。 |

内核升级会保留现有麦克风 ALSA 初始化服务及 WirePlumber 路由，不新建这些用户态文件；不能把“镜像已含采集链路”理解为在任意原始系统上自动完成全部音频配置。

原 20261118 日期内核升级已下架。当前可用条目见 [修复清单](catalog.json)；旧版方案见 [内核升级资料](contributions/kernel-offline-upgrade/attachments/KERNEL-README.md)。

相机和手电筒补充的安装与回退说明见 [相机与手电筒](contributions/kernel-offline-upgrade/attachments/flash-camera/README.md)。日常补光灯修复可从清单中的「后摄补光灯与手电筒快捷开关修复」运行。

[内核日期及构建规则](contributions/kernel-offline-upgrade/attachments/KERNEL-VERSIONING.md)

## 2026-11-22 BOOTFIX 收录调整

以最新 BOOTFIX 的已校验 rootfs 为准，下架以下旧条目及独立导入包。旧文件可通过 Git 历史查询。

| 已下架条目 | 原因 |
| --- | --- |
| `droidloom-speaker-audio` | 脚本只接受 Snap1 内核。BOOTFIX 使用 20261118，已内置 WirePlumber 播放时钟配置。 |
| `droidloom-r42-game-compat` | BOOTFIX 内置 Droidloom 49。旧脚本只识别 28、42 或全新安装，实机只读检查已确认拒绝 49。 |
| `droidloom-on-demand-launch` | 已内置按需启动器和已启用的启动器监听服务。 |
| `droidloom-window-navigation` | 包内的第 13 版扩展与该条目的四个运行文件哈希一致。 |
| `droidloom-window-memory` | 包内已安装 zram 生成器，3 GiB zram 和内存水位配置与条目逐字节一致。 |
| `full-virtual-keyboard` | 包内已内置七个扩展。旧条目会把当前账号的快捷键扩展从第 20 版换回第 18 版。 |

## USB-C 直连 SSD 测试候选（目标 SSD 尚未实测）

[Issue #19 的测试说明与恢复步骤](contributions/usb-c-ucsi-host-candidate/attachments/README.md)。提供保持 UCSI 绑定的单次 host 协商候选、只读采样和可并行的独立 UCSI tracing。已复核软件逻辑；macOS 与 Linux ARM64 隔离环境各 32 项测试、平板只读检查通过。**目标 SSD 尚未实测，真实 host/trace 管理员路径也未验证，不宣称故障已修复。**

[预发布测试包 v20261117.1](https://github.com/fakerUZI/tb710fu-repair-catalog/releases/tag/usb-c-ucsi-host-test-20261117.1)、[仓库内可导入包](packages/usb-c-ucsi-host-candidate-20261117.tbfix)及[SHA-256](packages/usb-c-ucsi-host-candidate-20261117.tbfix.sha256)。该候选通过预发布测试包提供，issue #19 保持开启，待目标硬件验收。

## TB710FU 修复助手

Ubuntu 安装包 [下载 0.10.10（.deb）](packages/repair-assistant/tb710fu-repair-assistant_0.10.10_all.deb)。安装后从顶部“应用更新”检查新版。安装包的 SHA-256 和文件大小见[更新清单](packages/repair-assistant/latest.json)。
