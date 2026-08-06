# SM-R9 — outstanding measurements, closed

Everything PR #15's review left open as "needs measuring", now measured. Two results
**overturn earlier conclusions in this PR — including conclusions of mine** — and are flagged
as such rather than quietly updated.

Config throughout: SM-R4 Mode A (`bge-base-en-v1.5`@768) unless stated, `link_threshold=0.65`,
k=5, Azure `gpt-5.4` for OpenIE/NER/reader.

---

## 1. Power analysis — the review's premise was wrong, and so was my agreement with it

**The ask:** "mark GO provisional and require n≥100 (or a power analysis) before SM-R1 ratifies
switch thresholds keyed on all-hops." The stated reason: the marginal CIs overlap
(vector .30–.67 vs hippo .60–.90).

**The problem with that reasoning:** both methods are scored on the **same questions**.
Overlapping *marginal* CIs do not imply a non-significant *paired* difference — the paired test
conditions on the shared questions and is far more powerful. `all-hops-hit` is Bernoulli per
question, so the right tests are McNemar / a paired bootstrap on discordant pairs.

**Measured** (`power_analysis.py`, and direct paired bootstraps, n=30):

| comparison | diff | 95% CI | p | verdict |
| --- | --- | --- | --- | --- |
| **hybrid vs dense — all-hops** | **+0.2333** | [+0.100, +0.400] | **0.0007** | **significant** |
| hybrid vs dense — recall@5 | +0.1417 | [+0.067, +0.217] | <0.0001 | significant |
| hippo vs dense — all-hops | +0.1667 | [−0.067, +0.400] | 0.226 | not significant |
| hybrid vs hippo — all-hops | +0.0667 | [−0.100, +0.233] | 0.586 | not significant |

Discordant pairs for hybrid-vs-dense all-hops: **7 favouring hybrid, 0 against** — which is why
it clears easily despite the overlapping marginals.

Required n for 80% power (McNemar, from observed discordance):

| comparison | n required | empirical power at n=30 |
| --- | --- | --- |
| hybrid vs dense — all-hops | **17** | **0.94** |
| hybrid vs dense — all-hops, chaining-only | **14** | 0.99 |
| hybrid vs pure PPR — all-hops | **462** | 0.12 |

**Conclusions, and they cut both ways:**

1. **The adopt criterion is already adequately powered at n=30 — for `hybrid`.** n≥100 was not
   the right remedy; a paired test was. This retires the review's Major finding on its own
   terms.
2. **But the criterion does *not* pass for pure PPR** (p=0.23). The original doc evaluated the
   threshold on pure PPR, and on that configuration the reviewer's scepticism was justified —
   just for a different reason than stated.
3. **A claim of mine was overstated.** I told the reviewer "the pure-PPR-vs-hybrid ranking
   flipped." The point estimates invert, but that comparison is **not significant** (p=0.59)
   and needs n≈500. What *is* significant is pure PPR's collapse at inherited thresholds
   (recall −0.275, p=0.0001; all-hops −0.300, p=0.0074) — and after re-tuning, that collapse is
   no longer significant (p=0.095). Corrected in SPIKE.md.

---

## 2. `rrf_k` ablation — my proposed mechanism is refuted

`summary_fusion.md` hypothesised that RRF beat weighted fusion because of its **flat rank
decay**, which rewards cross-leg agreement. `rrf_k` controls exactly that flatness, so the
prediction was: quality should rise with k.

**Measured** (`rrf_k_ablation.py`) — it does not:

| rrf_k | 0 | 1 | 2 | 5 | 10 | 20 | 60 | 200 | 1000 | 10000 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| flatness ratio | 10.0 | 5.5 | 4.0 | 2.5 | 1.82 | 1.43 | 1.15 | 1.04 | 1.01 | 1.00 |
| recall@5 | .8833 | .8833 | .8833 | .8833 | .8833 | .8833 | .8833 | .8833 | .8833 | .8833 |
| all-hops@5 | .7333 | .7333 | .7333 | .7000 | .7000 | .7000 | .7000 | .7000 | .7000 | .7000 |

recall@5 is **exactly constant** across four orders of magnitude, and all-hops slightly
*favours* the steepest settings — the opposite of the prediction (correlation of log k with
all-hops: **−0.67**).

**So the flatness explanation is wrong and is withdrawn.** RRF is essentially insensitive to k
on this leg pair, while still beating weighted min-max at every weight. The mechanism behind
that gap is **unexplained**, and is now recorded as an open question rather than a settled
story. (For context: 70.8% of gold passages appear in *both* legs' pools, so there is ample
agreement for a rank-based rule to exploit — it simply is not what distinguishes the methods.)

