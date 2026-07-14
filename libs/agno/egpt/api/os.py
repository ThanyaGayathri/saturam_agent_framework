from egpt.api.api import api
from egpt.api.routes import ApiRoutes
from egpt.api.schemas.os import OSLaunch
from egpt.utils.log import log_debug


def log_os_telemetry(launch: OSLaunch) -> None:
    """Telemetry recording for OS launches"""
    try:
        with api.Client() as api_client:
            response = api_client.post(
                ApiRoutes.AGENT_OS_LAUNCH,
                json=launch.model_dump(exclude_none=True),
            )
            response.raise_for_status()
    except Exception as e:
        log_debug(f"Could not register OS launch for telemetry: {type(e).__name__}")
