"""Use cases as stored things (K16): the catalog row, the draft, the spec, the compiled
workflow, and the runs -- plus the runtime that turns a compiled workflow into a running one.

The interface's six-step builder produces a spec; the orchestration compiler turns it into a
workflow definition; this package stores both, builds the runnable ``Workflow`` from the
definition against the executor registry, and records every run. In the cloud the trigger
comes from outside (a Logic App on Azure, a Step Functions task on AWS) and calls the run
endpoint; locally the run endpoint is called by the interface's Run button or the in-process
poller. The body of the run is the same in all three.
"""

from egpt_retail.usecases.ddl import (
    RUNS_TABLE,
    USE_CASES_TABLE,
    aapply_use_case_ddl,
    apply_use_case_ddl,
)
from egpt_retail.usecases.store import UseCaseStore, new_run_id, new_use_case_id

__all__ = [
    "RUNS_TABLE",
    "USE_CASES_TABLE",
    "UseCaseStore",
    "aapply_use_case_ddl",
    "apply_use_case_ddl",
    "new_run_id",
    "new_use_case_id",
]
