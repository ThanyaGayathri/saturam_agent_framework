"""#305 -- the outbound-host guard (``configure/host_guard.py``), pure logic, no network.

Name resolution is stubbed wherever the point is what the guard does with an answer, so
nothing here depends on DNS. The reviewed gap these pin down: the guard used to resolve a
hostname to check it and then let the driver resolve it again to connect, so a name whose
record changed between the two lookups was checked as one address and dialled as another.
The property that closes it is that :func:`check_host` resolves *once* and returns the checked
address for the caller to connect to.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Any, List

import pytest

from egpt_retail.configure import host_guard
from egpt_retail.configure.host_guard import HostCheck, check_host, run_with_timeout

METADATA = "169.254.169.254"


def _answer(*addresses: str) -> List[Any]:
    """What ``socket.getaddrinfo`` returns for ``addresses``."""
    infos = []
    for address in addresses:
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        sockaddr: tuple = (address, 5432, 0, 0) if ":" in address else (address, 5432)
        infos.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
    return infos


class TestRefusedAddresses:
    @pytest.mark.parametrize(
        "host",
        [
            "127.0.0.1",
            "127.1.2.3",
            "::1",
            METADATA,
            "fe80::1",
            "0.0.0.0",
            "::",
            # An IPv4 address inside an IPv6 one must not smuggle past the check.
            "::ffff:127.0.0.1",
            "::ffff:169.254.169.254",
            "::ffff:0.0.0.0",
        ],
    )
    def test_literals_are_refused(self, host: str) -> None:
        checked = check_host(host, 5432)
        assert not checked.ok
        assert checked.address is None
        assert checked.message is not None
        assert "loopback or link-local" in checked.message

    def test_localhost_is_refused(self) -> None:
        assert not check_host("localhost", 5432).ok

    def test_a_hostname_resolving_to_the_metadata_address_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _answer(METADATA))
        checked = check_host("metadata.attacker.example", 5432)
        assert not checked.ok
        assert "loopback or link-local" in (checked.message or "")

    def test_every_resolved_address_is_checked_not_only_the_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: _answer("93.184.216.34", "127.0.0.1"),
        )
        assert not check_host("mixed.example", 5432).ok

    def test_an_ipv6_scope_id_is_ignored_when_checking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            socket, "getaddrinfo", lambda *a, **k: _answer("fe80::1%eth0")
        )
        assert not check_host("linklocal.example", 5432).ok


class TestAllowedAddresses:
    def test_a_private_rfc1918_literal_is_allowed_and_returned(self) -> None:
        """Deliberately allowed: a customer's own warehouse legitimately lives here."""
        checked = check_host("10.0.5.20", 5432)
        assert checked == HostCheck(addresses=("10.0.5.20",))
        assert checked.ok

    def test_a_public_hostname_returns_the_address_it_resolved_to(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            socket, "getaddrinfo", lambda *a, **k: _answer("93.184.216.34")
        )
        assert check_host("db.customer.example", 5432).address == "93.184.216.34"


