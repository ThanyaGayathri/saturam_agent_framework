"""One image, one console script, four argv values.

``egpt-retail api`` serves requests; ``pipelines`` serves the same app with the scheduler poller;
``consolidator`` drains memory scopes; ``migrate`` runs the control-plane schema migrations. The
fourth is a stated deviation from R1's "three entrypoints": R2 requires migrations to run, and the
framework has no runner -- ``MigrationManager.up()`` is async-only with no CLI and no startup hook,
and ``auto_provision_dbs`` creates tables but never migrates them.
"""
