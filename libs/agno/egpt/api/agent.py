from egpt.api.api import api, is_telemetry_endpoint_configured
from egpt.api.routes import ApiRoutes
from egpt.api.schemas.agent import AgentRunCreate
from egpt.utils.log import log_debug


def create_agent_run(run: AgentRunCreate) -> None:
    """Telemetry recording for Agent runs"""
    if not is_telemetry_endpoint_configured():
        return
    with api.Client() as api_client:
        try:
            api_client.post(
                ApiRoutes.RUN_CREATE,
                json=run.model_dump(exclude_none=True),
            )
        except Exception as e:
            log_debug(f"Could not create Agent run: {e}")


async def acreate_agent_run(run: AgentRunCreate) -> None:
    """Telemetry recording for async Agent runs"""
    if not is_telemetry_endpoint_configured():
        return
    async with api.AsyncClient() as api_client:
        try:
            await api_client.post(
                ApiRoutes.RUN_CREATE,
                json=run.model_dump(exclude_none=True),
            )
        except Exception as e:
            log_debug(f"Could not create Agent run: {e}")
