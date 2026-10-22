# Windows 桌面网络监控系统

这是一个无需服务器的本地网络监控工具。程序运行在运维人员的 Windows 电脑上，按照各目标独立设置的间隔执行 Ping、TCP、HTTP 或 SNMP 检测，SNMP 检测还能看到交换机端口状态、服务器 CPU/内存等深度指标，并可通过企业微信群机器人发送故障和恢复通知。

## 主要功能

- 管理监控目标：添加、编辑、删除目标；配置名称、地址、检测类型、间隔和告警开关。
- 独立调度检测：后台调度线程配合有限工作线程池，避免单个慢请求阻塞其他目标。
- 支持四种检测：
  - Ping：包数量和单包超时可在“系统设置”里全局配置（默认 1 包、2000 毫秒），Windows 对应 `ping -n <数量> -w <超时>`，Linux 对应 `ping -c <数量> -W <超时秒>`；多包时只要收到一个回复即视为可达，详情列会附加丢包率。
  - TCP：连接超时 3 秒，地址格式为 `主机:端口`，IPv6 使用 `[IPv6]:端口`。
  - HTTP：发送 GET 请求，超时 5 秒，2xx/3xx 状态码视为正常。
  - SNMP（v1/v2c）：查询设备可达性，并按"设备类型"补充深度指标——交换机额外统计各端口 up/down 数量，服务器额外查 CPU/内存使用率。详见下方"SNMP 深度监控"。
- 设备分类：每个监控目标可标记"设备类型"（交换机/服务器/其他），用于在列表中区分展示。
- 告警控制：连续失败阈值、重复告警冷却、可选恢复通知；达到失败阈值时，本机还会立即播放系统提示音、闪烁窗口标题、在监控大屏显示红色告警标记，并尝试发送系统托盘通知。即使企业微信 Webhook 未配置或发送失败，本机声光告警仍会工作。SNMP 目标的深度指标会附加在告警消息里作为参考信息，但不参与失败阈值判断（阈值判断仍然只看目标是否可达）。
- 数据留存：SQLite 保存检测历史、今日网页巡检批次和告警状态，启动时及运行期间每天清理超过保留期限的数据。
- 桌面操作：实时状态列表、立即检测、CSV 导出、最小化到系统托盘。
- 每日网页巡检：点击主界面的“网页巡检”即可立即检查全部启用的 HTTP 目标，并留存本轮进度；失败项可一键重测，完成后可导出当日 CSV。每个网站或安全设备页面可设置页面标识文字、HTTP 超时，以及是否验证 HTTPS 证书；适合检查门户、管理登录页和使用自签名证书的内部设备。
- 监控大屏：启动后优先进入独立的深色值守大屏，集中显示目标状态矩阵、当前异常、每日网页/设备页面巡检，以及真实保存的近 24 小时状态轨迹；`F11` 切换全屏，`Esc` 返回工作台。大屏只消费同一份 SQLite 快照，不会另起检测或额外查询数据库。
- 个人运维工作台：采用 Signal Desk 紧凑操作台；异常队列优先展示，左侧可快速切换全部目标、网页/设备页面、网络与端口、仅异常，底部常驻显示当日网页巡检进度、重测与导出操作。
- 稳定性保护：同一目标正在检测时不会重复排队；SQLite 暂时写入失败不会把实际正常目标误判为异常；告警阈值与冷却状态可跨重启保留。
- 端口测试工具：菜单“工具 → 端口测试工具”，可手动输入主机和端口（支持列表和范围，如 `80,443,8000-8010`）即时测试 TCP 连通性，并发扫描、结果实时显示。
- 单文件打包：使用 PyInstaller 生成 `NetworkMonitor.exe`。

## 运行环境

- Windows 10/11
- Python 3.10 或更高版本
- 项目运行依赖见 `requirements.txt`

Tkinter 和 SQLite 随标准 Python 发行版提供。使用精简或自定义 Python 时，请确认已包含 Tcl/Tk。

## 从源码运行

在 PowerShell 中进入项目目录：

```powershell
cd D:\nxzxwl\NetworkMonitor
python -m pip install -r requirements.txt
python -m app.main
```

程序首次运行会在程序目录自动创建：

```text
config/targets.json
config/settings.json
data/monitor.db
logs/monitor.log
```

## 配置说明

推荐通过“文件 → 配置目标”修改配置。也可以在程序停止后直接编辑 JSON 文件。

### targets.json

每个目标包含以下字段：

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| `name` | 显示名称，同一配置中应唯一 | `核心交换机` |
| `address` | IP、域名、TCP 地址或 HTTP URL | `192.168.1.1` |
| `check_type` | `ping`、`tcp`、`http` 或 `snmp` | `tcp` |
| `device_role` | 设备类型：`switch`（交换机）、`server`（服务器）、`other`（其他） | `switch` |
| `interval` | 检测间隔，单位为秒，最小为 1 | `60` |
| `alert_enabled` | 是否启用企业微信与本机声光告警 | `true` |
| `snmp_port` | SNMP 端口，仅 `check_type=snmp` 时使用 | `161` |
| `snmp_version` | SNMP 版本，`v1` 或 `v2c` | `v2c` |
| `snmp_community` | SNMP Community 字符串 | `public` |
| `http_expected_text` | 页面必须包含的文字；空值仅检查 HTTP 连通性 | `管理登录` |
| `http_timeout_seconds` | HTTP 单目标超时，范围 1-60 秒 | `5` |
| `http_verify_tls` | 是否验证 HTTPS 证书；自签名设备可设为 `false` | `true` |

