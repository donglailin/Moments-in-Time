# 时迹 · 审计与修复进度

> 跨轮次交接文件，每完成一小批（≤8 步）即更新。最后更新：2026-11-05 17:05

## 〇、进度速览

| 主题 | 状态 |
| --- | --- |
| ① 闪退根因 | ✅ 已定位并修复验证 |
| ② 窗口拖动 / 最大化 / DPI | ✅ 已实测通过 |
| ③ 图标（SVG → ico → 运行时 + 打包） | ✅ 已完成 |
| ④ 前端勾选联动性能 | ✅ 已改造，GUI 端到端复验通过 |
| ⑤ 自动化测试 | ✅ 55 项 unittest + 18 项 GUI 端到端全部通过 |
| ⑥ 重新打包 + 双击冷启动 | ✅ 全部通过（含连续启动、单实例、中文/空格路径） |
| ⑦ README + 最终报告 | ✅ 已完成 |
| ⑧ WebView2 缺失检测 + 浏览器降级 | ✅ 已实现；模拟缺失实测通过（真实缺失环境待外部机器） |
| ⑨ 移除 CSV 清单与全部磁盘日志 | ✅ 已移除，打包后冷启动/导出全流程复验通过 |

## 一、① 闪退 / 未响应 —— 已结案

### 1.1 根因（决定性证据）

`Api.__init__` 曾把 pywebview 的 `Window` 对象保存为**公开属性** `self.window`。pywebview 注入 JS API 时会用 `inspect` 递归反射 js_api 对象的所有公开属性（`webview/util.py` `get_functions`），于是沿 `Window → native(WinForms Form) → browser → webview(WebView2)` 钻进整条 .NET COM 对象链，并在 `generate_js_object` 后台线程上 `getattr` 这些 COM 属性。

证据链：

1. 旧 `shiji.log`（11:36 段）有大量 `[ERROR] Thread-7 (generate_js_object) | Error while processing window.native.browser.webview.ZoomFactor / CanGoBack / DefaultBackgroundColor ...`，报错属性名 `window.native.*` 只可能来自对 `Api.window` 的反射。
2. 修复前 14:05 复现：`_pywebviewready event fired` 之后**再无任何日志**，`loaded` 事件永不触发；窗口 `SendMessageTimeout(WM_NULL, 4000)` 返回 0（无响应），持续 2 分钟以上。
3. 把 `Api.window` 改名为 `Api._window`（下划线开头被 pywebview 跳过）后：
   - 14:13:45.359 `_pywebviewready` → 14:13:45.418 `页面 loaded 事件`，间隔 **59ms**，不再卡死。
   - 14:13–14:22 连续 **11 次**启动全部成功，loaded 耗时 **1.63–2.67s**，每次均有 `窗口图标已设置` / `窗口位置检查` / `拖拽事件已挂载`。
   - 全日志 `[ERROR]` / `[CRITICAL]` 计数 **0**（仅剩 `generate_js_object` 线程名的 DEBUG 行）。

**结论：双击后"未响应/闪退"的主因是启动期 API 反射触发 WebView2 COM 死锁，不是慢启动本身。** 慢启动（冷启动 ~7s）只是放大器：反射一旦失败，窗口再等也不会活。

### 1.2 次要因素（同样已修）

- `dist\时迹.exe`（10:56，48679620 字节）是**过期构建**：PE 子系统 = 2（GUI 无控制台）、不含日志能力，因此无法从旧包日志定位崩溃点。**注意**：PyInstaller onefile 会由引导进程再启动子进程，窗口属于子进程，`Process.MainWindowHandle` 对引导进程恒为 0——旧的「30s 无窗口句柄」观测不能作为失败证据；本轮改为 `EnumWindows` + 进程路径匹配，能正确找到窗口。
- 旧包把 PyQt5 等无关依赖打进 onefile，解压与首扫显著拉长冷启动 → 打包时需 `--exclude-module`。
- `main.py` 原 `_hwnd_of_this_window` 回调用 `ctypes.DWORD()` → `AttributeError`（11:36:42.757 两处），导致图标与位置修正静默失效；已改 `ctypes.wintypes.DWORD`。

## 二、② 窗口拖动 —— 已实测通过

方案：弃用 `frameless` 自绘标题栏，改回**系统原生标题栏**（`frameless=False, easy_drag=False, draggable=False`），内容区仍为浅白风格。

实测数据（150% DPI，工作区物理 1920×1008）：

