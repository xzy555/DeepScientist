# 09 `ds doctor`：诊断与修复启动问题

当 DeepScientist 安装后无法正常启动时，请使用 `ds doctor` 做一次本地诊断。

## 推荐使用流程

1. 先安装 DeepScientist：

   ```bash
   npm install -g @researai/deepscientist
   ```

2. 先确保 Codex 自己已经可用：

   默认 OpenAI 路径：

   ```bash
   codex --login
   ```

   provider-backed profile 路径：

   ```bash
   codex --profile minimax
   ```

   如果 `codex` 缺失，请显式修复：

   ```bash
   npm install -g @openai/codex
   ```

   如果你的 Codex CLI 版本没有 `--login`，就运行 `codex` 并在交互式界面里完成认证。

3. 先直接尝试启动：

   ```bash
   ds
   ```

4. 如果启动失败，或者看起来不正常，再运行：

   ```bash
   ds doctor
   ```

5. 从上到下阅读诊断结果，优先修复失败项。

6. 修完后重新运行 `ds doctor`，直到检查通过，再运行 `ds`。

## `ds doctor` 会检查什么

- 本地 Python 运行时是否健康
- `~/DeepScientist` 是否存在且可写
- `uv` 是否可用，以便管理本地 Python 运行时
- `git` 是否安装并完成基本配置
- 必需配置文件是否有效
- 当前开源版本是否仍然使用 `codex` 作为可运行 runner
- Codex CLI 是否存在并通过启动探测
- 是否已经具备可选的本地 `pdflatex` 运行时，以便编译论文 PDF
- Web / TUI bundle 是否存在
- 当前 Web 端口是否空闲，或者是否已运行正确的 daemon

## 常见修复方式

### 没有安装 Codex

重新安装 DeepScientist，让随包的 Codex 依赖一起装好：

```bash
npm install -g @researai/deepscientist
```

如果装完以后 `codex` 仍然不可用，请显式安装：

```bash
npm install -g @openai/codex
```

### 已安装 Codex，但还没有登录

运行：

```bash
codex --login
```

如果你的 Codex CLI 版本没有 `--login`，就运行 `codex` 并在交互式界面里完成认证。

先完成一次登录，再重新执行 `ds doctor`。

### Codex profile 在终端里可用，但 DeepScientist 还是失败

请显式让 DeepScientist 使用同一个 profile：

```bash
ds doctor --codex-profile minimax
ds --codex-profile minimax
```

把这里的 `minimax` 换成你的真实 profile 名，例如 `m27`、`glm`、`ark`、`bailian`。

同时检查：

- 启动 DeepScientist 的这个 shell 中，provider API key 仍然可见
- 该 profile 指向的是 provider 的 Coding Plan endpoint，而不是普通通用 API endpoint
- 如果模型应该由 profile 自己决定，请在 `~/DeepScientist/config/runners.yaml` 中使用 `model: inherit`

### 当前配置的 Codex 模型不可用

DeepScientist 会在启动前强制做一次真实的 Codex hello 探测。当前版本里，这个探测会先使用：

```text
~/DeepScientist/config/runners.yaml
```

里配置的 runner 模型，默认值是 `gpt-5.4`。如果你的 Codex 账号或本地 CLI 配置不能访问这个模型，DeepScientist 现在会自动重试当前 Codex 默认模型，并把后续运行持久化为 `model: inherit`。如果你仍然想指定某个具体模型，再手动改配置并重新执行：

```bash
ds doctor
```

对于 provider-backed 的 Codex profile，通常建议直接使用 `model: inherit`。

### 没有安装 `uv`

正常情况下，第一次运行 `ds` 会自动在本地安装 `uv`。如果自动安装失败，再手动执行：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

如果你在 Windows PowerShell：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 本地论文 PDF 编译暂时不可用

如果你希望直接在 DeepScientist 里本地编译论文，可以安装一个轻量级 TinyTeX `pdflatex` 运行时：

```bash
ds latex install-runtime
```

如果你更倾向于系统级安装，也可以直接安装提供 `pdflatex` 和 `bibtex` 的 LaTeX 发行版。

### `20999` 端口被占用

如果是 DeepScientist 自己之前启动的守护进程：

```bash
ds --stop
```

然后重新执行 `ds`。

如果是其他服务占用了端口，请修改：

```text
~/DeepScientist/config/config.yaml
```

里的 `ui.port`。

也可以直接临时换一个端口启动：

```bash
ds --port 21000
```

### 当前激活的是 Python `3.10` 或更低版本

如果你已经在使用 conda，而当前环境过旧，请先激活正确环境：

```bash
conda activate ds311
python3 --version
which python3
ds
```

或者新建一个可用环境：

```bash
conda create -n ds311 python=3.11 -y
conda activate ds311
ds
```

如果你不手动切换，`ds` 也可以在 DeepScientist home 下自动准备受管的 `uv` + Python 运行时。

### Git 用户身份没有配置

运行：

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

### 误开启了 Claude

当前开源版本里，`claude` 仍然只是 TODO / 预留位，并不能正常运行。
请在：

```text
~/DeepScientist/config/runners.yaml
```

里把它重新设为禁用。

## 说明

- `ds docker` 保留为兼容别名，但正式命令是 `ds doctor`。
- 默认浏览器访问地址是 `http://127.0.0.1:20999`。
