from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from egpt.os.app import EgptOS


def get_home_router(os: "EgptOS") -> APIRouter:
    router = APIRouter(tags=["Home"])

    @router.get(
        "/",
        operation_id="get_api_info",
        summary="API Information",
        description=(
            "Get basic information about this EgptOS API instance, including:\n\n"
            "- API metadata and version\n"
            "- Available capabilities overview\n"
            "- Links to key endpoints and documentation"
        ),
        responses={
            200: {
                "description": "API information retrieved successfully",
                "content": {
                    "application/json": {
                        "examples": {
                            "home": {
                                "summary": "Example home response",
                                "value": {
                                    "name": "EgptOS API",
                                    "id": "demo-os",
                                    "version": "1.0.0",
                                },
                            }
                        }
                    }
                },
            }
        },
    )
    async def get_api_info():
        """Get basic API information and available capabilities"""
        return {
            "name": "EgptOS API",
            "id": os.id or "agno-agentos",
            "version": os.version or "1.0.0",
        }

    return router
