# 验证记录（2026-11-17）

对象：issue #19 的 UCSI 协商 host 候选与测试工具。不是内核发布，不宣称修复完成。

已执行：

- `bash -n script.sh`：通过。
- `python3 attachments/test-usb-c.py`：macOS 下 18 项通过；Linux ARM64 隔离容器中同样 18 项通过。使用临时合成 sysfs 和模拟 systemctl，未操作真实设备角色。
- 覆盖无 partner 时不写角色、只请求一次 UCSI host、请求被拒绝时不重试、仅稳定 host 无枚举仍失败、枚举消失仍失败、稳定新下游枚举观测、既有设备拒绝、UCSI 缺失拒绝、USB carrier 拒绝、活跃 NCM/绑定 gadget 拒绝、服务查询错误拒绝、watch 不写文件、隐私字段过滤、trace 独立实例与中断/部分初始化失败清理。
- 在已连接 PC 的真实 TB710FU 上经 SSH 从标准输入运行最终 helper 的 `check`：Ubuntu 26.04.1、`7.2.6-tb710fu-20261118`、UCSI 已绑定、partner 存在、data device、power sink、角色 device。未上传或安装设备文件，未请求角色切换。
- 同一设备 `watch --seconds 5`：50 个样本，最后一个约 4.995 秒；UCSI 始终绑定、partner 始终存在、角色始终 device、无下游 USB 设备。这只验证只读采样入口，不能验证 SSD host。
- `.tbfix` 往返导入验证、目录元数据及逐文件 SHA-256 校验；源码/包隐私审查：仅包含此候选的脚本、测试、说明与源码指纹，无设备备份、凭据、账户数据、设备唯一标识、镜像/模块或退役前摄材料。

未执行：

- 真实设备的 host 请求、gadget 停止/恢复、tracefs 实例创建/清理（管理员路径仅做隔离测试）。
- 目标 SSD `152d:0562` 的 C-C/OTG 对照、方向、速度、挂载、文件访问、热插拔或供电 dock 回归。用户确认无该 SSD。
- 任何内核、boot、固件、DT 或分区写入。

发布边界：以待审核 PR 和测试包提交，不加入根 `catalog.json`，不创建“已修复”release、不关闭 issue。`catalog-entry.candidate.json` 是供审核提升时参考的元数据草稿，不是正式目录。完成目标硬件复现与验收后，应按实测修订代码、哈希和状态，再经人工审核决定是否进入正式清单。
