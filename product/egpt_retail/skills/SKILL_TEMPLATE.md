# Skill template — K14

*Implements D2. Every agent step in every Enterprise GPT workflow uses this shape.*

A **skill** here is the prompt of exactly one agent. Not a control plane, not a plugin, not
a Claude Code skill — a prompt, authored in this shape, riding inside an agent component
that is already versioned. Under D1 the workflow is the spine and fully decided; the skill
is the payload of one step. Nothing in a skill decides what happens next.

There are seven agents in the system. Lane K writes four (enrichment proposer, mapping
proposer, KPI suggestion, use case suggestion) and writes them first. Lane R writes three
(spec extraction, RFQ explanation, playground). All seven use the sections below, in this
order, with none omitted.

---

## Why this shape

Three of the nine alignment rules are properties of a *prompt*, and nothing else in the
system can enforce them:

- Agent output is a typed schema and values come from closed sets.
- Nothing enters the graph unproposed and unconfirmed.
- Anything that permits or blocks is an executor, never a model.

A prompt written some other way cannot be reviewed against the others, and by the time the
difference shows up it shows up as a plausible-sounding wrong answer rather than an error.
Sections 5, 6 and 7 exist for those three rules specifically. They are not optional and
they are not stylistic.

---

## The eight sections

Copy `EXAMPLE.md` and replace its contents. Keep every heading, in this order.

### 1. Header

A fenced metadata block, first thing in the file. The workflow definition reads these.

```
identifier:   snake_case, unique, stable across edits
version:      semver; bump the minor on any prompt change, so R15 can attribute a score drop
model_tier:   small | frontier          (R8's routing table; see below)
writes:       proposals | nothing       (never "graph" — see section 5)
output_schema: dotted path to the pydantic model this agent must return
owner:        the lane that maintains this prompt
```

`model_tier` is a real decision, not a formality. Under G6: explanation and spec extraction
run on the small tier; enrichment and suggestion run on the frontier tier, because they are
rare and quality-critical. If you cannot say why your agent is on the tier it is on, it is
on the wrong one.

### 2. Identity

Two or three sentences. What this agent is, and — more usefully — what it is not. The
sentence that earns its place is the one ruling out the adjacent job somebody will
otherwise assume it does.

> You propose candidate KPIs. You do **not** decide whether a KPI is feasible; a
> deterministic executor does that by set difference, and its answer overrides anything you
> might infer.

### 3. Inputs

A table. Every input named, with where it comes from and whether it is required. An input
the prompt refers to but never declares is the most common way a skill works in testing and
degrades silently in production, because the caller stopped passing it and nothing noticed.

| Input | Source | Required | Notes |
|---|---|---|---|
| `harvest` | K4 metadata harvest | yes | Tables, columns, types, keys, row counts, sampled values |
| `ontology_concepts` | K3, via the semantic read API | yes | Concept names, descriptions, synonyms |
| `existing_kpis` | K11 registry | no | Suppresses duplicate proposals |

State explicitly what the agent may **not** read. An agent with a retrieval tool and no
stated scope will use it.

### 4. Phases

Numbered. One line of instruction each. Phases exist so the model's work is inspectable in
the run record — a single wall of instruction produces a single wall of reasoning, and when
it goes wrong there is no place to point.

Three to six phases. If you need more, the agent is doing two jobs and should be two steps
in the workflow. Every phase after the first says what it may use from the phase before.

```
1. Read the harvest and list every table with its columns and row count.
2. For each table, name the ontology concepts it plausibly instantiates. Use only concepts
   present in `ontology_concepts` — do not invent a concept name.
3. For each candidate, state the evidence: which columns, which sampled values.
4. Emit the output schema. Every field filled from phases 1-3, nothing added here.
```

### 5. Rules

The non-negotiables, stated as prohibitions. Copy the four below verbatim into every skill,
then add any specific to your agent.

```
- You propose. You never write to the graph, the registry or the warehouse. Your output is
  a proposal that a person accepts or rejects (D10, G5). If your output reaches the graph
  without a human decision, that is a defect in the pipeline, not a licence for you.
- You never decide what is permitted, supported, feasible or safe. Those are set
  differences and deterministic checks computed elsewhere, and your view of them is not
  consulted.
- Every value you emit for a closed-set field comes from the enumerated list in section 6.
  If nothing in the list fits, use the refusal behaviour in section 8. Do not coin a new
  value, and do not pick the nearest one.
- Every claim you make is traceable to a named input. If you cannot point at the table,
  column, concept or document a statement came from, do not make the statement.
```

The last rule is the one that makes K13's verification possible: a suggestion nobody can
trace back to specific tables was invented rather than derived.

