# USB-C / UCSI host 候选与测试包（issue #19）

状态：**待审核、待目标 SSD 实测**。2026-11-17 用户确认当前没有 issue 中的 SSD，要求先完成可审查候选和测试包。本包修正测试方法中“解绑 UCSI / 强写 role”的路径，尚不能宣称修复 C-C 直连 SSD，也不应关闭 [issue #19](https://github.com/fakerUZI/tb710fu-repair-catalog/issues/19)。

适用：Lenovo TB710FU、Ubuntu 26.04 arm64、`7.2.6-tb710fu` 系列内核，单口 `a600000.usb`、`ucsi_glink.pmic_glink_ucsi`。本轮读取的设备版本为 `7.2.6-tb710fu-20261118`；不是对其他内核的兼容性承诺。依赖 Python 3、Bash；host 候选另需 systemd 和 root，trace 需已挂载的 tracefs、UCSI tracepoints 和 root。无额外下载。

## 候选行为

- 默认 `check` 只读；`watch` 每 0.1 秒采样，默认 30 秒。
- `host` / 修复助手的 `apply`：要求 UCSI 已绑定、PC USB 网络已断开、NCM 服务已停止、gadget UDC 已释放、没有下游 USB 设备。等待 partner 出现后，最多向 `port0/data_role` 写一次 `host`，通过内核 UCSI `SET_UOR` 请求角色协商。保留 UCSI，不写底层 role，不要求 source，不强设 3A。
- 未出现 partner、只稳定 host 而没有枚举、短暂枚举后消失，均返回 **3（未解决）**。系统调用或前置条件错误返回 1，参数错误 2，中断 130。返回 0 仅表示观测到下游枚举与 host 连续至少 3 秒；VID:PID、设备容量、挂载和文件可读仍须现场确认，不能当作整个 issue 修复成功。
- `trace` 在独立 tracefs instance 中打开三个 UCSI 事件，停止后删除自己的 instance，不读写全局 trace 或全局事件开关；异常和 Ctrl+C 同样清理。缓冲区有限，极高事件量或硬杀进程可能需要手动清理，见下文。
- 不安装文件、替换原 `tb-usbmode`、启用守护进程、修改 boot/DT/固件/内核、写分区或挂载存储。相机适配不受本包修改。

## 执行与配合测试

先阅读脚本与风险，在解压目录执行。也可使用修复助手导入 `.tbfix` 审查；本候选尚未加入正式 `catalog.json`，无需更新内核。

```sh
bash script.sh
TB710FU_FIX_ACTION=watch bash script.sh --seconds 30
sudo env TB710FU_FIX_ACTION=trace bash script.sh --seconds 30
```

trace 与 watch 不主动改变 USB 数据角色，可以分别记录 C-C 失败及 C-OTG 成功的过程。不要在后台跑强制 host 循环或旧 `tb-usbmode host`；它们会污染证据。日志只输出角色、UCSI 是否绑定、是否有 partner、方向、电源模式、VID:PID/速率及 UCSI 命令/连接事件，省略序列号、账户、IP、UUID、挂载路径、进程名与 PID。发布前仍须人工审查日志。

host 候选须在平板本地终端或独立 Wi-Fi SSH 运行；**先卸载并拔出所有 USB 存储，再断开 PC**。在本地执行以下临时准备（会中断 USB 救援网络）：

```sh
# 若先前旧 host 命令已解绑 UCSI，重新绑定后确认有 port0：
sudo tb-usbmode rebind
ls /sys/class/typec/
sudo systemctl stop tb-ubuntu-usb-ncm.service
# 本机使用 g1；先确认没有其他 gadget，且 PC 已拔出：
sudo sh -c 'printf "\n" > /sys/kernel/config/usb_gadget/g1/UDC'
sudo env TB710FU_FIX_ACTION=host bash script.sh --seconds 30
# 此时插入 SSD，观察结果；不要同时运行其他 USB 切换程序。
```

若 UCSI 没有恢复 port0、服务停止失败或 UDC 释放失败，停止测试，保留证据。脚本不会绕过这些失败。等待时可在另一个终端运行 trace，获取协商/连接时间线。sysfs 的一次 UCSI 请求本身可能等待驱动超时，因此总墙钟时间可能比采样窗口长。

## 风险与恢复

数据角色切换会断开原 USB 数据连接，可能中断 USB SSH、键鼠、显示扩展坞或存储；已挂载存储可能损坏。禁止在存储传输期间试验。不要远程切断唯一救援链路。

本工具不自动反向切换已请求的角色，不自动启动 gadget 服务，避免测试成功后突然断开刚挂载的 SSD。退出、超时或 Ctrl+C 后，先在桌面安全卸载 SSD并拔线，再在平板本机执行：

```sh
sudo tb-usbmode rebind
# 只有仍存在 partner 时才支持协商 data_role；无 partner 时不要据 ENOTCONN 判定恢复失败。
sudo systemctl start tb-ubuntu-usb-ncm.service
# 重新连接 PC，确认 USB 网络和原服务恢复。
```

需要与仍连接的 PC 重新协商时，可在本机运行 `sudo sh -c 'echo device > /sys/class/typec/port0/data_role'`；失败时检查 UCSI 状态，不循环抢写 role。不使用旧 `tb-usbmode device`，它会加入历史默认路由。若此前服务本来停止，则保持停止。没有持久安装，无需卸载；普通重启会丢弃角色请求并回到系统原有配置，但须先卸载存储、保存工作。

trace 遭 SIGKILL/断电而未清理时，先确认没有本工具运行，再只对自己的 `/sys/kernel/tracing/instances/tb710fu-usb-<PID>` 写 `tracing_on=0` 并 `rmdir`；不要删除其他会话的实例。

## 证据边界和后续判据

现有脚本先解绑 UCSI 再强写 role，无法作为 C-C CC/PD 协商正确的证据。本轮核对当前构建源码发现：

1. `ucsi_dr_swap()` 要求已有 partner，使用 `SET_UOR` 协商；原始 `usb_role_switch` 写入只改变本地控制器，不等价于协商。
2. `ucsi_partner_change()` 在对端 USB 数据标志缺失时请求 `USB_ROLE_NONE`；DWC3 `dwc3_usb_role_switch_set()` 会将 NONE 映射到默认角色。因此 host→device 也可能是断连/数据标志缺失，尚不足以认定 PPM 错误 DR_SWAP。
3. data host 和 power sink 可以共存，尤其是带供电的 dock。不能从 source/sink 直接推断 host/device；不要无证据修改供电策略。

当前缺少目标 SSD，没有对故障做新的实机复现，不能排除 CC 接入、方向/mux、固件事件或供电瞬态。U 盘/USB2 对照也不能排除 SuperSpeed 路径。保留 issue 已报告可用的 C-OTG 作为临时接法；PD hub 未被该报告验证，不列为保证有效的替代方案。

验收至少包括：目标 `152d:0562` 的 C-C 两种插头方向、多次冷插与热插、实际枚举速率、块设备容量、只读文件访问、host 稳定性；再回归 C-OTG、U 盘、充电、PC NCM、带供电 dock 的 host/sink 与插拔恢复。未通过前不加入正式可执行清单，不发布“已修复”版本。

详见 `VALIDATION.md`。源文件 SHA-256 见 `source-evidence.json`；这是核对本机当前构建树的指纹，不是可安装内核包。后续如需内核/boot 写入，应重新核对当时固件和 GPT，单独制作兼容与回退方案。
