/**
 * Default Media profile — automation playbooks (M1, M2).
 *
 * M1 — Weekly trend intelligence digest    (no approval, runs on schedule)
 * M2 — Content opportunity brief           (approval-gated)
 *
 * Anchored to the two headline media use cases (UC-1, UC-2). Shape mirrors
 * the GFI (G1/G2) and retail (R1/R2) playbooks so `automation-registry.js`
 * can consume them without any adapter.
 */

/** @type {import("./middleby-automation-playbooks").MiddlebyAutomationPlaybook[]} */
export const MEDIA_AUTOMATION_PLAYBOOKS = [
  {
    id: "M1",
    label: "Weekly trend intelligence digest",
    catalogIds: ["media-uc-01-content-conversation-trends"],
    trigger:
      "Weekly trend scan complete. 6 sustained themes above the 4-week momentum threshold across open-web and social signals.",
    analyzeTitle: "Audience conversation & trend momentum",
    monitorTitle: "Content Intelligence",
    owner: "Content Strategy leadership",
    // No decisionCardId => no approval gate; the workflow is a digest.
    automationSummary:
      "Continuously listens to open-web, social, and search signals; extracts topics; scores 4-week momentum, sentiment, and spread; assembles a source-linked weekly digest for content teams.",
    automatedActions: [
      { what: "Ingest open-web + social signals", where: "Social listening · YouTube · Google Trends", detail: "Topic extraction across trade press, Instagram Reels, Twitter, YouTube shorts, and search interest." },
      { what: "Score topic momentum", where: "Enterprise GPT", detail: "4-week growth, sentiment, and audience spread; separate spikes from sustained trends." },
      { what: "Explain each trend", where: "Enterprise GPT", detail: "What is trending, why, who is driving it — with source citations traceable per topic." },
      { what: "Compose weekly digest", where: "Enterprise GPT", detail: "One-page content-team digest plus a full topic table with evidence links." },
      { what: "Schedule Monday distribution", where: "Email", detail: "7 AM send to Content Strategy, Programming, Development, and Consumer Insights." },
    ],
    staysHuman: [
      "Content strategy decides which themes deserve deeper research.",
      "Programming teams choose which trends translate into slate exploration.",
    ],
    journey: [
      { phase: "analyze", title: "Ingest audience signals", detail: "Open-web + social + search signals across 8 tracked source families." },
      { phase: "monitor", title: "Content Intelligence view", detail: "Trending topics + momentum scores + evidence linked on the content monitor." },
      { phase: "automate", title: "Weekly digest", detail: "Explainability layer: what / why / who / momentum / spike-vs-sustained." },
      { phase: "automate", title: "Distribute digest", detail: "Sent Monday 7 AM to content-team distribution list." },
    ],
    runSteps: [
      { label: "Ingest 8 signal families", duration: "1.6s" },
      { label: "Extract topics & entities", duration: "1.1s" },
      { label: "Score momentum & sentiment", duration: "0.9s" },
      { label: "Explain each trend with evidence", duration: "1.4s" },
      { label: "Schedule Monday 7 AM distribution", duration: "0.3s" },
    ],
    impact: [
      { label: "Signals ingested / wk", value: "3.4M", sub: "Across 8 source families" },
      { label: "Trends surfaced", value: "6", sub: "Above sustain threshold" },
      { label: "Manual effort saved", value: "~14 hrs", sub: "Vs. manual monitoring" },
    ],
    kpiTargets: [
      { name: "Trend Momentum Score", contributionType: "direct", targetDelta: { value: "Baseline established", horizon: "weekly" }, attribution: "Weekly digest publishes a calibrated momentum baseline for content teams." },
      { name: "Trend Sustained Rate", contributionType: "direct", targetDelta: { value: "+15 pts", horizon: "quarter" }, attribution: "Explainability layer filters transient viral spikes out of the sustained-trend set." },
      { name: "Share of Voice (Trade + Social)", contributionType: "indirect", targetDelta: { value: "Visibility", horizon: "weekly" }, attribution: "Earlier signal of theme momentum lets marketing lean in before category leaders do." },
    ],
  },
  {
    id: "M2",
    label: "Content opportunity brief",
    catalogIds: ["media-uc-02-future-content-opportunity"],
    trigger:
      "Two under-represented themes cleared the sustained-momentum threshold and match historical hit patterns. Commissioning review requested.",
    analyzeTitle: "Content opportunities & scoping",
    monitorTitle: "Content Intelligence",
    owner: "Head of Content Strategy",
    decisionCardId: "M2-card",
    decisionCard: {
      id: "M2-card",
      title: "Approve mythology-adjacent scoping shortlist",
      reasoning:
        "Mythological drama reboots have sustained 4-week momentum; audience is Hindi-speaking Tier-2. Similar themes have historically over-indexed on our mythology and family-drama slate. Two under-represented sub-themes (regional mythology, women-led mythology) are absent or thin in the current slate.",
      action:
        "Approve a scoping research shortlist of 3 mythology-adjacent theme areas to content-development; open a formal commissioning-research brief within 5 working days.",
    },
    automationSummary:
      "Extrapolates current audience trends against our historical hits and competitor slate; flags themes under-represented in our current lineup; separates fact vs. inferred trend vs. recommendation.",
    automatedActions: [
      { what: "Extrapolate trend trajectories", where: "Enterprise GPT", detail: "Project 3-6 months forward on each sustained trend with confidence bands." },
      { what: "Compare with historical hits", where: "Content catalog", detail: "Match trajectories against our own historically successful shows and known competitor hits." },
      { what: "Flag under-represented themes", where: "Enterprise GPT", detail: "Identify themes absent or thin in the current slate that pass the sustained-momentum bar." },
      { what: "Draft commissioning shortlist", where: "Enterprise GPT", detail: "Fact / inferred trend / recommendation separated per the customer standard." },
      { what: "Stage brief for review", where: "Enterprise GPT", detail: "Brief held for Head of Content Strategy approval before content-development handoff." },
    ],
    staysHuman: [
      "Head of Content Strategy approves the scoping shortlist before commissioning research begins.",
      "Content development leads the formal research brief and creative discussions.",
    ],
    journey: [
      { phase: "analyze", title: "Extrapolate + compare", detail: "Project sustained trends forward vs. our own and competitor hit patterns." },
      { phase: "monitor", title: "Content Intelligence view", detail: "Under-represented themes surfaced on the content monitor." },
      { phase: "automate", title: "Commissioning shortlist", detail: "Fact / inferred trend / recommendation labels applied per theme." },
      { phase: "automate", title: "Approval gate", detail: "Head of Content Strategy approves shortlist before research kickoff." },
    ],
    runSteps: [
      { label: "Extrapolate sustained trends", duration: "1.0s" },
      { label: "Compare with historical hits", duration: "1.3s" },
      { label: "Flag under-represented themes", duration: "0.6s" },
      { label: "Draft brief with fact/trend/rec labels", duration: "1.1s" },
      { label: "Stage brief (held until approval)", duration: "0.3s" },
    ],
    impact: [
      { label: "Themes evaluated", value: "38", sub: "Trend catchment" },
      { label: "Under-represented", value: "3", sub: "In our slate" },
      { label: "Prep time saved", value: "~10 hrs", sub: "Vs. manual scoping" },
    ],
    kpiTargets: [
      { name: "Under-represented Themes Flagged", contributionType: "direct", targetDelta: { value: "+3 wk", horizon: "weekly" }, attribution: "Automated scoping surfaces themes weekly instead of once per commissioning cycle." },
      { name: "Content ROI (Marquee Titles)", contributionType: "indirect", targetDelta: { value: "+3-5%", horizon: "annual" }, attribution: "Evidence-backed commissioning shortlist raises hit-rate on marquee acquisitions." },
      { name: "Share of Voice (Trade + Social)", contributionType: "indirect", targetDelta: { value: "+1-2 pts", horizon: "quarter" }, attribution: "Earlier moves against sustained trends lift trade + social conversation share." },
    ],
  },
];
