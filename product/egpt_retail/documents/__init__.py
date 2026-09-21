"""The document-to-record template's domain code (K15, PA02).

Everything an executor in ``executors/documents.py`` or ``executors/act.py`` needs that is not
itself a workflow step: the extraction templates and their validation, reading a document's
text layer, the mailbox (IMAP in, SMTP out), scored matching against master data, the six
record guardrails, the target mappings, the Salesforce client, the rollback ledger and the
sample set the rehearsal runs over.

Nothing here names an order, a purchase order or Salesforce as the *only* case: the
template's stage table in ``orchestration/compiler.py`` is generic verbs, and this package is
where each verb's data lives -- a second document-driven use case (a supplier invoice, a return
request) adds an extraction template, a target mapping and, if needed, a guardrail row, and
no executor.
"""
