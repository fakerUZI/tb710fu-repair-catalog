# snap1 网络内核模块修复

对应 issue #16/#17，仅支持 TB710FU、aarch64、`7.2.6-tb710fu-snap1` 的已核对
配置和模块基线。安装器同时检查运行配置指纹、原模块指纹、全部新模块哈希、
vermagic 和依赖。相同版本字符串并不足以允许安装；不匹配时拒绝覆盖。

本包不写 boot、GPT、DT 或固件，不更换摄像头、USB、AppArmor、Snap 配置。
修改 19 个网络模块文件（含 4 个原有模块替换）以及一份模块预载配置，
保留当前启动链和前后摄。安装后须手动重启，脚本不自动重启。

## 使用

需要 Python 3.12+、kmod、gzip；验证还需要 iproute2、iptables、nftables、
util-linux。安装本身离线，不安装软件包、不解除更新保护。附件解压上限
16 MiB；归档整体、清单和每个模块分别校验，拒绝越界路径、链接和特殊文件。

修复助手应下载本条目的所有附件并设置 `TB710FU_ATTACHMENTS_DIR`。
默认只读 `check`。显式设置 `TB710FU_FIX_ACTION=apply` 安装，保存工作后
手动重启，再执行 `TB710FU_FIX_ACTION=verify`。验证使用临时网络命名空间，
不会向宿主机防火墙添加测试规则。

## 修复范围与限制

- IPv4/IPv6 legacy 的 filter/mangle/raw/nat 表。
- legacy 与 nft iptables 的 DNAT、SNAT、REDIRECT、REJECT、NETMAP。
- `x_tables` legacy 实现与 `nf_nat` redirect 实现同步重建。
- ath12k restart 计数和 station 重复 start 的两枚上游补丁；保留板级轮询、
  硬件去重、节电参数。只读参数 `tb_vdev_count_fix=1` 识别已加载的修复版本。

没有增加 ARP/security 表。ARP 支持会改变运行内核的 `struct netns_nf`，
不适合以本包这种保持内核 ABI 的模块更新方式追加。此限制不影响上述
IPv4/IPv6 NAT 功能。`/proc/config.gz` 仍是原内核的嵌入配置；模块实际功能
应以加载结果和隔离流量测试为准。未来完整内核构建脚本已同步补齐配置。

此包仅处理匹配已审核配置和模块指纹的 snap1 设备，不适用于原
`7.2.6-tb710fu`。历史原版到 snap1 的升级入口和分片已移除；目录第一项
统一为日期内核升级，当前日期载荷待构建，不能借用旧包代替。

## 风险与回退

Wi-Fi 模块及 netfilter 依赖会在重启后更换，连接和 VPN 会中断。新模块
如不能正常工作，应通过 USB SSH 回退。首次安装的原模块和原预载配置
保存在 `/var/backups/tb710fu-network-repair-20261117/`，后续已审核修订
不会覆盖最初快照。备份仅保存在本机，勿公开上传。

保留离线附件，使用同一入口 `TB710FU_FIX_ACTION=rollback` 恢复模块文件，
然后手动重启。回退先验证完整备份；未知模块改动或备份损坏会停止。
安装发生可捕获错误时会尝试恢复原文件；掉电或内核崩溃不能保证自动恢复。

`verify-wifi.py` 是单独的现场压力测试工具，会断开并重新连接现有 Wi-Fi
配置，仅允许 USB-NCM 救援连接正常时运行。它不修改 AP、密码或保存的网络
配置，默认 10 次重连、总计五分钟观察；不会由安装入口自动执行。

编译输入为 Linux 7.2.6、原 snap1 端口补丁和本仓库附带的三份网络补丁。
没有包含设备备份、凭据、设备唯一标识、摄像头私有表或退休前摄材料。
