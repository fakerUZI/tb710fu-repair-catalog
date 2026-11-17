# 日期版本与旧版本匹配

当前已发布并实机启动的目标：`7.2.6-tb710fu-20261117`，Linux 基础版本仍为 7.2.6。本次更新实际重编 Image、完整模块和本机摄像头辅助模块，同时更新嵌入配置，包含 legacy/xt NAT、ARP/security 表及 ath12k 计数修复。

```json
{
  "format": 2,
  "source_policy": "undated",
  "target_version": "7.2.6-tb710fu-20261117"
}
```

本次 `undated` 接受所有合法且不含日期的运行内核字符串，无旧版本白名单。例如原版、snap1、snap2、其他自定义版本均通过版本条件；但机型、系统、固件/GPT、boot 和配套文件仍须独立通过安装预检。所有日期版本在本次迁移中拒绝，不能以非法日期或不同日期分隔符绕过。

`undated` 只允许首次固定目标 20261117。后续发布必须使用 `source_policy: dated-before-target`。例如设备运行 `7.2.6-tb710fu-20261117`，在实际 2026-11-18 或以后发布 `7.2.6-tb710fu-20261118`。后续可接受任意更早的标准日期版本，不维护日期白名单；拒绝无日期、同日、较新和无效日期版本，并禁止未来目标日期。只读 check 可报告已处于目标版本，install 仍拒绝重复刷入。

```bash
python3 kernel-release.py --plan kernel-release.json --running
python3 kernel-release.py --plan kernel-release.json --current 7.2.6-tb710fu-snap2
```

build-kernel.sh 以计划设置真实 CONFIG_LOCALVERSION、关闭自动追加版本，完成后断言 make kernelrelease、每个模块 vermagic 及关键网络模块。当前构建器支持 Linux 7.2.6；升级 Linux 基础版本须先适配源码和构建器。首次可从审核后的 Snap/网络构建树重编；发布源码也提供干净源树构建入口。输出放在独立 artifacts/kernel-<目标版本>/build-output，不覆盖历史产物。

打包工具用于适配开发工作区，不在平板安装时构建。pack-kernel.py 必须显式提供当前 inventory、baseline、Linux Image 和发布计划；它仅替换 Linux，逐字节保留已检查的 Android 载荷、DT 和 shim，并验证 AVB。build-bundle.py 需要 --project、--inventory、--output，在完整开发工作区核验候选证明后创建新包。capture-inventory.py 输出含本机身份的私有 JSON，不能上传。实际安装仍现场重新检查固件/GPT，历史坐标不作为写入依据。

bundle-parts.json 仅支持 format 2；release 必须与 kernel-release.json、升级器 RELEASE_PLAN 完全一致，VERSION 必须等于目标。入口校验全部分片、完整归档、升级器、manifest 和模块归档后才能调用安装器。缺失载荷、校验失败或旧格式包均停止。本轮发布的七个分片均为新版 r2 归档，不调用旧 snap1 包。

下一次发布须同步更新发布计划、升级器目标及独立备份/收尾服务、摄像头版本支持和模块；完成构建/启动验证后重新生成分片与 catalog 哈希。不能只改标题、清单或目录来冒充版本更新。