| 项 | 结果 |
| --- | --- |
| `WM_NCHITTEST` @ 标题栏 | 返回 `HTCAPTION(2)`，系统原生拖动生效 |
| 窗口样式 | `WS_CAPTION` / `WS_THICKFRAME` / `WS_MAXIMIZEBOX` / `WS_MINIMIZEBOX` 均为 True |
| 真实鼠标拖动 | 起点→终点位移 `(30, 30)`，窗口位移 `(30, 30)`，跟手无跳跃 |
| `SC_MAXIMIZE` | 窗口 rect `(-11, -11, 1942x1030)`，铺满工作区 |
| `SC_RESTORE` | 精确回到最大化前 rect |
| 最大化状态下拖动 | 自动还原并跟随光标（系统行为），无位置错乱 |
| 初始位置收敛 | (152,152) 越界 → `_clamp_window` 收敛到 (77,40) 1765×927，完全落在工作区内 |
| 尺寸单位 | `create_window` 传**逻辑像素** 1177×618（DPI 1.5 → 物理 1765×927），不再超屏 |

截图：[ui-preview.png](ui-preview.png)（1765×927，界面完整渲染）。

## 三、③ 图标 —— 已完成

- 矢量源：`docs/icon/app-icon.svg`（浅蓝渐变圆角底板 + 白色折角文件 + 文本行 + 青绿时钟；小尺寸隐藏文本行保证 16px 可辨）。
- 生成脚本：`tools/make_icon.py`（Pillow 8× 超采样**按尺寸矢量重绘**，非拉伸位图）。
- 产物：`assets/app.ico`（30653 字节，自检含 16/24/32/48/64/128/256 全尺寸）、`docs/icon/app-icon-512.png`。
- 运行时：`main.py _apply_icon()` 优先 `_resource("assets","app.ico")` → exe 同目录 assets → `sys.executable`，`ExtractIconExW` + `WM_SETICON` 同时设置标题栏与任务栏图标（日志已确认执行）。
- 打包时仍需加 `--icon assets/app.ico`（下一步做）。

## 四、已完成源码改动（均已落盘）

| 文件 | 改动 |
| --- | --- |
| `main.py` | 日志（exe 同目录 `shiji.log`，失败回退 `%TEMP%`，2MB 轮转）、faulthandler + 主/线程 excepthook、致命错误 MessageBox；PerMonitorV2 DPI；命名互斥体单实例（二次启动还原并置前已有窗口）；原生标题栏；按 DPI/工作区计算逻辑尺寸；`ctypes.DWORD` 修复；`_clamp_window` 最大化/最小化跳过且含右/下越界判定；图标应用 |
| `api.py` | **根因修复**：`self.window` → `self._window`（私有名跳过反射）；`_as_int()` 安全转换；`FileDialog.FOLDER/SAVE` 枚举；`normalize_extensions` 统一扩展名；扫描/导出 worker 加任务代际守卫（`if self._scan_job is not job: return`）丢弃过期结果；导出 ids 去非法值 |
| `scanner.py` | 枚举阶段缓存 `entry.stat(follow_symlinks=False)`（减半 IO）；跳过重解析点防目录环；扩展名支持全角分隔；时间边界 `[start, end)` 用 epoch 毫秒（不受时区影响）；上限 100k + `truncated` |
| `exporter.py` | 剪切改为同盘 `os.replace` 原子重命名、跨盘复制成功后再删源；失败清理残留目标、源文件不动（可回滚）；同文件判定 `os.path.samefile`；CSV 清单修正来源字段与取消说明 |
| `fileutil.py` | 可写探测临时文件带 PID；资源管理器定位加 `CREATE_NO_WINDOW` |
| `store.py` | 配置原子写：锁 + PID 临时名 + 4 次重试；读取兼容 BOM |

## 五、④ 前端勾选联动性能 —— 已完成

原实现 `folderState(node)` 每次对节点全部 id 与 `S.selected` 求交（勾选/渲染每个文件夹行都是 O(文件数)），10 万文件时会明显卡顿；`updateSelectionSummary` 每次遍历全部已选文件累加大小。

改为**增量计数**模型（`web/app.js`）：

- 节点新增 `parent`、`picked`（子树已选文件数）；`buildTree` 建立父子链，并把文件反向挂到 `it.__node`。
- `folderState()` 变为 O(1)：`picked===0 → none`、`picked===ids.size → all`、否则 `partial`。
- `toggleFile()`：选中集 ±1，并沿 `__node → parent` 链调整 `picked`（O(树深)）。
- `toggleFolder()`：批量改选中集，子树 `picked` 直接置为 `ids.size`/0，祖先按增量修正（O(子树)）。
- 新增 `recountSelection()`：仅在新树重建（`rebuildView`）和清空选择时做一次全量折算。
- `selectedBytes` 增量维护，`updateSelectionSummary` 不再遍历已选文件。
- `updateCheckAll()` 直接读当前节点 `picked/ids.size`，O(1)。
- 「全选本层」「清空选择」「表头复选框」改为复用上述函数，避免绕过计数。
- `node --check web/app.js` 通过（v24.19.0）。

行为待验证：勾选子文件夹→半选父链、逐文件勾选→父文件夹 `indeterminate`、筛选后选择保持、导出后清空，将在 GUI 端到端测试中复验。

