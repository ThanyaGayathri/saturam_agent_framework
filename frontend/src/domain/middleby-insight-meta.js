// Per-catalog reasoning + Ask-for-Middleby metadata used by the Use Case
// Detail page. Keyed by catalogId so the toolbar can look up the right
// explanation and production-readiness ask.

const DEFAULTS = {
  reasoning: {
    summary: "This view shows what Enterprise GPT learned from Middleby's data for this use case.",
    confidence: "medium",
  },
  askForMiddleby: {
    text: "See the full checklist for the production data + access this use case would need to go live.",
    sectionId: "",
  },
};

const META = {
  "middleby-uc-01": {
    reasoning: {
      summary: "We rank anonymous sessions by behavior signals (page depth, dwell time, repeat visits, multi-brand browsing) to estimate who is most likely to submit a quote soon.",
      steps: [
        "Loaded GA4 sessions with brand-page views in the last 7 days.",
        "Scored each session on page depth, dwell, repeat, and multi-brand behavior.",
        "Bucketed sessions into intent tiers with a rank per session.",
      ],
      confidence: "high",
      confidenceReason:
        "Every input is a direct GA4 event count (page views, dwell time, repeat visits, multi-brand browsing). The composite intent score is a weighted blend of those signals.\n\n" +
        "The 'High tier' flag applied to each session is a threshold rule: session behavior score >= 70 (out of 100) OR explicitly bucketed as tier = 'High' in the source data. That threshold is the same one used downstream by UX Insights and Revenue Recovery Simulator to define 'high-intent affected sessions'.\n\n" +
        "The scoring weights (page depth, dwell, repeat, multi-brand) are the modeling choice, they reflect standard B2B intent patterns and can be tuned once Middleby confirms which behavior signals best predict submitted quotes in the historical data.",
      caveats: [
        "Intent is scored from GA4 behavior signals (page depth, dwell, repeat, multi-brand browsing).",
      ],
    },
    askForMiddleby: {
      text: "A customer identifier that maps sessions to HubSpot contacts sharpens the ranking further, currently sessions are anonymous, so we can't yet confirm which high-intent sessions actually converted.",
      sectionId: "rfq-funnel",
    },
  },
  "middleby-uc-02": {
    seeNext: [
      { id: "middleby-uc-08", label: "UX errors on the drop-off steps" },
      { id: "middleby-uc-18", label: "Which brands leak most at cart" },
      { id: "middleby-ns-01", label: "Pipeline recoverable if we fix the top drop" },
      { id: "middleby-ns-04", label: "Forecast 12 weeks if we lift conversion by 5 percentage points" },
    ],
    reasoning: {
      summary: "Every quote you don't get is a session that dropped out at one of five funnel steps. This view rebuilds the RFQ funnel from GA4 events (product view → add-to-quote → cart open → form start → submit) and overlays LogRocket errors on the same page paths, so 'why did quotes decrease' has a step-and-cause answer, not a gut feel.",
      steps: [
        "Counted GA4 events at each funnel step for the selected window.",
        "Computed step-to-step conversion so the biggest single drop appears as the widest gap.",
        "Cross-referenced LogRocket error tags on the drop-off pages to distinguish UX bugs from behavior.",
      ],
      confidence: "high",
      confidenceReason:
        "Every step count is a direct GA4 event tally (product_view, add_to_quote, cart_open, form_start, submit) for the selected window, no estimation, no averaging, ratios between adjacent steps are exact. What we don't claim as high-confidence: the specific *causes* named in the RCA block (pricing panel rage-clicks, mobile Next-button unresponsive), those are strong LogRocket correlations, not statistically-proven causation.",
      caveats: [
        "The two biggest leaks in this window are cart-open → form-start and form-start → submit, the same two steps where LogRocket surfaces the top mobile UX issue, so the correlation is worth acting on.",
      ],
    },
    askForMiddleby: {
      text: "A recurring GA4-to-HubSpot event feed keeps this funnel refreshed automatically each week. Add a session-id join between GA4 and LogRocket to turn error-drop correlation into causal attribution.",
      sectionId: "rfq-funnel",
    },
  },
  "middleby-uc-18": {
    seeNext: [
      { id: "middleby-uc-19", label: "SKU-level gaps for the flagged brands" },
      { id: "middleby-uc-08", label: "UX errors on the quote form" },
      { id: "middleby-ns-03", label: "Which brands are rising or falling this week" },
      { id: "middleby-ns-02", label: "Accounts to nudge for the gap brands" },
    ],
    reasoning: {
      summary: "Every quote starts as a session, so a brand with heavy traffic and few quotes is either failing at PDP conversion or being mis-tagged in HubSpot, and both cost pipeline. We compare each brand's GA4 sessions against brand-tagged HubSpot quotes and rank by the gap so merchandising and dealer teams see which brand to fix this week, not next quarter.",
      steps: [
        "Pulled GA4 sessions by brand for the week of Jun 1 to 7, 2026.",
        "Pulled HubSpot deals with brand tags for the same week.",
        "Computed traffic-to-quote conversion for each brand and benchmarked against the Middleby portfolio average (0.7%) and the top performer (Follett at 4.5%).",
        "Ranked brands by absolute gap in sessions-per-quote to surface the highest-impact opportunities.",
      ],
      confidence: "high",
      confidenceReason:
        "GA4 sessions per brand and HubSpot deals tagged to brand are both direct counts for the selected window, no averaging, no modeling. The ranking of gap size is exact. The one variable: Globe and Star tag their deals inconsistently in HubSpot, so their quote counts are likely undercounted by 10-15%, meaning their gap is at least what the chart shows, possibly worse.",
      caveats: [
        "Follett tags brand consistently on every deal; Globe and Star don't, so their quote counts are likely undercounted by roughly 10–15%. Numbers reflect brand as tagged in HubSpot today, a data-hygiene pass on those brands would tighten the gap.",
      ],
    },
    askForMiddleby: {
      text: "Consistent HubSpot deal-brand tagging (top offenders: Globe, Star, Wells, Imperial) and a recurring GA4-to-HubSpot brand join keep this current automatically. Pair with the PO-reference coverage view for the full HubSpot hygiene story.",
      sectionId: "product-interest-gap",
    },
  },
  "middleby-uc-24": {
    reasoning: {
      summary: "We stitch a customer's activity by tying a GA4 session to a LogRocket recording, both to a HubSpot deal, and forward to the Salesforce opportunity created after HubSpot's sync.",
      steps: [
        "Picked a HubSpot deal with its matched LogRocket session.",
        "Pulled the browsing events immediately before the RFQ from GA4.",
        "Read the LogRocket session summary for UX issues on that path.",
        "Attached the Salesforce opportunity created after the RFQ was synced.",
      ],
      confidence: "high",
      confidenceReason:
        "Each linked record: GA4 session, LogRocket recording, HubSpot deal, Salesforce opportunity, is a direct system-of-record pull. No thresholds, no averaging, no dollar estimation.\n\n" +
        "Confidence is High for the linked example, but the sample dataset only contains the deals where all four systems already share a matchable identifier (~15-20% of active deals today). The remaining 80% can't yet be stitched end-to-end. Writing a shared identifier (email hash or account id) into every system's records, GA4, LogRocket, HubSpot, Salesforce, lifts the coverage from ~15% to 100%, at which point every deal has a linkable journey.",
      caveats: [
        "A shared identifier across the four systems keeps this join exact as volume grows.",
      ],
    },
    askForMiddleby: {
      text: "A shared identifier (email hash or account id) written into GA4, LogRocket, HubSpot, and Salesforce so records can be joined for real, today only ~15-20% of active deals have a matchable id across all four systems.",
      sectionId: "customer-journey",
    },
  },
  "middleby-uc-08": {
    seeNext: [
      { id: "middleby-uc-02", label: "The funnel drop these errors caused" },
      { id: "middleby-uc-18", label: "Which brands are worst hit" },
      { id: "middleby-ns-01", label: "Pipeline recoverable if we fix the top 3" },
    ],
    reasoning: {
      summary: "UX bugs are quote leaks with a screen recording. We rank LogRocket issues by how many sessions they touch this window, filter to sessions that also had cart or RFQ intent, then multiply high-intent affected sessions by the per-brand average deal size, turning 'rage-clicks' into 'pipeline at risk'.",
      steps: [
        "Loaded LogRocket issues tagged this window with rage-clicks, dead-clicks, or JS errors.",
        "For each issue, counted unique sessions affected and the high-intent subset.",
        "Estimated pipeline at risk by multiplying high-intent sessions by the per-brand average deal value.",
      ],
      confidence: "medium",
      confidenceReason:
        "Session and error counts are exact, pulled straight from LogRocket. The dollar-at-risk column is a chain of three estimates:\n\n" +
        "1. Which sessions counted as 'high intent': a threshold rule (session behavior score ≥ 70 OR reached the cart / RFQ form).\n" +
        "2. Whether those sessions would have converted: assumed at portfolio-average conversion for the affected pool.\n" +
        "3. What deal size they'd have carried: an implicit ~$3.4K per quote (portfolio average across won HubSpot deals in the current window).\n\n" +
        "Any single link could be off 20-30%, enough to prioritize fixes, not enough to underwrite a budget.",
      caveats: [
        "The top issue this window, 'unresponsive Add-to-Quote on iOS Safari', sits exactly at the biggest RFQ funnel drop-off, so the correlation is likely causal, not coincidence.",
      ],
    },
    askForMiddleby: {
      text: "A LogRocket → GA4 session id join, plus a stable per-brand average deal size feed from HubSpot, moves this view from directional to production-grade.",
      sectionId: "logrocket-ux",
    },
  },
  "middleby-uc-07": {
    reasoning: {
      summary: "We pull the top KPIs, compare to trailing weeks, and attribute significant moves to their most-likely driver from the funnel + UX data.",
      steps: [
        "Loaded weekly KPIs (sessions, RFQs, conversion, pipeline).",
        "Compared to prior 7-day and 30-day windows.",
        "Attributed any move > 5% to its top contributing factor from the funnel and UX layers.",
      ],
      confidence: "medium",
      confidenceReason:
        "The base KPI values (sessions, RFQs, conversion, pipeline) are direct source-of-truth pulls from GA4, HubSpot, and Salesforce.\n\n" +
        "Two specific rules baked into the math:\n" +
        "• Change-detection threshold: only surface moves greater than 5% versus the prior 7-day and 30-day windows (below that is treated as noise).\n" +
        "• Attribution rule: pick the single most-likely driver from the RFQ funnel + UX layers for each surviving move; when two drivers plausibly explain the same shift, both are shown.\n\n" +
        "Confidence is Medium because attribution is directional, a driver line is the most likely contributor, not a causal proof.",
    },
    askForMiddleby: {
      text: "A single weekly-refreshed view combining GA4, HubSpot, LogRocket, and Salesforce keeps this brief current automatically. Add a target-vs-actual field per KPI in HubSpot so 'up' versus 'up but behind plan' is distinguishable at a glance.",
      sectionId: "executive-brief",
    },
  },
  "middleby-uc-05": {
    seeNext: [
      { id: "middleby-uc-02", label: "The whole-catalog RFQ funnel" },
      { id: "middleby-uc-08", label: "UX errors on the journey steps" },
      { id: "middleby-uc-04", label: "Dealer follow-up on quotes that reach submit" },
    ],
    reasoning: {
      summary: "A funnel counts moves; a journey shows the sequence. This view walks the seven browse-to-quote steps a Pitco fryer buyer typically takes, from landing to Add-to-Quote to submitted deal, so dealer and merchandising teams see the drop-off moments as a story, not a percentage.",
      steps: [
        "Sampled GA4 sessions that hit any Pitco fryer page in the selected window.",
        "Ordered their browse events into the seven canonical journey steps.",
        "Attached the HubSpot deal and Salesforce opportunity where the session ended in a submitted quote.",
      ],
      confidence: "high",
      confidenceReason:
        "The seven-step journey uses direct GA4 event counts joined to HubSpot deal creation for the same window, every stage count is a measurement, not a projection. The one caveat: the journey is anchored to a specific product family (Pitco fryers) so the shape is generalizable to other brands but the drop-off ratios vary by product line.",
      caveats: [
        "The path is anchored to one product family (Pitco fryers) so the counts stay comparable. The shape of the journey holds across most Middleby brands, but the drop-off ratios vary.",
      ],
    },
    askForMiddleby: {
      text: "A shared session identifier across GA4, LogRocket, HubSpot, and Salesforce lets this follow a single named customer end to end, not just the aggregate path.",
      sectionId: "customer-journey",
    },
  },
  "middleby-uc-16": {
    seeNext: [
      { id: "middleby-uc-02", label: "Dig into the funnel move" },
      { id: "middleby-ns-03", label: "Which brands are gaining or losing momentum" },
      { id: "middleby-uc-18", label: "Which brands drove the move" },
      { id: "middleby-ns-04", label: "What this move implies for next quarter's pipeline" },
    ],
    reasoning: {
      summary: "An executive doesn't want a dashboard; they want the two or three moves that matter and the reason each moved. This view diffs traffic, errors, quotes, and pipeline against the prior window, keeps only the moves above the noise floor, and attaches a one-line driver from the funnel or UX layer, so a Monday briefing is a paragraph, not a review meeting.",
      steps: [
        "Pulled weekly GA4, LogRocket, HubSpot, and Salesforce metrics for the current window.",
        "Diffed each against the prior window and dropped anything below a 5% change.",
        "Attributed every surviving move to its top contributing driver from the funnel and UX layers.",
      ],
      confidence: "high",
      confidenceReason:
        "The base metrics (traffic, RFQs, conversion, pipeline) are direct source-of-truth pulls from GA4, HubSpot, LogRocket, and Salesforce. Change detection uses a 5% noise floor so we only surface real moves. The one soft edge is driver attribution, we pick the most-likely contributor for each move, but when two drivers plausibly explain the same shift the panel calls out both.",
      caveats: [
        "Attribution is directional: a driver line is the most likely contributor, not a causal proof. When two drivers plausibly explain the same move, we surface both.",
      ],
    },
    askForMiddleby: {
      text: "A weekly refresh of all four sources into one view keeps this briefing current automatically. Add a target-vs-actual field per KPI in HubSpot so 'up' versus 'up but behind plan' is distinguishable at a glance.",
      sectionId: "executive-brief",
    },
  },
  "middleby-ns-01": {
    seeNext: [
      { id: "middleby-uc-08", label: "The UX issues that drove the recovery estimate" },
      { id: "middleby-uc-02", label: "The funnel drop these fixes address" },
      { id: "middleby-ns-04", label: "Project 12 weeks of pipeline with a 5-point conversion lift" },
    ],
    reasoning: {
      summary:
        "We take each open UX issue and brand browse-to-quote gap, estimate the quotes it blocks, convert that to recoverable pipeline at the current average deal size, then rank by value.",
      steps: [
        "Pulled LogRocket issues and brand browse-to-quote gaps for the week.",
        "Estimated blocked quotes for each from affected sessions and intent.",
        "Converted to recoverable pipeline at the average deal size and ranked by value.",
      ],
      confidence: "high",
      confidenceReason:
        "The list of open UX issues and brand gaps is exact (LogRocket + HubSpot). Each fix's dollar value is then built from two estimates:\n\n" +
        "1. How many quotes it currently blocks: same 'high-intent' threshold as the UX Insights card (session behavior score ≥ 70 OR reached the cart / RFQ form).\n" +
        "2. What those quotes would be worth: the current portfolio-average deal size of ~$3.4K per quote (rolled from won HubSpot deals in the window).\n\n" +
        "Both estimates carry the same uncertainty as UX Insights (Medium confidence). Individual dollar figures could be off by 20-30%. But because every fix uses the same math, they shift together, so the ranking (which fix to work on first) stays reliable even if the exact dollars are approximate. Use this list to decide what to fix first, not to commit to a specific dollar target.",
      caveats: ["Effort ratings are directional and should be confirmed with engineering."],
    },
    askForMiddleby: {
      text:
        "Three specific things move this view from directional to production-grade:\n\n" +
        "1. A per-brand average deal size feed from HubSpot. Today we use a portfolio-wide average, which under-weights the recovery estimate for high-ticket brands (TurboChef, Blodgett) and over-weights it for low-ticket ones (Globe, Star).\n\n" +
        "2. A stable session-id join between LogRocket and GA4. This replaces the 'high-intent threshold rule' we use today with an exact count of affected buyers who actually reached the RFQ funnel, removing the 20-30% estimation uncertainty behind the current confidence rating.\n\n" +
        "3. Post-fix outcome tracking in HubSpot for the last 3-6 fixes shipped: did pipeline recover as estimated? That would let us calibrate the multiplier from anecdotal (roughly a percentage-point per fix) to statistical, moving the confidence rating from Medium to High.\n\n" +
        "Also useful (nice-to-have): engineering-confirmed effort ratings per fix, so the ROI ranking reflects actual sprint cost.",
      sectionId: "rfq-funnel",
    },
  },
  "middleby-ns-02": {
    seeNext: [
      { id: "middleby-uc-04", label: "Today's dealer follow-up worklist behind the accounts" },
      { id: "middleby-uc-18", label: "Which brands each recommended account cares about" },
      { id: "middleby-uc-13", label: "Pipeline stage for the flagged accounts" },
    ],
    reasoning: {
      summary:
        "We score every active account on intent signals, stalled deals, UX blocks, and SLA breaches, then rank the accounts sales should act on now and attach the reason.",
      steps: [
        "Combined GA4 intent, HubSpot quote and SLA status, LogRocket blocks, and Salesforce opportunity state per account.",
        "Scored each account 0-100 on likelihood and urgency.",
        "Ranked accounts and attached the single best next action.",
      ],
      confidence: "high",
      confidenceReason:
        "Account scoring uses direct signals. HubSpot deal age, GA4 recent intent activity, Salesforce opportunity state, LogRocket issue exposure. Each signal is a measurement. The one modeling choice is the weighting between them; we use a 0-100 blend that overweights recency and stalled-deal age, which sales-ops teams typically prefer.",
    },
    askForMiddleby: {
      text: "A shared account identifier across GA4, HubSpot, and Salesforce keeps this worklist exact.",
      sectionId: "customer-journey",
    },
  },
  "middleby-ns-03": {
    seeNext: [
      { id: "middleby-uc-18", label: "Brand gap detail for the movers" },
      { id: "middleby-uc-16", label: "Weekly executive summary with drivers" },
      { id: "middleby-kpi-09", label: "Top viewed PDPs by brand" },
    ],
    reasoning: {
      summary:
        "We compare this week's sessions and quotes per brand to the trailing weeks and index the change so rising and falling brands stand out.",
      steps: [
        "Pulled weekly GA sessions and HubSpot quotes by brand.",
        "Compared to the trailing 4-week average.",
        "Indexed the change to 100 (flat) and ranked by momentum.",
      ],
      confidence: "high",
      confidenceReason:
        "Weekly session and quote counts per brand are direct pulls. The momentum index compares this window to a trailing 4-window average, a standard rolling baseline, no estimation. What's directional is calling a small move 'momentum': with low-volume brands (under 200 sessions/week) even normal variance can flag noise as signal, so the small brands sit lower on the list intentionally.",
    },
    askForMiddleby: {
      text: "A consistent weekly GA and HubSpot brand feed keeps momentum current.",
      sectionId: "product-interest-gap",
    },
  },
  "middleby-ns-04": {
    seeNext: [
      { id: "middleby-uc-02", label: "The current-week funnel driving the baseline" },
      { id: "middleby-ns-01", label: "Which fixes contribute to the 5-point conversion lift" },
      { id: "middleby-uc-16", label: "Weekly executive summary anchoring the forecast" },
    ],
    reasoning: {
      summary:
        "We take the last 12 weeks of quote volume, fit the trend forward four weeks, and show what UX and content fixes would add on top.",
      steps: [
        "Collected 12 weeks of quote submissions.",
        "Projected the trend forward four weeks.",
        "Layered recoverable pipeline from open fixes onto the projection.",
      ],
      confidence: "medium",
      confidenceReason:
        "The 12-week history is exact (HubSpot RFQ submissions).\n\n" +
        "The forward projection uses two specific values baked into the math:\n" +
        "• Baseline cart→RFQ conversion rate: 15.6% (the current rolling rate across all brands).\n" +
        "• Average deal size per submitted quote: $3,582 (portfolio-wide average across won HubSpot opportunities).\n\n" +
        "The projection itself is a linear trend fit, it ignores seasonality and any deliberate campaign timing. The +5-point and +10-point scenarios apply an even lift across all brands; in practice a UX fix helps some brands more than others, so the scenario dollars are directional, not distributional.",
      caveats: ["The projection is a trend fit and is directional, not a commitment."],
    },
    askForMiddleby: {
      text: "A longer quote history and a per-brand average deal size feed sharpen the forecast.",
      sectionId: "executive-brief",
    },
  },
  "middleby-kpi-01": {
    reasoning: {
      summary: "We break sessions and quotes by traffic channel and device so mobile vs desktop conversion gaps are visible.",
      steps: [
        "Pulled GA4 sessions by channel and device.",
        "Joined HubSpot quote counts for the same window.",
        "Computed conversion rate per channel-device pair.",
      ],
      confidence: "medium",
      confidenceReason:
        "Sessions and quote counts are direct pulls from GA4 (by channel + device) and HubSpot (deals for the same window). No thresholds or dollar-value estimation are applied. Confidence is Medium because GA4 device categorization (desktop/mobile/tablet) sometimes mis-labels tablets as mobile, a well-known GA4 limitation. A ~5-10% shift in the mobile row would resolve if the device-type feed is upgraded.",
      caveats: ["Device mix is sample-aligned for the demo week."],
    },
    askForMiddleby: {
      text: "A recurring GA4 channel × device export keeps this current automatically. Enabling GA4's enhanced device-classification model (or a client-hints override) removes the tablet-as-mobile mislabel.",
      sectionId: "platform-performance",
    },
  },
  "middleby-kpi-02": {
    reasoning: {
      summary: "We compare closed-won volume to closed-lost reasons so win rate and loss drivers are clear.",
      steps: [
        "Counted HubSpot deals by closed stage.",
        "Grouped closed-lost deals by lost reason.",
        "Computed share of closed-won vs total closed opportunities.",
      ],
      confidence: "medium",
      confidenceReason:
        "Closed-won and closed-lost counts are direct HubSpot deal-stage tallies, exact for the window. The closed-won rate (% of total closed opportunities) is arithmetic, not estimation.\n\n" +
        "Confidence is Medium because the lost-reason breakdown depends on the HubSpot 'lost reason' picklist being filled in consistently. Today roughly 10-15% of closed-lost deals in the sample don't have a lost reason set, so 'Price' and 'Competitor' are likely slightly under-counted. Making the field required at close moves this to High.",
      caveats: ["Lost reasons depend on consistent HubSpot field usage."],
    },
    askForMiddleby: {
      text: "Required lost-reason picklists on closed-lost deals improve this view, currently ~10-15% of closed-lost deals in the sample don't have a lost reason populated, so 'Price' and 'Competitor' shares are slightly understated.",
      sectionId: "platform-performance",
    },
  },
  "middleby-kpi-03": {
    reasoning: {
      summary: "We count HubSpot live chats that open with buy intent and measure how many become quotes.",
      steps: [
        "Pulled live chat threads with buy-intent openers.",
        "Matched threads that created a quote deal.",
        "Ranked intents by inquiry volume and conversion.",
      ],
      confidence: "medium",
      confidenceReason:
        "Chat thread counts by intent are direct HubSpot Chat pulls. Conversion percent is arithmetic (converted_to_quote / inquiries), no thresholds applied.\n\n" +
        "Confidence is Medium because 'chat intent' is currently inferred from the opening message pattern (buy-intent keywords like 'buy now', 'available for purchase'). It works cleanly for the 4 named intents, but ~5% of chats sit in an 'ambiguous' bucket that's excluded from the table. A HubSpot intent-tagging convention (agent tags each thread) removes the ambiguity and moves this to High.",
    },
    askForMiddleby: {
      text: "Tagging 'Can I buy' openers explicitly in HubSpot Chat (either by an intent picklist on the conversation or by the chatbot workflow) makes this KPI production-ready. Currently intent is inferred from opening-message keywords, which excludes ~5% ambiguous chats.",
      sectionId: "intent-to-transact",
    },
  },
  "middleby-kpi-04": {
    reasoning: {
      summary: "We measure guided shopping tool starts, completions, and quote submits from LogRocket session paths.",
      steps: [
        "Identified guided tool session starts.",
        "Counted tool completions and subsequent quote submits.",
        "Ranked tools by quote conversion rate.",
      ],
      confidence: "example",
      confidenceReason:
        "This card uses example numbers to show the shape of the answer. The guided-shopping tools (Fryer selector, Oven configurator, Ice-machine finder, Brand comparison guide) don't currently emit LogRocket events on start/complete/submit, so we have no source data to pull.\n\n" +
        "To move from 'example' to Medium, add three custom LogRocket events per tool: tool_start, tool_complete, tool_submitted_quote. Once instrumented, this becomes a direct funnel count, no thresholds, no dollar estimation, and jumps straight to High confidence.",
    },
    askForMiddleby: {
      text: "Add three custom LogRocket events per guided tool: tool_start, tool_complete, tool_submitted_quote. Once instrumented, this becomes a direct funnel count and jumps to High confidence, the current example numbers become real numbers.",
      sectionId: "user-behavior",
    },
  },
  "middleby-kpi-05": {
    reasoning: {
      summary: "We rank entry pages by bounce rate, page depth, and session duration.",
      steps: [
        "Pulled GA4 landing-page sessions.",
        "Computed bounce rate, pages per session, and average duration.",
        "Flagged high-bounce entry pages for content review.",
      ],
      confidence: "medium",
      confidenceReason:
        "All three metrics: bounce rate, pages per session, average session duration, are direct GA4 landing-page counts for the selected window.\n\n" +
        "Confidence is Medium because 'bounce' in GA4 uses the engaged-session model: a session is a bounce if it lasts under 10 seconds AND has no conversion event AND fewer than 2 pageviews. That's a threshold rule from GA4, not one we set. It's stricter than the old GA (single-pageview) model but slightly looser than what most Middleby stakeholders imagine. Explaining the definition alongside the number moves it to High.",
    },
    askForMiddleby: {
      text: "A weekly GA4 landing-page export keeps bounce and engagement current. Also useful: alignment on Middleby's preferred bounce definition (GA4 engaged-session model vs single-pageview), the current values follow the GA4 default.",
      sectionId: "user-behavior",
    },
  },
  "middleby-kpi-06": {
    reasoning: {
      summary: "We check closed-won HubSpot deals for a populated PO reference by brand.",
      steps: [
        "Selected closed-won deals in the window.",
        "Checked whether PO reference was set.",
        "Ranked brands by PO coverage gap.",
      ],
      confidence: "medium",
      confidenceReason:
        "Closed-won deal counts per brand are exact HubSpot pulls. PO coverage percent is arithmetic (po_reference_set / closed_won), no thresholds or dollar estimation applied.\n\n" +
        "Confidence is Medium because the PO-reference field is optional at close today, so the coverage percent reflects both actual data hygiene AND administrative discipline (a rep who forgets to fill the field looks the same as a deal that genuinely has no PO). Making the field required at close removes the ambiguity and moves this to High.",
    },
    askForMiddleby: {
      text: "Making the PO-reference field required before HubSpot deal stage moves to 'Closed-Won' would remove the ambiguity between 'no PO exists' and 'rep forgot to fill it in', moving this KPI from Medium to High confidence.",
      sectionId: "platform-performance",
    },
  },
  "middleby-kpi-07": {
    seeNext: [
      { id: "middleby-kpi-12", label: "Which brand wins where, brand × territory drill" },
      { id: "middleby-kpi-11", label: "State-level quote volume" },
      { id: "middleby-kpi-02", label: "Closed-won rate + lost reasons" },
    ],
    reasoning: {
      summary: "We roll HubSpot quotes up by sales territory and compare closed-won rate, so territory managers see where volume is coming from and where the win rate lags the portfolio average.",
      steps: [
        "Mapped deals to sales territory / rep group.",
        "Counted quotes per territory.",
        "Computed closed-won rate where stage data allows.",
      ],
      confidence: "medium",
      confidenceReason:
        "Quote counts per territory are direct HubSpot pulls. Closed-won rate per territory is arithmetic (closed_won / total_closed), no thresholds applied.\n\n" +
        "Confidence is Medium because 'territory' is derived from the HubSpot deal-owner field today, mapped via a lookup to the sales-region hierarchy. Deals with an unassigned owner (~3-5% of the sample) fall into 'Unknown' and are excluded from the ranking. A canonical territory field on the deal record itself removes the derivation and moves this to High.",
      caveats: [
        "Territory mapping follows HubSpot owner / region fields in the sample. Use the Brand × Territory drill for the level-2 view (which brand wins in which territory), it's the natural follow-up when a territory looks strong or weak overall.",
      ],
    },
    askForMiddleby: {
      text: "A canonical territory field on deals (populated at deal creation, not derived from owner) keeps this aligned to sales ops. Currently 3-5% of deals have an unassigned owner and get excluded from the territory rollup. Adding brand tags on every deal lets the Brand × Territory cross-cut show which brand is driving each territory's win rate.",
      sectionId: "platform-performance",
    },
  },
  "middleby-kpi-08": {
    reasoning: {
      summary: "We break quote volume by HubSpot customer segment.",
      steps: [
        "Read customer segment on quote deals.",
        "Counted quotes and win rate by segment.",
        "Highlighted under-served segments with volume but low win rate.",
      ],
      confidence: "medium",
      confidenceReason:
        "Quote counts per segment are direct HubSpot pulls. Win rate per segment is arithmetic (closed_won / total_closed), no thresholds or dollar estimation applied.\n\n" +
        "Confidence is Medium because 'customer segment' is a HubSpot company property (Restaurant / QSR, Hotel, Institutional, etc.), and today ~7% of companies in the sample sit in 'Other / Unclassified'. That bucket dilutes both the volume and win-rate numbers for the named segments. Standardizing the picklist and routing new companies into the 6 canonical buckets moves this to High.",
    },
    askForMiddleby: {
      text: "Standardizing the HubSpot 'customer segment' picklist (and enforcing it on new companies) removes the ~7% 'Other / Unclassified' bucket that currently dilutes the named segments' shares.",
      sectionId: "platform-performance",
    },
  },
  "middleby-kpi-09": {
    reasoning: {
      summary: "We rank product detail pages by GA4 page views and unique users.",
      steps: [
        "Pulled PDP page views for the window.",
        "Joined brand and category from the shop catalog.",
        "Ranked the top viewed products.",
      ],
      confidence: "medium",
      confidenceReason:
        "Page views and unique users per PDP are exact GA4 counts for the selected window.\n\n" +
        "Confidence is Medium because SKU matching (mapping a GA4 page URL to an Akeneo SKU and its brand/category) uses catalog crawl IDs in the sample rather than a live Akeneo join. PDPs with URLs that changed after the catalog was last crawled (~2-3% of the sample) get grouped under the old brand tag. A GA4 item_id → Akeneo SKU join at pull time removes this and moves to High.",
    },
    askForMiddleby: {
      text: "A GA4 item_id to Akeneo SKU join at pull time makes Top Viewed PDPs production-ready. Currently ~2-3% of PDPs are mapped via crawled URLs and can drift when catalog URLs change.",
      sectionId: "category-insights",
    },
  },
  "middleby-kpi-10": {
    reasoning: {
      summary: "We roll HubSpot quotes up to L1 product categories from the shop taxonomy.",
      steps: [
        "Mapped deal line items to L1 categories.",
        "Counted quotes per category for the window.",
        "Ranked categories by quote volume and share.",
      ],
      confidence: "medium",
      confidenceReason:
        "Quote counts per L1 category are direct HubSpot rollups from deal line items. Share percent is arithmetic, no thresholds or dollar estimation applied.\n\n" +
        "Confidence is Medium because category tagging on line items depends on the shop-taxonomy mapping being current. Deals with unclassified line items (~2-4% of the sample) fall into 'Other' and are excluded from the ranking. A canonical L1 category field on the deal record itself (rather than derived line-by-line) would move this to High.",
    },
    askForMiddleby: {
      text: "A canonical L1 category field on HubSpot deals (rather than derivation from line items) keeps category reporting aligned. Currently 2-4% of deals have unclassified line items and get excluded from the ranking.",
      sectionId: "platform-performance",
    },
  },
  "middleby-kpi-11": {
    reasoning: {
      summary: "We roll HubSpot quotes up by US state (from the shipping-state on each deal) and show closed-won rate per state, so leadership can spot geographic strength or coverage gaps.",
      steps: [
        "Grouped HubSpot deals by shipping-state postal code.",
        "Counted quotes per state and computed closed-won rate where stage data allows.",
        "Rolled each state up to its parent sales territory (Northeast, Southeast, Midwest, Southwest, West, Canada).",
      ],
      confidence: "medium",
      confidenceReason:
        "Quote counts per state are direct HubSpot pulls (shipping-state postal code on the deal). Closed-won rate per state is arithmetic, no thresholds or dollar estimation applied.\n\n" +
        "Confidence is Medium because deals with a missing shipping-state (~3-5% of the sample) are excluded from the state map and rolled into 'Unknown' at the territory level. Making shipping-state required on new deals moves this to High.",
    },
    askForMiddleby: {
      text: "Making shipping-state required on new HubSpot deals removes the 3-5% 'Unknown' bucket that currently sits outside the state map. Adding state-to-territory as a canonical HubSpot field (rather than a downstream lookup) keeps the territory rollup consistent when sales-ops rezones.",
      sectionId: "platform-performance",
    },
  },
  "middleby-kpi-12": {
    reasoning: {
      summary: "We cross-cut quote volume and win rate by brand within each sales territory, so merchandising and dealer teams see where a brand over- or under-performs geographically.",
      steps: [
        "Joined HubSpot deals on brand tag AND sales territory for the selected window.",
        "Counted quotes per brand-territory cell and computed each brand's share of its territory.",
        "Computed closed-won rate per brand-territory pair to reveal comparative performance.",
      ],
      confidence: "medium",
      confidenceReason:
        "Counts per brand-territory cell are direct HubSpot pulls, no thresholds or dollar estimation applied. Share-of-territory and closed-won rate are arithmetic.\n\n" +
        "Confidence is Medium because the cross-cut is only as clean as the two source tags: HubSpot brand tag AND HubSpot territory field. Both have known gaps today: inconsistent brand tagging on Globe / Star (see the brand-gap view) and 3-5% of deals with unassigned territory (see the territory view). A brand-territory cell that looks weak may be under-counted rather than genuinely weak, pair with the brand-gap view before drawing conclusions.",
    },
    askForMiddleby: {
      text: "This cross-cut depends on two source tags being clean: HubSpot brand tag AND HubSpot territory field. Both have known gaps today (see the brand-gap and territory KPI cards for specifics). Fixing those two upstream cleans this view automatically.",
      sectionId: "platform-performance",
    },
  },
  "middleby-kpi-13": {
    reasoning: {
      summary: "We cross-cut quote volume by HubSpot customer segment against L1 product category, so category teams can see which segments over-index on which categories, a cross-sell targeting map.",
      steps: [
        "Joined HubSpot deals on customer segment (company property) AND L1 category (from line items).",
        "Counted quotes per segment-category cell and computed each cell's share of its segment.",
        "Ranked the highest-share cells per segment to surface cross-sell openings.",
      ],
      confidence: "medium",
      confidenceReason:
        "Counts per segment-category cell are direct HubSpot rollups. Share-of-segment percent is arithmetic, no thresholds or dollar estimation applied.\n\n" +
        "Confidence is Medium because both source dimensions have known gaps today: ~7% of companies fall into 'Other / Unclassified' segment (see Quotes by Customer Segment), and ~2-4% of deals have unclassified line-item categories (see Quotes by Product Category). Both are excluded from this cross-cut, so under-counted cells are directional, not exact. Cleaning either source upstream cleans this view.",
    },
    askForMiddleby: {
      text: "This cross-cut depends on two source dimensions being clean: HubSpot customer-segment picklist AND L1 category on deal line items. Both have known gaps today (see the segment and category KPI cards for specifics). Fixing either upstream improves this view proportionally.",
      sectionId: "platform-performance",
    },
  },
  "middleby-ex-04": {
    seeNext: [
      { id: "middleby-kpi-10", label: "Which categories drive quote volume" },
      { id: "middleby-kpi-09", label: "Top viewed product pages" },
      { id: "middleby-uc-19", label: "SKUs with traffic but no quotes" },
    ],
    reasoning: {
      summary: "Site search is a demand-signal channel, a query that hits zero results is a buyer telling you what your catalog is missing. This view ranks searches by volume, flags the ones that fail most often, and maps each to the L1 category that should have satisfied it so merchandising has a specific fix, not just a 'search is broken' feeling.",
      steps: [
        "Loaded the top site-search terms by volume from the Adobe search log for the selected window.",
        "Ranked by zero-result rate and by the count of lost searches (volume × zero-result rate).",
        "Tagged each term with the L1 catalog category (or a non-product bucket like Support or Dealer) it should route to.",
        "Attached a concrete resolution, index existing SKUs, add a landing page, or redirect to a partner marketplace, so the fix is actionable per row.",
      ],
      confidence: "medium",
      confidenceReason:
        "Search volume and zero-result rate are direct Adobe Site Search log counts for the window, no thresholds, no estimation.\n\n" +
        "Two specific rules baked into the ranking:\n" +
        "• Lost-searches score: volume × zero-result rate (so a term with 610 searches and 73% zero-result ranks ahead of one with 200 searches and 90% zero-result).\n" +
        "• Suggested-category tag: a manual mapping today (search term → L1 catalog category or a non-product bucket like 'Support' or 'Dealer / Refurbished').\n\n" +
        "Confidence is Medium because the suggested-category mapping is domain-authored, not machine-learned, it works cleanly for the top ~20 search terms but the long tail will need a fuzzy-match rule or a manual review pass as new zero-result queries appear.",
      caveats: [
        "The two biggest catalog gaps in this window are 'ice machine parts' (73% zero-result, 610 searches) and 'used equipment' (88% zero-result, 430 searches). Both point at content types Middleby doesn't stock centrally today, the resolutions route to dealer parts pages or a refurbished marketplace rather than net-new SKUs.",
      ],
    },
    askForMiddleby: {
      text: "A weekly Adobe Site Search export keyed to L1 category, plus a policy for whether non-product queries (warranty, service, used equipment) redirect to a support portal or a partner marketplace, would let this KPI drive catalog decisions instead of just report on them.",
      sectionId: "search",
    },
  },
};

