/**
 * Middleby analytics use case catalog (Insight Sessions).
 * Generated from middbleby-entgpt screen_data_spec + manifest.
 */

export const MIDDLEBY_USE_CASE_CATALOG_SEED = [
  {
    "id": "middleby-uc-01",
    "title": "RFQ Intent Intelligence",
    "items": [
      "Anonymous sessions ranked by illustrative behavior-driven intent score (pages, dwell, repeat, multi-brand). Production GA4 add-to-quote events not live yet.",
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Anonymous sessions ranked by illustrative behavior-driven intent score (pages, dwell, repeat, multi-brand). Production GA4 add-to-quote events not live yet.",
    "actionType": "Analyze",
    "analyticsUcNumber": 1,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Which visitors are most likely to submit a quote in the next 7 days?",
    "galleryOrder": 1,
    "source": "seed"
  },
  {
    "id": "middleby-uc-02",
    "title": "RFQ Funnel Intelligence",
    "items": [
      "Label funnel as example until RFQ cart events ship."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "When RFQ submissions dip, walk the funnel step-by-step to see exactly where sessions leak, so digital and dev teams can fix the tallest drop-off first, not chase the whole thing at once.",
    "actionType": "Diagnose",
    "analyticsUcNumber": 2,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Why did RFQ conversion decrease this month?",
    "galleryOrder": 2,
    "source": "seed"
  },
  {
    "id": "middleby-uc-03",
    "title": "Product Demand & Opportunity Discovery",
    "items": [
      "Per L1/L2 demand from shop_middleby_products taxonomy + GA brand sessions."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Per L1/L2 demand from shop_middleby_products taxonomy + GA brand sessions.",
    "actionType": "Analyze",
    "analyticsUcNumber": 3,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Which product categories are showing rising demand but low conversion?",
    "galleryOrder": 3,
    "source": "seed"
  },
  {
    "id": "middleby-uc-04",
    "title": "Sales Rep / Dealer Copilot",
    "items": [
      "Sortable rep worklist; highlight urgent timeframe + consultation=yes."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Sortable rep worklist; highlight urgent timeframe + consultation=yes.",
    "actionType": "Analyze",
    "analyticsUcNumber": 4,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Ready",
    "sampleQuestion": "Which quotes require immediate follow-up today?",
    "galleryOrder": 4,
    "source": "seed"
  },
  {
    "id": "middleby-uc-05",
    "title": "Customer Journey Visibility",
    "items": [
      "Example path narrative until session stitch exists."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "A funnel counts moves; a journey shows the sequence. Follow one product family end-to-end so dealer and ops teams see the drop-off moments as a story, where a buyer stalled, and where they could be re-engaged.",
    "actionType": "Analyze",
    "analyticsUcNumber": 5,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Show me how customers move from browsing to submitting an RFQ.",
    "galleryOrder": 5,
    "source": "seed"
  },
  {
    "id": "middleby-uc-06",
    "title": "Dealer Performance & Revenue Leakage Analysis",
    "items": [
      "Horizontal bar by rep group; filter spam deals in copy."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Horizontal bar by rep group; filter spam deals in copy.",
    "actionType": "Analyze",
    "analyticsUcNumber": 6,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Ready",
    "sampleQuestion": "Which dealer regions are losing the most RFQ opportunities?",
    "galleryOrder": 6,
    "source": "seed"
  },
  {
    "id": "middleby-uc-07",
    "title": "Executive AI Business Analyst",
    "items": [
      "Headline metrics + narrative callout block."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Headline metrics + narrative callout block.",
    "actionType": "Analyze",
    "analyticsUcNumber": 7,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "What is driving quote volume, and where are deals stalling?",
    "galleryOrder": 7,
    "source": "seed",
    "crossSourceKind": "summary"
  },
  {
    "id": "middleby-uc-08",
    "title": "UX / Experience Insights",
    "items": [
      "Real error types from LogRocket screenshots."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Site errors don't just annoy, they cost quotes. Rank LogRocket issues by sessions affected, then convert affected high-intent traffic into pipeline at risk so digital and eng know which single fix moves the number most.",
    "actionType": "Analyze",
    "analyticsUcNumber": 8,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Which site errors affect the most users, and how might that hurt quotes?",
    "galleryOrder": 8,
    "source": "seed"
  },
  {
    "id": "middleby-uc-09",
    "title": "Quote Form & Buyer Intent Mix",
    "items": [
      "Real HubSpot purchase timeframe distribution."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Real HubSpot purchase timeframe distribution.",
    "actionType": "Analyze",
    "analyticsUcNumber": 9,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Ready",
    "sampleQuestion": "What share of quotes want delivery in 0-1 month vs requested a consultation?",
    "galleryOrder": 9,
    "source": "seed"
  },
  {
    "id": "middleby-uc-10",
    "title": "Consultation-Led vs Self-Serve Pipeline",
    "items": [
      "Yes vs No consultation split; add stage breakdown in drill-down later."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Yes vs No consultation split; add stage breakdown in drill-down later.",
    "actionType": "Analyze",
    "analyticsUcNumber": 10,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Ready",
    "sampleQuestion": "Do consultation requests get faster dealer follow-up and better outcomes than self-serve quotes?",
    "galleryOrder": 10,
    "source": "seed"
  },
  {
    "id": "middleby-uc-11",
    "title": "Repeat vs New Buyer (Prior Brand Field)",
    "items": [
      "Show notes: prior-brand field absent; segments derived from contact+brand history."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Show notes: prior-brand field absent; segments derived from contact+brand history.",
    "actionType": "Analyze",
    "analyticsUcNumber": 11,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Ready",
    "sampleQuestion": "Are repeat-brand buyers quoting more than first-time buyers?",
    "galleryOrder": 11,
    "source": "seed"
  },
  {
    "id": "middleby-uc-12",
    "title": "Install & Delivery Preferences by Region",
    "items": [
      "Map or bar by state/zip from HubSpot deals."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases",
      "Client KPI"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Map or bar by state/zip from HubSpot deals.",
    "actionType": "Analyze",
    "analyticsUcNumber": 12,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Ready",
    "sampleQuestion": "Which regions show the most install-heavy or delivery-sensitive quote demand?",
    "galleryOrder": 12,
    "source": "seed"
  },
  {
    "id": "middleby-uc-13",
    "title": "Quote Pipeline by Brand, Region & Stage",
    "items": [
      "Pipeline heatmap; filter spam/test deals in footnote."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Pipeline heatmap; filter spam/test deals in footnote.",
    "actionType": "Analyze",
    "analyticsUcNumber": 13,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Ready",
    "sampleQuestion": "What is driving quote volume, and where are deals stalling?",
    "galleryOrder": 13,
    "source": "seed"
  },
  {
    "id": "middleby-uc-14",
    "title": "Marketing Channel & Traffic Quality",
    "items": [
      "Example channel mix until GA channel export is wired."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Example channel mix until GA channel export is wired.",
    "actionType": "Analyze",
    "analyticsUcNumber": 14,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Which channels drive deep product engagement vs bounce?",
    "galleryOrder": 14,
    "source": "seed"
  },
  {
    "id": "middleby-uc-15",
    "title": "Multi-Brand Portfolio Interest (One Visit)",
    "items": [
      "Distribution of brands viewed per session."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Distribution of brands viewed per session.",
    "actionType": "Analyze",
    "analyticsUcNumber": 15,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "How often do visitors browse more than one Middleby brand before quoting?",
    "galleryOrder": 15,
    "source": "seed"
  },
  {
    "id": "middleby-uc-16",
    "title": "Executive Summary / Weekly Insights",
    "items": [
      "Weekly briefing cards with up/down/flat indicators."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "One page for leadership: what moved this week, why it moved, what to do about it. No dashboard-hopping: just the two or three changes worth attention, each with its driver line already attached.",
    "actionType": "Diagnose",
    "analyticsUcNumber": 16,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "What changed this week across traffic, errors, or quotes?",
    "galleryOrder": 16,
    "source": "seed",
    "crossSourceKind": "summary"
  },
  {
    "id": "middleby-uc-17",
    "title": "Executive NL Prompt → Chart",
    "items": [
      "NL prompt renders chart from real GA brand sessions - not site search."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "NL prompt renders chart from real GA brand sessions - not site search.",
    "actionType": "Analyze",
    "analyticsUcNumber": 17,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "How many users visited between [dates], and what pages did they visit?",
    "galleryOrder": 17,
    "source": "seed"
  },
  {
    "id": "middleby-uc-18",
    "title": "Product Interest vs RFQ Gap Analysis",
    "items": [
      "Real GA sessions vs real HubSpot quote counts by brand."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Some brands pull heavy traffic but almost no quotes, surface the biggest browse-to-quote gaps so merchandising and dealer teams can act on the right brand this week.",
    "actionType": "Analyze",
    "analyticsUcNumber": 18,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Which brands have the biggest gap between traffic and quotes?",
    "galleryOrder": 18,
    "source": "seed",
    "crossSourceKind": "join"
  },
  {
    "id": "middleby-uc-19",
    "title": "Catalog / SKU Gap - High Traffic, Low Quotes",
    "items": [
      "High browse / low quote outliers; browse is estimated."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "High browse / low quote outliers; browse is estimated.",
    "actionType": "Analyze",
    "analyticsUcNumber": 19,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Which high-interest products have zero or low quote requests?",
    "galleryOrder": 19,
    "source": "seed"
  },
  {
    "id": "middleby-uc-20",
    "title": "Post-Release Regression Watch",
    "items": [
      "Release markers on timeline; example deploy dates."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Release markers on timeline; example deploy dates.",
    "actionType": "Diagnose",
    "analyticsUcNumber": 20,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Did last week's site release cause more errors and fewer quotes?",
    "galleryOrder": 20,
    "source": "seed"
  },
  {
    "id": "middleby-uc-21",
    "title": "Product Page Content Effectiveness",
    "items": [
      "Views vs quotes; color by page_type or brand."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Views vs quotes; color by page_type or brand.",
    "actionType": "Analyze",
    "analyticsUcNumber": 21,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Which product pages get views but few quote requests?",
    "galleryOrder": 21,
    "source": "seed"
  },
  {
    "id": "middleby-uc-22",
    "title": "Anonymous High-Intent Session Segments",
    "items": [
      "Segment list with behavior summary column."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Segment list with behavior summary column.",
    "actionType": "Analyze",
    "analyticsUcNumber": 22,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Which session patterns look like buyers but did not submit a quote?",
    "galleryOrder": 22,
    "source": "seed"
  },
  {
    "id": "middleby-uc-23",
    "title": "Unified Analytics vs Siloed Tools",
    "items": [
      "Side-by-side GA / LogRocket / HubSpot headline metrics."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Side-by-side GA / LogRocket / HubSpot headline metrics.",
    "actionType": "Analyze",
    "analyticsUcNumber": 23,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Why did quotes drop this month when traffic was up?",
    "galleryOrder": 23,
    "source": "seed",
    "crossSourceKind": "summary"
  },
  {
    "id": "middleby-uc-24",
    "title": "Session Replay Linked to HubSpot Quote",
    "items": [
      "Real deal IDs + example session IDs until stitch ships."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Real deal IDs + example session IDs until stitch ships.",
    "actionType": "Analyze",
    "analyticsUcNumber": 24,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Show me what this prospect did on site before they submitted quote #X.",
    "galleryOrder": 24,
    "source": "seed"
  },
  {
    "id": "middleby-uc-25",
    "title": "Multi-Product Quote Basket & Attach Rates",
    "items": [
      "Top SKU pairs by co-quote rate in example carts."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Top SKU pairs by co-quote rate in example carts.",
    "actionType": "Analyze",
    "analyticsUcNumber": 25,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Which products are quoted together in the same RFQ?",
    "galleryOrder": 25,
    "source": "seed"
  },
  {
    "id": "middleby-uc-26",
    "title": "Guest vs Registered vs Sales-Rep Quote Paths",
    "items": [
      "Summary donut - weekly_trend_file column points to stacked area chart data."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Summary donut - weekly_trend_file column points to stacked area chart data.",
    "actionType": "Analyze",
    "analyticsUcNumber": 26,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "How do quote journeys differ for guests, registered buyers, and sales reps?",
    "galleryOrder": 26,
    "source": "seed"
  },
  {
    "id": "middleby-uc-27",
    "title": "Cross-Sell Within Quote Cart",
    "items": [
      "Cart-side recommendation cards before submit."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Use cases"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Cart-side recommendation cards before submit.",
    "actionType": "Analyze",
    "analyticsUcNumber": 27,
    "analyticsSheet": "Use cases",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "What should we suggest before they submit this quote?",
    "galleryOrder": 27,
    "source": "seed"
  },
  {
    "id": "middleby-ex-01",
    "title": "RFQ Conversion Decline Analysis",
    "items": [
      "Shared funnel with decline narrative."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Existing"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Shared funnel with decline narrative.",
    "actionType": "Analyze",
    "analyticsUcNumber": 1,
    "analyticsSheet": "Existing",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Why did RFQ conversion decrease this month?",
    "galleryOrder": 101,
    "source": "seed"
  },
  {
    "id": "middleby-ex-02",
    "title": "Product Interest to Revenue Attribution",
    "items": [
      "Real GA brand sessions as interest proxy."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Existing"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Real GA brand sessions as interest proxy.",
    "actionType": "Analyze",
    "analyticsUcNumber": 2,
    "analyticsSheet": "Existing",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Which products drive the most interest but underperform in quotes?",
    "galleryOrder": 102,
    "source": "seed"
  },
  {
    "id": "middleby-ex-03",
    "title": "Lead Quality & Sales Follow-up Effectiveness",
    "items": [
      "Real HubSpot deal quality fields."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Existing"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Real HubSpot deal quality fields.",
    "actionType": "Analyze",
    "analyticsUcNumber": 3,
    "analyticsSheet": "Existing",
    "dataReadiness": "Ready",
    "sampleQuestion": "Which leads are high quality but slow to follow up?",
    "galleryOrder": 103,
    "source": "seed"
  },
  {
    "id": "middleby-ex-04",
    "title": "Site Search & Product Discovery Optimization",
    "items": [
      "Top search terms and zero-result searches for product discovery (Adobe / site search sample)."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Client KPI"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "What visitors search for and which queries return zero results.",
    "actionType": "Analyze",
    "analyticsUcNumber": 4,
    "analyticsSheet": "Client KPIs",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "What are visitors searching for but not finding?",
    "galleryOrder": 104,
    "source": "seed"
  },
  {
    "id": "middleby-ex-05",
    "title": "Category Revenue-at-Risk Analysis",
    "items": [
      "Example pipeline-at-risk dollars by category."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Existing"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Example pipeline-at-risk dollars by category.",
    "actionType": "Analyze",
    "analyticsUcNumber": 5,
    "analyticsSheet": "Existing",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Which categories are losing potential quote pipeline?",
    "galleryOrder": 105,
    "source": "seed"
  },
  {
    "id": "middleby-ex-06",
    "title": "Executive Growth Intelligence Dashboard",
    "items": [
      "Executive KPI strip with growth narrative."
    ],
    "tags": [
      "Middleby",
      "Analytics",
      "Existing"
    ],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Executive KPI strip with growth narrative.",
    "actionType": "Analyze",
    "analyticsUcNumber": 6,
    "analyticsSheet": "Existing",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "How is traffic vs quote volume trending?",
    "galleryOrder": 106,
    "source": "seed",
    "crossSourceKind": "summary"
  },
  {
    "id": "middleby-ns-01",
    "title": "Revenue Recovery Simulator",
    "items": [
      "Ranks fixes by recoverable quote pipeline so teams act on the highest-value work first."
    ],
    "tags": ["Middleby", "Analytics", "Aspirational"],
    "businessArea": "Digital & RFQ",
    "problemSignal": "See how much quote pipeline each site fix would recover, ranked by value.",
    "actionType": "Diagnose",
    "analyticsSheet": "Future",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "If we fix the top site issues, how much quote pipeline do we recover?",
    "galleryOrder": 200,
    "source": "seed"
  },
  {
    "id": "middleby-ns-02",
    "title": "Next Best Actions for Sales",
    "items": [
      "AI-ranked worklist of accounts to act on now, with the reason and pipeline at stake."
    ],
    "tags": ["Middleby", "Analytics", "Aspirational"],
    "businessArea": "Digital & RFQ",
    "problemSignal": "A prioritized worklist of accounts to act on today, with the reason for each.",
    "actionType": "Analyze",
    "analyticsSheet": "Future",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Which accounts should sales act on right now, and why?",
    "galleryOrder": 201,
    "source": "seed"
  },
  {
    "id": "middleby-ns-03",
    "title": "Brand Momentum Radar",
    "items": [
      "Week-over-week momentum by brand so teams catch rising and falling demand early."
    ],
    "tags": ["Middleby", "Analytics", "Aspirational"],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Which brands are gaining or losing momentum this week.",
    "actionType": "Analyze",
    "analyticsSheet": "Future",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Which brands are gaining or losing momentum this week?",
    "galleryOrder": 202,
    "source": "seed"
  },
  {
    "id": "middleby-ns-04",
    "title": "Pipeline Forecast and What-If",
    "items": [
      "Projects quote pipeline forward and shows what moves the trajectory."
    ],
    "tags": ["Middleby", "Analytics", "Aspirational"],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Where quote pipeline is heading over the next month, with drivers.",
    "actionType": "Diagnose",
    "analyticsSheet": "Future",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "What will quote pipeline look like next month, and what moves it?",
    "galleryOrder": 203,
    "source": "seed"
  },
  {
    "id": "middleby-kpi-01",
    "title": "Traffic Source and Device Analysis",
    "items": [
      "Sessions, quotes, and conversion by channel with desktop / mobile / tablet segmentation."
    ],
    "tags": ["Middleby", "Analytics", "Client KPI"],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Which traffic sources and devices drive sessions and quotes.",
    "actionType": "Analyze",
    "analyticsSheet": "Client KPIs",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "How do traffic sources and device types compare for sessions and quotes?",
    "galleryOrder": 300,
    "source": "seed"
  },
  {
    "id": "middleby-kpi-02",
    "title": "Closed-Won Rate and Lost Reasons",
    "items": [
      "Closed-won rate plus monthly closed-lost reason tallies from HubSpot."
    ],
    "tags": ["Middleby", "Analytics", "Client KPI"],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Win rate and why deals are lost.",
    "actionType": "Diagnose",
    "analyticsSheet": "Client KPIs",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "What is our closed-won rate, and why are deals lost?",
    "galleryOrder": 301,
    "source": "seed"
  },
  {
    "id": "middleby-kpi-03",
    "title": "Live Chat Buy Intent",
    "items": [
      "HubSpot chats that start with Can I buy / Buy Now Online, with conversion to quote."
    ],
    "tags": ["Middleby", "Analytics", "Client KPI"],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Buy-intent chat volume and how often it becomes a quote.",
    "actionType": "Analyze",
    "analyticsSheet": "Client KPIs",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "How many live chat Can I buy inquiries did we get, and how many became quotes?",
    "galleryOrder": 302,
    "source": "seed"
  },
  {
    "id": "middleby-kpi-04",
    "title": "Guided Shopping Tool Conversion",
    "items": [
      "LogRocket conversion from guided shopping tools to quote submit."
    ],
    "tags": ["Middleby", "Analytics", "Client KPI"],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Which guided shopping tools convert to quotes.",
    "actionType": "Diagnose",
    "analyticsSheet": "Client KPIs",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "What is the guided shopping tool conversion rate to quote?",
    "galleryOrder": 303,
    "source": "seed"
  },
  {
    "id": "middleby-kpi-05",
    "title": "Bounce and Engagement by Entry Page",
    "items": [
      "Bounce rate, page depth, and session duration by entry page (GA4)."
    ],
    "tags": ["Middleby", "Analytics", "Client KPI"],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Which entry pages bounce or engage well.",
    "actionType": "Analyze",
    "analyticsSheet": "Client KPIs",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Which entry pages have the highest bounce rate and lowest engagement?",
    "galleryOrder": 304,
    "source": "seed"
  },
  {
    "id": "middleby-kpi-06",
    "title": "PO Reference Coverage on Closed-Won",
    "items": [
      "Share of closed-won brand deals with PO reference set in HubSpot during conversion."
    ],
    "tags": ["Middleby", "Analytics", "Client KPI"],
    "businessArea": "Digital & RFQ",
    "problemSignal": "PO references missing on converted HubSpot deals.",
    "actionType": "Diagnose",
    "analyticsSheet": "Client KPIs",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Which brands are missing PO references on closed-won deals?",
    "galleryOrder": 305,
    "source": "seed"
  },
  {
    "id": "middleby-kpi-07",
    "title": "Quotes by Sales Territory",
    "items": [
      "Quote volume and closed-won rate by sales rep territory."
    ],
    "tags": ["Middleby", "Analytics", "Client KPI"],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Quote throughput by territory.",
    "actionType": "Analyze",
    "analyticsSheet": "Client KPIs",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "How many quotes did each sales territory generate?",
    "galleryOrder": 306,
    "source": "seed"
  },
  {
    "id": "middleby-kpi-08",
    "title": "Quotes by Customer Segment",
    "items": [
      "Quotes and win rate by HubSpot customer segment."
    ],
    "tags": ["Middleby", "Analytics", "Client KPI"],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Which customer segments generate and win quotes.",
    "actionType": "Analyze",
    "analyticsSheet": "Client KPIs",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "How do quotes break down by customer segment?",
    "galleryOrder": 307,
    "source": "seed"
  },
  {
    "id": "middleby-kpi-09",
    "title": "Top Viewed Product Pages",
    "items": [
      "Most-viewed PDPs by brand, SKU, and category (GA4)."
    ],
    "tags": ["Middleby", "Analytics", "Client KPI"],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Which product pages get the most traffic.",
    "actionType": "Analyze",
    "analyticsSheet": "Client KPIs",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "What are the top viewed product detail pages this week?",
    "galleryOrder": 308,
    "source": "seed"
  },
  {
    "id": "middleby-kpi-10",
    "title": "Quotes by Product Category",
    "items": [
      "HubSpot quote volume rolled up to L1 shop taxonomy categories."
    ],
    "tags": ["Middleby", "Analytics", "Client KPI"],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Which product categories drive the most quote demand.",
    "actionType": "Analyze",
    "analyticsSheet": "Client KPIs",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "How many quotes did each product category generate?",
    "galleryOrder": 309,
    "source": "seed"
  },
  {
    "id": "middleby-kpi-11",
    "title": "Quotes by State",
    "items": [
      "US quote volume and closed-won rate by state, rolled up to sales territory."
    ],
    "tags": ["Middleby", "Analytics", "Client KPI"],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Which states generate the most quotes and where win rates are strongest.",
    "actionType": "Analyze",
    "analyticsSheet": "Client KPIs",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Which US states generated the most quotes this week, and where is the win rate strongest?",
    "galleryOrder": 310,
    "source": "seed"
  },
  {
    "id": "middleby-kpi-12",
    "title": "Where each brand wins geographically",
    "items": [
      "HubSpot quote volume and win rate for each brand within each sales territory, highlights where a brand over- or under-performs geographically."
    ],
    "tags": ["Middleby", "Analytics", "Client KPI"],
    "businessArea": "Digital & RFQ",
    "problemSignal": "Where each brand is strong or weak across the sales territories, so merchandising and dealer teams see the brand-territory pockets worth doubling down on or fixing.",
    "actionType": "Analyze",
    "analyticsSheet": "Client KPIs",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Which brand-territory pairs show the strongest and weakest quote conversion?",
    "galleryOrder": 311,
    "source": "seed"
  },
  {
    "id": "middleby-kpi-13",
    "title": "What each customer segment buys",
    "items": [
      "How each customer segment splits quote volume across L1 product categories, e.g., Dealer segment weighting on Refrigeration vs Restaurant / QSR weighting on Cooking."
    ],
    "tags": ["Middleby", "Analytics", "Client KPI"],
    "businessArea": "Digital & RFQ",
    "problemSignal": "How each customer segment splits its quote mix across L1 product categories, for cross-sell targeting and category-team account planning.",
    "actionType": "Analyze",
    "analyticsSheet": "Client KPIs",
    "dataReadiness": "Partly Ready",
    "sampleQuestion": "Which customer segments concentrate on which product categories?",
    "galleryOrder": 312,
    "source": "seed"
  }
];