## 六、⑤ 自动化测试 —— 已完成

新增 `tests/test_core.py`（标准库 unittest，无额外依赖）与 `tests/gui_e2e.py`（真实 pywebview 窗口）。

### 6.1 核心逻辑：`python -m unittest discover -s tests -v` → **24/24 通过**

覆盖：
- 扫描：时间范围 `[start, end)` 边界、mtime 倒序与 id 重编号、全角分隔符扩展名（`，；、;|` 与换行）、大小写、关键字、非递归、预先取消抛 `JobCancelled`、**目录联接不跟随**（实测 `mklink /J` 自环仍只扫 6 个文件）。
- 导出：保留结构 / 平铺、冲突 skip / rename / overwrite、同文件跳过、剪切删除源、取消、CSV 清单 BOM 与行数。
- 配置：默认值、原子写后无残留 tmp、BOM 读取、类型不符回退默认。
- Job：百分比/完成/取消；fileutil：长路径前缀与回显、体积格式化。

发现并修复 1 个真实缺陷：**导出在首个文件前被取消时，目标目录尚未建立，导致清单写入失败且 `manifest` 为空**。已在 `export_files` 开头提前 `ensure_dir(dest)`，取消场景也能生成含「任务已取消」的清单（测试已覆盖）。

### 6.2 GUI 端到端：`python tests/gui_e2e.py` → **19/19 通过**

真实窗口（EdgeChromium）+ 真实 `Api` + 真实 web 资源，临时源目录含中文/空格路径（`源 文件夹`、`导出 结果`）：

扫描 6 文件 → 根层 3 文件夹 + 2 文件 → 勾选子文件夹 Alpha（表头半选、Alpha 行选中、已选 2）→ 进入 Alpha（表头全选）→ 取消单个文件（半选）→ 重新勾选（全选）→ 返回根层全选 6 → 导出：成功 6 / 跳过 0 / 失败 0，目标目录 6 个文件结构完整，清单 CSV 6 行，弹窗显示「复制 6 个文件」。

## 七、⑥ 重新打包与双击等价冷启动 —— 已完成

### 7.1 打包