class TestEveryVettedAddressIsReturned:
    """The harvest tries each address in turn, as a driver resolving the name would; the
    connection test uses the first. Both read the one answer."""

    def test_addresses_come_back_in_resolver_order_without_repeats(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: _answer("10.1.2.3", "10.1.2.4", "10.1.2.3"),
        )
        checked = check_host("multi.example", 5432)
        assert checked.addresses == ("10.1.2.3", "10.1.2.4")
        assert checked.address == "10.1.2.3"  # the first, for a caller that dials one

    def test_a_refusal_carries_no_address(self) -> None:
        checked = check_host("127.0.0.1", 5432)
        assert checked.addresses == ()
        assert checked.address is None
        assert not checked.ok

    def test_the_bound_is_read_when_called_not_when_defined(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        release = threading.Event()

        def hanging(*args: Any, **kwargs: Any) -> None:
            release.wait(30)

        monkeypatch.setattr(socket, "getaddrinfo", hanging)
        monkeypatch.setattr(host_guard, "RESOLVE_TIMEOUT_SECONDS", 0.1)
        started = time.monotonic()
        try:
            checked = check_host("slow-dns.example", 5432)  # no explicit timeout
        finally:
            release.set()
        assert time.monotonic() - started < 2
        assert checked.message == "Connection timed out."


class TestResolvesOnce:
    def test_a_name_that_rebinds_after_the_first_lookup_is_never_dialled_at_the_second(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DNS rebinding: a short-TTL record answers with a safe address the first time and
        the metadata address every time after. The guard resolves exactly once and hands back
        the address it checked, so the caller connects to that one -- there is no second
        lookup left for the record to change under."""
        calls: List[str] = []

        def rebinding(*args: Any, **kwargs: Any) -> List[Any]:
            calls.append(args[0])
            return _answer("93.184.216.34" if len(calls) == 1 else METADATA)

        monkeypatch.setattr(socket, "getaddrinfo", rebinding)

        checked = check_host("rebind.attacker.example", 5432)

        assert len(calls) == 1
        assert checked.address == "93.184.216.34"  # what a caller must connect to


class TestUnresolvableHostsNeverRaise:
    @pytest.mark.parametrize(
        "error",
        [
            socket.gaierror(socket.EAI_NONAME, "Name or service not known"),
            UnicodeError("label too long"),
            UnicodeDecodeError("idna", b"x", 0, 1, "bad"),
            OverflowError("host too long"),
            ValueError("embedded null"),
            OSError("network unreachable"),
        ],
    )
    def test_resolver_errors_become_a_fixed_message(
        self, monkeypatch: pytest.MonkeyPatch, error: Exception
    ) -> None:
        def failing(*args: Any, **kwargs: Any) -> None:
            raise error

        monkeypatch.setattr(socket, "getaddrinfo", failing)
        checked = check_host("whatever.example", 5432)
        assert not checked.ok
        assert checked.message == "The host name could not be resolved."

    def test_a_real_malformed_name_does_not_raise(self) -> None:
        for host in ("a" * 300 + ".example", "bad\x00host", "\ud800.example", ""):
            checked = check_host(host, 5432)
            assert not checked.ok
            assert checked.message

    def test_an_empty_answer_is_unresolved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [])
        assert check_host("empty.example", 5432).message == (
            "The host name could not be resolved."
        )

    def test_the_message_never_carries_the_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def failing(*args: Any, **kwargs: Any) -> None:
            raise socket.gaierror("no such host: warehouse.internal.corp")

        monkeypatch.setattr(socket, "getaddrinfo", failing)
        message = check_host("warehouse.internal.corp", 5432).message or ""
        assert "warehouse" not in message


class TestResolutionIsBounded:
    def test_a_resolver_that_never_answers_times_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        release = threading.Event()

        def hanging(*args: Any, **kwargs: Any) -> None:
            release.wait(30)

        monkeypatch.setattr(socket, "getaddrinfo", hanging)
        started = time.monotonic()
        try:
            checked = check_host("slow-dns.example", 5432, timeout=0.1)
        finally:
            release.set()
        assert time.monotonic() - started < 2
        assert not checked.ok
        assert checked.message == "Connection timed out."

    def test_the_default_bound_is_short(self) -> None:
        assert 0 < host_guard.RESOLVE_TIMEOUT_SECONDS <= 10


class TestRunWithTimeout:
    def test_returns_the_result(self) -> None:
        assert run_with_timeout(lambda a, b: a + b, 1, 2, 3) == 5

    def test_passes_keyword_arguments(self) -> None:
        assert run_with_timeout(lambda *, x: x, 1, x="kw") == "kw"

    def test_reraises_what_the_function_raised(self) -> None:
        def boom() -> None:
            raise KeyError("nope")

        with pytest.raises(KeyError):
            run_with_timeout(boom, 1)

    def test_times_out_without_waiting_for_the_function(self) -> None:
        release = threading.Event()
        started = time.monotonic()
        try:
            with pytest.raises(TimeoutError):
                run_with_timeout(release.wait, 0.1, 30)
        finally:
            release.set()
        assert time.monotonic() - started < 2