This does not change the fusion *disposition* — weighted still showed no advantage, and the
RRF-vs-weighted difference was never significant (p=0.26) — only the explanation offered for it.

---

## 3. Threshold portability across three embedders

Tests SPIKE.md's recommendation to replace the absolute cosine cutoff with a **percentile of
the observed background distribution**. Same KG (same cached triples → identical topology),
three embedders, `threshold_portability.py`.

**Background entity-entity cosine distributions are wildly different** — which is the whole
problem with an absolute constant:

| embedder | p50 | p90 | p99 | p99.9 |
| --- | --- | --- | --- | --- |
| `bge-base`@768 (SM-R4 Mode A) | **0.4114** | 0.4934 | 0.5877 | 0.6993 |
| `text-embedding-3-large`@1024 (Mode B) | **0.1541** | 0.2598 | 0.4158 | 0.6034 |
| `text-embedding-3-small`@1536 (placeholder) | **0.1464** | 0.2448 | 0.4012 | 0.6224 |

A cutoff of 0.5 sits near bge-base's *median* but out at ~p99.7 for the Azure models — the same
number means opposite things.

**Regret vs each embedder's own oracle** (isolates threshold-rule quality from embedder quality;
raw spread conflates the two):

| rule | bge-base | 3-large | 3-small | **worst** |
| --- | --- | --- | --- | --- |
| absolute 0.5 (inherited) | **0.175** | 0.008 | 0.000 | **0.175** |
| absolute 0.65 (tuned on bge) | −0.008 | 0.033 | 0.033 | 0.033 |
| percentile p99.0 | 0.092 | 0.058 | 0.067 | 0.092 |
| percentile p99.5 | 0.067 | 0.033 | 0.025 | 0.067 |
| **percentile p99.9** | **0.000** | **0.000** | 0.033 | **0.033** |

**What this does and does not establish:**

- **Confirmed:** the inherited **0.5 is genuinely miscalibrated** — 0.175 regret on bge-base,
  ~0 on the Azure models. An absolute constant does rot across embedders.
- **Partly confirmed:** p99.9 is the most robust rule tested, hitting each embedder's oracle
  exactly on two of three.
- **Not established, and weaker than SPIKE.md implied:** p99.9's worst-case regret (0.033)
  **ties** a well-chosen absolute constant (0.65). So the defensible claim is *"0.5 was the
  wrong constant"*, **not** *"percentiles are inherently superior to constants"*. SPIKE.md's
  recommendation is softened accordingly. p99.9 is still preferable on the grounds that it
  needs no per-embedder retuning, but that is an operational argument, not a measured win.

**Side observation, deliberately not overclaimed.** At p99.9, bge-base trails both Azure models
for PPR entity linking (recall@5 0.808 vs 0.917 / 0.900). That is the *opposite* of SM-R4's
ranking — but the paired tests are **not significant** at n=30 (3-small vs bge p=0.057; 3-large
vs bge p=0.075; 3-small vs 3-large p=0.93). So this is a directional signal worth re-testing at
larger n, **not** a finding, and specifically **not** grounds to revisit SM-R4. Worth noting
only because SM-R4's Mode A/B tie was measured on *dense retrieval over memory text*, whereas
entity-name linking is a different task — so a difference here would not contradict SM-R4.

---

## 4. Episodic memory vs Wikipedia — my stated expectation was only half right

SPIKE.md asserted that short, elliptical, coreference-heavy conversational turns "should be
expected to score materially worse" than encyclopaedic prose. That was inherited from SM-R6 and
never measured for OpenIE. Measured now (`episodic_kg_quality.py`) on **LongMemEval-S** haystack
sessions — real multi-session assistant dialogue, the same third-party source SM-R8 used —
against a 2Wiki control run through identical code and the same model.

Gold reasoning chains do not exist for LongMemEval, so **gold-chain recall is not computable
there and is not reported.** These are the metrics that *are* comparable:

| metric | 2Wiki (control) | LongMemEval | reading |
| --- | --- | --- | --- |
| passages / sessions | 40 | 40 | |
| words | 2,682 | 55,425 | sessions are ~20× longer than passages |
| triples | 478 | 4,189 | |
| **triples per 100 words** | **17.82** | **7.56** | **2.4× less fact-dense** |
| **grounding rate** | 0.9561 | **0.9642** | **not worse — slightly better** |
| **unresolved-entity rate** | 0.0262 | **0.0772** | **~3× more pronoun/speaker-relative entities** |
| singleton-entity rate | 0.9585 | 0.9849 | confounded — see below |
| empty extractions | 0 | 1 | |

**Where the expectation was wrong:** OpenIE does **not** hallucinate more on dialogue. Grounding
is 0.964 on episodic vs 0.956 on Wikipedia — statistically indistinguishable and directionally
*better*. The "extraction quality degrades on conversation" concern, as far as faithfulness
goes, is not supported.

