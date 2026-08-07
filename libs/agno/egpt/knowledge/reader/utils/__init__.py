"""Deprecated compatibility shim (EGPT-1.8).

Import from ``egpt.integrations.knowledge.reader.utils`` instead; this module will be
removed.

Names are re-exported explicitly (not proxied through ``__getattr__`` alone) so that
star-imports, ``dir()`` and static type checking keep working on this path.

``mock.patch`` against this module patches the shim's binding, not the implementation in the
target module. Patch the target path instead.
"""

import warnings as _warnings

import egpt.integrations.knowledge.reader.utils as _target
from egpt.integrations.knowledge.reader.utils import (  # noqa: F401
    convert_xls_cell_value,
    excel_rows_to_documents,
    get_workbook_name,
    infer_file_extension,
    row_to_csv_line,
    stringify_cell_value,
)

_warnings.warn(
    "egpt.knowledge.reader.utils is deprecated; import from "
    "egpt.integrations.knowledge.reader.utils instead. This shim will be removed in a "
    "future release.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "convert_xls_cell_value",
    "excel_rows_to_documents",
    "get_workbook_name",
    "infer_file_extension",
    "row_to_csv_line",
    "stringify_cell_value",
]


def __dir__():
    return sorted(set(globals()) | set(__all__))


def __getattr__(name: str):
    return getattr(_target, name)
