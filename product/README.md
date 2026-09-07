# Enterprise GPT — retail product

Product code for Enterprise GPT. Everything here is retail-specific and deliberately lives
**outside** `libs/agno/egpt/`, so nothing in it ships in the published `egpt` wheel.

```
product/
├── pyproject.toml            distribution `egpt-retail`; import root `egpt_retail`
└── egpt_retail/
    ├── contracts/            Lane K → Lane R contracts: KPI formula shape, semantic read API + stub
    ├── executors/            executor registry convention and its check
    ├── ontology/             the shipped retail vocabularies (D9)
    ├── skills/               skill template and the agents authored against it (D2)
    └── api/                  Lane R: the API contract (OpenAPI source of truth), generated models, stub server
```

## Installing

The dev and demo environments install this package in editable mode alongside the framework:

```bash
./scripts/dev_setup.sh        # .venv       -> egpt[dev] + egpt-retail[dev]
./scripts/demo_setup.sh       # .venvs/demo -> egpt[demo] + egpt-retail
```

`egpt` is pinned to the framework version in this checkout and must be installed first —
public PyPI holds an unrelated package under the same name.

The cookbooks under `cookbook/94_retail/` also put `product/` on `sys.path` themselves, so
they run without an install.

## The API contract

`egpt_retail/api/openapi/egpt-retail.v1.yaml` and its fragments are the source of truth.
`egpt_retail/api/models/_generated.py` is generated from it and must not be edited by hand:

```bash
./scripts/gen_api_models.sh          # bundle the fragments, regenerate the models
./scripts/validate.sh                # includes the openapi lint and the model drift check
```

Run the stub server, which serves every operation's declared examples:

```bash
.venv/bin/python -m egpt_retail.api.stub
```

Contract changes are recorded in `egpt_retail/api/openapi/CHANGELOG.md`, per the change
protocol in `docs/plan/delivery-plan.md`.
