/**
 * Default Media profile — operational processes for a digital OTT / broadcast media
 * customer. Covers the content lifecycle, discovery / recommendation, ad-ops, subscription
 * lifecycle, market / AEO intelligence, and content-integrity operations.
 *
 * Reuses the shared retail business-area taxonomy (merchandise → content, e-com → OTT
 * platform, finance → monetization, support → subscriber care) so no schema changes are
 * required. Subject-area strings pick the closest match from `business-areas.js` for each
 * business area so process normalization does not silently fall back.
 */

export const MEDIA_PROCESSES_SEED = [
  // ── Trend & content intelligence (merchandise) ──────────────────────────
  {
    id: "media-trend-1",
    name: "Audience Conversation Ingestion & Topic Detection",
    businessAreaId: "merchandise",
    subjectArea: "Trend & Intelligence",
    iconKey: "ClipboardCheck",
    description:
      "Ingest open-web, social, YouTube, and search signals; extract entities, topics, and genres in near-real time; keep source-level evidence traceable per topic.",
    impact: "Replaces manual monitoring across fragmented sources with one evidence-backed feed.",
    seed: true,
  },
  {
    id: "media-trend-2",
    name: "Trend Momentum & Explanation",
    businessAreaId: "merchandise",
    subjectArea: "Trend & Intelligence",
    iconKey: "ClipboardCheck",
    description:
      "Score topic momentum on 4-week growth, sentiment, and audience spread; explain why a topic is trending, who is driving it, and whether it is a spike or a sustained trend.",
    impact: "Cuts days-to-signal from weeks to under a week; evidence-backed content briefs.",
    seed: true,
  },
  {
    id: "media-scope-1",
    name: "Content Opportunity Scoping",
    businessAreaId: "merchandise",
    subjectArea: "Trend & Intelligence",
    iconKey: "ClipboardCheck",
    description:
      "Extrapolate trend trajectories 3-6 months ahead; compare with historical hits and competitor slate; flag under-represented themes as commissioning-research candidates. Separates fact vs. inferred trend vs. recommendation.",
    impact: "Move commissioning from intuition to evidence-backed shortlists; earlier idea development.",
    seed: true,
  },

  // ── Content lifecycle (merchandise) ─────────────────────────────────────
  {
    id: "media-cnt-1",
    name: "Content Acquisition & Rights Review",
    businessAreaId: "merchandise",
    subjectArea: "Content Library",
    iconKey: "ClipboardCheck",
    description:
      "Evaluate acquisition, licensing, and originals deals across genres and regional languages; align with library gaps, rights windows, and cost per watch hour targets.",
    impact: "Cut low-ROI acquisitions 15-25%; sharpen originals slate against demand.",
    seed: true,
  },
  {
    id: "media-cnt-2",
    name: "Title Metadata & Catalog Publishing",
    businessAreaId: "merchandise",
    subjectArea: "Content Library",
    iconKey: "ClipboardCheck",
    description:
      "Publish and QA title metadata, artwork, subtitles, and language tracks across web, mobile, and connected TV before release windows.",
    impact: "Improve discoverability; cut metadata-related content complaints 40%+.",
    seed: true,
  },
  {
    id: "media-cnt-3",
    name: "New Title Launch War-Room",
    businessAreaId: "merchandise",
    subjectArea: "Publishing & Launch",
    iconKey: "ClipboardCheck",
    description:
      "Coordinate merchandising, editorial, ad-ops, and marketing around every marquee release; track pre-launch buzz, day-one watch hours, and retention curve.",
    impact: "Lift opening-window watch hours 10-20%; tighten cross-team handoff.",
    seed: true,
  },

  // ── Discovery / recommendation engine (e-com) ───────────────────────────
  {
    id: "media-eng-1",
    name: "Recommendation Engine Tuning",
    businessAreaId: "e-com",
    subjectArea: "Discovery",
    iconKey: "ClipboardCheck",
    description:
      "Retrain and re-rank homepage rows, category shelves, and post-play recommendations against watch-hour and retention lift targets.",
    impact: "Incremental watch minutes +5-12%; higher D7 retention on new signups.",
    seed: true,
  },
  {
    id: "media-eng-2",
    name: "Content Discovery & Search Relevance Review",
    businessAreaId: "e-com",
    subjectArea: "Discovery",
    iconKey: "ClipboardCheck",
    description:
      "Monitor on-platform search click-through, zero-result rate, and typo tolerance; correct relevance drift for tent-pole titles and long-tail queries.",
    impact: "Reduce zero-result searches 30-50%; lift search-to-play conversion.",
    seed: true,
  },

  // ── Ad-ops / monetization (finance) ─────────────────────────────────────
  {
    id: "media-adops-1",
    name: "Ad Inventory & Yield Management",
    businessAreaId: "finance",
    subjectArea: "Ad Yield",
    iconKey: "Tag",
    description:
      "Forecast avails by daypart and device; balance house-sold, direct-IO, and programmatic; enforce frequency caps and creative rotation.",
    impact: "Fill rate +3-6 pts; eCPM +5-10% through mix and pacing discipline.",
    seed: true,
  },
  {
    id: "media-adops-2",
    name: "Programmatic Auction Health Review",
    businessAreaId: "finance",
    subjectArea: "Ad Yield",
    iconKey: "Tag",
    description:
      "Audit header-bidding waterfall, demand-partner mix, VPAID error and timeout rates; tune floors and bidder priority against blended yield.",
    impact: "Recover yield lost to timeouts / errors; lift blended eCPM 4-8%.",
    seed: true,
  },

  // ── Subscription lifecycle (support) ────────────────────────────────────
  {
    id: "media-sub-1",
    name: "Subscription Lifecycle Management",
    businessAreaId: "support",
    subjectArea: "Care & Escalations",
    iconKey: "Repeat",
    description:
      "Manage signup, plan-tier movement, dunning, and renewal workflows across mobile-only, standard, premium, and telco-bundle cohorts.",
    impact: "Cut involuntary churn from failed payments 20-30%; smoother onboarding.",
    seed: true,
  },
  {
    id: "media-sub-2",
    name: "Churn Intervention & Retention Play",
    businessAreaId: "support",
    subjectArea: "Care & Escalations",
    iconKey: "Repeat",
    description:
      "Score renewal cohorts on churn risk; stage save offers, plan downgrades, and content nudges 14 / 7 / 3 days before renewal.",
    impact: "Reduce voluntary churn 1-3 pts on at-risk cohorts.",
    seed: true,
  },

  // ── Market & AEO intelligence (merchandise + e-com) ─────────────────────
  {
    id: "media-mkt-1",
    name: "Competitor & Market Landscape Watch",
    businessAreaId: "merchandise",
    subjectArea: "Trend & Intelligence",
    iconKey: "ClipboardCheck",
    description:
      "Daily scan of competing platforms: plan pricing, bundle deals, upcoming premieres, top-title watch signals, and trade-press announcements.",
    impact: "Same-day competitive signal for programming, pricing, and marketing.",
    seed: true,
  },
  {
    id: "media-mkt-2",
    name: "AEO / GEO Visibility Tracking",
    businessAreaId: "e-com",
    subjectArea: "AEO / GEO",
    iconKey: "ClipboardCheck",
    description:
      "Track our brand and title presence across ChatGPT, Perplexity, Google AI Overviews, and Gemini answers on curated prompt sets; brief SEO and content teams.",
    impact: "Baseline generative-search share; recover eroding discovery volume.",
    seed: true,
  },

  // ── Content integrity (support) ─────────────────────────────────────────
  {
    id: "media-piracy-1",
    name: "Piracy & Leak Monitoring",
    businessAreaId: "support",
    subjectArea: "Content Integrity",
    iconKey: "Repeat",
    description:
      "Detect unauthorized uploads on Telegram, torrent indexes, and rogue OTT clones within the launch window; auto-draft takedown requests and legal escalations.",
    impact: "Cut leak-to-takedown time 60-80%; protect opening-window watch hours.",
    seed: true,
  },

  // ── Content Library (inventory) ─────────────────────────────────────────
  {
    id: "media-lib-1",
    name: "Rights Window & Licensing Review",
    businessAreaId: "merchandise",
    subjectArea: "Content Library",
    iconKey: "PackageCheck",
    description:
      "Track rights-window expiries by title, region, and language; surface high-watch-hour titles ahead of renewal or re-acquisition negotiations.",
    impact: "Prevent silent library churn; protect watch-hour delivery on expiring titles.",
    seed: true,
  },
  {
    id: "media-lib-2",
    name: "Catalog Health & Metadata QA",
    businessAreaId: "merchandise",
    subjectArea: "Content Library",
    iconKey: "PackageCheck",
    description:
      "Audit metadata, artwork, subtitles, and language tracks across web, mobile, and connected TV; catch drift on high-traffic titles.",
    impact: "Reduce metadata-driven discovery failures and support complaints.",
    seed: true,
  },

  // ── Content Publishing (orders) ─────────────────────────────────────────
  {
    id: "media-pub-1",
    name: "Content Ingestion Pipeline",
    businessAreaId: "merchandise",
    subjectArea: "Publishing & Launch",
    iconKey: "ClipboardCheck",
    description:
      "Monitor ingest queue depth, encoding failures, subtitle QC, and metadata sync across regions; keep publishing windows on time.",
    impact: "Hold publishing SLA on marquee releases; reduce ingest incident rate.",
    seed: true,
  },
  {
    id: "media-pub-2",
    name: "Launch War-Room Coordination",
    businessAreaId: "merchandise",
    subjectArea: "Publishing & Launch",
    iconKey: "ClipboardCheck",
    description:
      "Coordinate cross-team readiness for marquee releases 48 hours ahead of drop: promotional assets, ad inventory, editorial rows, subscriber comms, and CDN capacity.",
    impact: "Quantified war-room readiness; fewer launch-day surprises.",
    seed: true,
  },

  // ── Broadcast Ops (store-ops) ───────────────────────────────────────────
  {
    id: "media-broadcast-1",
    name: "Linear Playout Health Monitoring",
    businessAreaId: "store-ops",
    subjectArea: "Playout Health",
    iconKey: "Store",
    description:
      "Monitor linear-channel playout for schedule adherence, encoder health, and broadcast-quality anomalies against BARC delivery expectations.",
    impact: "Reduce playout incidents; keep advertiser make-good exposure low.",
    seed: true,
  },
  {
    id: "media-broadcast-2",
    name: "CDN & Regional Delivery Review",
    businessAreaId: "store-ops",
    subjectArea: "CDN Delivery",
    iconKey: "Store",
    description:
      "Track CDN edge health and streaming quality (start-time, buffer ratio, bitrate) by region and device; catch regressions proactively.",
    impact: "Recover watch hours lost to regional streaming regressions.",
    seed: true,
  },

  // ── Signup & Checkout (pos) ─────────────────────────────────────────────
  {
    id: "media-signup-1",
    name: "Signup Funnel Diagnostics",
    businessAreaId: "support",
    subjectArea: "Signup Funnel",
    iconKey: "Store",
    description:
      "Diagnose signup drop-off by device, plan tier, and payment method; surface friction on OTP, coupons, and telco-bundle activation.",
    impact: "Lift paid conversion 5-12%; identify per-device fix priorities.",
    seed: true,
  },
  {
    id: "media-signup-2",
    name: "Payment Decline & Dunning Recovery",
    businessAreaId: "support",
    subjectArea: "Payments",
    iconKey: "Store",
    description:
      "Cluster declines by BIN, wallet, and telco; time dunning retries against payday cycles and BIN uptime windows to recover renewals.",
    impact: "Cut involuntary churn 20-30% on paid cohorts.",
    seed: true,
  },

  // ── Plans & Pricing (pricing) — Plan Mix scope ──────────────────────────
  {
    id: "media-plan-1",
    name: "Telco Bundle & Partner Attribution",
    businessAreaId: "support",
    subjectArea: "Plans & Bundles",
    iconKey: "Tag",
    description:
      "Attribute paid conversions, engagement, and ARPU to each telco bundle and partner deal; identify under-performing partners vs. contract.",
    impact: "Strengthen renewal leverage; reallocate marketing to high-yield partners.",
    seed: true,
  },

  // ── Content Teams (workforce) ───────────────────────────────────────────
  {
    id: "media-wf-editorial",
    name: "Editorial Team Productivity Review",
    businessAreaId: "merchandise",
    subjectArea: "Editorial & Team Ops",
    iconKey: "Store",
    description:
      "Track editorial team throughput on homepage curation, category shelves, and launch merchandising; surface bottlenecks against release calendar.",
    impact: "Protect editorial quality when launch calendar accelerates.",
    seed: true,
  },
  {
    id: "media-wf-adops",
    name: "Ad-Ops Team Workload Review",
    businessAreaId: "merchandise",
    subjectArea: "Editorial & Team Ops",
    iconKey: "Store",
    description:
      "Balance ad-ops workload across direct-IO, house-sold, and programmatic; flag campaigns approaching pacing risk before end-of-flight.",
    impact: "Reduce pacing-risk campaigns; improve advertiser satisfaction.",
    seed: true,
  },

  // ── Monetization — Content ROI attribution ──────────────────────────────
  {
    id: "media-roi-1",
    name: "Content ROI Attribution",
    businessAreaId: "finance",
    subjectArea: "Content ROI",
    iconKey: "Tag",
    description:
      "Attribute paid conversion, retention, and ad revenue to each marquee title vs. its acquisition or production cost; identify winners for renewal and losers to trim.",
    impact: "Move commissioning decisions from qualitative estimates to attributed ROI.",
    seed: true,
  },

  // ── Subscribers — Competitor Pricing watch ──────────────────────────────
  {
    id: "media-comp-pricing-1",
    name: "Competitor Pricing & Bundle Watch",
    businessAreaId: "support",
    subjectArea: "Competitor Pricing",
    iconKey: "Repeat",
    description:
      "Monitor competing OTT platforms for plan-price and bundle changes; simulate impact on our plan mix, ARPU, and churn before renewal windows.",
    impact: "Same-day competitive pricing intelligence for the subscribers team.",
    seed: true,
  },
];
