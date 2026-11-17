# 使用修复助手进行 Snap 内核升级

请先阅读目录条目的 description/risk 和 VALIDATION.md。执行前，修复助手必须完成脚本/附件的 SHA256 核验并向用户展示写入风险，由用户确认。

下载本条目的所有附件。入口只通过 `TB710FU_ATTACHMENTS_DIR` 查找附件；不要逐个执行分片。每个分片不超过 8 MiB（小于应用限制 10 MiB），7 个分片会在 root 私有临时目录中重组成完整离线包并再次校验。载荷重建后不依赖 GitHub 网络，临时文件结束后清理。

默认执行 `install`：旧内核系统中备份并升级，不自动重启。保存工作后手动重启，systemd 会在新内核完成 Snap 收尾。设备/固件/GPT 不匹配时停止。已经运行目标新内核时跳过写入。

高级调用示例：

```bash
sudo env TB710FU_ATTACHMENTS_DIR=/path/to/downloaded-attachments \
  TB710FU_SNAP_KERNEL_ACTION=check bash /path/to/script.sh
sudo env TB710FU_ATTACHMENTS_DIR=/path/to/downloaded-attachments \
  TB710FU_SNAP_KERNEL_ACTION=install TB710FU_SNAP_USER=ubuntu bash /path/to/script.sh
```

支持的 action：`validate` 仅校验/解包，不访问设备；`check` 现场只读预检；`install` 升级；`status` 查询；`finish` 重试收尾；`rollback` 回退，随后需要手动重启。不要把 action 设为其他命令，不支持传入任意 shell 参数。

收尾/回退的完整说明见重建包内 UPGRADE-USAGE-zh.md。所有安装后代码和本机备份都保留在 root 私有备份目录，分片下载目录删除后不影响收尾。源码回退不等于设备启动分区回退。

版本命名、精确旧版本要求与严格递增日期规则见 [KERNEL-VERSIONING.md](KERNEL-VERSIONING.md)。附带日期发布计划是构建示例；现有分片仍为历史 snap1，不是日期版载荷。
