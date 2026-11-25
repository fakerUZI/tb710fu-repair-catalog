# Wi-Fi 问号与连通性检测修复（测试版）

适用于 TB710FU 的 Ubuntu 桌面。网页可以打开、当前主连接是 Wi-Fi、图标却持续显示问号时，在修复助手中单独打开此条目并运行。公共 Wi-Fi 若弹出登录页，请先完成认证。

脚本先显示 NetworkManager 的连通性、探测开关、探测地址和 Wi-Fi 的 IPv4、IPv6 状态。全局结果为 `full` 时直接结束。对于 `limited` 或 `unknown`，脚本沿用现有探测地址。地址为空时会尝试安装 Ubuntu 的 `network-manager-config-connectivity-ubuntu` 软件包并重新加载配置，软件源需要可达。探测可用后按需打开开关，发起一次检测。启用后 NetworkManager 会定期访问配置的探测地址。

在修复助手中运行需要管理员授权。在终端中可用 `bash script.sh check` 单独查看状态。日志最后一行若报告 `full`，请同时观察桌面图标。若仍显示问号，请记录脚本输出和图标状态，方便复查。探测返回 `portal` 表示需检查网络认证，`limited` 也可能由 DNS 或探测地址不可达引起。

## 撤销本次更改

查看运行日志中最初的开关状态。若原为 `ConnectivityCheckEnabled = b false` 且脚本打印了“正在启用连通性检测”，可在 GNOME 隐私设置中关闭连通性检测。若本次 apt 输出还显示新安装了上述软件包，可先预览 `apt -s remove network-manager-config-connectivity-ubuntu`，确认仅移除该包后运行 `sudo apt remove network-manager-config-connectivity-ubuntu` 和 `sudo nmcli general reload conf`。原来已安装的软件包应保留。
