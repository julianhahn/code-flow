# Code Flow instructions

## macOS and Linux are both required

Every feature, fix, setup step, and instruction must work on both macOS and Linux.
Do not treat either platform as an afterthought.

- Read files and settings without assuming an operating system. Use `Path.home()`, environment variables, and path helpers. Never hard-code a user's home directory or a Linux-only executable path.
- Use the current Python interpreter (`sys.executable`) for Python child processes. Find other commands through the configured command search path.
- Use the same Pi settings directory, default model, and default thinking level as the user's Pi setup. Do not hard-code model names or effort levels.
- Desktop launches and terminal launches must use the same settings. Carry required environment settings into desktop launchers explicitly.
- Keep shared behavior platform-independent. If an OS-specific step is necessary, isolate it and document the equivalent path for the other OS.
- Add regression tests for changed behavior. Test on both platforms when available. If only one platform was tested, say so; do not claim the other passed.

Protect the main source checkout on both platforms. Reviews use a separate clone, not the main checkout or a linked worktree.
