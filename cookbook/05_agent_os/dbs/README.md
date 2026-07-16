# Dbs Cookbook

Examples for `dbs` in EgptOS.

## Files
- `agentos_default_db.py` — EgptOS Demo.
- `dynamo.py` — Example showing how to use EgptOS with a DynamoDB database.
- `firestore.py` — Example showing how to use EgptOS with a Firestore database.
- `gcs_json.py` — Example showing how to use EgptOS with JSON files hosted in GCS as database.
- `json_db.py` — Example showing how to use EgptOS with JSON files as database.
- `mongo.py` — Mongo Database Backend.
- `mysql.py` — MySQL Database Backend.
- `neon.py` — Example showing how to use EgptOS with Neon as our database provider.
- `postgres.py` — Postgres Database Backend.
- `redis_db.py` — Example showing how to use EgptOS with Redis as database.
- `singlestore.py` — Example showing how to use EgptOS with SingleStore as our database provider.
- `sqlite.py` — Example showing how to use EgptOS with a SQLite database.
- `supabase.py` — Example showing how to use EgptOS with Supabase as our database provider.
- `surreal.py` — Example showing how to use EgptOS with SurrealDB as database.

## Prerequisites
- Load environment variables with `direnv allow` (requires `.envrc`).
- Run examples with `.venvs/demo/bin/python <path-to-file>.py`.
- Some examples require local services (for example Postgres, Redis, Slack, or MCP servers).
