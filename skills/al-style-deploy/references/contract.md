# Deployment contract v1

The agent prepares this JSON as `.alstyle/deploy.json` and creates the necessary Dockerfiles from the actual project. The employee does not need to prepare them. Keys not in the contract are rejected server-side. Projects can use any language with a working Linux container.

```json
{
  "schema_version": 1,
  "services": [
    {
      "name": "web",
      "context": ".",
      "dockerfile": "Dockerfile",
      "port": 3000,
      "public": true,
      "test_stage": "tests",
      "health_path": "/health",
      "user": "10001:10001",
      "memory_mb": 256,
      "cpus": 0.25,
      "environment": {"NODE_ENV": "production"},
      "secrets": ["APP_SECRET", "ADMIN_PASSWORD"],
      "volumes": [{"name": "uploads", "path": "/app/uploads"}]
    }
  ],
  "databases": [{"name": "db", "kind": "postgres", "memory_mb": 192, "cpus": 0.15}],
  "admin": {"service": "web", "path": "/admin"}
}
```

`context` is relative to the project. `dockerfile` is relative to that context. Up to four built services and two managed databases are supported. A service with `public:false` has no public route and may run a background process with no HTTP listener. Its `port`/`PORT` metadata does not require it to listen; private HTTP services can use that port internally. Each public service receives its own HTTPS hostname. Connect a frontend to its backend using the actual assigned URLs or an internal reverse proxy; browser requests cannot resolve Compose service names.

Available managed databases: `postgres`, `mariadb`, `redis`. The gateway generates a per-project password and persistent named volume. Each built service receives `<DATABASE_NAME>_URL`; a database named `db` also provides `DATABASE_URL`. Credentials stay out of Git. Database initialization or migration logic belongs in the application's startup command.

Public health paths must return 2xx without login or redirects. The numeric non-root `user` must match directories and file ownership inside the image. Persistent storage uses named volumes; host bind mounts, host ports, host networking, Docker sockets, privileges and arbitrary Compose are unavailable.

A volume entry accepts `name`, `path`, and optional `shared` (default `false`). Ordinary volumes belong to one service. Entries with the same `name` and `shared:true` share one project-local named volume across services. All participating services must use the same numeric `user`. This does not expose storage to other projects. Create the empty mount directory with matching ownership in both images; do not bake real uploads into either image. Setting `shared:true` on an established private volume switches storage identity and is rejected until an operator-coordinated migration secures and copies the old data.

Read [persistent application data](persistence.md) to prepare data migration, upload volumes and survival checks. The gateway prevents deployment/rollback from removing or renaming an established database, changing its engine, or changing an established service/volume name or destination path. New databases/volumes and resource updates remain available. Planned storage changes require an explicit operator-coordinated migration.

Current pilot quota: 768 MB RAM and 0.75 CPU per project, three projects per employee. Use `me` to read the actual server limits. Increasing limits requires operator configuration and sufficient worker capacity. Builds have a 25-minute runner timeout, a 50 MB source limit and a 1 GB compressed image limit.

The admin block reports an existing admin URL. It does not provision a login by itself. Use the application's own administrator initialization, pass any bootstrap password as a secret, and verify login before reporting working administrator access.

Runtime secret updates apply on the next publication. Releases snapshot encrypted runtime secrets for reliable configuration rollback. Existing database passwords remain stable. Rollback preserves data volumes and does not reverse database migrations.

## Multi-component projects

Discover and configure all required components yourself. Do not interpret the example above as a one-Dockerfile limit. For a FastAPI panel, Telegram polling bot and PostgreSQL, adapt this manifest to the real entrypoints, tests, authentication and directories:

```json
{
  "schema_version": 1,
  "services": [
    {
      "name": "web",
      "context": ".",
      "dockerfile": "Dockerfile.web",
      "test_stage": "tests",
      "port": 8000,
      "public": true,
      "health_path": "/health",
      "user": "10001:10001",
      "memory_mb": 256,
      "cpus": 0.25,
      "environment": {"RECEIPTS_DIR": "/app/receipts"},
      "secrets": ["APP_SECRET"],
      "volumes": [{"name": "receipts", "path": "/app/receipts", "shared": true}]
    },
    {
      "name": "bot",
      "context": ".",
      "dockerfile": "Dockerfile.bot",
      "test_stage": "tests",
      "public": false,
      "user": "10001:10001",
      "memory_mb": 192,
      "cpus": 0.2,
      "environment": {"RECEIPTS_DIR": "/app/receipts"},
      "secrets": ["BOT_TOKEN"],
      "volumes": [{"name": "receipts", "path": "/app/receipts", "shared": true}]
    }
  ],
  "databases": [{"name": "db", "kind": "postgres", "memory_mb": 192, "cpus": 0.15}],
  "admin": {"service": "web", "path": "/admin"}
}
```

