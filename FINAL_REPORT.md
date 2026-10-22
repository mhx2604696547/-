# Windows 桌面网络监控系统最终验收报告

验收日期：2026-08-17  
项目路径：`D:\nxzxwl\NetworkMonitor`

## 交付结论

项目已完成核心功能、自动化测试、单文件打包和干净目录首次启动验证，可进入用户现场试运行。真实企业微信 Webhook 未由本次验收持有，因此真实消息投递明确标记为未验证。

## 已完成内容

- Tkinter 桌面界面、目标配置、状态表格、立即检测、CSV 导出和系统托盘。
- Ping、TCP、HTTP 三种检测及超时、错误信息和延迟记录。
- 后台独立调度和有限工作线程池。
- SQLite 历史存储、启动清理和每 24 小时周期清理。
- 连续失败阈值、重复告警冷却和恢复通知。
- 企业微信 Markdown 通知及 Webhook 日志脱敏。
- 恢复通知与新故障并发时的状态保护和纠正告警。
- JSON 默认配置和运行目录首次自动创建。
- PyInstaller 单文件构建配置。
- 中文使用说明和 Git 忽略规则。

## 自动化验证

执行命令：

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app tests
```

结果：

- 9 项单元测试全部通过。
- 全部应用与测试 Python 文件编译通过。
- 覆盖配置初始化、SQLite 写入、周期清理、Ping 编码容错、Webhook 脱敏、失败阈值、冷却和恢复竞态。

## 打包结果

执行命令：

```powershell
python -m PyInstaller --clean --noconfirm build.spec
```

结果：构建成功。

| 项目 | 值 |
| --- | --- |
| 文件 | `dist\NetworkMonitor.exe` |
| 大小 | 32,741,424 字节 |
| SHA-256 | `305AAE2FFD6941BBCA55A1915D46A6B7D03B862DAFB21B73A30E9DC32B201B6E` |
| Python | 3.13.1（满足 Python 3.10+） |
| PyInstaller | 6.22.1 |

## 干净目录首次启动验证

验证目录：`verification\clean-first-run-20260817-0642`

验证步骤：

1. 将新构建的 EXE 单独复制到不存在配置和数据库的新目录。
2. 隐藏启动 EXE，确认进程未提前退出。
3. 等待程序自动创建运行文件。
4. 关闭本次测试启动的进程。
5. 校验 JSON、SQLite 完整性、表结构和检测记录。

结果：

- `config\targets.json`：已创建，包含 `192.168.1.1` 和 `8.8.8.8` 两个默认目标。
- `config\settings.json`：已创建，Webhook 默认为空。
- `data\monitor.db`：已创建，`PRAGMA integrity_check` 返回 `ok`。
- `check_history` 字段与需求一致。
- 首次运行写入 2 条检测记录。
- `logs\monitor.log`：已创建并记录监控引擎启动。
- 测试进程已正常终止，无遗留运行实例。

## 已知边界与未验证项

- 未使用真实企业微信 Webhook；真实故障告警和恢复通知需要凭据持有人在测试群中验证。
- 本次完成了打包程序启动和持久化文件验证，未自动点击遍历所有 GUI 控件和托盘菜单。
- 配置、数据库和日志位于 EXE 同级目录，部署目录必须允许当前用户写入。
- Ping 是否可达取决于目标、防火墙和系统 ICMP 策略，Ping 失败不必然表示对应业务端口不可用。

## 建议的现场验收

1. 在企业微信测试群创建机器人并填写 Webhook。
2. 添加一个可控 TCP 或 HTTP 测试目标。
3. 模拟连续失败达到阈值，确认只发送一条故障告警。
4. 在冷却期内保持失败，确认不会重复告警。
5. 恢复目标，确认收到恢复通知。
6. 手动验证托盘隐藏、恢复、退出和 CSV 导出。

