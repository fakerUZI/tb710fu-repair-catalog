# TB710FU 修复目录

此目录供修复助手读取。`catalog.json` 中每条问题包括标题、说明、设备、状态、脚本相对路径、SHA-256、是否需要管理员权限及风险。`contributions/` 中的社区修复须按各自适用范围、验证边界和风险单独审核。

清单第一项为唯一的 [TB710FU 日期内核升级](contributions/kernel-offline-upgrade/attachments/KERNEL-README.md)。本次接受所有不带日期的合法旧内核；后续更新只接受日期版本且目标日期严格递增。旧 Snap 内核升级入口及七个分片已删除。日期版完整载荷已构建，并完成 snap1 → 日期版实机升级和启动验证。

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

## snap1 内置麦克风修复

`microphone-snap1`：[说明与回退](contributions/microphone-snap1/attachments/README.md)。仅允许匹配的内核、固件、GPT 与启动镜像。命令行不设动作时为只读 check；修复助手会显式传入动作，当前默认 apply，会写 boot_a，运行前须确认风险。核心实机声学验证已完成，本轮修改的完整 apply/rollback 仍未在真实块设备上重跑，详见[验证记录](contributions/microphone-snap1/attachments/VALIDATION.md)。

[内核日期命名与升级版本检查](contributions/kernel-offline-upgrade/attachments/KERNEL-VERSIONING.md)：本次接受无日期旧内核，后续要求目标日期严格晚于设备当前日期版本。当前平板已运行 `7.2.6-tb710fu-20261117`；后续目标日期必须严格晚于 20261117。
