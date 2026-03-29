# Windows 适配记录

这份文件用于在仓库根目录记录 Windows 支持相关工作，方便后续贡献者快速接着当前状态继续开发。

## 范围

当前这轮工作分成四个实现阶段：

1. 让原生 Windows 下的导入链和底层 helper 路径安全可用
2. 让 `bash_exec` 的普通命令会话使用具备平台感知的 shell 与进程后端
3. 让交互式 terminal 运行时在 Windows 上通过 pipe + PowerShell 后端可用
4. 更新 launcher / doctor / 文档 / 测试，方便后续继续稳定迭代

## 已修改文件

### 新增文件

- `src/deepscientist/file_lock.py`
  - 为 quest/runtime 状态提供跨平台 advisory 文件锁
- `src/deepscientist/process_control.py`
  - 提供跨平台的进程会话创建与进程终止 helper
  - Windows 后台进程启动时可以隐藏额外弹出的控制台窗口
- `src/deepscientist/bash_exec/models.py`
  - 将 terminal 数据模型从 POSIX runtime 模块中拆出，避免 Windows 导入失败
- `src/deepscientist/bash_exec/shells.py`
  - 为普通命令与交互 terminal 提供平台感知的 shell 启动解析
- `tests/test_windows_support.py`
  - 为新的 Windows helper 路径补充针对性单测

### 更新文件

- `src/deepscientist/quest/service.py`
  - 用新的跨平台锁 helper 替换原先仅依赖 `fcntl` 的实现
- `src/deepscientist/daemon/app.py`
  - 将 terminal model 的导入改到平台安全模块
  - Windows 下 update-status 相关 subprocess 调用现在会使用隐藏窗口创建参数
- `src/deepscientist/bash_exec/runtime.py`
  - 重构交互 terminal runtime，使其支持：
    - POSIX PTY 模式
    - Windows pipe runtime
    - Windows 下隐藏后台 shell，避免弹出空白控制台窗口
    - 平台安全导入
    - 平台感知的进程关闭
    - 不依赖 POSIX-only 模块的 prompt 元数据更新
- `src/deepscientist/bash_exec/service.py`
  - 为 bash session 状态增加 shell/backend 元数据
  - 将 exec session 接到新的 shell 启动抽象
  - 将交互 terminal session 接到新的启动抽象
  - 去掉直接写死的 POSIX 进程终止假设，改用 helper
- `src/deepscientist/bash_exec/monitor.py`
  - 增加平台安全导入
  - 将 exec-session 启动改为新的 shell 启动抽象
  - 增加 Windows 兼容的 pipe reader 路径
  - 为 Windows 后台命令会话隐藏额外弹出的控制台窗口
  - 去掉直接写死的 POSIX-only 进程管理
- `src/ui/src/components/workspace/QuestWorkspaceSurface.tsx`
  - 原生 Windows 下，工作区打开时不再自动创建默认交互 terminal session
  - Windows 现在会等用户在 Terminal 面板里显式点击后才创建终端会话
- `src/deepscientist/doctor.py`
  - 增加 shell backend readiness 检查
  - 在诊断输出中将原生 Windows 支持标记为 experimental
- `bin/ds.js`
  - 改进 Python runtime 校验失败时的诊断输出，能够直接打印 stderr
  - 为 Windows 下 launcher 的 detached 后台子进程（如 managed daemon / supervisor）增加隐藏窗口启动，避免生成可见空白控制台
  - Windows 下后台 managed daemon 现在优先使用 `pythonw.exe` 而不是 `python.exe`，避免后台 Python 自己占用可见控制台
  - Windows 下包括 npm 更新检查在内的同步子进程调用现在也会使用隐藏窗口参数
- `src/ui/src/lib/system-update-status.ts`
  - 增加共享的 update-status 请求缓存，避免首页首次加载时重复触发更新探测
