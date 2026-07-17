# CLAUDE.md - Scheduler Cookbook

Instructions for Claude Code when testing the Scheduler cookbooks.

---

## Quick Reference

**Test Environment:**
```bash
# Virtual environment with all dependencies
.venvs/demo/bin/python

# Requires croniter and pytz (included in agno[scheduler] and agno[demo])
```

**Run a cookbook:**
```bash
.venvs/demo/bin/python cookbook/05_agent_os/scheduler/basic_schedule.py
```

**Test results file:**
```
cookbook/05_agent_os/scheduler/TEST_LOG.md
```

---

## Testing Workflow

### 1. Before Testing

Ensure the virtual environment exists:
```bash
./scripts/demo_setup.sh
```

The scheduler cookbooks require a running EgptOS instance with a database.
Start PostgreSQL if needed:
```bash
./cookbook/scripts/run_pgvector.sh
```

### 2. Running Tests

Run individual cookbooks with the demo environment:
```bash
.venvs/demo/bin/python cookbook/05_agent_os/scheduler/basic_schedule.py
.venvs/demo/bin/python cookbook/05_agent_os/scheduler/schedule_management.py
```

### 3. Updating TEST_LOG.md

After each test, update `cookbook/05_agent_os/scheduler/TEST_LOG.md` with:
- Test name and path
- Status: PASS or FAIL
- Brief description of what was tested
- Any notable observations or issues

---

## Code Locations

| Component | Location |
|-----------|----------|
| Scheduler core | `libs/agno/egpt/scheduler/` |
| Cron utilities | `libs/agno/egpt/scheduler/cron.py` |
| Schedule executor | `libs/agno/egpt/scheduler/executor.py` |
| Schedule poller | `libs/agno/egpt/scheduler/poller.py` |
| DB schemas | `libs/agno/egpt/db/schemas/scheduler.py` |
| API router | `libs/agno/egpt/os/routers/schedules/` |
| API schemas | `libs/agno/egpt/os/routers/schedules/schema.py` |
| EgptOS integration | `libs/agno/egpt/os/app.py` |

---

## Key Concepts

- **croniter** and **pytz** are optional dependencies under `agno[scheduler]`
- The scheduler polls the database at a configurable interval (default: 15s)
- Schedules are claimed atomically to prevent duplicate execution
- The executor calls endpoints on the EgptOS server using an internal service token
- Run endpoints (`/agents/*/runs`, `/teams/*/runs`) are called with `background=true` and the executor polls for completion

---

## Known Issues

1. SQLite claim is best-effort, not multi-process safe (use PostgreSQL in production)
2. The scheduler requires `scheduler=True` on the `EgptOS` constructor