地址示例：

```text
Ping: 192.168.1.1
TCP: 192.168.1.10:443
TCP IPv6: [2001:db8::1]:443
HTTP: https://intranet.example.com/health
HTTP 安全设备: https://192.168.1.254/login （可设置页面标识“管理登录”；自签名证书可关闭验证）
SNMP: 192.168.1.1   （管理 IP，端口/版本/Community 在单独字段里配置）
```

## SNMP 深度监控

`check_type=snmp` 的目标在判断可达性（GET `sysDescr`/`sysName`）之外，会按 `device_role` 补充展示型指标（存入数据库、显示在列表"详情"列、附加在告警消息里，但**不**参与失败阈值判断——阈值告警仍然只看目标是否可达）：

- `device_role=switch`：统计所有接口的 `ifOperStatus`，给出"端口 N 个：X up，Y down"的摘要（testing/dormant 等其他状态归入"其他"，不计入 down）。
- `device_role=server`：查询 HOST-RESOURCES-MIB 的 CPU 使用率（`hrProcessorLoad` 均值）和内存使用率（按 `hrStorageType=hrStorageRam` 定位内存条目）。

注意事项：

- 仅支持 SNMP v1/v2c（community 字符串认证），不支持 SNMPv3（认证/加密），目标设备需已开启 SNMP 服务并允许本机访问。
- Community 字符串以明文在网络上传输，请勿在不受信任的网络上使用，也不建议继续使用默认的 `public`；程序不会把明文 Community 写入数据库或日志，告警发送失败时会先脱敏再记录。
- Linux 主机上"内存使用率"是原始占用（未扣减可回收的缓存/缓冲区），通常比 `free` 命令里的"已用"偏高，仅供参考，不建议直接当作阈值告警依据。
- 交换机/服务器的 CPU、内存、端口指标目前只用于展示，暂不支持基于这些指标单独设置告警阈值。

### settings.json

| 字段 | 含义 | 默认值 |
| --- | --- | --- |
| `wechat_webhook` | 企业微信群机器人 Webhook | 空 |
| `failure_threshold` | 触发告警前的连续失败次数 | `3` |
| `cooldown_minutes` | 同一目标重复告警冷却时间 | `5` |
| `send_recovery_notification` | 恢复后是否发送通知 | `true` |
| `desktop_alert_enabled` | 是否启用本机声光告警 | `true` |
| `desktop_alert_sound_enabled` | 是否播放 Windows 系统告警声音 | `true` |
| `desktop_alert_flash_enabled` | 是否闪烁窗口标题并显示系统托盘通知 | `true` |
| `log_level` | 日志级别 | `INFO` |
| `history_retention_days` | 历史数据保留天数 | `30` |
| `ping_count` | 每次 Ping 检测发送的包数量（1-10） | `1` |
| `ping_timeout_ms` | Ping 单个包的超时时间，单位毫秒（100-10000） | `2000` |

## 企业微信机器人

在企业微信群中添加群机器人，将 Webhook 填入配置窗口或 `config/settings.json`：

```text
https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY
```

安全注意事项：

- Webhook 中的 `key` 等同于发送凭据，不要提交到 Git、截图公开或发送给无关人员。
- 程序不会在正常日志中记录完整 Webhook；请求异常会先对 URL 和 `key` 参数脱敏。
- 发布或复制程序时，不要一并分发包含真实 Webhook 的 `config/settings.json`。

## 打包为单文件 EXE

```powershell
cd D:\nxzxwl\NetworkMonitor
python -m PyInstaller --clean --noconfirm build.spec
```

输出文件：

```text
dist/NetworkMonitor.exe
```

将 EXE 复制到一个用户具有写权限的独立目录后运行。配置、数据库和日志会创建在 EXE 同级目录，因此不建议直接放入需要管理员权限才能写入的 `Program Files`。

## 测试

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app tests
```

## 数据与日志

- 历史数据库：`data/monitor.db`（含检测历史、网页巡检批次和告警状态）
- 运行日志：`logs/monitor.log`（单文件达到 2 MB 后自动滚动，最多保留 5 个历史文件）
- CSV 报告：通过“文件 → 导出报告”选择保存位置
- 今日网页巡检 CSV：通过数据面板或“文件 → 导出今日巡检”保存
- 配置备份：每次保存 `config/targets.json` 或 `config/settings.json` 前会保留同目录的 `.bak` 文件；发现损坏 JSON 时会复制为带时间戳的 `.broken-*.json`，避免手工配置被覆盖。

修改配置或迁移程序前，建议先停止监控并备份 `config` 与 `data` 目录。

## 本机声光告警

本机声光告警使用与企业微信相同的“连续失败阈值”，避免短暂丢包反复打扰。一个目标从故障到恢复期间只会播放一次故障提示音；恢复时会清除大屏和工作台的红色告警状态，并可按“恢复正常时发送通知”设置给出托盘恢复提示。可在“文件 → 配置目标 → 系统设置”中分别关闭本机告警、声音或窗口/托盘提醒。

此功能是软件内的声音与视觉提醒，不会直接控制外接实体声光报警器；如需接入实体设备，需根据设备提供的网络接口或 USB/串口协议另行适配。

## 当前验证状态

本项目已完成自动化测试、Python 编译检查、PyInstaller 单文件打包及干净目录首次启动验证。真实企业微信消息发送需要有效 Webhook，未随项目提供或验证；使用前请由凭据持有人在测试群中完成一次故障与恢复演练。
