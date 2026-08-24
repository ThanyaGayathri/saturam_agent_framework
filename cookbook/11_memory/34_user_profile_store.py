"""User profile: a structured, per-user profile updated on consolidation (SM-S3 #120).

`SemanticProfileStore` persists one `UserProfile` per user through the vector substrate,
tenant/user-scoped through the same `Scope` every other semantic-tier read and write already gets
(SM-C4) -- see `egpt/memory/semantic/profile.py`'s module docstring for why it does not reuse
`egpt.learn.stores.user_profile.UserProfileStore`'s own `self.db` path (that store predates
tenant scoping and stays single-tenant, per SM-C4's own scope-out; ticket #175 owns fixing it).

`UserProfileStore` itself supplies the extraction: `CoLocatedMemoryProvider.consolidate` calls its
storage-free `extract_profile`/`aextract_profile` (this file's model call) on the episodes each
pass kept, and persists what comes back through `SemanticProfileStore` -- opt-in via
`profile_store=`, same shape as `entity_pipeline`/`relation_pipeline`/`fact_pipeline`.

Requires a real model (`OPENAI_API_KEY`) -- there is no offline path for LLM-driven extraction,
same as `32_fact_extraction_merge.py`/`33_reflection.py` -- and a real vector substrate:

    ./cookbook/scripts/run_pgvector.sh
"""

import os

from egpt.integrations.models.openai import OpenAIResponses
from egpt.learn.config import UserProfileConfig
from egpt.learn.stores.user_profile import UserProfileStore
from egpt.memory.config import MemoryProviderConfig, build_memory_provider
from egpt.memory.types import MemoryEvent, Scope
from egpt.providers.model_provider import ProviderMode

ACME_ADA = Scope(tenant_id="acme", user_id="ada")

config = MemoryProviderConfig(
    provider="co_located",
    mode=ProviderMode.LOCAL,
    vector={
        "backend": "pgvector",
        "db_url": os.getenv(
            "EGPT_MEMORY_PG_URL", "postgresql+psycopg://ai:ai@localhost:5532/ai"
        ),
        "table": "memory_vectors_sm_s3",
    },
    options={
        # Injected, not resolved by registry key: extraction needs a model, and naming one in
        # provider config would put model selection here -- same reasoning
        # `_build_co_located`'s docstring gives for `entity_pipeline`/`relation_pipeline`.
        "profile_store": UserProfileStore(
            config=UserProfileConfig(model=OpenAIResponses(id="gpt-5.5"))
        ),
    },
)

provider = build_memory_provider(config)
if provider.vector is None:
    raise RuntimeError(
        "This cookbook needs the vector substrate -- check EGPT_MEMORY_PG_URL and that "
        "./cookbook/scripts/run_pgvector.sh is running."
    )

provider.write(
    ACME_ADA,
    [
        MemoryEvent(
            content="Hi, I'm Ada Lovelace, but everyone calls me Ada.", scope=ACME_ADA
        )
    ],
)

report = provider.consolidate(ACME_ADA)
print(f"--- consolidation report: profile={report.details['profile']} ---")

# No public read verb for a profile yet (the seam's `read`/`aread` covers episodic/semantic-fact
# retrieval, not this store) -- `provider._semantic_profile` is the actual, if internal, path
# `consolidate` itself just wrote through.
profile = provider._semantic_profile.get(ACME_ADA)
if profile is None:
    raise RuntimeError("Expected consolidation to have written a profile for ada.")
print(
    f"--- profile: name={profile.name!r} preferred_name={profile.preferred_name!r} ---"
)
assert profile.name == "Ada Lovelace"
assert profile.preferred_name == "Ada"

provider.close()
