---
name: al-style-deploy
description: Prepare the employee's computer, install missing Git and Python, and publish or update their web project in AL-STYLE private GitHub repositories and Coolify. Check deployment status and roll back releases. Use when the user asks to publish a company project or update its deployed version.
---

# AL-STYLE deployment

Publish the user's current project using the bundled client and the company gateway at `https://server.al-style.kz/platform`. It works in Codex and Claude Code. Install missing Git/Python automatically using the launchers below; the employee need not know Git or install developer tools beforehand. Employees need their own operator-issued invitation, not SSH or Coolify credentials.

The canonical skill source is `https://github.com/alstyle-vibecoding/al-style-deploy`. When the user asks to install or update **the skill itself**, follow [installation and updates](references/install.md), then read the updated entrypoint. Ordinary application deployments use the already installed skill.

## Prepare the computer

Start with the bundled launcher in the employee's local execution environment, before invoking Python or Git directly:

- macOS/Linux/WSL: `sh "<skill-directory>/scripts/run.sh" --setup-only`.
- Native Windows: `powershell.exe -NoProfile -File "<skill-directory>/scripts/run.ps1" -SetupOnly` (use `pwsh` if that is the available PowerShell).

The launchers verify working Git and Python 3.12+, reuse installed tools and install what is missing. Python is installed privately through a pinned, checksum-verified Astral uv release. Native Windows uses checksum-verified MinGit from Git for Windows in the same personal directory. They do not need a preinstalled Python, Git, Node or Docker. Read [setup details](references/setup.md) only for a setup failure or system prompt.

Run **all** client commands through the corresponding launcher: `sh "<skill-directory>/scripts/run.sh" inspect --path "<project>"`, or `powershell.exe -NoProfile -File "<skill-directory>/scripts/run.ps1" inspect --path "<project>"`. Replace `inspect` with `login`, `publish`, `status`, etc. This uses the selected runtime and private Git without restarting the agent or changing global PATH. Use `--help` for the client's commands.

If the OS requires employee confirmation or an administrator password, explain the specific system request and let the employee confirm it privately; never request the password in chat. On macOS exit 20 / `SETUP_PENDING` means Apple's Command Line Tools installer needs completion. Resume the same setup command after completion, then continue publishing. A Windows execution-policy error has an ordinary default-policy case covered in [setup details](references/setup.md); resolve that case instead of asking the employee to install Git/Python manually. Respect corporate installation restrictions and the agent's actual permissions; do not bypass them or report a pending installation as successful.

Never print credentials, read the client's session file into the conversation, put credentials in the project, or substitute a company-wide GitHub/Coolify token.

## First publication

1. Complete computer preparation above and identify the intended project directory. Run `inspect --path <project>` through the launcher to discover files, stack indicators, and the publication configuration. If not signed in, open an interactive terminal for the employee to run the launcher's `login` command and enter their invitation privately. Continue the deployment after successful login. A missing invitation or unconfigured organization requires an operator; do not fabricate an account or use another person's invitation.
2. Read the relevant project entrypoints and build configuration. Prepare working Dockerfiles for its actual technology, using a non-root numeric user and an HTTP port above 1023. Listen on `0.0.0.0`. Account for framework build output, migrations, native libraries, and writable directories. Read [the contract](references/contract.md) to configure multiple services, databases, volumes, environment and admin routes. For static sites, [the static Dockerfile](assets/static.Dockerfile) is a usable starting point.
3. Write `.alstyle/deploy.json` using the contract. Include a public health endpoint returning 2xx. Specify the admin route only if the application has a real admin interface. Implement administrator creation inside the application/container using its existing authentication system. Require first-login password replacement when the application supports it; never claim the gateway creates an admin account merely from an admin route.
4. Run appropriate local application checks with the tools already available, then `scan --path <project>`. Docker and the application language runtime are not prerequisites on the employee computer: builds run on GitHub. If local checks need unavailable tools, use the remote build and public/runtime checks to validate the actual result instead of blocking publication to install Docker or every app dependency. Resolve exposed credentials and unexpected files. Report affected filenames without secret contents. Database dumps, local databases, credentials and project-defined GitHub Actions workflows are excluded from publication; put required configuration in the gateway secret store. Do not upload sensitive history to work around a failed scan.
5. Run `publish --path <project> --name <short-project-name>`. The client creates a private repository, commits a clean snapshot and requests the build/deploy. It maintains a separate publication checkout, preserving the user's existing Git remotes and history. The project mapping in `.alstyle/project.json` contains no secrets.
6. Run `status --path <project>` while deployment progresses. Use `wait --path <project>` for a bounded wait. Treat `healthy` as the gateway's confirmation of the deployed release and public HTTP checks. A successful push or accepted build is not a working application. Report the repository, actual application URL, admin URL if present, and deployment result.

## Updates and recovery

Publish the same project again after changes; the existing repository is reused. Concurrent deployment requests are rejected. Repeat requests for the same commit/configuration reuse the durable job.

For runtime secrets, the user runs `secret-set --path <project> --name KEY` and enters a value privately. The agent can use `--stdin` with an existing authorized secret source without printing it. The manifest refers to secret names only. Missing required secrets are actionable configuration errors.

Use `history --path <project>` to find successful releases, then `rollback --path <project> --release <id>` when the user asks to undo a release. Rollback restores application images/configuration; database migrations and persisted data require their own recovery procedure. Do not describe an image rollback as a database restore.

If publication fails, use `logs --path <project> --kind build` or `logs --path <project> --service <name>` to inspect the employee's own diagnostics, repair the concrete project/configuration error and publish a new commit. Authorization failures, operator connection requirements, quotas or an unresolved provider outage require operator help. Do not switch to personal repositories, shared credentials, arbitrary SSH commands or direct Coolify access to bypass them.
