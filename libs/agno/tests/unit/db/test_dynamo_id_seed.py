"""DynamoDb id seeding: the seed must describe what the client actually connects to.

Split from ``test_db_constructor_contracts.py``: importing ``DynamoDb`` raises
``ImportError`` without ``boto3``, and a module-level import here would take the
SDK-free MySQL / AsyncPostgres tests in that file down with it on a driverless
environment. The skip below keeps the split honest -- this file reports that it
checked nothing rather than erroring, and the sibling file stays collectable.

The defect these tests pin: ``DynamoDb`` seeded from its *arguments* only, never the
``AWS_REGION`` / ``AWS_ACCESS_KEY_ID`` environment fallbacks the client is actually
built from. So ``DynamoDb()`` under env credentials seeded the constant
``"None_None"`` -- every env-configured instance colliding on one id -- and got a
*different* id from ``DynamoDb(region_name=..., aws_access_key_id=...)`` naming the
same table set.

No live connection is touched: the client is either injected as a mock or built from
fake env credentials, and boto3 resource construction is local.
"""

from unittest.mock import MagicMock

import pytest

pytest.importorskip("boto3", reason="DynamoDb imports boto3 at module level")

from egpt.integrations.db.dynamo.dynamo import DynamoDb  # noqa: E402


@pytest.fixture
def aws_env(monkeypatch):
    """Fake AWS credentials in the environment, as a deployed service would have."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")


class TestDynamoIdSeed:
    def test_env_and_argument_configuration_agree(self, aws_env):
        """The regression: same target, two ways of naming it, must be one id.

        The client is built from ``region_name or getenv("AWS_REGION")``, so an instance
        configured by environment and one configured by argument are the same database.
        Seeding from the raw arguments made them disagree.
        """
        from_env = DynamoDb()
        from_args = DynamoDb(region_name="us-east-1", aws_access_key_id="AKIAEXAMPLE")

        assert from_env.id == from_args.id, (
            "env-configured and argument-configured instances point at the same DynamoDB; "
            "giving them different ids splits one database into two registry entries"
        )

    def test_different_regions_get_different_ids(self, aws_env):
        assert DynamoDb(region_name="us-east-1").id != DynamoDb(region_name="eu-west-1").id

    def test_different_access_keys_get_different_ids(self, aws_env):
        assert DynamoDb(aws_access_key_id="AKIA_ONE").id != DynamoDb(aws_access_key_id="AKIA_TWO").id

    def test_env_only_instances_are_not_all_identical(self, monkeypatch):
        """Two deployments with different env credentials must not collide.

        Pre-fix both seeded the literal ``"None_None"``.
        """
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_ONE")
        one = DynamoDb()
        monkeypatch.setenv("AWS_REGION", "eu-west-1")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_TWO")
        two = DynamoDb()

        assert one.id != two.id

    def test_stable_across_constructions(self, aws_env):
        assert DynamoDb().id == DynamoDb().id

    def test_injected_client_still_seeds_from_the_client(self):
        client = MagicMock()
        assert DynamoDb(db_client=client).id == DynamoDb(db_client=client).id

    def test_explicit_id_wins(self, aws_env):
        assert DynamoDb(id="pinned").id == "pinned"

    def test_missing_region_still_raises_value_error(self, monkeypatch):
        """The guards must keep firing off the resolved values, not the raw arguments."""
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")

        with pytest.raises(ValueError, match="AWS_REGION"):
            DynamoDb()