- `src/ui/src/components/system-update/SystemUpdateButton.tsx`
  - 原生 Windows 下不再每 60 秒自动轮询更新状态
  - 改为使用共享 update-status loader，避免首页重复探测
- `src/ui/src/components/landing/UpdateReminderDialog.tsx`
  - 改为使用共享 update-status loader，避免首页重复探测
- `README.md`
  - 补充说明原生 Windows 支持目前为 experimental，并继续推荐 WSL2
- `docs/en/00_QUICK_START.md`
  - 更新平台支持措辞
- `docs/zh/00_QUICK_START.md`
  - 更新平台支持措辞

## 当前状态

- 阶段 1：已完成
- 阶段 2：已完成
- 阶段 3：已在当前代码中实现，Windows 下走 pipe + PowerShell backend
- 阶段 4：已完成，覆盖 launcher 诊断、doctor、文档与针对性测试

## 追加根因说明

- 后续排查发现，原生 Windows 上出现的可见空白控制台窗口，并不只来自 terminal backend。
- 后续排查还发现，启动时和运行中的短暂 `cmd` 闪窗，很可能来自更新检查链路：
  - 首页首次加载时有两个组件同时请求 update status
  - daemon 的 update endpoint 会再拉起 launcher 子进程
  - launcher 的 update probe 在 Windows 上会调用 npm 子进程
- 这些更新检查子进程可能会短暂显示一个 Windows 控制台窗口。
- launcher 本身在拉起 managed daemon 与 supervisor 时，也会使用 detached 后台子进程；之前这些子进程没有统一加 `windowsHide: true`。
- 此外 managed daemon 之前还是通过 `python.exe` 启动的；在 Windows 上，这种后台 Python 进程即使放到后台也仍可能持有一个可见控制台窗口。
- 因此这些 launcher 子进程也可能生成可见空白控制台，而且在手动关闭后被监督流程重新拉起。
- 现在已经同步更新 `bin/ds.js`，让这类 Windows 后台 launcher 子进程以隐藏窗口方式启动，并在可用时优先用 `pythonw.exe` 启动 managed daemon；同时更新检查相关的同步子进程也会走隐藏窗口模式。
- Web 端首页的 update-status 请求也已经去重，并在原生 Windows 下关闭自动更新轮询。

## 已执行验证

- `python3 -m compileall src/deepscientist/bash_exec src/deepscientist/file_lock.py src/deepscientist/process_control.py src/deepscientist/quest/service.py src/deepscientist/daemon/app.py`
- `python3 -m compileall src/deepscientist/bash_exec src/deepscientist/file_lock.py src/deepscientist/process_control.py src/deepscientist/doctor.py tests/test_windows_support.py`
- `node -c bin/ds.js`
- `python3 -c "import sys; sys.path.insert(0, 'src'); import deepscientist.bash_exec.shells, deepscientist.process_control, deepscientist.file_lock; print('helpers-ok')"`

## 当前本地环境说明

- 在当前 Linux 沙箱里，直接执行 `import deepscientist.cli` 仍然失败，原因是沙箱 Python 环境缺少 `websockets` 依赖，而不是这次 Windows 适配改动本身出错。
- 当前沙箱里没有安装 `pytest`，所以新增的针对性测试已经加入仓库，但没有在这里实际执行。

## 后续建议

- 在真实 Windows 主机上补做交互 terminal 的端到端验证，重点覆盖：
  - `powershell.exe`
  - `pwsh`
  - 缓慢持续输出的命令
  - 需要优雅中断的命令
- 一旦具备 Windows runner，补上 Windows CI 任务。
- 如果原生 Windows 下的交互 shell 体验仍然不够强，下一步升级方向是基于当前这次新增抽象再接入 ConPTY / `pywinpty` backend。
- 如果这次改动后用户仍然反馈 Windows terminal 弹窗，请优先排查是否还有别的 `ensure_terminal_session(...)` 调用路径，而不是立刻再次改底层 backend。
