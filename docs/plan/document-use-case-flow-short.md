# A document use case, in one page

*Who does what, from the form to the scheduled run. The long version, with file names and
every executor, is `document-use-case-flow.md`.*

**1. The form fills itself — no model.** The person types one sentence. A deterministic
reader in the browser fills the form from it: shape Act, template document-to-record,
mailbox, schedule, document kind, target. What it cannot fill becomes a gap, and the
assistant offers options with a reason. Templates, guardrails and mappings are data the
person extends on the form; which master-data roles are needed follows from what the
mapping reads. The one model call on the form is "New document kind", where a drafting
agent proposes a field list for a document the platform has never seen.

**2. The spec goes to the platform.** Save & validate creates the use case row, its draft
and its spec (`POST /use-cases`, `PUT /draft`, `PUT /spec`). Credentials never travel —
a source carries only `credentialRef: env:gmail`, resolved on the server.

**3. Readiness probes are real connections.** The screen checks the gaps locally, then
asks the platform one probe at a time: IMAP login with the use case's own filter, a
Salesforce token and a count on the mapping's object, the master data counts for the roles
this use case needs, one call to the model, the golden-case summary. Each probe compiles
the spec as it is now, so an edit on the form is what gets probed.

**4. The orchestration compiler builds the flow — no model.** `compile_workflow` turns
shape + template into eleven steps in a fixed order — receive, screen, extract, resolve,
guardrails, build, cap, write, rollback point, notify, provenance — each naming its
executor and the slots it consumes, filled from the spec. Extract is the one model step;
its tier comes from its role. "How it will run" draws this as a pipeline and an
orchestration canvas; nothing there is editable, because a plan edited by hand stops
matching the spec.

**5. Rehearse is the real run with the side effects held.** Messages from the mailbox,
uploaded PDFs, or generated samples are injected instead of polled; writes preview, mail
is composed but not sent; every golden case is scored against what it expected. "Accept as
expected" turns a run's output into the golden record.

**6. Activation freezes a version.** Live preflight must pass; the compiler runs once
more; the row gets `status=activated`, `version+1` and the compiled config as a snapshot.
Nothing else is persisted: the Agno workflow is rebuilt from that snapshot at the start of
every run. An edit on the form changes what readiness probes, not what runs, until the use
case is activated again.

**7. The orchestrator only fires the trigger.** Its only job is to call the run endpoint
on the cadence — `POST /use-cases/{id}/runs` (mode live, trigger schedule). Locally that
is the in-process poller; on Azure it would be a Logic App recurrence; on AWS a Step
Functions task. It never sees the steps.

**8. The platform API runs the steps.** It receives that call, rebuilds the Agno workflow
from the compiled config, and Agno executes all eleven steps in that same process — the
executors, the extraction agent, the Salesforce writes, the email. Where the API runs
(your laptop now, a Container App on Azure later) is where the run happens. A document that
fails a step becomes an exception with a class and a reason; the rest carry on. Each
message is carried once: the runs table is the ledger, and the mailbox is never flagged or
deleted.

**9. Everything afterwards reads the platform.** The use case page shows each run's
result with the record's fields and its Salesforce link, and "How it runs" from the stored
spec; the gallery lists only platform use cases; Monitor reads the runs table for cadence,
trigger, last and next run, and silence.

**Where a model runs:** exactly twice — drafting a new template on the form, and reading
each document during a run. Everything else is deterministic code, and every decision that
allows a write (resolve, guardrails, cap) is made by code, never by the model.

**A second document kind** — return requests into Cases — changes only the data: a
different template, a mapping onto Case, two guardrails, and the roles those read. The same
compiler emits the same eleven steps; Resolve asks only for what the mapping reads.
