# Persistent application data

Read this before first publication and when updating an existing project. Reuse a completed, verified migration on later updates; never reimport/reset data just because the app is rebuilt. This is part of deployment preparation, not an optional add-on. Preserve the application's features and design; changing a file-based datastore to a managed database is authorized within this workflow.

## Find the actual data

Use `inspect --path <project>` and review its `storage` candidates. These are hints, not a complete classification: inspect the code that writes records and handles uploads, configuration and ORM connection strings. Look for SQLite/database files, mutable JSON/CSV, in-memory records, `localStorage`/IndexedDB, and upload/receipt/document directories. A browser-only prototype needs a small backend and migration/export path for shared durable records; server access cannot extract every user's browser data automatically. Keep immutable logos, product images, example fixtures and build metadata in source; only changing user data belongs in persistent storage.

Identify the source of truth before touching an already deployed application. Production may contain newer data than the employee's local copy. Never replace a live database with local demo data. Preserve an existing PostgreSQL/MariaDB schema and data; do not create a replacement database just because deployment is being updated.

## Keep a private snapshot and remove data from the build

For existing local data use `stage-data --path <project> --source <relative-data-file-or-directory>`. This makes a protected snapshot under the employee's private client state, outside the project, and reports only paths, counts and sizes. Original data is retained. SQLite uses the backup API, including committed WAL records. The adjacent private `inventory.json` has file sizes and SHA-256 checksums. Stop writers while copying ordinary files; a 1 GB/10000-file limit requires batching larger migrations. Do not read snapshots or record contents into chat. This is a migration snapshot, not scheduled backups.

Create `.alstyle/storage.json` with the reviewed paths of real local data, for example:

```json
{
  "schema_version": 1,
  "data_paths": ["data/fuel.sqlite", "data/fuel.sqlite-wal", "data/fuel.sqlite-shm", "data/records.json", "uploads"]
}
```

The client excludes these exact paths and their descendants from every published snapshot, even when already tracked in the local Git repository. Do not exclude source code, schema/migration code, configuration or test fixtures. For a stateless app use an empty list. Add matching entries to `.gitignore` and `.dockerignore` without replacing existing rules. Never put actual records, receipts, dumps or snapshots in Git, build layers, GitHub Actions artifacts, ENV or chat. Synthetic fixtures and migration code can be published.

## Database and uploads

For new file-based business records use the managed PostgreSQL `databases` entry from `contract.md`, normally named `db`. Add the matching driver and minimal data access changes; use the gateway-supplied `DATABASE_URL`, not a hardcoded credential or a manually generated database URL in `secrets`. Use versioned, repeatable schema migrations. Preserve IDs, relationships, decimal balances, timestamps/time zones, nulls, password hashes and references to attachments. Existing managed databases remain unchanged. SQLite/JSON/CSV cannot simply be renamed or copied into the PostgreSQL data directory.

Put changing files in a named volume, for example `{"name":"receipts","path":"/app/receipts"}` on the actual writing service. Configure its upload/read paths to that directory and create it in the Dockerfile with ownership matching the manifest's non-root numeric user. Keep the application code outside mounted directories: mounting over `/app` can hide the image's code. Preserve relative filenames and record references. Do not use temporary directories or the container's writable layer for lasting data. Keep service, database and volume names and mount paths stable across releases. The gateway rejects accidental changes to established storage; planned changes need operator-coordinated migration, not disabling this check.

An empty first volume does not migrate existing uploaded files for you. Prepare a working import path before deploying: reuse the app's authenticated admin import if available, or implement a narrowly scoped bootstrap importer in that project's own language. It must accept only the intended dataset/files, validate types and relative paths, reject traversal/symlinks and oversize uploads, and require a strong per-project migration secret stored privately through `--secrets-file`. Do not expose database ports, create a public upload bypass, use a shared gateway/Coolify token or run employee code on the host.

Transfer the private snapshot directly to the application's authenticated HTTPS importer, with bounded requests and credentials read privately from a file. The gateway itself accepts configuration, not bulk application records. Convert SQLite/JSON/CSV using a project-specific importer into a transaction in PostgreSQL; repair sequences after preserving explicit IDs. Use an import ID/checksum and a durable completion ledger so retries do not duplicate records or overwrite a nonempty database. Copy attachments atomically into the configured volume, verify file hashes, then commit references; leave the source snapshot intact. On interruption resume the same import, not a fresh reset. The app should fail clearly if required initialization/import has not completed, not silently initialize sample data. Disable the bootstrap importer after completion; if it needs a configuration change, publish it while retaining the same storage names.

For data already on the server's container layer, export/copy it before replacing that container. Do not deploy the new empty mount first. Use an existing authorized export path or ask the operator for a scoped snapshot of that application's data. Do not redeploy merely to add an export endpoint when the old container holds the only copy. Prepare the import path in the app only after that copy is secured. Do not claim that existing data was migrated or proceed with replacement while the only copy is inaccessible. Do not attempt automatic destructive conversion of an unknown database format.

## Prove preservation

Add meaningful tests to the application's Dockerfile test stage: migration with representative synthetic records, foreign keys/IDs/decimal totals, retrying the same import without duplicates, startup without reseeding, upload/save/read, and refused unauthorized/path-traversing/oversize imports. Tests must use isolated temporary files/databases and never reset production data.

After initial import compare source/target counts and aggregate balances, key relationships and the inventory's attachment hashes; do not print private rows. Check a representative attachment through the app's authorized interface. Then publish a harmless application update, wait for the new release, and verify the same records and file hashes remain. Use synthetic health-check data in a separate namespace if direct read-only checks are unavailable; preserve real records. An image push or healthy HTTP endpoint alone is not evidence of data preservation. Only report completed migration after these checks. On mismatch stop and retain source/destination copies instead of deleting or resetting them.

Named volumes protect against container replacement. They remain on this server; scheduled off-server backups and server-loss recovery are separate work. Do not claim they are configured by these steps.
