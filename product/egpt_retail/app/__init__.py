"""The application: settings, database, components and the EgptOS assembly.

``main.py`` is the uvicorn import target (``egpt_retail.app.main:app``). Everything else here is
what it is assembled from. Nothing in this package runs a model or touches memory on import; it
constructs, so ``/config`` can list what is registered before any credential is present.
"""
