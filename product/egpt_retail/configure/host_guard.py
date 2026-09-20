"""The outbound-host guard for a user-registered data source (#305).

Registering a source hands this deployment a caller-supplied host and port to open a
connection to. That is an SSRF surface, and this module is the one place its policy lives:

* **Refuse loopback, link-local and unspecified addresses.** Link-local is where a cloud
  metadata endpoint lives (AWS/Azure/GCP's ``169.254.169.254``, the highest-value SSRF
  target there is); loopback would let a caller probe the API process's own host;
  ``0.0.0.0``/``::`` is treated as loopback by some kernels. An IPv4 address wrapped in an
  IPv6 one (``::ffff:127.0.0.1``) is unwrapped first so it cannot smuggle any of them past
  the check. Private RFC1918 addresses are deliberately allowed: that is where a real
  customer's own warehouse lives, and refusing it would break the router's primary use.
* **Resolve once, then connect to what was checked.** Checking a hostname and then handing the
  same hostname to the driver resolves it twice, and a DNS record with a short TTL can answer
  "safe" to the first lookup and ``169.254.169.254`` to the second (DNS rebinding).
  :func:`check_host` returns the address it checked in :attr:`HostCheck.address`; the caller
  connects to *that* (``psycopg.connect(host=<name>, hostaddr=<address>, ...)`` -- the name is
  kept so TLS verification and SNI still use it, while the TCP target is pinned). Every address
  the name resolves to is checked, not only the one that is returned.
* **Bound the resolution.** ``socket.getaddrinfo`` takes no timeout, so a name served by an
  unresponsive DNS server would otherwise hold the request for the OS resolver's own, far
  longer, timeout. It runs on a helper thread under :data:`RESOLVE_TIMEOUT_SECONDS`.
* **Never raise for a host that cannot be checked.** A malformed name (bad IDNA, an oversized
  label) makes ``getaddrinfo`` raise ``UnicodeError``/``OverflowError``/``ValueError`` rather
  than ``gaierror``; all of them come back as a :class:`HostCheck` with a message, so a
  caller-supplied host can never surface as a raw 500.

Pure standard library, no database and no driver, so it is importable from anywhere that opens
a connection to a registered source -- the test-connection route today, the harvest executor
next (which still carries its own older copy of the check, without the pinning).
"""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Any, Callable, Optional, TypeVar

__all__ = [
    "HostCheck",
    "RESOLVE_TIMEOUT_SECONDS",
    "check_host",
    "run_with_timeout",
]

#: How long name resolution may take before the host counts as unreachable.
RESOLVE_TIMEOUT_SECONDS = 5.0

_REFUSED = (
    "refused: this host resolves to a loopback or link-local address, which this "
    "deployment does not connect to."
)
_UNRESOLVED = "The host name could not be resolved."
_TIMED_OUT = "Connection timed out."

T = TypeVar("T")


@dataclass(frozen=True)
class HostCheck:
    """The outcome of :func:`check_host`. Exactly one of ``address`` and ``message`` is set.

    ``address`` is the checked address to connect to. ``message`` is a fixed-vocabulary reason
    the host must not (or cannot) be connected to -- never text taken from an exception, so an
    internal hostname or resolver detail cannot reach a response through it."""

    address: Optional[str] = None
    message: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.address is not None


def run_with_timeout(
    function: Callable[..., T], timeout: float, *args: Any, **kwargs: Any
) -> T:
    """Runs ``function`` on a daemon thread and returns its result, re-raising what it raised.
    Raises :class:`TimeoutError` if it has not finished after ``timeout`` seconds.

    The thread is abandoned, not killed, on a timeout -- a stuck resolver call or socket cannot
    be interrupted from outside -- so it lives until the OS gives up on it. It is a daemon, so
    it never holds the process open."""
    outcome: dict = {}

    def target() -> None:
        try:
            outcome["value"] = function(*args, **kwargs)
        except BaseException as exc:  # re-raised on the caller's thread below
            outcome["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"no result within {timeout} seconds")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def _is_blocked(address: "IPv4Address | IPv6Address") -> bool:
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback or address.is_link_local or address.is_unspecified


def check_host(
    host: str, port: Optional[int] = None, *, timeout: float = RESOLVE_TIMEOUT_SECONDS
) -> HostCheck:
    """Resolves ``host`` once and returns the address to connect to, or why not. See the
    module docstring for the policy and for why the caller must connect to the returned
    address rather than to ``host`` again."""
    try:
        infos = run_with_timeout(
            socket.getaddrinfo, timeout, host, port, type=socket.SOCK_STREAM
        )
    except TimeoutError:
        return HostCheck(message=_TIMED_OUT)
    except (OSError, UnicodeError, OverflowError, ValueError):
        # OSError covers socket.gaierror; the others are what a malformed name raises from
        # getaddrinfo's own encoding step.
        return HostCheck(message=_UNRESOLVED)

    addresses = []
    for info in infos:
        # The scope id on an IPv6 literal ("fe80::1%eth0") is not part of the address.
        text = str(info[4][0]).split("%", 1)[0]
        try:
            address = ip_address(text)
        except ValueError:
            return HostCheck(message=_UNRESOLVED)
        if _is_blocked(address):
            return HostCheck(message=_REFUSED)
        addresses.append(str(info[4][0]))
    if not addresses:
        return HostCheck(message=_UNRESOLVED)
    return HostCheck(address=addresses[0])
