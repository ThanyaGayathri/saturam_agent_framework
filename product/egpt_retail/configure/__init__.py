"""The self-serve data-source pipeline: registration, credential storage, and (from #306) the
per-source harvest that connects a registered source to the semantic layer. See #302 for the
full plan and #304 for this package's own scope: the control-plane schema, the wire contract
(``egpt_retail.contracts.data_source``), and credential storage (``secrets.py``).
"""
