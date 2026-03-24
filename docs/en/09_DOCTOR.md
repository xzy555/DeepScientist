# 09 `ds doctor`: Repair Startup and Environment Problems

Use `ds doctor` when DeepScientist does not start cleanly after installation.

## Recommended flow

1. Install DeepScientist:

   ```bash
   npm install -g @researai/deepscientist
   ```

2. Make sure Codex itself is working first:

   Default OpenAI path:

   ```bash
   codex --login
   ```

   Provider-backed profile path:

   ```bash
   codex --profile minimax
   ```

   If `codex` is missing, repair it explicitly with:

   ```bash
   npm install -g @openai/codex
   ```

3. Try to start DeepScientist:

   ```bash
   ds
   ```

4. If startup fails or looks unhealthy, run:

   ```bash
   ds doctor
   ```

5. Read the checks from top to bottom and fix the failed items first.

6. Run `ds doctor` again until all checks are healthy, then run `ds`.

## What `ds doctor` checks

- local Python runtime health
- whether `~/DeepScientist` exists and is writable
- whether `uv` is available to manage the local Python runtime
- whether `git` is installed and configured
- whether required config files are valid
- whether the current release is still using `codex` as the runnable runner
- whether the Codex CLI can be found and passes a startup probe
- whether an optional local `pdflatex` runtime is available for paper PDF compilation
- whether the web and TUI bundles exist
- whether the configured web port is free or already running the correct daemon

## Common fixes

### Codex is missing

Run the package install again so the bundled Codex dependency is present:

```bash
npm install -g @researai/deepscientist
```

If `codex` is still unavailable afterward, install it explicitly:

```bash
npm install -g @openai/codex
```

### Codex is installed but not logged in

Run:

```bash
codex --login
```

If your Codex CLI version does not expose `--login`, run `codex` and finish the interactive setup there.

Finish login once, then rerun `ds doctor`.

### Codex profile works in the terminal, but DeepScientist still fails

Run DeepScientist with the same profile explicitly:

```bash
ds doctor --codex-profile minimax
ds --codex-profile minimax
```

Replace `minimax` with your real profile name such as `m27`, `glm`, `ark`, or `bailian`.

Also check:

- the same shell still exports the provider API key
- the profile points at the provider's Coding Plan endpoint, not the generic API endpoint
- `~/DeepScientist/config/runners.yaml` uses `model: inherit` if the provider expects the model to come from the profile itself

### The configured Codex model is unavailable

DeepScientist blocks startup until Codex passes a real startup hello probe. In the current release, that probe first uses the runner model configured in:

```text
~/DeepScientist/config/runners.yaml
```

The default is `gpt-5.4`. If your Codex account or CLI config cannot access that model, DeepScientist now retries with the current Codex default model and persists `model: inherit` for future runs. If you still want a specific model, edit the runner config manually and rerun:

```bash
ds doctor
```

For provider-backed Codex profiles, `model: inherit` is usually the right default.

### `uv` is missing

Normally `ds` will bootstrap a local `uv` automatically. If that bootstrap fails, install it manually:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Local paper PDF compilation is unavailable

DeepScientist can compile papers without a full TeX Live install if you add a lightweight TinyTeX runtime:

```bash
ds latex install-runtime
```

If you prefer a system package instead, install a distribution that provides `pdflatex` and `bibtex`.

### Port `20999` is busy

If it is your managed daemon:

```bash
ds --stop
```

Then run `ds` again.

If another service already uses the port, change `ui.port` in:

```text
~/DeepScientist/config/config.yaml
```

Or start on another port directly:

```bash
ds --port 21000
```

### Python `3.10` or older is active

DeepScientist still prefers the active conda environment when it already satisfies Python `>=3.11`.

If your current conda environment is too old, either activate a newer one:

```bash
conda activate ds311
python3 --version
which python3
ds
```

Or create a suitable one:

```bash
conda create -n ds311 python=3.11 -y
conda activate ds311
ds
```

If you do nothing, `ds` can still bootstrap a managed `uv` + Python runtime automatically under the DeepScientist home.

### Git user identity is missing

Run:

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

### Claude was enabled by mistake

Current open-source releases keep `claude` as a TODO/reserved slot only.
Set it back to disabled in:

```text
~/DeepScientist/config/runners.yaml
```

## Notes

- `ds docker` is kept as a compatibility alias, but the official command is `ds doctor`.
- The normal browser URL is `http://127.0.0.1:20999`.
