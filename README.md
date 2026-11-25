# 网哨 · Windows 桌面网络监控系统 (NetworkMonitor)

一款轻量、免部署服务器的 **Windows 本地网络监控与个人运维工作台**。支持 Ping / TCP / HTTP(S) / SNMP 实时监测、深色 NOC 监控大屏、SNMP 交换机端口与服务器资源深度采集、网段自动发现、历史趋势曲线、内置排障工具箱，以及企业微信机器人告警与每日巡检报告。

## 核心亮点

- **多协议实时监控**：
  - **Ping**：支持自定义发包数与单包超时，多包自动统计丢包率。
  - **TCP**：端口连通性检测（支持 IPv4 / `[IPv6]:端口`）。
  - **HTTP(S) 网页巡检**：支持页面关键字匹配、单目标超时、自签名证书跳过校验，以及 **HTTPS 证书到期天数自动检测与提前预警**。
  - **SNMP（v1/v2c）深度监控**：自动读取设备名、运行时长（`sysUpTime`）、交换机端口 UP/DOWN 统计与**实时进/出带宽（Mbps）**、服务器 CPU / 内存 / **磁盘分区使用率**。
- **现代深色监控大屏（纯原生 Tkinter Canvas）**：
  - 圆角 KPI 卡片 + 环形可用率仪表盘（Donut Gauge）+ 24 小时状态热力图。
  - 节点卡片内置 **SNMP CPU/内存水位条**与**交换机端口占比条**，支持分页与自动轮播（`F11` 全屏，`Esc` 返回工作台）。
- **高效运维工作台与一站式排障**：
  - 主列表支持**实时搜索**（含名称/地址/分组/运维备注）、**分组筛选**、**双击编辑**、添加/编辑时**一键测试连接**（SNMP 自动回填设备名）。
  - 右键菜单直达：**SNMP 详情（端口/带宽/磁盘）**、**单设备历史延迟曲线与状态跳变时间线**、**持续 Ping 诊断**、**路由追踪（Tracert）**、**端口测试工具**、**SSH 终端**、**远程桌面（RDP）**、**维护静默（30 分钟 / 2 小时）**。
  - **网段 SNMP 自动发现**：并发扫描网段内开启 SNMP 的交换机与服务器，一键勾选导入监控。
- **精准告警闭环与常驻体验**：
  - 连续失败阈值、冷却控制、故障恢复通知、**关联告警抑制（防断网刷屏）**、**慢响应预警**、**SNMP 资源阈值告警**、**HTTPS 证书到期提醒**。
  - 本机声光告警（系统提示音、窗口标题闪烁、托盘气泡）+ **每日定时企业微信巡检报告**（含异常清单、网页巡检进度、证书与高负载提醒）。
  - **单实例防多开**、**Windows 开机自启动（`--autostart` 静默到托盘）**、**托盘红绿灯状态图标**、**高 DPI 高清适配**、**休眠唤醒防误报**、**配置包 ZIP 一键备份与脱敏导出**。

## 运行环境

- Windows 10 / 11（64 位）
- Python 3.10+（推荐 Python 3.13，依赖见 [requirements.txt](requirements.txt)）

## 快速开始

### 方式一：从源码运行

```powershell
python -m pip install -r requirements.txt
python -m app.main
```

### 方式二：打包为单文件 EXE

```powershell
python -m PyInstaller --clean --noconfirm build.spec
```

生成的 `dist/NetworkMonitor.exe` 为独立单文件，复制到任意可写目录即可直接双击运行。首次运行会自动在同级目录创建：

```text
config/targets.json   # 监控目标配置（保存前自动保留 .bak 备份）
config/settings.json  # 系统与告警设置
data/monitor.db       # SQLite 历史记录、巡检批次与告警状态
logs/monitor.log      # 运行日志（2 MB 自动滚动）
```

## 项目架构

```text
app/
├── main.py                     # 入口（高 DPI、单实例互斥、--autostart 静默托盘）
├── core/
│   ├── monitor.py              # 后台独立调度、线程池、休眠唤醒防抖、日报触发
│   ├── alert.py                # 阈值/冷却/恢复、关联告警合并、资源与证书预警、静默
│   ├── desktop_alert.py        # 本机声光告警策略
│   ├── daily_report.py         # 企业微信每日巡检报告生成
│   ├── ping_check.py           # Ping 检测与丢包解析
│   ├── tcp_check.py            # TCP 端口检测
│   ├── http_check.py           # HTTP(S) 页面校验与 TLS 证书到期检测
│   ├── snmp_check.py           # SNMP 采集、端口实时带宽、并发网段发现
│   └── port_scan.py            # 并发 TCP 端口扫描
├── gui/
│   ├── main_window.py          # 个人运维工作台主窗口与右键排障联动
│   ├── monitor_wall.py         # Canvas 重绘深色监控大屏与分页轮播
│   ├── dashboard.py            # 数据概览面板与巡检底栏
│   ├── config_dialog.py        # Tab 化系统设置、目标编辑（含测试连接）、批量导入
│   ├── snmp_dialogs.py         # SNMP 设备详情窗（端口/带宽/磁盘）与网段发现窗
│   ├── diagnostics_dialogs.py  # 历史延迟曲线/故障时间线、实时 Ping/Tracert 诊断窗
│   ├── port_test_dialog.py     # 手动端口测试工具
│   ├── tray.py                 # 系统托盘与红绿灯图标
│   └── theme.py                # 工作台视觉主题
├── integrations/
│   └── wechat.py               # 企业微信机器人推送（含 Webhook 脱敏）
└── utils/
    ├── config.py               # JSON 原子写入、备份与 ZIP 配置包导入/导出
    ├── database.py             # SQLite 历史存储、状态跳变提取与周期清理
    ├── autostart.py            # Windows 开机自启注册表与单实例互斥锁
    ├── shortcut.py             # 桌面快捷方式创建
    └── logger.py               # 滚动日志初始化
```

## 自动化测试

```powershell
python -m unittest discover -s tests -v
```

## 安全说明

- 企业微信 Webhook `key` 与 SNMP `community` 均不会写入日志，异常信息自动脱敏。
- 通过「文件 → 备份配置包 (ZIP)」导出配置时，可选择自动清除 Webhook 与 Community 敏感字段，便于安全分享或迁移。