- `build.bat` 已改造：旧 exe 备份到 `dist_backup\时迹_旧包_20261105_1056.exe` 后再清理 `dist\`/`build\`，不再丢失唯一产物。
- 打包参数：`--onefile --windowed --icon assets/app.ico --add-data "web;web" --add-data "assets/app.ico;assets"`，并排除 PyQt5/PyQt6/PySide2/PySide6/matplotlib/numpy/pandas/PIL/IPython/jupyter。
- 产物（第七轮版本）：`dist\时迹.exe`，**17,163,500 字节**（旧包 48,679,620），SHA256 `3BD72E9B910334CBC0C9487737CF36F47F0BB8D7FACAB8D9D932C9CCE2752ED1`，PE 子系统 = 2（无控制台）。

### 7.2 双击等价验证（`tests/coldstart_check.ps1`）—— ALL PASS

在 `%TEMP%\时迹 冷启动 测试\`（**中文 + 空格路径**）复制 exe 后以 `Start-Process`（等价双击、无 console）执行：

| 检查 | 实测 |
| --- | --- |
| 第 1 次冷启动 | 窗口 **2.60s** 出现，进程存活（早期包 2.39s） |
| 日志 | `loaded` 已记录，`[ERROR]/[CRITICAL]` = **0** |
| 窗口几何 | 1765×927 @ (38,38)，完全在工作区 |
| 拖动 | `WM_NCHITTEST` = **2 (HTCAPTION)** |
| 图标 | `WM_GETICON` big/small 均非 0（来自打包 ico） |
| 截图 | `docs/exe-coldstart.png`（真实 exe 窗口，界面完整） |
| 第 2 次连续启动 | 窗口 **2.28s** 出现，日志 `loaded`、无 ERROR（早期包 2.17s） |
| 第 3 次并发启动 | 进程自动退出，已有窗口保留（**单实例生效**） |

## 八、⑧ WebView2 缺失检测与浏览器降级 —— 已完成（模拟缺失）

目标：没有 WebView2 Runtime 的 Windows 机器上，程序也不能变成砖。

### 8.1 检测（`runtime_check.py`，纯函数可注入）

- 依次读取注册表 `HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}`、`HKLM\SOFTWARE\Microsoft\EdgeUpdate\Clients\{...}`、`HKCU\SOFTWARE\Microsoft\EdgeUpdate\Clients\{...}`，取 `pv` / `location`。
- 安装目录校验：`location` 与 `ProgramFiles(x86)` / `ProgramFiles` / `LOCALAPPDATA` 下的 `Microsoft\EdgeWebView\Application\<pv>\msedgewebview2.exe`（及 `Core.dll`）。
- Loader 校验：pywebview 的 `lib\runtimes\<arch>\native\WebView2Loader.dll`；打包时额外检查 `_MEIPASS\webview\...`，避免把"打包路径不同"误判成缺失。
- 结果状态：`present` / `missing`（无注册表项或强制模拟）/ `broken`（`pv=0.0.0.0`、安装目录不存在、Loader 缺失）。
- 失败分类 `classify_startup_failure()`：超时 → `timeout`；异常文本含 `access is denied` / `拒绝访问` / `0x80070005` / `0x800704ec` / 杀毒/安全软件/拦截 → `blocked`；其余 → `init_failed`。每种情况 `browser_mode_reason()` 给出不同文案 + 下载地址。
- 测试开关：`WEBVIEW2_FORCE_MISSING=1`（强制走缺失分支）；`TFP_BROWSER_NO_OPEN=1`（自动化测试时不弹浏览器）。
- 单元测试 `tests/test_runtime_check.py`：**11/11 通过**，覆盖无注册表项、注册表存在但路径不存在、`pv=0.0.0.0`、Loader 缺失、正常、强制缺失、超时（标志与异常文本）、英文/中文拦截、初始化异常，并断言文案包含下载地址。

### 8.2 降级（`browser_mode.py` + `api.py` + 前端）

- `BrowserModeServer`：`ThreadingHTTPServer` 监听 `127.0.0.1:0`（随机端口），`GET /` 读取 `web/index.html`，把 `<!--TFP_BRIDGE-->` 替换为 JS 桥（`fetch('/api/<name>')` 代理成 `window.pywebview.api`），页面每 5 秒心跳；`POST /api/<method>` 调真实 `Api`；`quit` 后关闭；无心跳约 120 秒自动退出。
- `Api`：新增 `browser_mode` / `mode_reason`；`bootstrap` 返回 `browserMode` / `modeReason` / `downloadUrl`；浏览器模式下 `pick_folder` 不调系统对话框而是返回提示，`export_list` 直接导出到目标目录默认文件名，`quit` 返回后由服务端退出。
- 前端：顶部黄色横幅显示原因 + 「下载 WebView2」链接 + 关闭按钮；源/目标按钮改文案为「输入路径」，点击用 `prompt` 手动填绝对路径；拖拽事件提示浏览器模式不支持；关于弹窗的"界面引擎"显示"系统浏览器（未使用 WebView2）"。
- `main.py`：单实例之后、`import webview` 之前检测；非 `present` → 日志 + 启动浏览器模式。原生启动另加 **30 秒 watchdog**（未 shown → 归类 timeout、尝试 `window.destroy()`；再等 15 秒仍卡住则后台启动浏览器模式兜底）。`webview.start` 抛异常 → 分类后进入浏览器模式，不再只弹 MessageBox。浏览器模式会把地址写入 `%TEMP%\shiji_browser_url.txt`，二次启动时若找不到窗口则重新打开该页面；退出时删除地址文件。

### 8.3 实测证据（模拟缺失，非真实缺失机器）

- 源码模式 `WEBVIEW2_FORCE_MISSING=1`：日志 `WebView2 检测：status=missing；测试开关强制模拟...` → `浏览器模式已启动（+0.11s）：http://127.0.0.1:10714/` → `自动打开系统默认浏览器：True`；真实 Chrome 窗口截图 `docs/browser-mode-real.png`（横幅 + 完整界面），无头 Edge 截图 `docs/browser-mode.png`。
- 打包 exe `WEBVIEW2_FORCE_MISSING=1 TFP_BROWSER_NO_OPEN=1`：`tests/browser_mode_probe.py --scan --quit` **11/11 通过**：
  - `GET /` 18,397 字节，桥接脚本已注入、横幅存在、占位符已替换；
  - `bootstrap` 返回 `browserMode=true`、`modeReason` 含"浏览器模式"、`downloadUrl` 为 fwlink；
  - `start_scan` 扫描 3 个探针文件 → 完成并命中 3；
  - `quit` 后进程退出、`%TEMP%\shiji_browser_url.txt` 被删除。
- 正常路径未回归：最终打包 exe 冷启动复测 **ALL PASS**（2.60s / 2.28s），冻结点检测日志 `status=present；版本=156.0.4314.58；Loader=..._MEI...\webview\lib\runtimes\win-x64\native\WebView2Loader.dll`，`loaded` 正常、ERROR/CRITICAL=0。
- **如实标注**：本机已安装 WebView2，未在真实未安装运行时的干净机器/被安全软件拦截的机器上实测；超时分支仅单元测试覆盖，未构造真实挂起场景。

### 8.4 是否内置 WebView2 运行时

