# Windows Adaptation Log

This file records Windows support work in the repository root so later contributors can continue from the current state quickly.

## Scope

The current work targets four implementation stages:

1. make the import and low-level helper path safe on native Windows
2. make `bash_exec` exec sessions use a platform-aware shell and process backend
3. make the interactive terminal runtime usable on Windows with a pipe + PowerShell backend
4. update launcher/doctor/docs/tests so future contributors can keep iterating safely

## Files Modified

### New files

- `src/deepscientist/file_lock.py`
  - cross-platform advisory file locking for quest/runtime state
- `src/deepscientist/process_control.py`
  - cross-platform process-session creation and process termination helpers
  - Windows background-process launch can now hide the extra console window
- `src/deepscientist/bash_exec/models.py`
  - terminal data models moved out of the POSIX runtime module to make imports Windows-safe
- `src/deepscientist/bash_exec/shells.py`
  - platform-aware shell launch resolution for exec and interactive terminal sessions
- `tests/test_windows_support.py`
  - targeted unit coverage for the new Windows helper paths

### Updated files

- `src/deepscientist/quest/service.py`
  - replaced `fcntl`-only locking with the new cross-platform lock helper
- `src/deepscientist/daemon/app.py`
  - changed terminal model import to the platform-safe module
  - Windows update-status subprocess calls now use hidden-window creation flags
- `src/deepscientist/bash_exec/runtime.py`
  - refactored interactive terminal runtime to support:
    - POSIX PTY mode
    - Windows pipe-based runtime
    - hidden background shell launch on Windows so no empty console window pops up
    - platform-safe imports
    - platform-aware process shutdown
    - prompt metadata updates without importing POSIX-only modules on Windows
- `src/deepscientist/bash_exec/service.py`
  - added shell/backend metadata to bash session state
  - wired exec sessions to the new shell launch abstraction
  - wired interactive terminal sessions to the new launch abstraction
  - removed direct POSIX process-kill assumptions in favor of helper functions
- `src/deepscientist/bash_exec/monitor.py`
  - added platform-safe imports
  - changed exec-session launch to use the new shell launch abstraction
  - added Windows-compatible pipe reader path
  - hides the extra Windows console window for background command sessions
  - removed direct POSIX-only process management
- `src/ui/src/components/workspace/QuestWorkspaceSurface.tsx`
  - native Windows no longer auto-creates the default interactive terminal session when the workspace opens
  - terminal session creation on Windows now waits for an explicit user action via the Terminal pane
- `src/deepscientist/doctor.py`
  - added a shell-backend readiness check
  - marked native Windows support as experimental in diagnostics
- `bin/ds.js`
  - improved Python runtime verification diagnostics by surfacing stderr when import verification fails
  - hides detached Windows launcher child processes such as the managed daemon and supervisor so they do not create a visible blank console window
  - managed daemon startup on Windows now prefers `pythonw.exe` over `python.exe` so the background Python daemon does not own a visible console window
  - synchronous Windows subprocess calls such as npm-based update checks now use hidden-window spawn options
- `src/ui/src/lib/system-update-status.ts`
  - shared update-status request cache to avoid duplicate homepage update probes on initial load
- `src/ui/src/components/system-update/SystemUpdateButton.tsx`
  - Windows no longer polls update status every 60 seconds automatically
  - uses shared update-status loader to avoid duplicate startup probes
- `src/ui/src/components/landing/UpdateReminderDialog.tsx`
  - uses shared update-status loader to avoid duplicate startup probes
- `README.md`
  - documented experimental native Windows support and WSL2 recommendation
- `docs/en/00_QUICK_START.md`
  - updated platform-support wording
- `docs/zh/00_QUICK_START.md`
  - updated platform-support wording

## Current Status

- Stage 1: completed
- Stage 2: completed
- Stage 3: implemented in the current codebase with a Windows pipe + PowerShell backend
- Stage 4: completed for launcher diagnostics, doctor, docs, and targeted tests

## Additional Root-Cause Note

- A later investigation showed that one visible blank console window on native Windows was not only caused by the terminal backend.
- A later investigation also showed that brief `cmd` flashes during startup and runtime were likely caused by the update-check path:
  - the homepage mounted two separate update-status callers on load
  - the daemon update endpoint spawned launcher subprocesses
  - the launcher update probe used npm subprocesses on Windows
- Those update-check subprocesses could briefly surface a visible Windows console window.
- The launcher itself was also spawning detached background child processes for the managed daemon and supervisor without `windowsHide: true`.
- The managed daemon was also being started with `python.exe`, which can still own a visible console window on Windows even when launched in the background.
- Those detached launcher children could create a visible blank console window and, when closed manually, be recreated by the supervision flow.
- `bin/ds.js` was updated so detached background launcher children now use hidden-window spawn options on Windows, the managed daemon prefers `pythonw.exe` when available, and sync subprocesses such as update probes also use hidden-window options.
- The web UI now deduplicates homepage update-status requests and disables automatic update polling on native Windows.

## Verification Performed

- `python3 -m compileall src/deepscientist/bash_exec src/deepscientist/file_lock.py src/deepscientist/process_control.py src/deepscientist/quest/service.py src/deepscientist/daemon/app.py`
- `python3 -m compileall src/deepscientist/bash_exec src/deepscientist/file_lock.py src/deepscientist/process_control.py src/deepscientist/doctor.py tests/test_windows_support.py`
- `node -c bin/ds.js`
- `python3 -c "import sys; sys.path.insert(0, 'src'); import deepscientist.bash_exec.shells, deepscientist.process_control, deepscientist.file_lock; print('helpers-ok')"`

## Local Environment Notes

- A direct `import deepscientist.cli` check in the current Linux workspace still failed because the sandbox Python environment does not have the `websockets` dependency installed, not because of the Windows adaptation work itself.
- `pytest` is not installed in the current sandbox, so the new targeted tests were added but not executed here.

## Follow-Up Suggestions

- Validate the interactive Windows terminal end to end on a real Windows host with:
  - `powershell.exe`
  - `pwsh`
  - commands that stream output slowly
  - commands that require graceful interruption
- Add a Windows CI job once a Windows runner is available.
- If interactive shell behavior still feels too limited on native Windows, the next upgrade path is a ConPTY/`pywinpty` backend behind the same `bash_exec` abstractions added in this change set.
- If users still report Windows terminal popups after this change, inspect any remaining non-workspace callers of `ensure_terminal_session(...)` before changing the backend again.
