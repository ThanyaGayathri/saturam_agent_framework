"""One image, one console script, five argv values.

``egpt-retail api`` serves requests; ``pipelines`` serves the same app with the scheduler poller;
``consolidator`` drains memory scopes; ``migrate`` runs the control-plane schema migrations; and
``warehouse-migrate`` creates the warehouse schema. The last two are stated deviations from R1's
"three entrypoints".

``migrate`` because R2 requires migrations to run and the framework has no runner --
``MigrationManager.up()`` is async-only with no CLI and no startup hook, and ``auto_provision_dbs``
creates tables but never migrates them.

``warehouse-migrate`` (R4) because the warehouse is a different database (D5) -- on AKS a different
server, with different credentials and a different network path -- and because ``migrate`` runs as
a ``pre-install`` Helm hook gating the whole release, which must not be blocked by warehouse
reachability. Folding the two together would also make ``migrate.py``'s "control plane only" scope
untrue.
"""