- 方案：PyInstaller 收集"固定版本运行时"（约 +100 MB），或首次启动自动下载安装（需联网/管理员权限）。
- 取舍：内置版完全离线、行为一致，但包体从 ~17 MB 涨到 ~120 MB，且需跟随安全更新手动换版本；默认轻量版 + 浏览器降级已能保证"不变砖"，且用户装一次运行时后永久恢复原生窗口。
- **建议：默认仍走轻量包**；仅对"内网/完全离线/不允许装运行时"的场景另出一个内置运行时版本。

## 九、最终交付

- 可执行文件：`C:\Users\BALANCE\Desktop\文件整理\开发\时间文件\dist\时迹.exe`（17,170,301 字节，SHA256 `7CEBA6B1B592349A973776AB59D9BB04FA668D8D4CC2C970570A25DD601F310F`）
- 旧包备份：`dist_backup\时迹_旧包_20261105_1056.exe`、`dist_backup\时迹_旧包_20261105_152853.exe`、`dist_backup\时迹_旧包_20261105_154059.exe`、`dist_backup\时迹_旧包_20261105_162629.exe`、`dist_backup\时迹_旧包_20261105_163430.exe`
- 图标：`assets\app.ico`（16–256 七尺寸）、矢量源 `docs\icon\app-icon.svg`、预览 `docs\icon\app-icon-512.png`
- 截图：`docs\ui-preview.png`（源码模式）、`docs\exe-coldstart.png`（打包 exe）、`docs\browser-mode.png`（无头 Edge 降级页）、`docs\browser-mode-real.png`（真实 Chrome 窗口降级页）
- 测试：`tests\test_core.py`（26 项）、`tests\test_browser_mode.py`（19 项）、`tests\gui_e2e.py`（19 项）、`tests\test_runtime_check.py`（11 项）、`tests\browser_mode_probe.py`（HTTP 探针）、`tests\coldstart_check.ps1`
- 说明文档：`README.md`、本文件

### 仍未解决 / 如实标注

- 未在 125% / 175% 及其他缩放比例、多显示器跨屏拖动下重新实测（仅 150% 单屏实测）。
- 未在**真实未安装 WebView2 Runtime** 的干净机器上实测；缺失/降级路径通过 `WEBVIEW2_FORCE_MISSING=1` 模拟验证，初始化超时仅单元测试覆盖。
- 浏览器模式下无法调用系统文件夹选择器：源/目标需手动输入绝对路径，拖拽不可用；另外浏览器模式依赖系统默认浏览器（本机验证为 Chrome）。
- 旧包（10:56 构建）无日志，其具体崩溃点无法回溯，只能确认新包在相同环境稳定启动。

## 十、第八轮：独立回归审查（2026-11-05 16:30）

### 10.1 结论

- **这批新改动确实引入了风险**：浏览器降级 HTTP 服务原先没有任何鉴权，任意本机网页/脚本都能调用扫描、导出、打开路径等接口（P0）；覆盖导出存在"先删旧文件再复制"的数据丢失窗口（P1）；空闲退出会在导出进行中杀死进程（P1）；单实例按标题"包含"匹配，会把旧的 Chrome 标签页当成原生窗口（P1）。
- 上述四项已全部修复，并补充了 19 项浏览器安全/生命周期测试与导出数据安全测试。全量 unittest **56 项通过**，GUI 端到端 **19/19**，真实 Chrome 降级页 **4/4**，打包 exe 原生冷启动 **1.96s 出现窗口**、浏览器降级 **11+8 项探针通过**、退出码 0、地址文件清理。
- 因为修改涉及 `browser_mode.py` / `api.py` / `exporter.py` / `scanner.py` / `runtime_check.py` / `main.py` / `web/app.js`，**旧 exe 已失效，已重新打包**；当前 `dist\时迹.exe` 可放心使用（SHA256 `7CEBA6B1B592349A973776AB59D9BB04FA668D8D4CC2C970570A25DD601F310F`）。

### 10.2 问题 → 证据 → 修法 → 验证

#### P0：浏览器降级 HTTP 服务无鉴权（任意本机页面可调用高危接口）

