# 日期版本与旧版本匹配

本次迁移目标为 `7.2.6-tb710fu-20261117`，`source_policy` 为 `undated`：接受所有合法且不含日期的运行内核版本，不再要求精确等于 snap1，也没有旧版本白名单。例如 `7.2.6-tb710fu`、`7.2.6-tb710fu-snap1`、`7.2.6-tb710fu-snap2` 和其他不带日期的自定义版本均通过版本条件。

```json
{
  "format": 2,
  "source_policy": "undated",
  "target_version": "7.2.6-tb710fu-20261117"
}
```

所有带日期的旧版本在本次迁移中拒绝，包括早于目标的日期。含 YYYYMMDD 或 YYYY-MM-DD / YYYY.MM.DD / YYYY_MM_DD 的版本不能绕过检查；不合法日期也不能当作无日期版本。

`undated` 仅允许本次固定目标 `7.2.6-tb710fu-20261117`，不能复制到后续日期包。后续更新使用 `source_policy: dated-before-target`。例如设备已经运行 `7.2.6-tb710fu-20261117`，在实际 2026-11-18 或以后发布 `7.2.6-tb710fu-20261118`。目标日期必须严格大于设备当前内核日期；同日更新、日期倒退及目标使用未来日期都拒绝。后续包仅接受标准 `<Linux版本>-tb710fu-YYYYMMDD` 日期版本，不再接受无日期内核。无需为每个历史日期维护白名单。

版本条件与设备可安装性分开校验。接受某个版本字符串并不代表其固件、GPT、boot/DT、模块和摄像头服务均兼容。真正执行安装必须由审核后的日期升级器完成现场校验、备份、写前检查、写后回读及回退。

只读检查版本条件：

```bash
python3 kernel-release.py --plan kernel-release.json --running
python3 kernel-release.py --plan kernel-release.json --current 7.2.6-tb710fu-snap2
```

`build-kernel.sh` 读取此计划设置真实 `CONFIG_LOCALVERSION`，关闭自动追加版本，并断言 `make kernelrelease` 与目标一致。输出保存在独立的 `artifacts/kernel-<目标版本>/build-output`。构建脚本目前适配 Linux 7.2.6。

仓库仅保留 `kernel-offline-upgrade/script.sh` 这一项内核升级入口，放在 catalog 第一项。历史“原版 → snap1”入口、兼容分支和七个二进制分片已从当前树删除。Git 历史中的旧版本不会被此入口调用。

当前日期版 Image、完整模块及摄像头配套尚未构建/验证，`bundle-parts.json` 明确标记 `payload_status: not-built`，不含任何旧分片。check 报告版本资格及载荷缺失；install/validate/finish/rollback 等动作会停止。不会将旧 snap1 Image 改名冒充日期版。

真正发布载荷时使用同一 format 2 清单，`release` 必须与 kernel-release.json、升级器 `RELEASE_PLAN` 完全一致，`VERSION` 必须等于目标版本；载荷状态改为 ready，并提供所有分片、根目录、归档/升级器/manifest 哈希。入口仍验证上述绑定后才能调用升级器。必须先完成对应 Image、完整模块、前后摄辅助模块和服务适配、当前固件/GPT 审核及实机启动和功能验证。
