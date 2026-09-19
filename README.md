
## Production Live Process

The canonical autonomous live-process entrypoint is:

```bash
python -m src.execution.live_entry_process_launcher
```

Do not launch individual BUY, SELL, recovery, evidence, or legacy process runners as independent production workers. The unified launcher owns the intended production composition.

### Environment contract

`.env.example` lists the complete required live-process environment surface. It contains variable names only and deliberately contains no production values or secrets.

For local development, values may be supplied through the untracked `.env` file. In deployment, use the platform private environment or secret-management facility.

The process fails closed when required configuration is missing or invalid.

### Persistent live database

`DELVE_LIVE_DB_PATH` is the authoritative SQLite location for the production live-capital process.

In production it MUST point to durable storage that survives process, container, and host restarts. An ephemeral container filesystem is not an acceptable location for the live ledger.

Do not point `DELVE_LIVE_DB_PATH` at the research database `logs/delve_meme.db`.

The live database contains durable state used for recovery, reservations, positions, transaction journals, SELL claims, execution records, and accounting continuity.

### Startup authority

The unified launcher performs configuration bootstrap and a narrow database-readiness preflight before constructing the process-lifetime live authority owner.

When a shutdown signal is already pending, startup preflight is skipped and the unified runner exits without acquiring process authority.

Startup preflight deliberately does not load signer secrets, resolve wallet balances, or create an RPC client. Those fresh-capital requirements remain inside the recovery-first runtime so durable BUY and SELL obligations can be recovered before fresh authority is considered.

### Deployment command

Process managers that support a Procfile should run the worker process:

```text
worker: python -m src.execution.live_entry_process_launcher
```

A production deployment must additionally provide persistent storage for the path configured by `DELVE_LIVE_DB_PATH`.
