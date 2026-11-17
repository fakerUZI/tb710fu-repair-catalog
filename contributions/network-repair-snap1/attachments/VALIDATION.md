# 验证记录 — 2026-11-17

## 构建及适用边界

Linux `7.2.6-tb710fu-snap1`、aarch64、完整匹配的 Module.symvers 和构建配置。
更改前，对照平板实际模块与保留构建树，ath12k、ath12k_wifi7、x_tables 的
`.text` 逐字节相同。新模块完成完整构建及 modpost，vermagic 和模块依赖
通过核查。不是仅凭相同版本字符串加载任意模块。

修正了 issue #16 的根因表述：此树的 Makefile 以
`CONFIG_IP_NF_IPTABLES_LEGACY` 控制 `ip_tables.ko`，其上游总开关是
`CONFIG_NETFILTER_XTABLES_LEGACY`。因此 `IP_NF_IPTABLES=m` 却无该模块，
不足以证明构建配置与产物不一致。完整构建流程已加入依赖配置及产物断言。

模块更新不启用 `NETFILTER_FAMILY_ARP`，以免改变运行内核的网络命名空间
结构布局；不添加 security 表。新配置中影响现有实现的部分仅在 x_tables
和 nf_nat 模块中，未替换 Image、boot、DT、固件或摄像头模块。

## netfilter 实机

四种后端全部通过：iptables-legacy、ip6tables-legacy、iptables-nft、
ip6tables-nft。每组在全新 `unshare --net` 中创建 loopback 测试环境：

- DNAT：真实 UDP 数据到达重写后的地址及端口。
- REDIRECT：真实 UDP 数据到达重定向端口。
- SNAT：接收端观察到重写后的来源地址。
- REJECT：接收端未收到数据，发送端同步 EPERM 或收到 ICMP 对应错误。
- NETMAP：规则插入成功；未声称完成实际 NETMAP 转发测试。
- filter、mangle、raw、nat 四张表可查询。

测试没有向宿主机防火墙添加规则。修复目录真实 Bash 入口的 verify 动作
也已在实机执行通过，而不只是在开发机直接调用测试脚本。

## Wi-Fi 实机

实际加载的 ath12k 报告 `tb_vdev_count_fix=1`，原有 `tb_poll_off=1`、
`tb_hw_dedup=1` 保留。未更换 WCN7850 固件或保存的网络配置。

USB 救援正常时，对原 Wi-Fi profile 做 10 次断开/重连，并持续查询：

- 观察 302.21 秒。
- 169 次 station 查询均返回 signal 字段。
- 最慢查询 9.46ms，未出现先前每次约三秒的等待。
- 新观察窗口内 `time out while waiting for fw stats done` 为 0。
- 开始与结束各一次强制经 Wi-Fi 接口的 HTTPS 请求均返回 200。

这证明本次短时重连场景通过，不等于全天压力测试、所有 AP 带宽切换、
漫游或 aTrust 业务验证均已完成；未操作用户 VPN 账号或 AP 设置。

## 安装、回退与集成

9 项隔离环境测试通过：错误内核、载荷/清单损坏、未知原模块、写入中断
恢复、损坏备份拒绝、重复安装、已审核修订升级保留首次快照、主动回退、
模块索引权限修正。实际安装与重复 apply 通过，原备份保留。

初版安装曾因私密备份 umask 导致 depmod 索引为 0600，已在安装器中修正
为标准 0644，并加入回归测试。早期 REJECT 测试只接受异步 ICMP 错误，
本机 LOCAL_OUT 可同步返回 EPERM；测试已修正，同时检查接收端未收到数据。

首次整机重启后 netfilter 流量测试通过；最终模块修订又通过 Wi-Fi 重载。
随后在实机主动回退模块文件，逐项读回原始哈希，再重新安装，首次备份
快照保持不变。最后再次整机重启，冷启动即加载修复版本 1，四种 netfilter
后端再次通过，Wi-Fi 信号查询正常，前后摄再次完成采集。
前摄再次采集 30 帧 1280×720 软件 ISP 输出，约 30fps；后摄再次采集三帧
4208×3120 RAW。前摄、后摄和对焦服务 active，systemd 无失败单元。
boot_a 与 boot_b 哈希在本次网络模块更新前后保持一致。

设备专属清单、原模块备份、原始日志、Wi-Fi profile 标识和摄像头图像均
未包含在此贡献中。归档仅包含网络模块、通用清单、预载配置和执行/验证器。

## 对应源码

本目录的离线包内模块来自以下输入及同仓库构建脚本：

- Linux 7.2.6 tar.xz SHA256：
  `039aef84f2b0994aeda3f4fcfc3d02ec9d7a9bbb9020ea264c43f446c860f606`。
- [armadaos-freedom](https://github.com/mydanyi/armadaos-freedom/tree/ba692d8c06fc1d5d7cafe91325448b19765a7b08)，
  提交 `ba692d8c06fc1d5d7cafe91325448b19765a7b08` 的端口补丁。
- [TB710FU-Archlinux](https://github.com/deinthoedoka/TB710FU-Archlinux/tree/110fbccb59ed3ea3bc323fc05601ad6fd442f02d)，
  提交 `110fbccb59ed3ea3bc323fc05601ad6fd442f02d`。
- [tb710fu-hyperos-steamos](https://github.com/lich-wang/tb710fu-hyperos-steamos/tree/5e99ee13df849f592a4594910473f1bbdbd79ee3)，
  提交 `5e99ee13df849f592a4594910473f1bbdbd79ee3`。
- `snap-kernel-offline-upgrade/attachments/network-repair/` 中的三份补丁、
  配置片段和构建断言。前两份保留上游作者，第三份仅添加只读版本标识。

上游来源：[restart 计数](https://lists.openwall.net/linux-kernel/2026/10/20/2963)、
[station start/stop 及作者后续测试](https://lore-kernel.gnuweeb.org/ath12k/20261111-ath12k-sta-vdev-start-once-v1-1-a01ed78d5460@gmail.com/T/)。
