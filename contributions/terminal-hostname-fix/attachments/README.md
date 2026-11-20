# 终端 sudo「无法解析主机」修复

适用于 Lenovo TB710FU、Ubuntu 26.04 ARM64。终端执行 `sudo` 时出现
`sudo: 无法解析主机：<主机名>: 名称或服务未知`，随后仍能正常输密码执行。
原因是系统主机名没有出现在 `/etc/hosts` 的解析条目里，
`sudo` 反查主机名失败时就会打印该警告。

应用会向 `/etc/hosts` 追加一行 `127.0.1.1\t<当前主机名>`，不改动文件中的
任何既有条目（例如设备原生的 `127.0.1.1 tb710fu` 保留不动）。写入前核对
文件内容在预检后未被修改，再用临时文件原子替换并保留文件权限。
主机名来自 `socket.gethostname()`，只接受常规字母数字与点连字符。

## 应用与回退

在修复助手里用管理员权限应用。直接执行 Bash 入口默认只读。

```sh
bash script.sh
sudo env TB710FU_FIX_ACTION=apply bash script.sh
sudo env TB710FU_FIX_ACTION=rollback bash script.sh
```

已存在映射时应用会直接报告无需修改。若 `/etc/hosts` 已把当前主机名指向
其他地址，预检会拒绝执行并提示人工确认，不会覆盖任何既有解析。

## 验证

修复前后各执行一次 `sudo -k; sudo true`（或任意 sudo 命令），确认警告
不再出现。`getent hosts <主机名>` 应返回 `127.0.1.1`。此前在 TB710FU
上验证过追加映射可消除警告。

回退会恢复原始文件内容。若应用后 `/etc/hosts` 又被修改，回退会保留当前
文件和快照，供管理员核对。快照位于 `/var/backups/tb710fu-terminal-hostname-fix/`。