### 6. Output

The typed schema, given twice: once as the dotted path in the header, and once here as an
enumerated field list. Both, because the model reads this section and the code reads the
header, and they drift.

For every field: name, type, required, and — for any closed set — **the complete list of
permitted values written out in the prompt**. Not "a valid business area". The actual terms.
A model that can only see the field name will produce a plausible value for it, and
plausible is exactly what makes it expensive to catch.

**Render that list from the vocabulary; do not hand-type it.** `grain` and `business_area`
are not Python enums — their permitted terms are the shipped ontology plus the customer's
overlay (D9, G10), so a deployment can add a business area without a release. A hand-typed
list is correct exactly until the first customer extends the ontology, and then it is
silently short and the agent never proposes the term the customer added.
`Vocabulary.render_for_prompt()` and `.describe()` produce the two forms this section needs.

Sets that genuinely cannot vary by customer — `result_shape`, `feasibility` — stay as Python
enums and are mirrored in the vocabulary, with `enums_match_vocabulary()` checking the two
have not drifted.

| Field | Type | Values |
|---|---|---|
| `identifier` | string | `snake_case`, unique within the response |
| `business_area` | enum | `customer`, `product`, `inventory`, `supply_chain`, `marketing`, `sales` |
| `priority` | enum | `high`, `medium`, `low` |
| `rationale` | string | One sentence. Names the tables or concepts it derives from |

Where the schema is rendered directly by a screen — K12 and K13 are — say so, and say which
screen. It stops a later edit adding a field the interface cannot show, or renaming one it
depends on.

**One constraint on the pydantic model, found the hard way.** Do not put `Field(description=...)`
on an enum-typed field. Pydantic serialises such a field to a `$ref`, and strict
structured-output schemas reject a `$ref` carrying sibling keywords — Azure OpenAI returns a
400 before the model is called at all:

```
Invalid schema for response_format: context=('properties', 'kind'),
$ref cannot have keywords {'description'}.
```

Describe enum fields in this section instead, which is where the model reads them anyway.
Plain string and integer fields take a `description` normally.

### 7. Worked example

One complete, valid output. Real values — but **from the shipped retail ontology, never
from a customer's harvest**. This template ships with the product to every retail customer,
so a worked example built from one customer's brands, product names or contacts would put
their data in an artifact the next customer receives. It would also teach the model that
customer's vocabulary as if it were retail's.

Use concepts and terms the shipped ontology defines. If the example needs a value the
ontology does not have, that is a signal the ontology is missing something, not a licence to
borrow one from a harvest.

The example is the highest-leverage part of the prompt and the part most often skipped or
filled with `foo`.

Then one **near-miss**: an output that looks right and is not, with a line saying why. The
near-miss is what teaches the boundary. For a proposer, the useful near-miss is nearly
always a confident claim with no traceable source.

### 8. Refusal and uncertainty

What the agent does when it cannot comply. Every skill needs this, and it needs to be a
*structured* answer rather than prose, because the caller is code.

State: which field carries the refusal, what an empty result looks like, and what the agent
does when an input is missing or an enumerated value does not fit. The default, unless your
agent has a reason to differ:

> Return the schema with an empty result list and a populated `notes` field naming what was
> missing. Never return prose instead of the schema. Never fill a required enum with your
> best guess in order to return something.

An agent that cannot express "I don't know" in its schema will express it as a confident
wrong answer, because that is the only shape available to it.

---

## Authoring checklist

Before an agent component carrying this prompt is published:

- [ ] All eight sections present, in order, none empty.
- [ ] `model_tier` chosen deliberately and justifiable in one sentence.
- [ ] Every closed set enumerated **in full** in section 6, not referred to by name, and
      rendered from the vocabulary rather than hand-typed.
- [ ] The four standing rules copied verbatim into section 5.
- [ ] Worked example uses values from the shipped ontology — never from a customer's
      harvest — plus one near-miss with its reason.
- [ ] Refusal behaviour is a schema, not prose.
- [ ] The pydantic model in `output_schema` exists and its enums match section 6 exactly.
- [ ] No `Field(description=...)` on any enum-typed field (see the note in section 6).
- [ ] A golden question set exists for this skill (G13/R15), so the next edit is measurable.
- [ ] Two runs on identical input produce substantially the same output. Wild variation
      means the prompt is underspecified — this is K12's stated evidence and it applies to
      every skill.

## How this gets used in code

The prompt is the agent's instructions and the schema is its `output_schema`. Nothing else
is required, which is the point of D2 — no skills control plane, because the agent
component is already versioned.

See `EXAMPLE.md` for a complete filled template and
`cookbook/94_retail/K14_skill_template.py` for it running end to end.
