"""Composition root for the EgptOS control plane.

``egpt.container`` holds the DI primitive; this module holds the *bindings* for the
control-plane service graph. Per ADR 0001 the composition root lives in the api layer
(``egpt.os``), which is the only place allowed to know concrete implementations.

Two token styles are used, and the rule is deliberate:

- **Type tokens** (``Registry``, ``ServiceAccountVerifier``, ``JWTValidator``) — used when
  the service module is import-safe for anyone who already installed ``egpt[os]``. These
  keep ``container.resolve(...)`` typed.
- **String tokens** (``SCHEDULE_EXECUTOR``, ``SCHEDULE_POLLER``) — used for services behind
  an optional extra (``egpt[scheduler]`` pulls croniter/pytz), so importing this module
  never imports those packages. Annotate the call site to keep type checking.

Component resolution *by name/id* (models, tools, dbs, vector dbs, knowledge) stays with
``Registry``: the container binds the Registry, it does not duplicate it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from egpt.container import Container
from egpt.os.middleware.jwt import JWTValidator
from egpt.os.service_accounts import ServiceAccountVerifier
from egpt.registry import Registry

if TYPE_CHECKING:
    from egpt.os.app import EgptOS

# Default base URL the scheduler calls back on when none is configured.
DEFAULT_SCHEDULER_BASE_URL = "http://127.0.0.1:7777"

# String tokens for bindings whose type cannot be imported eagerly (optional extras) or
# which are plain config values rather than services.
SCHEDULE_EXECUTOR = "scheduler.executor"
SCHEDULE_POLLER = "scheduler.poller"
JWT_MIDDLEWARE_KWARGS = "auth.jwt_middleware_kwargs"

__all__ = [
    "DEFAULT_SCHEDULER_BASE_URL",
    "JWT_MIDDLEWARE_KWARGS",
    "SCHEDULE_EXECUTOR",
    "SCHEDULE_POLLER",
    "build_os_container",
]


def build_os_container(agent_os: "EgptOS", container: Optional[Container] = None) -> Container:
    """Bind the EgptOS internal service graph and return the container.

    Factories close over ``agent_os`` and are lazy, so this may be called from
    ``EgptOS.__init__`` before the instance is fully initialized.

    Args:
        agent_os: The EgptOS instance whose configuration the services are built from.
        container: Optional pre-existing container to add bindings to. Bindings already
            present are left untouched, so a caller can pre-bind a fake before the
            defaults are registered.

    Returns:
        The container holding the control-plane bindings.
    """
    c = container if container is not None else Container(name="egpt-os")

    def bind_default(token: Any, factory: Any, singleton: bool = True) -> None:
        # Never clobber a binding the caller supplied: a container passed to EgptOS is
        # a deliberate substitution, and the defaults must not overwrite it.
        if token not in c:
            c.bind(token, factory, singleton=singleton)

    bind_default(Registry, _registry_factory)
    bind_default(ServiceAccountVerifier, _service_account_verifier_factory(agent_os))
    bind_default(JWT_MIDDLEWARE_KWARGS, _jwt_middleware_kwargs_factory(agent_os))
    bind_default(JWTValidator, _jwt_validator_factory)
    bind_default(SCHEDULE_EXECUTOR, _schedule_executor_factory(agent_os))
    # The poller owns a polling task, so it is transient: a second lifespan (tests, an
    # app restart) must get a fresh poller rather than a stopped one. It still shares the
    # singleton executor, which is what the lifespan puts on ``app.state``.
    bind_default(SCHEDULE_POLLER, _schedule_poller_factory(agent_os), singleton=False)

    return c


def _registry_factory(_c: Container) -> Registry:
    return Registry()


def _service_account_verifier_factory(agent_os: "EgptOS") -> Any:
    def factory(_c: Container) -> ServiceAccountVerifier:
        if agent_os.db is None:
            raise ValueError("ServiceAccountVerifier requires a db: service accounts live in the EgptOS database.")
        return ServiceAccountVerifier(
            db=agent_os.db,
            cache_ttl_seconds=agent_os.settings.service_account_cache_ttl_seconds,
        )

    return factory


def _jwt_middleware_kwargs_factory(agent_os: "EgptOS") -> Any:
    def factory(_c: Container) -> Dict[str, Any]:
        from egpt.os.middleware.jwt import build_jwt_middleware_kwargs

        return build_jwt_middleware_kwargs(
            agent_os.authorization_config,
            authorization=agent_os.authorization,
            service_account_verifier=agent_os._get_service_account_verifier(),
        )

    return factory


def _jwt_validator_factory(c: Container) -> JWTValidator:
    kwargs: Dict[str, Any] = c.resolve(JWT_MIDDLEWARE_KWARGS)
    return JWTValidator(
        verification_keys=kwargs["verification_keys"],
        jwks_file=kwargs["jwks_file"],
        algorithm=kwargs["algorithm"],
    )


def _schedule_executor_factory(agent_os: "EgptOS") -> Any:
    def factory(_c: Container) -> Any:
        from egpt.scheduler import ScheduleExecutor

        internal_token = agent_os._internal_service_token
        if internal_token is None:
            raise ValueError("internal_service_token must be set when scheduler is enabled")
        return ScheduleExecutor(
            base_url=agent_os._scheduler_base_url or DEFAULT_SCHEDULER_BASE_URL,
            internal_service_token=internal_token,
        )

    return factory


def _schedule_poller_factory(agent_os: "EgptOS") -> Any:
    def factory(c: Container) -> Any:
        from egpt.scheduler import SchedulePoller

        return SchedulePoller(
            db=agent_os.db,
            executor=c.resolve(SCHEDULE_EXECUTOR),
            poll_interval=agent_os._scheduler_poll_interval,
        )

    return factory