This example budgets 640 MB and 0.6 CPU; always compare with live `me` limits. Include only secrets and admin routes the application actually uses. The gateway provisions PostgreSQL and places the same private `DATABASE_URL` in both containers; do not ask the employee for an address or paste credentials in chat. Adapt connection settings to the existing ORM/driver, for example `postgresql+asyncpg` for SQLAlchemy with asyncpg, rather than replacing the ORM. Use one migration owner (normally panel startup), versioned idempotent migrations, and bounded database/schema readiness retries for the bot. Never start both processes with competing schema resets or seeds.

Prepare two runtime Dockerfiles with their actual foreground commands. Run the panel with one suitably bounded web worker; run the bot's existing polling entrypoint directly as the private service. Separate containers provide independent restarts and budgets and keep the employee's component layout. Create `/app/receipts` owned by `10001:10001` in both images before mounting; set both real write/read configurations to `RECEIPTS_DIR` or the equivalent existing setting. The bot's stored receipt references must resolve in the panel. Shared storage is automatic from the manifest and persists when either container is rebuilt. Follow `persistence.md` to import existing data and uploads without loss.

Prevent simultaneous polling instances using the same token. Handle SIGTERM and bounded retries; remove a competing old instance only through existing authorized controls. Do not silently reset Telegram updates, switch webhook mode or discard pending updates. Keep real tokens in private runtime secrets. If a token is genuinely missing, ask the employee to enter it privately; never ask them to set up containers or ENV.

Add tests for the real panel routes, database access/migrations and bot handlers with synthetic updates and a fake Telegram transport. Verify that a bot-produced receipt and associated record are readable by the panel, including authorization and invalid upload cases. Where a real database is required, prepare an isolated disposable test database in the Dockerfile test stage using the project's tooling; no production connection is available during builds. Each declared test stage must execute its checks, not merely install dependencies or echo success.

After `healthy`, inspect the bot's startup logs and verify bounded Telegram API authentication, database/schema readiness and background activity through the application's existing diagnostics. Do not poll `getUpdates` from a second diagnostic process or send real Telegram messages without explicit authorization. If existing diagnostics cannot prove activity, add a small private readiness/heartbeat check to the app and test it; an HTTP listener in the bot itself is not required. Never claim a functioning bot from the panel's 200 response alone. Recheck shared receipt visibility and database/file preservation after an update, then give the employee the working panel/admin addresses and a plain result for the bot.

## Project name and public address

New projects must declare a Dockerfile `test_stage` for every built service. The company runner builds that stage before the runtime image; a failing test stage blocks image publication. Put meaningful tests and their runner in the named stage, using only synthetic credentials. Do not rely on having test tools in the final production image.

Use `stage-env --path <project> --source <project>/.env --names APP_SECRET,ADMIN_PASSWORD --move-source` to privately stage authorized existing values. It prints only variable names and the protected JSON path. Add each name to its service's `secrets` array and pass that private path to `publish --secrets-file <path>`; the client uploads ENV values before Git push. The source `.env` backup and staged JSON stay outside the project. Never pass credential values on command lines or make them Docker build arguments.

Project identity is separate from the deployment manifest. First publication accepts `--name catalog` (repository slug), `--title "Каталог товаров"` (displayed in Coolify), and `--domain catalog` or the complete hostname under the suffix returned by `me`. Run `domains --label catalog` to check availability before creating the project. The final reservation is atomic across employees; a taken address returns a conflict without changing its owner.

The chosen hostname belongs to the public `web` service, or the first public service when there is no public `web`. Additional public services retain separate gateway-assigned hostnames. Health checks, admin links and Coolify routing use the chosen address. Existing projects without a selected domain keep their previous generated URLs.

This pilot allows 1-48 lowercase Latin letters/digits with single hyphens, starting with a letter. Addresses ending in a platform-style 12-character hexadecimal ID are reserved. External/custom domains cannot be assigned by employees: the operator must configure an approved suffix and DNS first. For a company wildcard, point `*.<approved-suffix>` to `89.126.193.220`, verify DNS, then set `ALSTYLE_DOMAIN_SUFFIX` and restart the gateway. Coordinate changes before any projects exist; changing the suffix for deployed projects requires a planned migration and DNS/TLS verification.