- **证据**：原实现只绑定 `127.0.0.1`，`POST /api/<method>` 直接 `getattr(api, name)`；不校验 `Host`/`Origin`、无一次性 token；`Content-Type: text/plain` 的跨站"简单请求"即可触发写操作，DNS rebinding 还能读取响应。可被利用的接口包括 `start_scan`（任意目录枚举）、`start_export`（复制/剪切/覆盖到任意目录）、`export_list`（任意位置写 CSV）、`open_path`（`os.startfile` 启动任意文件）。
- **修法**（[browser_mode.py](file:///c:/Users/BALANCE/Desktop/%E6%96%87%E4%BB%B6%E6%95%B4%E7%90%86/%E5%BC%80%E5%8F%91/%E6%97%B6%E9%97%B4%E6%96%87%E4%BB%B6/browser_mode.py)）：
  - 每次启动生成 `secrets.token_urlsafe(24)`，页面与 API 全部挂在 `/<token>/` 下；无 token 一律 403，`/<token>` 302 补斜杠（[browser_mode.py:93-101](file:///c:/Users/BALANCE/Desktop/%E6%96%87%E4%BB%B6%E6%95%B4%E7%90%86/%E5%BC%80%E5%8F%91/%E6%97%B6%E9%97%B4%E6%96%87%E4%BB%B6/browser_mode.py#L93-L101)）。
  - `Host` 必须严格等于 `127.0.0.1:<port>` / `localhost:<port>`；存在 `Origin` 时必须同源，阻断 DNS rebinding 与跨站请求（[browser_mode.py:179](file:///c:/Users/BALANCE/Desktop/%E6%96%87%E4%BB%B6%E6%95%B4%E7%90%86/%E5%BC%80%E5%8F%91/%E6%97%B6%E9%97%B4%E6%96%87%E4%BB%B6/browser_mode.py#L179)）。
  - API 仅接受 `application/json`、请求体上限 16 MiB，并改用显式白名单，不再反射任意公开属性（[browser_mode.py:282-317](file:///c:/Users/BALANCE/Desktop/%E6%96%87%E4%BB%B6%E6%95%B4%E7%90%86/%E5%BC%80%E5%8F%91/%E6%97%B6%E9%97%B4%E6%96%87%E4%BB%B6/browser_mode.py#L286-L320)）。
  - 响应统一加 `X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`、`X-Frame-Options: DENY`，避免 token 经 Referer 外泄与点击劫持。
  - 前端桥改为相对 `fetch('api/...')`，天然带上 token 路径；服务端静态资源仍做 commonpath 目录穿越校验。
- **验证**：新增 [test_browser_mode.py](file:///c:/Users/BALANCE/Desktop/%E6%96%87%E4%BB%B6%E6%95%B4%E7%90%86/%E5%BC%80%E5%8F%91/%E6%97%B6%E9%97%B4%E6%96%87%E4%BB%B6/tests/test_browser_mode.py) 覆盖：无 token 403、`/<token>` 302、恶意 Host 403、恶意 Origin 403、同源 200、`text/plain` 415、私有方法 404、目录穿越 404、超大请求体 413、心跳 200。真实 Chrome 打开降级页：横幅可见、按钮为"输入路径"、Console 无报错、`window.pywebview.api.bootstrap()` 返回 `ok=true / browserMode=true`。

#### P1：覆盖模式导出先删除目标再复制，复制失败会丢原文件

- **证据**：原 `exporter.export_files` 在 `conflict="overwrite"` 时先 `os.remove(target)` 再 `shutil.copy2`；磁盘满/权限/占用导致复制失败时，旧文件已被删除，且失败清理逻辑还会再次删除目标。
- **修法**（[exporter.py:57-91](file:///c:/Users/BALANCE/Desktop/%E6%96%87%E4%BB%B6%E6%95%B4%E7%90%86/%E5%BC%80%E5%8F%91/%E6%97%B6%E9%97%B4%E6%96%87%E4%BB%B6/exporter.py#L57-L91)）：复制统一写"目标目录内临时文件"再 `os.replace` 原子替换；目标旧文件在复制成功前保持不动。剪切优先 `os.replace`（同盘原子、覆盖模式也不丢旧文件）；跨盘改为"复制临时文件 → 替换目标 → 删源文件"，非覆盖模式下删源失败会回滚目标，源文件始终保留。
- **验证**：`test_conflict_overwrite_keeps_old_file_when_copy_fails` 用 `mock.patch("shutil.copy2", side_effect=OSError("磁盘已满"))` 断言旧文件内容仍为 `old`、失败数 1、无 `.shiji-tmp-*` 残留；`test_no_temp_files_left_after_success` 断言成功导出后无临时文件。

#### P1：120 秒空闲退出会打断正在执行的扫描/导出

- **证据**：原 `_idle_watch` 只看心跳时间，页面关闭后 120 秒直接 `shutdown()`；`server.wait()` 返回后主进程退出，daemon 导出线程被强杀，目标目录留下半批文件与不完整清单。`quit` 也是固定 0.4 秒后关服务，剪切可能停在半路。
- **修法**：`Api._busy()` 汇总扫描/导出任务状态（[api.py:319](file:///c:/Users/BALANCE/Desktop/%E6%96%87%E4%BB%B6%E6%95%B4%E7%90%86/%E5%BC%80%E5%8F%91/%E6%97%B6%E9%97%B4%E6%96%87%E4%BB%B6/api.py#L319)）；`_idle_watch` 检测空闲时若任务仍在执行则继续等待（[browser_mode.py:118-131](file:///c:/Users/BALANCE/Desktop/%E6%96%87%E4%BB%B6%E6%95%B4%E7%90%86/%E5%BC%80%E5%8F%91/%E6%97%B6%E9%97%B4%E6%96%87%E4%BB%B6/browser_mode.py#L118-L131)）；`quit` 先取消任务，最多等待 8 秒收敛后再关服务。
- **验证**：`IdleTests` 两项——无心跳 0.4s 后自动退出；`busy=True` 时 2.2 秒后服务仍可用、`busy=False` 后正常退出。打包 exe 降级模式扫描探针 11/11，`--quit` 后进程退出码 0。

#### P1：单实例按标题"包含"匹配，会激活旧浏览器标签页

- **证据**：`_activate_existing` 原用 `if WINDOW_TITLE in buf.value`；浏览器模式下 Chrome 标签标题为"时迹 · 文件时间筛选器 - Google Chrome"，也会命中。二次启动可能把旧标签当成原生窗口，导致真正运行的原生窗口没被前置，或误判实例状态。
- **修法**：`_activate_existing` 与 `_hwnd_of_this_window` 均改为标题**精确相等**（[main.py:140](file:///c:/Users/BALANCE/Desktop/%E6%96%87%E4%BB%B6%E6%95%B4%E7%90%86/%E5%BC%80%E5%8F%91/%E6%97%B6%E9%97%B4%E6%96%87%E4%BB%B6/main.py#L140)）。原生窗口标题由 pywebview 精确设置，浏览器标签带后缀，不再混淆。
- **验证**：原生模式二次启动日志 `检测到已运行的窗口`，二次进程退出码 0、首实例保留；浏览器模式二次启动在修复后走 `_read_browser_url` 分支并记录"跳过重新打开已有浏览器页面"，不再误激活 Chrome。

#### P2：陈旧地址文件、位数判断、取消粒度、导出标记等

- **陈旧地址文件**：`_read_browser_url` 只接受 `http://127.0.0.1:<port>/<16+位token>/`，并对服务发 GET 探活；无 token/非回环/已失活一律忽略。拿到单实例互斥体后主动删除残留地址文件（[main.py:159-199](file:///c:/Users/BALANCE/Desktop/%E6%96%87%E4%BB%B6%E6%95%B4%E7%90%86/%E5%BC%80%E5%8F%91/%E6%97%B6%E9%97%B4%E6%96%87%E4%BB%B6/main.py#L159-L199)、[main.py:529](file:///c:/Users/BALANCE/Desktop/%E6%96%87%E4%BB%B6%E6%95%B4%E7%90%86/%E5%BC%80%E5%8F%91/%E6%97%B6%E9%97%B4%E6%96%87%E4%BB%B6/main.py#L529)）。验证：`UrlFileTests` 4 项（无 token、非回环、已失活地址均拒绝；清理函数删除文件）。
- **WebView2Loader 位数**：原按 `platform.machine()` 选 `win-x64`，32 位 Python 在 64 位系统上会找错目录；改为按 `sys.maxsize` 判断进程位数（[runtime_check.py:89](file:///c:/Users/BALANCE/Desktop/%E6%96%87%E4%BB%B6%E6%95%B4%E7%90%86/%E5%BC%80%E5%8F%91/%E6%97%B6%E9%97%B4%E6%96%87%E4%BB%B6/runtime_check.py#L89)）。同时删除重复的 HKLM 注册表路径，保留 HKLM(WOW6432Node)/HKLM/HKCU 三个不同目标。
- **取消粒度**：单个超大目录内每 512 个条目检查一次取消（[scanner.py:91](file:///c:/Users/BALANCE/Desktop/%E6%96%87%E4%BB%B6%E6%95%B4%E7%90%86/%E5%BC%80%E5%8F%91/%E6%97%B6%E9%97%B4%E6%96%87%E4%BB%B6/scanner.py#L91)）。
- **导出标记**：结果新增 `exported_ids`，前端只给真正成功的文件打"已导出"，被跳过/失败的不再误标；重名清单文件名同一分钟内自动加序号，避免覆盖上一份清单（[api.py:236](file:///c:/Users/BALANCE/Desktop/%E6%96%87%E4%BB%B6%E6%95%B4%E7%90%86/%E5%BC%80%E5%8F%91/%E6%97%B6%E9%97%B4%E6%96%87%E4%BB%B6/api.py#L236)）。
- **浏览器模式路径输入**：`promptFolder` 输入后调用 `check_path` 校验，不存在时提示且不写入设置（同时让原本无引用的 `check_path` 生效）；删除未使用的 `open_file` 别名，缩小 API 面。

### 10.3 实测记录（本轮实际执行）

| 项目 | 结果 |
| --- | --- |
| 全量 unittest | **56 项通过**（core 26 / browser_mode 19 / runtime 11） |
| GUI 端到端（真实 EdgeChromium） | **19/19**，导出 6 文件、结构完整、清单正确 |
| 真实 Chrome 降级页 | **4/4**：横幅、输入路径按钮、Console 无报错、`bootstrap()` ok/browserMode=true |
| 源码模式强制降级全流程 | 扫描探针 **11/11**；`--quit` **8/8**；退出码 0；地址文件已删除 |
| 源码模式原生冷启动 | `shown` 正常；退出码 0；loaded 后图标/位置检查正常 |
| 打包 exe 原生冷启动 | **1.96s 出现窗口**，loaded 后图标/位置检查正常，**ERROR/WARNING = 0**，退出码 0 |
| 打包 exe 强制降级 | 扫描 11/11 + 退出 8/8，退出码 0，地址文件删除，ERROR = 0 |
| 单实例 | 原生二次启动被前置并退出；浏览器模式二次启动复用 URL 文件并退出 |

### 10.4 本轮仍未覆盖 / 如实标注

- 本机装有 WebView2，**未在真实未安装运行时的干净机器**上复测；降级路径仍以 `WEBVIEW2_FORCE_MISSING=1` 模拟。
- 120 秒真实空闲退出未等待两分钟实测，使用 0.4 秒超时的等价单元测试；任务运行中不退出同样由该测试覆盖。
- 跨盘剪切（`os.replace` 抛 EXDEV 后走复制路径）未在真实双盘环境实测；覆盖失败回滚用 `mock` 复制失败验证。
- 未重新实测 125%/175% 缩放与多显示器跨屏。
- 观察到一次"窗口出现后 2 秒内立即关闭"时 pywebview 打印 `E_ABORT` ERROR（WebView2 初始化被中止的关闭竞态）；等待 5 秒再关闭即 ERROR=0，判定为关闭过早的良性日志，未改动 pywebview。
- 清单 CSV 对以 `= + - @` 开头的文件名未做 Excel 公式注入转义（本地文件、用户自建清单，风险低，本轮未改）。

> 说明：上述"清单 CSV 公式注入"与"E_ABORT 日志"两项，因第九轮按要求整体移除 CSV 清单与磁盘日志，已不再存在，见第十一节。

## 十一、第九轮：移除 CSV 清单与全部磁盘日志

### 11.1 变更内容

- **导出不再生成任何清单文件**：删除 `exporter.write_rows_csv`、`_manifest_path`、`export_scan_list`，删除 `api.export_list` 及浏览器模式 API 白名单条目，移除前端的"导出清单"按钮、"打开清单"入口与相关文案。导出弹窗仍展示成功 / 跳过 / 失败数量与失败项列表。
- **不再写任何日志文件**：删除 `shiji.log` 写入（exe 同目录与 `%TEMP%` 回退）、日志轮转常量、`faulthandler` 落盘与 `_open_log` / `_setup_logging`；移除 `main.py` 与 `browser_mode.py` 的全部 logger 调用、HTTP access log 与 `api.py` 的 `traceback.print_exc`。
- **唯一异常出口**：主线程未捕获异常、后台线程异常、启动失败只弹一个简短 MessageBox（不写盘），正常运行完全静默；控制流（超时降级、单实例、取消/退出等待）保持不变，未用宽泛 `except` 掩盖真实错误。
- 上一轮待办中的"CSV 公式注入转义"随清单功能移除而作废；"E_ABORT 日志降级"因不再产生日志文件而自然消失。

### 11.2 实测记录

| 项目 | 结果 |
| --- | --- |
| 全量 unittest | **55 项通过**（core 25 / browser_mode 19 / runtime 11） |
| GUI 端到端（真实 EdgeChromium） | **18/18**；导出 6 文件，目标目录 `extra=[]`，无 CSV |
| 源码浏览器降级 | 页面/bootstrap/扫描 **11/11**，`--quit` 8/8，退出码 0，地址文件清理 |
| 打包 exe 强制降级 | `--scan --export --quit` **15/15**，导出目录仅 3 个原文件 |
| 打包 exe 双击等价冷启动 | 首次 **2.43s**、二次 **2.30s**；`nchittest=2`、大小图标已设置、截图 636 色；正常关闭退出码 0 |
| 单实例 | 第三次启动被激活并自行退出，进程数保持 2（引导 + 子进程） |
| 无 CSV 验证 | GUI 端到端与 exe 导出均对目标目录做完整文件树比对，只含预期文件 |
| 无日志验证 | 冷启动脚本三次启动/关闭后检查 `dist\shiji.log`、`shiji.log.1`、`%TEMP%\shiji.log` 均不存在；exe 导出全流程后同样为 False |

### 11.3 本轮仍未覆盖

- 真机未安装 WebView2 的干净环境仍未实测（仍用 `WEBVIEW2_FORCE_MISSING=1` 模拟）。
- 跨盘剪切未在真实双卷环境实测（本机仅 C: 一个固定卷）。
- 125% / 175% 缩放与负坐标副屏未真机实测；150% 为以往轮次结果。
- MessageBox 的视觉效果未截图归档；仅验证正常路径不弹窗、不写盘。

