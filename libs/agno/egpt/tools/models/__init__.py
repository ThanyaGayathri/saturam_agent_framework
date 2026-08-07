"""Deprecated compatibility shim (EGPT-1.7).

Import from ``egpt.integrations.tools.models`` instead; this module will be removed.

Names are re-exported explicitly (not proxied through ``__getattr__`` alone) so that
star-imports, ``dir()`` and static type checking keep working on this path.

Known limitations:

* ``mock.patch`` against this module patches the shim's binding, not the implementation in
  the target module. Patch the target path instead.
* Submodule identity is order-dependent. ``from egpt.tools.models import <sub>`` resolves through the
  ``__getattr__`` below to the *target* submodule and registers no ``egpt.tools.models.<sub>`` entry in
  ``sys.modules``, whereas ``import egpt.tools.models.<sub>`` loads the shim submodule. The same dotted
  path can therefore name two different module objects depending on which route ran first.
  Calls are unaffected -- both routes reach the same function objects -- but it compounds
  the ``mock.patch`` limitation above.
"""

import warnings as _warnings

import egpt.integrations.tools.models as _target

_warnings.warn(
    "egpt.tools.models is deprecated; import from egpt.integrations.tools.models instead. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)


def __getattr__(name: str):
    # The target package's __init__ binds nothing -- every toolkit is reached by submodule --
    # so there is no eager re-export block here and nothing to list in __all__.
    return getattr(_target, name)
