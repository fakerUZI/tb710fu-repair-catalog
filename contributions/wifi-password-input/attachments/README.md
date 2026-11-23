# TB710FU Wi-Fi 密码输入修复

适用于 Ubuntu 桌面点击新 Wi-Fi 后密码提示立即消失，或在 GNOME 右上角的 Wi-Fi 密码框点触屏键盘无效的情况。脚本通过 Ubuntu 软件源补齐 GNOME 密钥环和登录时所需的 PAM 模块，随后在现有 TB710FU 屏幕键盘组件中启用 Wi-Fi 密码框的触摸输入。

密码字符直接进入当前 Wi-Fi 密码框。GNOME 和 NetworkManager 继续负责提交并保存网络配置。安装后保存工作，注销并重新登录，再尝试连接新 Wi-Fi。

若平板当前无法联网，可先连接有线网络、使用 USB 网络共享，或在本机终端用 `nmcli --ask device wifi connect "网络名称"` 交互式连接一次。

- `TB710FU_FIX_ACTION=check` 检查系统依赖和补丁状态。
- `TB710FU_FIX_ACTION=apply` 由修复助手默认执行。现有扩展只在补丁上下文兼容时更新。
- `TB710FU_FIX_ACTION=rollback` 恢复本条目更新的键盘代码。GNOME 密钥环继续为桌面和已保存凭据提供服务。
