"""Built-in integration declarations, one module per kind.

Every module here is pure data: ``(module, attr)`` strings passed to
``egpt.registry.discovery.register``. None of them imports the integration it names, which
is what lets ``discovery`` enumerate all 58 model providers and all 18 vector dbs on a base
install that has no SDKs at all.

``discovery`` imports these lazily -- ``egpt.registry._declarations.<group suffix>`` on
first touch of a kind -- so a module named here must match the suffix in
``discovery._GROUP_SUFFIX`` (``models``, ``dbs``, ``vectordbs``, ``tools``, ``embedders``).

Adding a provider means adding one row to one of these modules. Adding one from *outside*
this repository means declaring an entry point instead and editing nothing here -- see
``discovery.entry_point_group``.

The rule these modules must not break: **no import of an integration module, ever.**
``tests/unit/registry/test_discovery_laziness.py`` asserts it two ways, statically and in a
clean interpreter.
"""
