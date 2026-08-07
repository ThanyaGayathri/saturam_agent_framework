"""A pretend third-party integration package.

Deliberately does not import ``egpt``. A real out-of-tree provider would subclass
``egpt.models.base.Model``, but the point being proved here is that discovery resolves an
entry point without this repository knowing anything about the package -- and keeping the
fixture dependency-free means it can be installed and imported in any environment.

The module name has no ``test_`` prefix on purpose, so pytest does not try to collect it.
"""


class DummyModel:
    """Stands in for a model provider shipped by someone else's package."""

    provider = "Dummy"
    name = "DummyModel"

    def __init__(self, id="dummy-1"):
        self.id = id

    def to_dict(self):
        return {"id": self.id, "name": self.name, "provider": self.provider}


class DummyVectorDb:
    """Stands in for a vector db backend shipped by someone else's package."""

    def __init__(self, name="dummy-vectordb"):
        self.name = name


class DummyToolkit:
    """Stands in for a toolkit shipped by someone else's package."""

    def __init__(self, name="dummy-toolkit"):
        self.name = name


class DummyEmbedder:
    """Stands in for an embedder shipped by someone else's package."""

    def __init__(self, id="dummy-embedder"):
        self.id = id