**Where it was right, and it matters more than expected:**

1. **Fact density collapses.** 7.56 triples per 100 words vs 17.82 — dialogue is 2.4× more
   verbose per extracted fact. Since OpenIE is billed on input tokens, this makes **cost per
   useful triple ~2.4× higher on episodic memory than the 2Wiki figures in §Cost at scale
   imply.** The at-scale table should be read as a floor for conversational corpora.
2. **Coreference is ~3× worse** (7.7% vs 2.6% of entity mentions are pronouns or
   speaker-relative phrases like "my wife", "the user"). These entities cannot be linked to a
   stable KG node, so they are dead weight PPR cannot traverse. The absolute level is lower than
   the SM-R6 framing suggested, but the direction is confirmed.

**Not claimed:** the singleton-entity rate looks worse on episodic (0.985 vs 0.959), but the two
corpora differ ~20× in words per passage and ~9× in entities at equal passage count, so
singleton rate is confounded by corpus shape rather than measuring connectivity fairly. Reported
for completeness, not used as evidence.

**Net for SM-H4:** the KG substrate risk on episodic memory is **not** extraction faithfulness —
it is *density* (cost) and *linkability* (coreference). Those are different problems with
different mitigations than "the extractor will be unreliable", which is what the doc previously
implied.

---

## 5. n=120 — the empirical check, and it changes one n=30 verdict

Built a 120-question stratified set (949 passages, ~$2.18 OpenIE) that is a **strict superset**
of the committed n=30 set, so results are nested and directly comparable. Same config
(SM-R4 Mode A, `link_threshold=0.65`, RRF).

| method | recall@5 [CI] | all-hops@5 [CI] | chaining all-hops [CI] |
| --- | --- | --- | --- |
| vector | 0.723 [.679, .767] | 0.425 [.342, .517] | 0.281 [.188, .375] |
| hippo | 0.779 [.731, .829] | 0.558 [.467, .650] | **0.531 [.438, .635]** |
| **hybrid** | **0.815 [.773, .856]** | **0.592 [.500, .683]** | 0.490 [.385, .583] |

Paired tests at n=120, with the n=30 verdict for comparison:

| comparison | n=30 | n=120 | change |
| --- | --- | --- | --- |
| hybrid vs dense — all-hops | +0.233, p=0.0007 **SIG** | +0.167, p<0.0001 **SIG** (discordant **20:0**) | confirmed, stronger |
| **hippo vs dense — all-hops** | +0.167, **p=0.226 ns** | +0.133, **p=0.017 SIG** | **flipped to significant** |
| hybrid vs hippo — all-hops | +0.067, p=0.586 ns | +0.033, p=0.507 ns | unchanged (needs n≈500) |
| hybrid vs dense — recall@5 | +0.142, p<0.0001 SIG | +0.092, p<0.0001 SIG | confirmed |

**Both sides of the original disagreement were partly right.**

- My correction holds for the headline: hybrid-vs-dense was already significant at n=30, and
  the paired test — not more data — was the missing piece. At n=120 it is overwhelming
  (20 discordant pairs, none against).
- **But the reviewer's "get more data" instinct paid off for pure PPR**: hippo-vs-dense on
  all-hops was *not* significant at n=30 (p=0.226) and **is** at n=120 (p=0.017). That is a real
  verdict change from sample size alone, exactly the kind the review was worried about.
- The hybrid-vs-pure-PPR comparison remains unresolved at n=120, consistent with the n≈500
  power estimate — so the "ranking flip" I claimed earlier is still **not** established, and
  4× more data did not rescue it.

**Adopt criterion (chaining all-hops gain ≥ 0.15) at n=120: MET by both configs** —
hippo **+0.250**, hybrid **+0.208**, both significant (p=0.0001 / p<0.0001). At n=30 it was met
on point estimate but unsupported for pure PPR. **It is now supported for both.**

**A caution the larger set exposes.** Absolute numbers drop across the board (hybrid recall@5
0.883 → 0.815, all-hops 0.700 → 0.592) because the shared corpus is 3.3× bigger (949 vs 285
passages) and retrieval runs over the whole pool. **The n=30 headline was optimistic about
absolute quality** — the *gaps* between methods are what replicate, not the levels. Any
threshold expressed as an absolute score, rather than as a gain over the dense baseline, would
not survive this change of corpus size.

**Also confirmed at scale:** 1 OpenIE parse failure and **3 passages refused by the Azure
content filter** out of 949 (0.3%) — silent holes in the KG that the earlier n=285 run never
surfaced, and which crashed the first n=120 attempt outright before `openie.py` was hardened.
`openie_content_filtered` now reports them.
