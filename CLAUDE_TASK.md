# NetworkMonitor 开发任务清单

你是一位资深的 Windows 桌面应用开发者。请阅读项目现状，然后**逐项**完成下面的任务清单。每完成一项，跑一次测试验证，并提交一个独立的 git commit。

## 项目现状

- 项目位置：`D:\nxzxwl\NetworkMonitor`
- 技术栈：Python 3.13 + Tkinter（GUI）+ SQLite（本地存储）+ requests + pysnmp + pystray + PyInstaller（打包）
- 架构分层：
  - `app/main.py` —— 入口
  - `app/core/` —— 检测（ping/tcp/http/snmp）、调度（monitor.py）、告警（alert.py、desktop_alert.py）、端口扫描（port_scan.py）
  - `app/gui/` —— 界面（main_window.py 主窗口、config_dialog.py 配置、dashboard.py 工作台、monitor_wall.py 大屏、tray.py 托盘、theme.py、port_test_dialog.py）
  - `app/utils/` —— config.py 配置、database.py 数据库、logger.py 日志
  - `app/integrations/` —— wechat.py 企业微信
- 现状：81 项单元测试全部通过，代码可正常构建运行
- 运行环境：Windows，本项目依赖安装在 Python 3.13，路径为 `C:\Program Files\Python313\python.exe`

## ⚠️ 环境注意事项（必须遵守）

1. 跑测试、运行程序、编译检查，必须用 Python 3.13，即 `/c/Program Files/Python313/python.exe`（bash 下）或 `C:\Program Files\Python313\python.exe`（PowerShell 下）。不要用系统默认的 `python`（那是 3.11，没有本项目依赖）。
2. 验证命令（在项目目录下执行）：
   - 测试：`python -m unittest discover -s tests -v`
   - 编译：`python -m compileall -q app tests`
3. 这是 Windows 环境，GUI 程序（Tkinter）不能无头运行测试；单元测试都是纯逻辑测试，不需要真实显示。新功能涉及 GUI 的部分，尽量把可测逻辑抽到纯函数/纯类里再测试（参考现有 `desktop_alert.py` 的做法——它把状态策略和 Tkinter 完全解耦）。
4. 不要新增会让 PyInstaller 打包变脆弱的依赖。优先用 Python 标准库（winreg 写注册表、ctypes 或调 PowerShell 建 .lnk）。如果确需新增依赖，必须先说明理由。

## 第 0 步：初始化 git（必须先做，所有改动的前提）

1. 确认项目根目录当前没有 `.git`。
2. 执行 `git init`。
3. 检查 `.gitignore` 已存在（它已经正确忽略了 `__pycache__/`、`build/`、`dist/`、`config/`、`data/`、`logs/`、`.venv/` 等）。
4. 做首次提交：`git add -A && git commit -m "chore: initial commit (baseline before feature work)"`。
5. 之后每完成一个功能就提交一个 commit，commit message 用中文，格式如 `feat: 每日巡检报告`。

---

## 功能 1：每日早晨 8 点自动发巡检报告到企业微信

### 需求

软件常驻运行时，每天早晨 08:00 自动生成一份"巡检报告"，通过企业微信机器人发送。

### 具体设计

1. **配置项**：在 `settings.json`（`app/utils/config.py` 的 `DEFAULT_SETTINGS`）里新增字段：
   - `report_time`: 字符串，格式 `"HH:MM"`，默认 `"08:00"`
   - `daily_report_enabled`: 布尔，默认 `true`
   同时要在 GUI 的"系统设置"界面（`app/gui/config_dialog.py`）里提供这两个配置的编辑入口，并加进 README 的 settings.json 字段表。

2. **调度触发**：在 `app/core/monitor.py` 的调度循环 `_run` 里，增加"每日报告"的到点检查。要求：
   - 每 30 秒左右检查一次当前时间是否达到/超过今天配置的报告时间点
   - 每天只触发一次（用一个 `_last_report_date` 记录上次发送日期，跨天判断，避免同一天重复发）
   - 触发后调用报告生成与发送逻辑；如果当时软件刚启动且已过报告时间，是否补发由你判断（建议：不补发昨天，只按"今天还没发过"为准，今天到点后再发，避免开机时集中补发）

