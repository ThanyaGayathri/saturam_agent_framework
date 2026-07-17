"""Agent factory for per-request, context-driven agent construction."""

from egpt.factory import BaseFactory


class AgentFactory(BaseFactory):
    """A factory that produces an Agent per request.

    Factories live alongside agents in ``EgptOS(agents=[...])``.
    On each request, EgptOS invokes the factory with a :class:`RequestContext`
    and uses the returned Agent for that request.
    """

    def _post_resolve(self, component) -> None:
        super()._post_resolve(component)
        component.initialize_agent()
