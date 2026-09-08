# 94 · Retail (Enterprise GPT)

Runnable checks for the Lane K contracts in `product/egpt_retail/contracts/`.

These are product examples, not framework examples. They import from `product/`, which is
outside the published `egpt` wheel, so each script puts `product/` on `sys.path` itself.

| Script | Package | What it proves |
|---|---|---|
| `K11_kpi_formula_shape.py` | K11 | The KPI formula shape against nineteen real formulas: what fits, what has to be rewritten, and that feasibility is a set difference. |
| `K00_executor_registry.py` | — | The executor naming convention: the silent collision it prevents, and the check that enforces it. |
| `K01_memory_scope_convention.py` | K1 | The memory scope convention against the real `Scope`: write scopes, read scopes, and the isolation matrix. |
| `K05_semantic_read_api.py` | K5 | The semantic read API stub: unloaded behaviour, proposal visibility, join paths, and the seam to feasibility. |
| `K14_skill_template.py` | K14 | The skill template, by running an agent written against it: closed sets agree, output conforms, refusal is schema-shaped. |

Run:

```bash
.venv/bin/python cookbook/94_retail/K11_kpi_formula_shape.py
```

K11 needs no services. Its corpus is vendored at
`product/egpt_retail/contracts/_fixtures/poc_kpis.json`.

K14 calls a model, so it needs credentials and the demo environment:

```bash
set -a && source azure_key.env && set +a && .venvs/demo/bin/python cookbook/94_retail/K14_skill_template.py
```