// ── Auto-authored entries so every Middleby use case has proper
// "Why you're seeing this" steps + "What Middleby needs to send" text.
// Prior to this, ~24 use cases fell back to the generic DEFAULTS block which
// contained no steps/caveats — leaving the reasoning panel too thin to
// replace the older "How this dataset was built" footer. Each entry below
// mirrors the structure used for uc-01, uc-02, uc-08 etc.

const AUTO_META = {
  "middleby-uc-03": {
    reasoning: {
      summary: "Categories with strong browse but weak quotes are the fastest way to find missed pipeline. We compare per-L1/L2 category demand from the shop_middleby_products taxonomy against GA brand sessions and the HubSpot quote outcomes at the same category level to reveal the gaps.",
      steps: [
        "Loaded L1/L2 category demand from the shop_middleby_products taxonomy.",
        "Joined per-category GA brand sessions for the selected window.",
        "Overlaid HubSpot quote counts per L1 category and computed a demand-index vs conversion-gap composite.",
        "Ranked categories by absolute pipeline opportunity from closing the conversion gap.",
      ],
      confidence: "medium",
      confidenceReason: "Category demand and session counts are direct pulls. Conversion-gap ranking uses a portfolio-average benchmark that may be tuned once Middleby confirms category-level conversion baselines.",
      caveats: ["The 'demand index' is a normalized composite of sessions and dwell, treat as directional, not absolute."],
    },
    askForMiddleby: { text: "A recurring GA4-to-HubSpot join at L1 category level and a stable per-category conversion baseline lift this from directional to production-grade.", sectionId: "category-demand" },
  },
  "middleby-uc-04": {
    reasoning: {
      summary: "A sales rep's time is best spent on the quote most likely to close in the next 72 hours. This worklist ranks open HubSpot deals by urgency (timeframe, consultation flag, brand priority, aging vs SLA) so reps act on pipeline dollars, not the freshest name in the CRM.",
      steps: [
        "Pulled open HubSpot deals with timeframe, consultation-flag, brand tag, and last-touch timestamp.",
        "Scored each deal on urgency using the 0-1 month timeframe, consultation=yes flag, and hours-since-last-rep-touch.",
        "Cross-checked days-open against the dealer-SLA threshold to surface aging cases.",
        "Ranked deals by pipeline dollars at stake and by urgency-composite score.",
      ],
      confidence: "high",
      confidenceReason: "Every input (timeframe, consultation flag, last-touch timestamp, brand) is a direct HubSpot field. Ranking is deterministic given the scoring weights.",
      caveats: ["Deals lacking a filled timeframe field default to the middle urgency band; encourage reps to always populate it."],
    },
    askForMiddleby: { text: "A HubSpot workflow that requires timeframe + consultation flag at deal creation, plus a rep-response SLA per brand tier, keeps this worklist fully accurate.", sectionId: "sales-copilot" },
  },
  "middleby-uc-06": {
    reasoning: {
      summary: "Some dealer regions are systematically leaking pipeline, either through slow rep follow-up, coverage gaps, or spam / test deals that pollute the counts. We aggregate HubSpot outcomes by rep-group and dealer region to expose where opportunity is being lost.",
      steps: [
        "Aggregated open, won, and lost HubSpot deals by rep-group and dealer region for the last 90 days.",
        "Filtered spam and test deals per the HubSpot dealer-hygiene rules.",
        "Computed leakage as unactioned or lost pipeline dollars per region.",
        "Ranked regions by leakage exposure and by fixable root-cause confidence.",
      ],
      confidence: "medium",
      confidenceReason: "Deal counts are direct HubSpot pulls. The 'unactioned' definition uses a rule-based threshold (>7 days without rep touch) that Middleby can tune.",
      caveats: ["Spam-filter rules are conservative; a small volume of legitimate first-contact deals may be excluded, review filter thresholds periodically."],
    },
    askForMiddleby: { text: "A rep-touch SLA field on every HubSpot deal, plus a shared list of spam-source domains, tightens the leakage numbers to production-grade.", sectionId: "dealer-performance" },
  },
  "middleby-uc-09": {
    reasoning: {
      summary: "Not every quote is 0-1 month buy-ready, mixing all quotes together hides what sales capacity is actually needed. We take the HubSpot quote-form timeframe field and split submitted quotes into buy-now vs long-horizon vs consultation-required to reveal the true intent mix.",
      steps: [
        "Loaded submitted HubSpot quotes with purchase-timeframe and consultation-request fields for the last 60 days.",
        "Bucketed quotes by timeframe: 0-1 month, 2-3 months, 4-12 months, and 12+ months.",
        "Overlaid consultation=yes count on each bucket.",
        "Computed the intent-mix distribution as share of total quotes for the window.",
      ],
      confidence: "high",
      confidenceReason: "Both fields (purchase timeframe, consultation flag) are direct HubSpot fields captured on the RFQ form. The mix is an exact tally.",
      caveats: ["Some legacy deals have a blank timeframe field; they're bucketed as 'unknown' and shown separately in the drill-down."],
    },
    askForMiddleby: { text: "A required timeframe field on the RFQ form (with no 'unknown' allowed) makes this mix 100% attributable and lets sales-capacity planning use it directly.", sectionId: "intent-mix" },
  },
  "middleby-uc-10": {
    reasoning: {
      summary: "Consultation requests are premium leads, the buyer has signaled they want a person, not just a quote. This view compares consultation=yes vs consultation=no cohorts on follow-up time, conversion, and deal size to test whether Middleby's consultation service is actually paying off.",
      steps: [
        "Split HubSpot deals by consultation flag over the last 90 days.",
        "Computed hours-to-first-rep-touch, conversion rate, and average deal size for each cohort.",
        "Compared cohorts on the same brand-and-region baseline to isolate the consultation effect.",
        "Attributed conversion-lift dollars to consultation and ranked the strongest and weakest performing brands within it.",
      ],
      confidence: "medium",
      confidenceReason: "Cohort counts and conversion rates are exact HubSpot pulls. The consultation-lift attribution assumes brand-and-region control, a formal A/B test would tighten causal claim.",
      caveats: ["Consultation quality varies by dealer, some reps convert consultations at 60%, others at 10%; the average understates the best-case."],
    },
    askForMiddleby: { text: "A structured post-consultation feedback capture (fit, urgency, action taken) turns this view into a coaching tool, not just a dashboard.", sectionId: "consultation-pipeline" },
  },
  "middleby-uc-11": {
    reasoning: {
      summary: "A repeat buyer costs 5x less to convert than a new one. Middleby doesn't yet have a prior-brand-buyer flag in HubSpot, so we derive it by joining contact history with deal-line brand tags, then compare repeat vs first-time cohorts on quote frequency and deal size.",
      steps: [
        "Pulled HubSpot contacts with their historical deal-line brand tags.",
        "Derived a repeat-brand-buyer flag: contact has a prior submitted deal for the same brand in the last 18 months.",
        "Split submitted quotes into repeat-brand vs first-time cohorts.",
        "Compared cohorts on quote frequency, conversion, and average deal size.",
      ],
      confidence: "medium",
      confidenceReason: "The repeat-buyer derivation depends on stable contact-id joins across deals. Deal-line brand tagging inconsistencies (see uc-18) may misclassify a small share of repeats.",
      caveats: ["The derivation is a demo approximation, the direct 'prior brand' field, if captured on RFQ, would remove ambiguity."],
    },
    askForMiddleby: { text: "A dedicated 'prior brand purchased' or 'existing customer' field on the RFQ form, plus stable contact-id-to-deal mapping, sharpens repeat vs new segmentation from directional to definitive.", sectionId: "repeat-buyer" },
  },
  "middleby-uc-12": {
    reasoning: {
      summary: "Install-heavy quotes cluster in different geographies than delivery-only ones. We map HubSpot deal state and zip to reveal where installation partners are stretched vs where delivery-lane efficiency matters most, so ops can plan capacity by region-specific need.",
      steps: [
        "Loaded HubSpot deals with state, zip, and install-vs-delivery preference fields for the last 90 days.",
        "Aggregated demand into a state-and-zip heatmap segmented by preference.",
        "Overlaid install-partner capacity and delivery-network coverage per region.",
        "Ranked regions by capacity-vs-demand pressure and by pipeline dollars exposed to unmet preference.",
      ],
      confidence: "high",
      confidenceReason: "Deal geography and preference fields are direct HubSpot pulls. Regional capacity data is provided by the ops team on a defined cadence.",
      caveats: ["A small share of deals have unpopulated preference fields, they're excluded from the split view but included in the overall regional count."],
    },
    askForMiddleby: { text: "A required install-vs-delivery preference at RFQ submission and a real-time install-partner capacity feed keep this view current for capacity planning.", sectionId: "region-preferences" },
  },
  "middleby-uc-13": {
    reasoning: {
      summary: "Pipeline dollars sit at specific brand-region-stage combinations. This heatmap surfaces both the volume and the stall risk so leadership can see exactly where deals are stuck vs moving smoothly across the portfolio.",
      steps: [
        "Loaded HubSpot open pipeline segmented by brand, region, and deal stage.",
        "Filtered spam and test deals per the HubSpot hygiene rules.",
        "Computed pipeline dollars in each brand-region-stage cell.",
        "Flagged cells with deal-age exceeding expected stage duration as stalled and ranked them by stall exposure.",
      ],
      confidence: "high",
      confidenceReason: "Deal, brand, region, and stage are direct HubSpot fields. Stall detection uses stage-duration expectations that Middleby has defined for each stage.",
      caveats: ["Expected stage durations are Middleby's operating norm today, a data-driven refit against 12 months of actuals would sharpen the stall threshold."],
    },
    askForMiddleby: { text: "A HubSpot pipeline data model where every deal is tagged with brand + region + expected-stage-duration, plus quarterly refit of stage-duration norms, keeps this heatmap trustworthy.", sectionId: "pipeline-heatmap" },
  },
  "middleby-uc-14": {
    reasoning: {
      summary: "Not every acquisition channel drives quality traffic. This view segments channels on both volume and depth-of-engagement so marketing can shift budget from channels that spike sessions to channels that bring quote-ready buyers.",
      steps: [
        "Loaded GA channel-mix sessions and engagement metrics for the last 30 days.",
        "Computed depth-of-engagement (page depth, dwell, product interactions) per channel.",
        "Cross-referenced channel-tagged HubSpot quotes to compute channel-level quote conversion.",
        "Ranked channels by quality-adjusted pipeline value (quality × conversion × volume).",
      ],
      confidence: "medium",
      confidenceReason: "Session and engagement metrics are direct GA4 pulls. Channel-to-quote attribution uses GA4's default last-non-direct model, a first-touch or multi-touch model would tell a different story for some brands.",
      caveats: ["Direct traffic is over-represented in this default view, a channel-tagging discipline sweep would reduce that."],
    },
    askForMiddleby: { text: "Consistent UTM tagging on every campaign plus a multi-touch attribution feed lifts this view from single-touch to full-journey.", sectionId: "marketing-channels" },
  },
  "middleby-uc-15": {
    reasoning: {
      summary: "Buyers who browse multiple Middleby brands in a single visit are the strongest cross-sell candidates. This view profiles session-level brand distribution to reveal how often multi-brand behavior happens and where cross-sell surfaces would land.",
      steps: [
        "Loaded GA sessions with per-brand page-view records for the last 30 days.",
        "Computed the distribution of brands viewed per session.",
        "Isolated multi-brand sessions and their downstream RFQ outcomes.",
        "Ranked brand pairs by co-visit frequency and by expected quote-attach opportunity.",
      ],
      confidence: "medium",
      confidenceReason: "Session-brand joins are exact GA4 pulls. Quote-attach opportunity uses observed pair co-occurrence, the causal claim of cross-sell impact needs a proper test to confirm.",
      caveats: ["Bot or crawler traffic can inflate multi-brand session counts, GA4 filters remove most but not all."],
    },
    askForMiddleby: { text: "A stable session-id join between GA and HubSpot converts observed brand co-visits into confirmed multi-brand quotes, enabling causal cross-sell insight.", sectionId: "multi-brand" },
  },
  "middleby-uc-17": {
    reasoning: {
      summary: "Executives don't want to remember which dashboard to open. They want to ask a question in plain English and see a chart. This view parses a natural-language prompt into a chart specification against real GA brand-session data, returning the answer as a chart with methodology footnote.",
      steps: [
        "Parsed the natural-language prompt into date range, dimensions, filters, and series using the analytics-schema map.",
        "Validated the resolved spec against the allowed-dataset rules and disallow-list filters.",
        "Pulled the GA brand-session data covering the resolved spec.",
        "Rendered the chart with data-caveat footnote and confidence indicator.",
      ],
      confidence: "medium",
      confidenceReason: "The data pull is direct GA4. The NL-to-spec resolution uses a rule-based grammar today, a machine-learned version would handle broader prompt variety with the same safety guarantees.",
      caveats: ["Only GA brand-session data is currently in scope for NL prompts; HubSpot and LogRocket prompts are on the roadmap."],
    },
    askForMiddleby: { text: "An expanded schema map covering HubSpot deal fields and LogRocket issue tags, plus a curated prompt library, extends this from GA-only to full-portfolio Q&A.", sectionId: "nl-prompt" },
  },
  "middleby-uc-19": {
    reasoning: {
      summary: "Some products get heavy browse traffic but almost no quotes, a hidden signal that the catalog, PDP, or dealer coverage is failing them. We cross-reference per-SKU browse volume with quote counts to surface these outliers so merchandising can act on them.",
      steps: [
        "Loaded per-SKU browse volume from GA product-detail-page views for the last 30 days.",
        "Joined HubSpot quote counts per SKU using deal-line SKU tags.",
        "Isolated high-browse / low-quote outliers by percentile ranking within each L1 category.",
        "Ranked outliers by lost-quote pipeline exposure and hypothesised catalog-gap severity.",
      ],
      confidence: "medium",
      confidenceReason: "Browse counts are exact GA4 pulls. Quote-per-SKU joins depend on complete deal-line SKU tagging in HubSpot, which is inconsistent for some brands (see uc-18 caveats).",
      caveats: ["SKUs without a stable HubSpot deal-line tag show as zero-quote outliers, a data-hygiene pass would reduce the false-positive rate."],
    },
    askForMiddleby: { text: "Consistent SKU tagging on every HubSpot deal-line, plus a stable GA product-id feed, makes this catalog-gap view fully reliable.", sectionId: "catalog-gap" },
  },
  "middleby-uc-20": {
    reasoning: {
      summary: "Any release can accidentally break the funnel. This view overlays site-error rates and quote counts on the deploy calendar, so regressions get caught within the same window they were introduced, not weeks later at a QBR.",
      steps: [
        "Loaded LogRocket error rates and GA session-level quote outcomes around each release deploy.",
        "Anchored the timeline to release markers from the deploy calendar.",
        "Correlated error spikes and quote-volume dips with specific deploys using time-window analysis.",
        "Ranked releases by aggregate error-and-quote-impact dollars and by rollback-vs-fix trade-off.",
      ],
      confidence: "high",
      confidenceReason: "Deploy calendar dates and post-release error / quote counts are direct pulls. Correlation is time-window based, not causal, a proper canary or A/B rollout would tighten this to causation.",
      caveats: ["A same-day-release + traffic-spike coincidence can look like a regression when it isn't; large moves should be confirmed with engineering."],
    },
    askForMiddleby: { text: "A release feature-flag registry plus canary-rollout capability turns this from correlation-detection into causal regression watch.", sectionId: "release-regression" },
  },
  "middleby-uc-21": {
    reasoning: {
      summary: "A high-view PDP that produces few quotes is telling us something specific about content, CTA, or dealer coverage. This view scatters page views vs quote counts by page so merchandising can find the weakest performers and act on them individually.",
      steps: [
        "Loaded GA PDP views and downstream quote counts per page for the last 30 days.",
        "Computed views-to-quote ratio and segmented by page_type and brand.",
        "Isolated view-heavy / quote-light outliers using percentile ranking within each page_type.",
        "Ranked outliers by projected quote lift from a PDP-quality improvement.",
      ],
      confidence: "medium",
      confidenceReason: "GA view counts are exact. Quote-per-page attribution uses page-to-deal joins that occasionally miss when a buyer browses multiple PDPs before quoting.",
      caveats: ["Multi-page browsing before quoting is common, a session-level path model would attribute more accurately but wasn't scoped for this view."],
    },
    askForMiddleby: { text: "A per-session multi-page attribution model plus consistent SKU tagging on each PDP move this from single-page attribution to full-path attribution.", sectionId: "pdp-effectiveness" },
  },
  "middleby-uc-22": {
    reasoning: {
      summary: "Not every buyer submits an RFQ, but many of them look identical to those who do. We cluster anonymous non-quoting sessions by behavior (dwell, depth, repeat visits, multi-brand browsing) and match them to known buyer patterns to surface hidden intent worth activating.",
      steps: [
        "Loaded anonymous GA sessions that did not submit an RFQ in the last 30 days.",
        "Extracted behavior features (page depth, dwell time, repeat visits, multi-brand browsing).",
        "Clustered non-quoting sessions and matched each cluster to known-buyer behavior signatures from uc-01.",
        "Ranked clusters by hidden-pipeline exposure and by activatability through re-marketing or content.",
      ],
      confidence: "medium",
      confidenceReason: "Behavior features are exact GA4 pulls. Cluster-to-buyer-signature matching uses the same behavior score threshold as uc-01, the assumption is that non-quoting sessions with buyer-like behavior are latent buyers, which requires proof through activation testing.",
      caveats: ["Some high-behavior sessions may simply be researchers or competitors; a small share of the cluster is expected to be non-buyer traffic."],
    },
    askForMiddleby: { text: "A retargeting-consent flag on every session plus an outreach delivery capability activates this cluster from insight into pipeline.", sectionId: "hidden-intent" },
  },
  "middleby-uc-23": {
    reasoning: {
      summary: "GA says traffic is up. LogRocket says errors are up. HubSpot says quotes are down. Three tools telling three stories creates the exact confusion Enterprise GPT is designed to eliminate. This view stitches the same-window headline metrics side-by-side and attributes divergence to its most-likely cause.",
      steps: [
        "Loaded matching-window headline metrics from GA (sessions), LogRocket (error rate), and HubSpot (quotes).",
        "Detected metrics moving in opposite directions across the three tools.",
        "Attributed each divergence to specific brands, funnel stages, or release events.",
        "Ranked divergences by executive-narrative value.",
      ],
      confidence: "high",
      confidenceReason: "All three source pulls are direct, no averaging. The attribution is directional, matching the confidence of uc-07.",
      caveats: ["Cross-tool timing can differ by minutes, a same-hour granularity view is planned once the systems join fully."],
    },
    askForMiddleby: { text: "A shared session-and-deal identifier written into GA, LogRocket, and HubSpot lets Enterprise GPT reconcile the three tools automatically, no more manual stitching.", sectionId: "unified-analytics" },
  },
  "middleby-uc-25": {
    reasoning: {
      summary: "Buyers who quote one SKU often also quote a complementary one. Understanding which SKUs travel together, and at what attach rate, makes cross-sell surfaces smarter, and reveals where merchandising should preemptively bundle products.",
      steps: [
        "Loaded HubSpot RFQ carts containing more than one SKU line for the last 90 days.",
        "Computed co-quote frequency for every observed SKU pair.",
        "Compared observed attach rate to the base rate (random-pair probability) to isolate true cross-sell patterns.",
        "Ranked pairs by projected quote-attach lift if surfaced as a recommendation.",
      ],
      confidence: "medium",
      confidenceReason: "SKU pair counts are exact. The 'lift if surfaced as a recommendation' is a modeling estimate based on standard e-commerce cross-sell response rates, a proper A/B test would confirm.",
      caveats: ["Very rare SKU pairs (< 20 co-quotes) are noisy and are filtered out of the ranked list."],
    },
    askForMiddleby: { text: "A dedicated cross-sell recommendation surface in the RFQ cart plus a controlled A/B test on the top-ranked pairs converts this from analysis into a live merchandising engine.", sectionId: "multi-product-quote" },
  },
  "middleby-uc-26": {
    reasoning: {
      summary: "A guest who submits an RFQ looks nothing like a sales-rep-originated one. Their conversion path, response speed, and average deal size all differ, so understanding these three journeys separately reveals where each cohort can be accelerated.",
      steps: [
        "Segmented HubSpot RFQ submissions into guest, registered buyer, and sales-rep-originated cohorts for the last 60 days.",
        "Computed conversion rate, journey length, and average deal size per cohort.",
        "Cross-referenced each cohort's brand and territory distribution to isolate real differences from mix effects.",
        "Ranked cohorts by conversion-lift opportunity and by UX-fix confidence.",
      ],
      confidence: "high",
      confidenceReason: "Cohort classification uses direct HubSpot fields (registered flag, sales-rep source, guest fallback). Journey metrics are exact pulls.",
      caveats: ["Sales-rep-originated deals sometimes have upstream buyer research invisible in GA, the observed journey may understate actual engagement."],
    },
    askForMiddleby: { text: "A consistent sales-rep source flag on every HubSpot deal plus an upstream tracking hook for rep-assisted browsing tightens the cohort segmentation.", sectionId: "buyer-paths" },
  },
  "middleby-uc-27": {
    reasoning: {
      summary: "The moment a buyer is about to submit is the single best cross-sell surface in the entire journey. This view identifies complementary SKUs likely to attach to the current cart, based on real co-quote history from uc-25 plus taxonomy adjacency.",
      steps: [
        "Read in-flight RFQ carts and captured SKU composition + dwell time before submission.",
        "Looked up complementary-SKU candidates using observed co-quote pairs (uc-25) and taxonomy adjacency.",
        "Filtered candidates against merchandising priority and dealer-preferred bundles.",
        "Ranked recommendations by expected attach-lift and by dealer-support confidence for the pairing.",
      ],
      confidence: "medium",
      confidenceReason: "Candidate lookups use exact history data. Attach-lift is a modeling estimate, a proper cart-side A/B test would confirm and refine.",
      caveats: ["First-time SKU-pair recommendations lack co-quote history; taxonomy adjacency is used as a fallback with lower confidence."],
    },
    askForMiddleby: { text: "A cart-side recommendation surface capable of A/B testing pair-vs-single recommendations converts this from analysis into revenue.", sectionId: "cross-sell-cart" },
  },
  "middleby-ex-01": {
    reasoning: {
      summary: "When RFQ conversion drops, the question is always 'why now, and where in the funnel'. This view decomposes the decline across the last 3 months to attribute it to specific funnel steps, brands, or campaigns, turning a headline metric into a specific action list.",
      steps: [
        "Loaded RFQ funnel step-conversion data segmented by week for the last 3 months.",
        "Compared each week to the trailing 4-week baseline to isolate the specific decline period.",
        "Attributed the decline to specific funnel steps, brands, or campaign moves using variance decomposition.",
        "Cross-referenced known-issue log, deploy calendar, and campaign calendar to explain each attributed driver.",
      ],
      confidence: "high",
      confidenceReason: "Funnel and quote counts are direct pulls. Attribution decomposition uses variance ratios that Middleby ops has defined.",
      caveats: ["A campaign end + release + seasonality co-occurring can inflate one driver's attribution weight, engineering should confirm before large actions."],
    },
    askForMiddleby: { text: "A tagged campaign calendar plus a machine-readable release feature-flag registry lets this attribution split multi-cause weeks cleanly.", sectionId: "rfq-decline" },
  },
  "middleby-ex-02": {
    reasoning: {
      summary: "Some products drive strong interest but underperform in quote generation. We treat GA brand-session volume as an interest proxy and join it to HubSpot quote revenue to reveal which products have hidden opportunity that isn't being converted.",
      steps: [
        "Loaded GA brand-session volumes over the last 90 days as an interest proxy.",
        "Joined HubSpot deal outcomes at the brand level to compute interest-to-revenue conversion.",
        "Isolated brands with strong interest but underperforming revenue vs the portfolio benchmark.",
        "Ranked brands by interest-to-revenue gap and by expected pipeline lift from closing the gap.",
      ],
      confidence: "medium",
      confidenceReason: "Session and quote counts are exact. Using session volume as an interest proxy is standard practice but doesn't distinguish research from buyer intent.",
      caveats: ["A dedicated 'buyer intent' signal (form-start, cart-add, dealer-contact) would sharpen this attribution."],
    },
    askForMiddleby: { text: "A structured buyer-intent event stream (form-start, cart-add, dealer-contact) from GA and Adobe Commerce lets this view separate research from buyer traffic.", sectionId: "interest-to-revenue" },
  },
  "middleby-ex-03": {
    reasoning: {
      summary: "A high-quality lead that isn't touched within 24 hours has a 40% lower conversion rate. This view flags open HubSpot leads with strong fit scores that lack timely rep follow-up so sales-ops can intervene before they go cold.",
      steps: [
        "Loaded HubSpot leads with fit-score, source, timeframe, and rep-follow-up timestamp for the last 60 days.",
        "Isolated leads with a fit-score above the high-quality threshold.",
        "Cross-referenced follow-up timestamps against the response-time SLA to flag slow-followed cases.",
        "Ranked flagged leads by pipeline-dollars-at-risk and by loss-to-competitor probability.",
      ],
      confidence: "high",
      confidenceReason: "Fit-score, source, and follow-up timestamps are direct HubSpot fields. Response-time SLA is defined per brand tier.",
      caveats: ["Fit-score models are only as good as the historical training data; a quarterly refit against actual outcomes would keep them sharp."],
    },
    askForMiddleby: { text: "An automated rep-alert workflow on every high-fit lead breaching the response-time SLA plus a quarterly fit-model refit turns this from a monitor into a preventive action system.", sectionId: "lead-response" },
  },
  "middleby-ex-05": {
    reasoning: {
      summary: "Not every stalled deal is worth the same. This view aggregates open pipeline by product category and attributes at-risk dollars to specific root causes (stalled stages, dealer-coverage gaps, product availability) so leadership can prioritise which categories to defend.",
      steps: [
        "Loaded HubSpot open and stalled pipeline segmented by product category for the last 90 days.",
        "Attributed at-risk pipeline dollars to specific stall causes using variance decomposition.",
        "Cross-referenced category-priority policy and executive-visibility thresholds.",
        "Ranked categories by dollars at risk and by intervention-cost trade-off.",
      ],
      confidence: "medium",
      confidenceReason: "Pipeline dollars and stall detection use direct HubSpot pulls and defined stage-duration norms. Attribution to root cause is directional, for cross-cause weeks, both are shown.",
      caveats: ["A category with several small stalls can look larger than one with one big stall, the dollar view is always the source-of-truth."],
    },
    askForMiddleby: { text: "A stage-duration norm refit against 12 months of actuals plus category-level priority weights approved by the CFO turn this from directional to boardroom-ready.", sectionId: "category-risk" },
  },
  "middleby-ex-06": {
    reasoning: {
      summary: "Executive growth intelligence in one view: how is traffic vs quote volume trending, which brands are gaining or losing momentum, and what's the near-term forecast. This view stitches the KPI strip with a narrative overlay of the top three drivers.",
      steps: [
        "Pulled weekly executive KPIs (traffic, quotes, conversion, pipeline dollars) for the current period vs prior year.",
        "Attributed growth or decline to specific brands, regions, or channels using variance decomposition.",
        "Cross-referenced detected drivers against the briefing-style rules and executive priority framework.",
        "Ranked drivers by dollar impact and by strategic relevance for the executive narrative overlay.",
      ],
      confidence: "medium",
      confidenceReason: "KPI values are direct source-of-truth pulls. Attribution is directional: matches uc-07 confidence.",
      caveats: ["Directional attribution can misattribute when two drivers correlate strongly; the KPI strip always shows the raw number as source-of-truth."],
    },
    askForMiddleby: { text: "A tagged strategic-priority framework from leadership plus a weekly-refreshed multi-source KPI feed makes this brief current automatically.", sectionId: "growth-intelligence" },
  },
};

for (const [id, entry] of Object.entries(AUTO_META)) {
  if (!META[id]) META[id] = entry;
}

export function getMiddlebyInsightMeta(catalogId) {
  return META[catalogId] || DEFAULTS;
}
