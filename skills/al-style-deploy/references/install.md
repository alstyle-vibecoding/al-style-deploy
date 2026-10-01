# Installation and skill updates

Canonical source: https://github.com/alstyle-vibecoding/al-style-deploy. It is a public **skill-only** repository. Company application repositories and the deployment control plane remain private. Reading/installing this skill does not grant deployment access: each employee still needs their personal invitation.

For initial installation or an explicit skill update, read the repository's current README. Download its `install.sh` (macOS/Linux/WSL) or `install.ps1` (native Windows) over HTTPS to a temporary file, inspect it, and run it with `--agent codex|claude` or `-Agent codex|claude` for the current agent. The installer uses native OS tools, not Git/Python; it downloads a single GitHub commit and verifies every skill file against that commit's SHA-256 manifest.

The installers replace only the selected personal `al-style-deploy` directory, save the previous directory alongside it, and preserve `~/.al-style-deploy` (login, private runtimes, publication checkouts). They refuse a destination belonging to another skill. After an update, read the new `SKILL.md` before using the client. Installed files include `.github-source` with the repository and commit, without credentials.

Updates happen when the employee asks to install/update the skill again. A GitHub push alone does not change employees' installed copies. Do not silently download or run changed code for an ordinary application deployment.

On Windows, follow the same execution-policy handling in [setup.md](setup.md) for the downloaded trusted installer: use a process-scoped `RemoteSigned` only for the ordinary default-policy case, and unblock only the inspected installer if required. Respect corporate policies. Do not use `Bypass`, change system policy, or ask for administrator passwords in chat.

After installation, run the setup launcher from `SKILL.md` to install missing Git/Python. If the agent does not discover the skill on the next turn, restart its session. This supports agents with a local terminal, including Codex and Claude Code; a web chat without local execution cannot prepare the employee's computer.