3. **报告内容**（markdown 格式，复用 `app/integrations/wechat.py` 的 `WeChatNotifier.send_markdown`）：
   - 标题：`# 每日巡检报告（YYYY-MM-DD）`
   - **目标状态总览**：检测目标总数、正常数、异常数、未知/过期数（复用 `app/utils/database.py` 的 `get_dashboard_snapshot` 的 `status_counts`）
   - **异常目标清单**：每个异常目标列出 名称、地址、检测类型、错误信息、最后检测时间（复用 snapshot 的 `exceptions`）
   - **今日网页巡检进度**：应检 / 已完成 / 异常 / 未检测 数量（复用 `get_latest_inspection_run(on_date=今天)`）
   - **一句话结论**：例如"今日巡检 N 个目标，M 个异常，请关注：XXX、YYY"；无异常则写"今日巡检全部正常"
   - 报告发送失败（无 webhook / 网络异常 / 企业微信拒绝）要记录日志，但不能影响监控主循环。

4. **报告前自动巡检（可选但建议做）**：触发报告时，如果"今天还没有发起过网页巡检"，先自动调用一次 `check_websites_now()`，让报告里的巡检数据更完整。注意这是异步的，报告不阻塞等待巡检完成，直接基于当前数据库快照发送即可。

5. **复用原则**：报告的数据全部来自已有的数据库快照方法（`get_dashboard_snapshot`），不要新写一遍统计逻辑，也不要触发额外的网络检测（除上面第 4 点可选的一键巡检外）。

### 验收标准

- 新增字段写入 settings.json 后，程序重启能正确读取
- 把报告时间改成一分钟后，能看到软件自动生成报告并尝试发送（webhook 未配置时应记录"未配置 Webhook，跳过发送"日志）
- 同一天内不会重复发送
- 报告 markdown 内容包含总览、异常清单、巡检进度、结论四部分
- 新增单元测试覆盖"报告内容生成"的纯逻辑（把报告文本拼装抽成纯函数）

---

## 功能 2：桌面快捷方式 + 开机自启动（静默启动到托盘）

### 需求

1. 首次运行（或提供菜单入口）时，在桌面创建快捷方式。
2. 支持"开机自启动"，开机自启时静默启动到系统托盘（不弹出主窗口）。

### 具体设计

1. **创建桌面快捷方式**：
   - 提供两种触发：程序首次启动时自动创建（如果桌面还没有该快捷方式）；同时提供菜单项"工具 → 创建桌面快捷方式"手动创建
   - 快捷方式指向当前 exe（frozen 模式）或 `pythonw.exe app/main.py`（源码模式）
   - 实现方式：优先用 `ctypes` 调用 Windows Shell COM 接口，或通过 `subprocess` 调 PowerShell 的 `WScript.Shell` 创建 `.lnk`。不要新增 pip 依赖（如 winshell/pywin32），避免打包变脆
   - 快捷方式名称固定为 `NetworkMonitor`

2. **开机自启动**：
   - 用标准库 `winreg` 写注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，键名 `NetworkMonitor`，键值为 `"<exe路径>" --autostart`
   - 在 GUI"系统设置"里加一个开关 `autostart_enabled`（布尔，默认 false），可开启/关闭开机自启（开启=写注册表，关闭=删注册表键）
   - 注意：源码模式下 exe 路径不可用，此功能在 frozen（打包后）模式下才真正生效；源码模式下可跳过写注册表并提示

3. **静默启动到托盘**：
   - 程序启动时解析命令行参数，如果带 `--autostart`，则启动后主窗口最小化到系统托盘、不抢占前台（`app/main.py` 里处理参数）
   - 复用现有 `app/gui/tray.py` 的托盘逻辑，确保自启模式下主窗口仍可通过托盘恢复

### 验收标准

- 运行"创建桌面快捷方式"后，桌面出现可用的 `NetworkMonitor.lnk`，双击能启动程序
- 开启"开机自启"后，注册表 `HKCU\...\Run` 出现对应键；关闭后键被删除
- 带 `--autostart` 启动时，主窗口不弹到前台（静默到托盘）
- 新增单元测试覆盖：命令行参数解析、注册表键值的生成逻辑（把"构造注册表键值字符串"抽成纯函数测试，不真的写注册表）

---

## 功能 3：批量添加巡检目标

### 需求

用户可以把一批目标一次性粘贴进来，软件解析后预览、确认、批量写入配置。

### 具体设计

1. **粘贴批量添加**（主推，最简单）：
   - 在"配置目标"界面（`app/gui/config_dialog.py`）加一个"批量添加"按钮，弹出一个文本框
   - 格式：每行一个目标，用英文逗号分隔 `名称,地址,检测类型`，检测类型可省略（默认 `ping`）
   - 支持的检测类型：`ping` / `tcp` / `http` / `snmp`
   - 示例输入：
     ```
     核心交换机,192.168.1.1,snmp
     文件服务器,192.168.1.10,tcp
     门户网站,https://portal.example.com,http
     备用网关,192.168.1.2
     ```
   - 解析后弹出一个预览表格，显示每行解析出的 名称/地址/类型，并对非法行（缺名称、缺地址、类型不在支持列表、地址格式错误）标红提示
   - 用户确认后批量写入 `targets.json`（复用 `ConfigManager.save_targets`），并调用 `monitor.update_targets` 让新目标立即生效
   - 去重：如果某个目标（按 name+address+check_type 组合）已存在，跳过并提示重复数量

