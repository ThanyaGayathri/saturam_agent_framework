# Symmetric Cookbook

Examples for `rbac/symmetric` in EgptOS.

## Files
- `advanced_scopes.py` — Advanced RBAC Example with EgptOS - Simplified Scopes with Audience Verification.
- `agent_permissions.py` — Per-Agent Permissions Example with EgptOS.
- `basic.py` — Basic RBAC Example with EgptOS.
- `custom_scope_mappings.py` — Custom Scope Mappings Example.
- `user_isolation.py` — Adds opt-in per-user data isolation on top of `basic.py`. Non-admins see only their own data; `agent_os:admin` (or custom `admin_scope`) bypasses isolation.
- `with_cookie.py` — Basic RBAC Example with EgptOS.

## Prerequisites
- Load environment variables with `direnv allow` (requires `.envrc`).
- Run examples with `.venvs/demo/bin/python <path-to-file>.py`.
- Some examples require local services (for example Postgres, Redis, Slack, or MCP servers).
