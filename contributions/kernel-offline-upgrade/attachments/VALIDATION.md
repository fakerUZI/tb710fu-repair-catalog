# 验证记录：2026-11-17 日期内核

## 实机升级结果

TB710FU / Ubuntu 26.04.1 ARM64，从 `7.2.6-tb710fu-snap1` 经本包安装器升级并重启为 `7.2.6-tb710fu-20261117`。升级状态 complete，自动收尾 Result=success / ExecMainStatus=0，systemd 无失败单元。

- `/proc/config.gz` 解压内容与包内 kernel.config 逐字节相同，SHA256 `16eb9ee0e4f3037da4331cd83deda398f63278bb41979a1808e152ed2e96b933`。
- Snap confinement=strict，AppArmor enabled；自动收尾完成 strict/full 与普通用户实际文件/网络拒绝及 Seccomp 验证，原 APT hold 列表保持一致。
- USB-NCM SSH 重启后恢复，既有私有 USB 身份保留；boot_b 哈希不变。
- 前摄、后摄、对焦服务 active；前摄采集 30 帧 1280×720 RGB，约 30fps；后摄采集三帧 4208×3120 RAW；对焦位置 700。
- 四种后端 iptables-legacy、ip6tables-legacy、iptables-nft、ip6tables-nft，分别在临时 netns 通过真实 UDP DNAT/REDIRECT/SNAT/REJECT；NETMAP 验证规则插入，未声称验证其实际转发。
- 四种后端的 security 表均在独立 netns 查询成功；ARP 三个模块加载成功，仅记录模块加载，未进行 ARP 规则/流量验收。
- ath12k 运行时 `tb_vdev_count_fix=1`，本次启动暂未发现 fw stats 超时或模块版本错误。

Wi-Fi 在 USB 救援保持可用的条件下完成 10 次重连，连续观察 301.67 秒，160 次 station 查询均返回信号，最慢 8.49ms，fw stats 超时为 0；首尾两次强制经 Wi-Fi 的 HTTPS 请求均返回 200。未更改 AP、保存的连接配置或 VPN 账号。此结果不等于全天稳定性、AP 信道/带宽切换或 aTrust 业务已验收；issue #16/#17 不因本次短测自动关闭。

## 镜像和模块

- 新 Image SHA256：`03e8de2bf17c18686f597a8e5dfe5c2b133057b947daced0d043749ff32000e7`。
- 新 boot SHA256：`8ef8203624371525150904700f7ffab7aec5f72e66a66cb62d74733f7603a4c9`。
- 完整模块 tar SHA256：`8792fe1751be922b29c8ec3edce9b8c6cd6c7af351bc47263a18cbd638dcc3d0`。
- 最终 r2 压缩包 SHA256：`1ff7106439fe1485fba44b8af276668bc5516ae15e13cadb3fcf265847f53f7b`，约 53 MiB，分为七个不超过 8 MiB 的附件。

新构建沿用已经匹配实际 snap1 模块的源码、Module.symvers 和完整配置，在独立构建卷中重编 Image/全部模块，回移两枚 ath12k 计数补丁并保留板级参数。全量更新允许启用 NETFILTER_FAMILY_ARP，故增加 ARP/security 表；与独立 snap1 模块修复包的 ABI 保持限制不同。

本次 freshly inventoried boot 为输入，Linux 之外逐字节保留 Android 载荷、当前 DT（含已工作的麦克风和前摄电源配置）与 shim；AVB SHA256_RSA4096/footer/hash 验证通过。所有模块 vermagic 与目标一致，关键网络模块存在且唯一，旧 extra/xt_MASQUERADE 不复制到新树。私有前摄模块在当前工作区单独重编并留在设备私有目录。

## 安装与回退测试

47 项目录/版本测试、9 项既有网络安装器测试、10 项本次完整升级集成测试，共 66 项通过；全部七个新分片已在目录入口 validate 中完成校验/重组。另将目录所列脚本/附件按哈希复制到平板，完整运行唯一 Bash 入口 check：分片、载荷及现场固件/GPT 校验通过，正确报告已运行目标版本且未重复刷写。

10 项真实镜像/模块文件、模拟硬件/服务集成测试通过：完整安装/收尾/主动回退、部分 boot 写入中断恢复、Snap 网络失败后重试、归档路径/链接/重复文件拒绝、GPT 身份/几何保护、写前 boot 变化拒绝、已处目标版本不重复写入、前摄私有模块缺失拒绝及单独依赖检查、备份损坏拒绝回退、用户文件被其他操作修改时拒绝回退。模块索引权限检查为 0644。

首次实机安装在写 boot 前发现私有前摄不在标准模块树而停止；已自动清理新公共模块并保留旧 boot。r2 将私有前摄按已核验文件单独检查依赖，避免生成自动加载别名，新增该场景回归覆盖后实机安装/重启成功。不是仅修改说明绕过错误。

本次真实设备完整执行了 snap1 → 日期版流程。原版及其他无日期内核只有版本规则/受支持指纹的检查路径，未逐版本实机刷写。完整 boot 写入中断与主动回退主要由模拟块设备测试覆盖；未为了验收将新内核再次物理降级重刷。回退所需现场 boot、全部 GPT、文件及状态备份已保留在设备 root 私有目录。

## 发布与来源

归档精确文件白名单及模块 tar 均已检查；无设备备份、GPT/文件系统 UUID 文本或二进制、SSH 私钥、凭据、Mac 路径、私有前摄模块或表源码。initramfs 仅含 BusyBox、启动/USB/NBD 脚本及设备节点等必要启动文件。通用 GPT 清单去掉磁盘/分区 UUID，安装时仍现场检查。

Linux 7.2.6 源码 SHA256：`039aef84f2b0994aeda3f4fcfc3d02ec9d7a9bbb9020ea264c43f446c860f606`。基础补丁来源：

- [armadaos-freedom](https://github.com/mydanyi/armadaos-freedom/tree/ba692d8c06fc1d5d7cafe91325448b19765a7b08)
- [TB710FU-Archlinux](https://github.com/deinthoedoka/TB710FU-Archlinux/tree/110fbccb59ed3ea3bc323fc05601ad6fd442f02d)
- [tb710fu-hyperos-steamos](https://github.com/lich-wang/tb710fu-hyperos-steamos/tree/5e99ee13df849f592a4594910473f1bbdbd79ee3)

内核/打包/安装源码随本条目附带，网络补丁在 network-repair/。不使用已退役前摄资料。本机专属日志、原 boot/GPT、Wi-Fi profile 标识和私有前摄数据不在发布内容中。