2. **CSV 文件导入（可选补充）**：如果实现成本不高，加一个"从 CSV 导入"按钮，读取 `名称,地址,检测类型` 三列的 CSV（第一行可带表头）。这一项是加分项，不是必须。

3. **复用原则**：解析逻辑抽成纯函数（输入多行文本 → 输出 `(解析成功的目标列表, 错误行列表)`），方便单元测试。

### 验收标准

- 粘贴上面示例的 4 行，能正确解析出 4 个目标（第 4 行默认 ping）
- 非法行（如 `缺地址的目标,,ping`、`x,1.2.3.4,ftp`）能标出错误且不影响其他合法行导入
- 重复目标会被跳过并提示
- 新增单元测试覆盖解析纯函数（合法行、缺类型默认 ping、非法类型、缺字段、重复去重）

---

## 功能 4（补充）：目标分组 + 筛选

### 需求

目标数量变多后，按"分组/标签"组织，并在主界面按组筛选。

### 具体设计

1. **数据层**：`targets.json` 的每个目标新增可选字段 `group`（字符串，默认 `"默认"`）。
2. **配置界面**：添加/编辑目标时可填分组；提供分组管理（新建/删除/重命名，删除分组时把该组目标归入"默认"）。
3. **主界面筛选**：`app/gui/main_window.py` 的目标列表上方加一个分组下拉筛选框（选项含"全部"和所有已存在的分组），选择后只显示该组目标。
4. **保持向后兼容**：旧配置里没有 `group` 字段的目标，加载时自动补为 `"默认"`，不要因此报错。

### 验收标准

- 旧 targets.json（无 group 字段）能正常加载，所有目标归入"默认"
- 给部分目标设置分组后，主界面能按组筛选显示
- 新增单元测试覆盖分组补默认值、按组筛选的纯逻辑

---

## 顺手修复的 bug（与上面功能一起做）

1. **"立即检测"不过滤已停用目标**：`app/core/monitor.py` 的 `check_now()` 里，候选目标没有过滤 `enabled` 字段，导致已停用的目标点"立即检测"仍会被触发。而 `check_websites_now()` 有过滤。请统一：`check_now()` 也加上 `item.get("enabled", True)` 过滤，与巡检逻辑一致。
2. **默认目标字段不全**：`app/utils/config.py` 的 `DEFAULT_TARGETS` 只有 `name/address/check_type/interval/alert_enabled` 5 个字段，缺 `device_role`、`enabled`、`http_verify_tls`、`http_timeout_seconds`、`snmp_port`、`snmp_version`、`snmp_community` 等。请补齐默认目标的完整字段（与 README 的 targets.json 字段表一致），让所有目标的字段结构统一，减少手改 JSON 踩坑。

---

## 全局硬性约束

1. **不破坏现有测试**：最终 `python -m unittest discover -s tests -v` 必须全绿（原 81 项 + 你新增的测试）。
2. **不破坏现有功能**：ping/tcp/http/snmp 检测、告警阈值/冷却/恢复、网页巡检、大屏、托盘、端口测试等现有功能必须照常工作。
3. **代码风格**：与现有代码保持一致——中文注释和文档字符串、类型标注（`from __future__ import annotations` + 类型注解）、把可测逻辑抽成纯函数/纯类、脱敏处理（webhook key、SNMP community 不能进日志）。
4. **依赖纪律**：不新增会让 PyInstaller 打包变脆的依赖；新增依赖前必须说明理由并评估打包影响。
5. **commit 纪律**：第 0 步 git init 后，每完成一个功能（含测试）提交一个 commit，不要把所有改动混成一个 commit。
6. **验证纪律**：每个功能完成后，运行测试 + 编译检查确认无误，再进入下一个功能。报告最终结果时，明确列出：改了哪些文件、新增/修改了哪些测试、测试是否全绿、有没有未完成项或风险点。

## 完成后请汇报

逐项说明：每个功能是否完成、改了哪些文件、测试结果、有没有遗留风险（比如某功能只能打包后验证）。不要只给结论，要给"我实际跑了什么命令、看到了什么结果"。
