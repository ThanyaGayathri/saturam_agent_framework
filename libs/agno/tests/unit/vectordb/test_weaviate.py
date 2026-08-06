"""Deterministic-id seeding for the Weaviate vector db.

Regression guard for the operator-precedence bug at ``weaviate.py``::

    connection_identifier = wcd_url or "local" if local else "default"

Python parses that as ``(wcd_url or "local") if local else "default"``, and ``local``
defaults to ``False`` -- so on the *ordinary Weaviate Cloud path* the identifier was the
constant ``"default"`` and ``wcd_url`` never reached the seed. Two instances against
different WCD clusters sharing a collection name hashed to the same id. Only the
``local=True`` path worked, which is the inverse of what the code intends.

Ids are how the registry tells two vector dbs apart, so a collision means one instance
shadows the other and a component reads the wrong collection.

Construction opens no connection: the client is created lazily.
"""

from egpt.integrations.vectordb.weaviate.weaviate import Weaviate

CLUSTER_A = "https://cluster-a.weaviate.cloud"
CLUSTER_B = "https://cluster-b.weaviate.cloud"


class TestWeaviateIdSeed:
    def test_different_cloud_clusters_get_different_ids(self):
        """The regression -- this is the default path, and it collided."""
        a = Weaviate(wcd_url=CLUSTER_A, collection="docs")
        b = Weaviate(wcd_url=CLUSTER_B, collection="docs")

        assert a.id != b.id, (
            "two Weaviate Cloud clusters sharing a collection name must not share an id; "
            "the registry keeps only one and a component would read the wrong cluster"
        )

    def test_cloud_url_is_stable_across_constructions(self):
        assert Weaviate(wcd_url=CLUSTER_A, collection="docs").id == Weaviate(wcd_url=CLUSTER_A, collection="docs").id

    def test_collection_still_contributes_to_the_seed(self):
        a = Weaviate(wcd_url=CLUSTER_A, collection="docs")
        b = Weaviate(wcd_url=CLUSTER_A, collection="other")

        assert a.id != b.id

    def test_env_configured_url_is_honoured(self, monkeypatch):
        """``self.wcd_url`` falls back to ``WCD_URL``, so the seed must too.

        Otherwise every env-configured client seeds from a constant -- the same defect
        the explicit-argument path had.
        """
        monkeypatch.setenv("WCD_URL", CLUSTER_A)
        from_env = Weaviate(collection="docs")
        monkeypatch.setenv("WCD_URL", CLUSTER_B)
        from_other_env = Weaviate(collection="docs")

        assert from_env.id != from_other_env.id
        assert from_env.id == Weaviate(wcd_url=CLUSTER_A, collection="docs").id, (
            "an env-configured client and an argument-configured one naming the same "
            "cluster are the same target and must agree on an id"
        )

    def test_local_path_is_stable_and_distinct_from_cloud(self, monkeypatch):
        monkeypatch.delenv("WCD_URL", raising=False)
        local_a = Weaviate(local=True, collection="docs")
        local_b = Weaviate(local=True, collection="docs")

        assert local_a.id == local_b.id
        assert local_a.id != Weaviate(wcd_url=CLUSTER_A, collection="docs").id

    def test_explicit_id_wins(self):
        assert Weaviate(wcd_url=CLUSTER_A, collection="docs", id="pinned").id == "pinned"
