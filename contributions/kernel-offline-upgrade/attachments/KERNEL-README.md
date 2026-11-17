# TB710FU 日期内核升级

这是目录中唯一的内核升级入口，位于第一项。当前目标为 `7.2.6-tb710fu-20261117`，本次接受所有不带日期的合法旧内核版本。后续更新只接受日期版本，目标日期必须严格晚于设备当前内核日期，详见 [KERNEL-VERSIONING.md](KERNEL-VERSIONING.md)。

当前状态：日期载荷待构建；旧 snap1 升级脚本和七个分片已删除，不能安装旧包。默认 check 只读，检查机型、系统和版本资格，并明确报告载荷缺失，不声称完成固件/GPT/boot 预检。

```bash
TB710FU_ATTACHMENTS_DIR=/path/to/attachments \
  TB710FU_KERNEL_ACTION=check bash /path/to/script.sh
```

`TB710FU_KERNEL_ACTION` 支持 check/install/validate/status/finish/rollback，兼容已有客户端的 `TB710FU_SNAP_KERNEL_ACTION`；两者都未设置时为 check。install/finish/rollback 等写操作需要管理员权限。当前缺少日期版载荷，除 check 外均停止且不写设备。

载荷实际发布后，客户端必须下载清单全部附件并逐个验证 SHA256，再通过 `TB710FU_ATTACHMENTS_DIR` 指定目录。分片单个不得超过 8 MiB；单附件须小于应用 10 MiB 限制。安装前需展示实际刷写风险并取得用户授权，不自动重启。现场固件/GPT、镜像、模块及回退校验仍须通过。
