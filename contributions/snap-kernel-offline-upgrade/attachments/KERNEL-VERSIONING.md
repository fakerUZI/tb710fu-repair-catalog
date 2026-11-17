# 内核版本与升级资格

2026-11-17 USB 实测运行版本仍为 `7.2.6-tb710fu-snap1`。issue #16/#17 的现有修复包替换网络模块，没有更换 Image，因此 `uname -r` 没有改变。

新构建使用 `<Linux版本>-tb710fu-YYYYMMDD`。本次发布计划示例：

```json
{
  "format": 1,
  "source_version": "7.2.6-tb710fu-snap1",
  "target_version": "7.2.6-tb710fu-20261117"
}
```

`source_version` 是本次升级唯一接受的运行内核，必须与 `uname -r` 完全相等。`target_version` 是新 Image 的真实内核 release；不能只改清单、目录、脚本标题或 uname 显示。历史 snap1 的已审核发布日期按 2026-11-16 处理，未知历史版本拒绝推测日期。

后续例子：设备已运行 `7.2.6-tb710fu-20261117`，下一次发布应将 `source_version` 改成此版本，并在实际 2026-11-18 或以后构建 `7.2.6-tb710fu-20261118` 或更晚日期。目标日期必须严格晚于旧版本，且不晚于构建/检查机器当天；同日再次更新会拒绝，不能虚构明天日期绕过。Linux 基础版本也不能降低。版本日期不匹配的设备必须取得为其单独审核的升级计划，不能忽略旧版本检查。

只读校验（不会构建或写入设备）：

```bash
python3 kernel-release.py --plan kernel-release.json --running
# 在构建主机上明确给出已经从设备核对的版本：
python3 kernel-release.py --plan kernel-release.json --current 7.2.6-tb710fu-snap1
```

`build-kernel.sh` 默认读取旁边的 `kernel-release.json`，通过策略检查后设置 `CONFIG_LOCALVERSION`、关闭自动追加版本，并断言实际 `make kernelrelease` 等于目标。输出放在独立的 `artifacts/kernel-<目标版本>/build-output`，不覆盖历史 snap1 产物。可以通过 `TB710FU_KERNEL_RELEASE_PLAN` 指定新的完整计划；它仍须通过相同校验。当前源码构建器仅支持 Linux 7.2.6，升级 Linux 基础版本需先适配源代码及构建器。

日期版本离线包采用 `bundle-parts.json` format 2，必须提供 `source_version` 和 `target_version`。入口先校验分片、升级器哈希，再核对升级器的 `OLD_VERSION` / `VERSION` 与清单一致，并在调用 install/check 前检查设备运行版本和日期。validate 只验证包，不要求运行在平板；status/finish/rollback 交由包内升级器的现场与备份校验处理，不将“回退”误当作向前升级。

现有 7 个分片仍是 format 1 的历史包：仅允许 `7.2.6-tb710fu` → `7.2.6-tb710fu-snap1`。已运行 snap1 时保留原有无写入路径；遇到日期版本会拒绝。没有把旧 Image 冒充为日期版，也没有将本例标记为已发布可安装载荷。

发布真正的日期内核前，必须同步构建 Image、完整模块及前后摄辅助模块；调整绑定旧版本的摄像头服务，检查所有 vermagic。随后根据当前固件、GPT、boot/DT 制作候选，完成启动、Snap、网络、USB、前后摄验证并准备回退。审核后才能生成新分片与全部 SHA256，更新目录条目的适用旧版本、目标版本和验证状态。此版本策略脚本本身不执行这些刷写动作。

验证：新增 16 项版本/升级入口测试与既有 26 项目录测试、9 项网络安装器测试通过；历史完整分片通过只读重组、升级器、manifest 和模块归档校验。已通过 USB 在当前平板运行同一版本策略进行只读检查，确认 snap1 符合示例 source_version；未写设备文件。尚未构建、刷写或实机验证日期版内核。
