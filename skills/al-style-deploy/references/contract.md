# Deployment contract v1

Save this JSON as `.alstyle/deploy.json`. Keys not in the contract are rejected server-side. Projects can use any language with a working Linux Dockerfile.

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

`context` is relative to the project. `dockerfile` is relative to that context. Up to four built services and two managed databases are supported. A service with `public:false` has no public route; other services can reach it by its service name and declared port. Each public service receives its own HTTPS hostname. Connect a frontend to its backend using the actual assigned URLs or an internal reverse proxy; browser requests cannot resolve Compose service names.

Available managed databases: `postgres`, `mariadb`, `redis`. The gateway generates a per-project password and persistent named volume. Each built service receives `<DATABASE_NAME>_URL`; a database named `db` also provides `DATABASE_URL`. Credentials stay out of Git. Database initialization or migration logic belongs in the application's startup command.

Public health paths must return 2xx without login or redirects. The numeric non-root `user` must match directories and file ownership inside the image. Persistent storage uses named volumes; host bind mounts, host ports, host networking, Docker sockets, privileges and arbitrary Compose are unavailable.

Current pilot quota: 768 MB RAM and 0.75 CPU per project, three projects per employee. Use `me` to read the actual server limits. Increasing limits requires operator configuration and sufficient worker capacity. Builds have a 25-minute runner timeout, a 50 MB source limit and a 1 GB compressed image limit.

The admin block reports an existing admin URL. It does not provision a login by itself. Use the application's own administrator initialization, pass any bootstrap password as a secret, and verify login before reporting working administrator access.

Runtime secret updates apply on the next publication. Releases snapshot encrypted runtime secrets for reliable configuration rollback. Existing database passwords remain stable. Rollback preserves data volumes and does not reverse database migrations.

## Project name and public address

New projects must declare a Dockerfile `test_stage` for every built service. The company runner builds that stage before the runtime image; a failing test stage blocks image publication. Put meaningful tests and their runner in the named stage, using only synthetic credentials. Do not rely on having test tools in the final production image.

Use `stage-env --path <project> --source <project>/.env --names APP_SECRET,ADMIN_PASSWORD --move-source` to privately stage authorized existing values. It prints only variable names and the protected JSON path. Add each name to its service's `secrets` array and pass that private path to `publish --secrets-file <path>`; the client uploads ENV values before Git push. The source `.env` backup and staged JSON stay outside the project. Never pass credential values on command lines or make them Docker build arguments.

Project identity is separate from the deployment manifest. First publication accepts `--name catalog` (repository slug), `--title "Каталог товаров"` (displayed in Coolify), and `--domain catalog` or the complete hostname under the suffix returned by `me`. Run `domains --label catalog` to check availability before creating the project. The final reservation is atomic across employees; a taken address returns a conflict without changing its owner.

The chosen hostname belongs to the public `web` service, or the first public service when there is no public `web`. Additional public services retain separate gateway-assigned hostnames. Health checks, admin links and Coolify routing use the chosen address. Existing projects without a selected domain keep their previous generated URLs.

This pilot allows 1-48 lowercase Latin letters/digits with single hyphens, starting with a letter. Addresses ending in a platform-style 12-character hexadecimal ID are reserved. External/custom domains cannot be assigned by employees: the operator must configure an approved suffix and DNS first. For a company wildcard, point `*.<approved-suffix>` to `89.126.193.220`, verify DNS, then set `ALSTYLE_DOMAIN_SUFFIX` and restart the gateway. Coordinate changes before any projects exist; changing the suffix for deployed projects requires a planned migration and DNS/TLS verification.
