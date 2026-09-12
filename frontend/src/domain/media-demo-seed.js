/**
 * Default Media profile — demo seeds for a digital OTT / broadcast media & entertainment
 * customer. Content is India-market flavored (Hindi + regional-language streaming, linear
 * broadcast, programmatic ad-ops, subscription tiers, AEO/GEO visibility) so it resonates
 * with subscription-video and channel operators without naming any specific customer.
 *
 * Loaded only when the `default-media` demo profile is active. Uses the shared retail
 * business-area taxonomy (merchandise → content, e-com → OTT platform, pricing →
 * subscription plans, finance → monetization, support → subscriber care) so no changes
 * are required to `business-areas.js`.
 *
 * UC-1 and UC-2 (Trend Intelligence and Content Scoping) are the headline use cases
 * requested in the customer AI Content Intelligence definition; they lead the gallery.
 */

const TS = new Date().toISOString();

/** Media & OTT use case catalog — trend intelligence, content scoping, competitor / OTT / AEO-GEO / market landscape. */
export const MEDIA_USE_CASE_CATALOG_SEED = [
  // ── Headline use cases (Content Intelligence brief) ──────────────────────
  {
    id: "media-uc-01-content-conversation-trends",
    title: "Content conversation & trend intelligence",
    items: [
      "Continuously scan open-web and social conversations; detect emerging topics, explain why they are trending, and separate temporary spikes from sustained 4-week trends with source-level evidence.",
    ],
    tags: ["Media", "Trend", "Intelligence", "Social", "Content"],
    businessArea: "Content",
    problemSignal:
      "Content, programming, and marketing teams monitor fragmented open-web and social sources manually; by the time an emerging theme is spotted, rival networks are already commissioning against it.",
    actionType: "Analyze",
    process: "media-trend-1, media-trend-2, media-mkt-1",
    galleryOrder: 1,
    source: "seed",
    updatedAt: TS,
  },
  {
    id: "media-uc-02-future-content-opportunity",
    title: "Future content opportunity (content scoping)",
    items: [
      "Extrapolate current audience-conversation trends 3-6 months forward against our historical library and competitor hits; surface under-represented themes worth commissioning research.",
    ],
    tags: ["Media", "Scoping", "Commissioning", "Content", "Intelligence"],
    businessArea: "Content",
    problemSignal:
      "Commissioning briefs lean on intuition and lagging BARC / OTT ratings; there is no evidence-backed layer that projects emerging themes and flags gaps in our current slate.",
    actionType: "Improve",
    process: "media-scope-1, media-trend-2, media-cnt-1",
    galleryOrder: 2,
    source: "seed",
    updatedAt: TS,
  },

  // ── Competitor / market landscape ────────────────────────────────────────
  {
    id: "media-uc-03-competitor-content-benchmark",
    title: "Competitor OTT content benchmark",
    items: [
      "Track weekly release cadence, genre mix, star-cast pull, and catalog depth of leading Indian OTT platforms vs. our library.",
    ],
    tags: ["Media", "Competitor", "OTT", "Content"],
    businessArea: "Content",
    problemSignal:
      "Competing OTT platforms refresh their library aggressively; without a weekly benchmark our programming team reacts late to genre and star-cast shifts.",
    actionType: "Analyze",
    process: "media-mkt-1, media-cnt-1",
    galleryOrder: 3,
    source: "seed",
    updatedAt: TS,
  },
  {
    id: "media-uc-04-digital-market-landscape",
    title: "Digital OTT market landscape scan",
    items: [
      "Daily competitive intel: plan pricing, bundle changes, distribution deals, upcoming premieres, and platform-level MAU / watch-hour movements.",
    ],
    tags: ["Media", "Competitor", "OTT", "Market"],
    businessArea: "Content",
    problemSignal:
      "Competitive changes (price drops, telco bundles, exclusive licensing) surface in trade press days after the fact; we need a same-day signal.",
    actionType: "Analyze",
    process: "media-mkt-1, media-mkt-2",
    galleryOrder: 4,
    source: "seed",
    updatedAt: TS,
  },
  {
    id: "media-uc-05-content-gap-analysis",
    title: "Content gap analysis by genre & language",
    items: [
      "Identify genres, languages, and dayparts where competitor libraries out-index ours on watch hours; brief acquisitions and originals.",
    ],
    tags: ["Media", "Content", "Gap", "Regional"],
    businessArea: "Content",
    problemSignal:
      "Regional-language demand (Marathi, Tamil, Telugu, Bengali) is growing faster than acquisition planning cycles; commissioning briefs lag audience demand.",
    actionType: "Diagnose",
    process: "media-cnt-1, media-mkt-1",
    galleryOrder: 5,
    source: "seed",
    updatedAt: TS,
  },

  // ── Title & audience performance ─────────────────────────────────────────
  {
    id: "media-uc-06-title-launch-performance",
    title: "Title launch performance deep dive",
    items: [
      "Blend pre-launch buzz, day-one watch hours, D7 retention curve, and cannibalization on the back catalog for every marquee release.",
    ],
    tags: ["Media", "OTT", "Content", "Launch"],
    businessArea: "Content",
    problemSignal:
      "Big-ticket premieres are evaluated by opening-weekend numbers alone; we miss retention drop-off and cannibalization of the back catalog until much later.",
    actionType: "Diagnose",
    process: "media-cnt-3, media-eng-1",
    galleryOrder: 6,
    source: "seed",
    updatedAt: TS,
  },
  {
    id: "media-uc-07-social-sentiment-releases",
    title: "Social sentiment for upcoming releases",
    items: [
      "Blend Twitter / Instagram / YouTube trailer chatter with search-intent trends to forecast opening-window watch hours.",
    ],
    tags: ["Media", "Social", "Sentiment", "Launch"],
    businessArea: "Content",
    problemSignal:
      "Trailer buzz is tracked qualitatively; we have no calibrated model tying pre-release chatter to opening-week watch hours.",
    actionType: "Analyze",
    process: "media-mkt-1, media-cnt-3",
    galleryOrder: 7,
    source: "seed",
    updatedAt: TS,
  },
  {
    id: "media-uc-08-regional-language-performance",
    title: "Regional-language content performance",
    items: [
      "Compare watch hours, completion, and paid-conversion for Hindi vs. Marathi / Tamil / Telugu / Bengali libraries by state and device.",
    ],
    tags: ["Media", "Regional", "Content", "OTT"],
    businessArea: "Content",
    problemSignal:
      "Hindi headline metrics mask fast-moving regional shifts; without a language cut we mis-allocate marketing and commissioning spend.",
    actionType: "Analyze",
    process: "media-cnt-2, media-mkt-1",
    galleryOrder: 8,
    source: "seed",
    updatedAt: TS,
  },

  // ── Discovery / recommendation / AEO-GEO ─────────────────────────────────
  {
    id: "media-uc-09-recommendation-uplift",
    title: "Content recommendation uplift",
    items: [
      "Measure incremental watch minutes and completion lift from the recommendation carousel vs. an unpersonalised control, by row and slot.",
    ],
    tags: ["Media", "OTT", "Personalisation", "Discovery"],
    businessArea: "OTT Platform",
    problemSignal:
      "Recommendation rows dominate homepage real estate but their incremental lift is estimated, not measured — hard to defend against editorial curation.",
    actionType: "Analyze",
    process: "media-eng-1, media-eng-2",
    galleryOrder: 9,
    source: "seed",
    updatedAt: TS,
  },
  {
    id: "media-uc-10-aeo-geo-visibility",
    title: "AEO / GEO brand visibility tracker",
    items: [
      "Monitor how often our titles, characters, and brand surface as citations in ChatGPT, Perplexity, Google AI Overviews, and Gemini answers.",
    ],
    tags: ["Media", "AEO", "GEO", "Discovery"],
    businessArea: "OTT Platform",
    problemSignal:
      "Generative search is siphoning discovery traffic from Google SERPs; without a visibility baseline we cannot brief content or SEO teams on where to invest.",
    actionType: "Analyze",
    process: "media-mkt-2, media-eng-2",
    galleryOrder: 10,
    source: "seed",
    updatedAt: TS,
  },

  // ── Subscription & monetization ──────────────────────────────────────────
  {
    id: "media-uc-11-subscription-churn-predictor",
    title: "Subscription churn predictor",
    items: [
      "Cohort-level churn drivers by plan tier, region, and content-affinity segment; auto-stage save offers before renewal date.",
    ],
    tags: ["Media", "OTT", "Churn", "Subscription"],
    businessArea: "Subscribers",
    problemSignal:
      "Post-launch renewal cohorts churn heavily once a marquee title finishes; save-offer eligibility is decided too late to intervene.",
    actionType: "Improve",
    process: "media-sub-2, media-sub-1",
    galleryOrder: 11,
    source: "seed",
    updatedAt: TS,
  },
  {
    id: "media-uc-12-plan-pricing-optimization",
    title: "Subscription plan & pricing optimization",
    items: [
      "Diagnose plan-mix drift, telco bundle cannibalization, and upgrade / downgrade signals; simulate the effect of a new mobile-only or annual tier.",
    ],
    tags: ["Media", "Pricing", "Subscription", "Bundles"],
    businessArea: "Subscribers",
    problemSignal:
      "Mobile-only and annual plans launched by competitors are eroding ARPU; without a scenario view we cannot pick a defensible pricing response.",
    actionType: "Improve",
    process: "media-plan-1, media-comp-pricing-1, media-sub-1",
    galleryOrder: 12,
    source: "seed",
    updatedAt: TS,
  },
  {
    id: "media-uc-13-ad-yield-optimization",
    title: "Programmatic ad yield optimization",
    items: [
      "Diagnose fill rate, eCPM, and VPAID error trends by daypart, device, and demand partner; surface yield gaps and creative fatigue.",
    ],
    tags: ["Media", "Ad-Ops", "Yield", "Programmatic"],
    businessArea: "Monetization",
    problemSignal:
      "Programmatic yield is flat despite AVOD watch-hour growth; header-bidding waterfall is opaque and demand-partner mix rarely re-tuned.",
    actionType: "Improve",
    process: "media-adops-1, media-adops-2",
    galleryOrder: 13,
    source: "seed",
    updatedAt: TS,
  },

  // ── Content integrity ───────────────────────────────────────────────────
  {
    id: "media-uc-14-piracy-leak-monitoring",
    title: "Piracy & content leak monitoring",
    items: [
      "Detect unauthorized uploads on Telegram, torrent indexes, and rogue OTT clones within hours of premiere; auto-file takedown requests.",
    ],
    tags: ["Media", "Piracy", "Compliance", "Content"],
    businessArea: "Subscribers",
    problemSignal:
      "Marquee-title leaks now surface within hours of premiere; manual takedown routing loses the first 24-hour window where damage is highest.",
    actionType: "Improve",
    process: "media-piracy-1, media-cnt-3",
    galleryOrder: 14,
    source: "seed",
    updatedAt: TS,
  },

  // ── Content Library (Rights & catalog health) ─────────────────────────────
  {
    id: "media-uc-15-rights-window-expiry",
    title: "Rights window expiry & renewal watch",
    items: [
      "Track upcoming rights-window expiries by title, region, and language; flag high-watch-hour titles for renewal or re-acquisition before the window closes.",
    ],
    tags: ["Media", "Rights", "Content Library", "Licensing"],
    businessArea: "Content",
    problemSignal:
      "Rights windows close silently and high-performing titles fall off the platform before renewal negotiations start; audience notices before we do.",
    actionType: "Diagnose",
    process: "media-lib-1, media-cnt-1",
    galleryOrder: 15,
    source: "seed",
    updatedAt: TS,
  },
  {
    id: "media-uc-16-catalog-health-metadata",
    title: "Catalog health & metadata QA",
    items: [
      "Audit title metadata, artwork, subtitles, and language tracks across web, mobile, and connected TV; surface missing or stale fields on high-watch-hour titles.",
    ],
    tags: ["Media", "Metadata", "Catalog", "QA"],
    businessArea: "Content",
    problemSignal:
      "Metadata drift on high-traffic titles hurts discovery and generates avoidable subscriber complaints; QA is manual and lags publishing.",
    actionType: "Improve",
    process: "media-lib-2, media-cnt-2",
    galleryOrder: 16,
    source: "seed",
    updatedAt: TS,
  },

  // ── Content Publishing (Ingestion & launch prep) ──────────────────────────
  {
    id: "media-uc-17-content-ingestion-pipeline",
    title: "Content ingestion & publishing pipeline health",
    items: [
      "Monitor ingest queue depth, encoding failures, subtitle QC, and metadata sync across regions; keep publishing windows on time for marquee releases.",
    ],
    tags: ["Media", "Publishing", "Pipeline", "Ingest"],
    businessArea: "Content",
    problemSignal:
      "Late-arriving masters and subtitle delays slip publishing windows; there is no single view of ingest health across regions and languages.",
    actionType: "Improve",
    process: "media-pub-1, media-cnt-2",
    galleryOrder: 17,
    source: "seed",
    updatedAt: TS,
  },
  {
    id: "media-uc-18-launch-war-room-readiness",
    title: "Launch war-room readiness scorecard",
    items: [
      "Score every marquee release on cross-team readiness: promotional assets, ad inventory, editorial rows, subscriber comms, and CDN capacity 48 hours before drop.",
    ],
    tags: ["Media", "Publishing", "Launch", "War-Room"],
    businessArea: "Content",
    problemSignal:
      "War-room readiness is judged qualitatively; there is no scorecard that quantifies cross-team preparedness ahead of a marquee launch.",
    actionType: "Diagnose",
    process: "media-pub-2, media-cnt-3",
    galleryOrder: 18,
    source: "seed",
    updatedAt: TS,
  },

  // ── Broadcast Ops (Playout & CDN delivery) ────────────────────────────────
  {
    id: "media-uc-19-playout-schedule-optimization",
    title: "Linear playout schedule optimization",
    items: [
      "Align linear-channel playout schedule with BARC ratings, promo windows, and OTT premiere calendar; recover promo inventory value.",
    ],
    tags: ["Media", "Broadcast", "Playout", "BARC"],
    businessArea: "Broadcast",
    problemSignal:
      "Linear playout and OTT release schedules are planned in silos; promo inventory value leaks when premieres compete for the same audience.",
    actionType: "Improve",
    process: "media-broadcast-1, media-cnt-3",
    galleryOrder: 19,
    source: "seed",
    updatedAt: TS,
  },
  {
    id: "media-uc-20-cdn-regional-performance",
    title: "CDN & regional streaming performance",
    items: [
      "Monitor CDN edge health and streaming performance (start-time, buffer ratio, bitrate) by region and device; catch regressions before subscribers complain.",
    ],
    tags: ["Media", "Broadcast", "CDN", "Delivery"],
    businessArea: "Broadcast",
    problemSignal:
      "Regional streaming quality regressions surface via subscriber-care escalations, not proactive monitoring; watch-hour loss goes uncounted.",
    actionType: "Diagnose",
    process: "media-broadcast-2, media-cnt-3",
    galleryOrder: 20,
    source: "seed",
    updatedAt: TS,
  },

  // ── Signup & Checkout (Signup funnel + payment health) ────────────────────
  {
    id: "media-uc-21-signup-funnel-conversion",
    title: "Signup funnel conversion optimization",
    items: [
      "Diagnose signup-funnel drop-off by device, plan tier, and payment method; surface friction on OTP, coupon codes, and telco-bundle activation.",
    ],
    tags: ["Media", "Signup", "Funnel", "Conversion"],
    businessArea: "Subscribers",
    problemSignal:
      "Signup funnel conversion is tracked at the summary level; where paid conversion is leaking by device or payment method is not visible until quarterly review.",
    actionType: "Diagnose",
    process: "media-signup-1, media-sub-1",
    galleryOrder: 21,
    source: "seed",
    updatedAt: TS,
  },
  {
    id: "media-uc-22-payment-decline-recovery",
    title: "Payment decline & dunning recovery",
    items: [
      "Cluster payment declines by BIN, wallet, and telco; auto-time dunning retries against payday cycles and BIN uptime windows to recover renewals.",
    ],
    tags: ["Media", "Signup", "Payments", "Dunning"],
    businessArea: "Subscribers",
    problemSignal:
      "Involuntary churn from payment declines is treated as a fixed cost; dunning retries are timed on generic schedules that ignore BIN and wallet uptime.",
    actionType: "Improve",
    process: "media-signup-2, media-sub-1",
    galleryOrder: 22,
    source: "seed",
    updatedAt: TS,
  },

  // ── Subscriber Care (top up — Plans & Pricing / Monetization have 1 each) ─
  {
    id: "media-uc-23-content-integrity-brand-safety",
    title: "Content integrity & ad-adjacency brand safety",
    items: [
      "Flag ad-adjacency risk against sensitive content categories; keep major-brand campaigns on approved title inventory across regional-language libraries.",
    ],
    tags: ["Media", "Brand Safety", "Ad-Ops", "Compliance"],
    businessArea: "Subscribers",
    problemSignal:
      "Ad-adjacency exceptions surface via post-campaign advertiser complaints; brand-safety rules are enforced manually per campaign.",
    actionType: "Improve",
    process: "media-piracy-1, media-adops-1",
    galleryOrder: 23,
    source: "seed",
    updatedAt: TS,
  },

  // ── Plans & Pricing (add 1 more so section has ≥2 UCs) ────────────────────
  {
    id: "media-uc-24-telco-bundle-attribution",
    title: "Telco bundle & partner deal attribution",
    items: [
      "Attribute paid conversions, engagement, and ARPU to each telco bundle and partner deal; identify partners over- or under-performing vs. contract.",
    ],
    tags: ["Media", "Bundles", "Telco", "Attribution"],
    businessArea: "Subscribers",
    problemSignal:
      "Telco bundle contracts have opaque incremental value; renewal negotiations rely on partner-supplied numbers not our own attribution.",
    actionType: "Analyze",
    process: "media-plan-1, media-comp-pricing-1, media-sub-1",
    galleryOrder: 24,
    source: "seed",
    updatedAt: TS,
  },

  // ── Monetization (add 1 more so section has ≥2 UCs) ──────────────────────
  {
    id: "media-uc-25-content-roi-diagnostics",
    title: "Content ROI diagnostics (marquee titles)",
    items: [
      "Attribute paid conversion, retention, and ad revenue to each marquee title vs. its acquisition cost; identify winners for renewal and losers to trim.",
    ],
    tags: ["Media", "Monetization", "ROI", "Content"],
    businessArea: "Monetization",
    problemSignal:
      "Marquee-title ROI is estimated qualitatively; there is no consistent attribution model that ties paid conversion, retention, and ad revenue to a specific title.",
    actionType: "Analyze",
    process: "media-roi-1, media-cnt-1",
    galleryOrder: 25,
    source: "seed",
    updatedAt: TS,
  },

  // ── Content Teams (Workforce — editorial + ad-ops) ────────────────────────
  {
    id: "media-uc-26-editorial-productivity",
    title: "Editorial team productivity & throughput",
    items: [
      "Track editorial team throughput on homepage curation, category shelves, and launch merchandising; surface bottlenecks against release calendar.",
    ],
    tags: ["Media", "Editorial", "Workforce", "Productivity"],
    businessArea: "Content",
    problemSignal:
      "Editorial capacity is planned by gut; when launch calendar accelerates, homepage curation quality slips without a leading indicator.",
    actionType: "Analyze",
    process: "media-wf-editorial, media-cnt-3",
    galleryOrder: 26,
    source: "seed",
    updatedAt: TS,
  },
  {
    id: "media-uc-27-adops-team-workload",
    title: "Ad-ops team workload & campaign hygiene",
    items: [
      "Balance ad-ops workload across direct-IO, house-sold, and programmatic; flag campaigns approaching pacing risk before end-of-flight.",
    ],
    tags: ["Media", "Ad-Ops", "Workforce", "Campaign"],
    businessArea: "Content",
    problemSignal:
      "Ad-ops team capacity is opaque; pacing-risk campaigns surface after end-of-flight, not in time to reallocate impressions.",
    actionType: "Improve",
    process: "media-wf-adops, media-adops-1",
    galleryOrder: 27,
    source: "seed",
    updatedAt: TS,
  },
];

/** Media & OTT KPI registry — single "Media & OTT" category with 5 subject-area sections. */
export const MEDIA_KPI_REGISTRY_SEED = [
  {
    category: "Media & OTT",
    sections: [
      {
        name: "Trend & Intelligence",
        kpis: [
          {
            name: "Trend Momentum Score",
            desc: "Composite 4-week growth signal for a topic or theme across open-web and social sources; separates spikes from sustained trends.",
            active: true,
            seed: true,
            businessAreaId: "merchandise",
            subjectArea: "Trend & Intelligence",
            level: "",
            processMapping: ["media-trend-2"],
            unit: "Score",
            industryBenchmark: "Emerging metric; baseline weekly",
          },
          {
            name: "Trend Sustained Rate",
            desc: "Share of detected trends still active after 4 weeks; higher = better signal quality vs. viral spikes.",
            active: true,
            seed: true,
            businessAreaId: "merchandise",
            subjectArea: "Trend & Intelligence",
            level: "",
            processMapping: ["media-trend-2"],
            unit: "%",
            industryBenchmark: "Target > 40% by quarter 2",
          },
          {
            name: "Under-represented Themes Flagged",
            desc: "Count of high-momentum themes absent or thin in our current slate; feeds commissioning research shortlists.",
            active: true,
            seed: true,
            businessAreaId: "merchandise",
            subjectArea: "Trend & Intelligence",
            level: "",
            processMapping: ["media-scope-1"],
            unit: "Count",
            industryBenchmark: "Weekly review target 5-10",
          },
        ],
      },
      {
        name: "Engagement",
        kpis: [
          {
            name: "Monthly Active Viewers (MAV)",
            desc: "Unique authenticated viewers streaming at least once in the last 30 days across web, mobile, and connected TV.",
            active: true,
            seed: true,
            businessAreaId: "e-com",
            subjectArea: "Engagement",
            level: "",
            processMapping: ["media-eng-1"],
            unit: "Viewers",
            industryBenchmark: "India OTT bands: 25-70M per platform",
          },
          {
            name: "Average Watch Time per Session",
            desc: "Streaming minutes per session; separates casual sampling from committed viewing.",
            active: true,
            seed: true,
            businessAreaId: "e-com",
            subjectArea: "Engagement",
            level: "",
            processMapping: ["media-eng-1", "media-eng-2"],
            unit: "Minutes",
            industryBenchmark: "35-60 min movie catalog avg",
          },
          {
            name: "Content Completion Rate",
            desc: "Share of started plays finished (>= 90% duration); movie and series-episode granularity.",
            active: true,
            seed: true,
            businessAreaId: "e-com",
            subjectArea: "Engagement",
            level: "",
            processMapping: ["media-eng-1"],
            unit: "%",
            industryBenchmark: "55-75% Indian film catalog",
          },
          {
            name: "D7 / D30 Retention",
            desc: "Share of new signups returning to stream after 7 and 30 days; core habit-formation metric.",
            active: true,
            seed: true,
            businessAreaId: "e-com",
            subjectArea: "Engagement",
            level: "",
            processMapping: ["media-sub-1", "media-sub-2"],
            unit: "%",
            industryBenchmark: "D7 35-50%, D30 20-30%",
          },
          {
            name: "Subscriber Churn Rate",
            desc: "Monthly churn as % of paying base; tracked separately for monthly, quarterly, and annual plans.",
            active: true,
            seed: true,
            businessAreaId: "e-com",
            subjectArea: "Engagement",
            level: "",
            processMapping: ["media-sub-2"],
            unit: "%",
            industryBenchmark: "3-7% monthly SVOD",
          },
        ],
      },
      {
        name: "Content Performance",
        kpis: [
          {
            name: "Watch Hours (Top Titles)",
            desc: "Aggregate streaming hours per title over 7/28/90-day windows; primary content performance metric.",
            active: true,
            seed: true,
            businessAreaId: "merchandise",
            subjectArea: "Content Performance",
            level: "",
            processMapping: ["media-cnt-3"],
            unit: "Hours",
            industryBenchmark: "Marquee film 1-5M hrs in week 1",
          },
          {
            name: "Cost per Watch Hour",
            desc: "Fully loaded content spend (acquisition + originals + licensing) divided by watch hours delivered.",
            active: true,
            seed: true,
            businessAreaId: "merchandise",
            subjectArea: "Content Performance",
            level: "",
            processMapping: ["media-cnt-1", "media-cnt-3"],
            unit: "INR / hr",
            industryBenchmark: "Track vs. content P&L target",
          },
          {
            name: "Content ROI (Marquee Titles)",
            desc: "Modeled revenue attributed to a title (paid conversions + ad revenue) vs. its acquisition / production cost.",
            active: true,
            seed: true,
            businessAreaId: "merchandise",
            subjectArea: "Content Performance",
            level: "",
            processMapping: ["media-cnt-1", "media-cnt-3"],
            unit: "Ratio",
            industryBenchmark: ">1.2x for marquee acquisitions",
          },
          {
            name: "Genre Mix Share",
            desc: "Share of watch hours by genre (drama, action, comedy, thriller, family, romance) vs. our catalog mix.",
            active: true,
            seed: true,
            businessAreaId: "merchandise",
            subjectArea: "Content Performance",
            level: "",
            processMapping: ["media-cnt-2", "media-mkt-1"],
            unit: "%",
            industryBenchmark: "Rebalance quarterly",
          },
          {
            name: "Regional-Language Watch Share",
            desc: "Share of watch hours from Hindi vs. regional-language (Marathi, Tamil, Telugu, Bengali, Kannada) catalog.",
            active: true,
            seed: true,
            businessAreaId: "merchandise",
            subjectArea: "Content Performance",
            level: "",
            processMapping: ["media-cnt-2"],
            unit: "%",
            industryBenchmark: "Regional up-trend 2024-2026",
          },
        ],
      },
      {
        name: "Ad Yield",
        kpis: [
          {
            name: "Ad Fill Rate",
            desc: "Share of ad requests filled with a monetizable impression; core AVOD yield metric.",
            active: true,
            seed: true,
            businessAreaId: "finance",
            subjectArea: "Ad Yield",
            level: "",
            processMapping: ["media-adops-1"],
            unit: "%",
            industryBenchmark: "70-85% healthy",
          },
          {
            name: "eCPM",
            desc: "Effective revenue per thousand ad impressions blended across demand partners and formats.",
            active: true,
            seed: true,
            businessAreaId: "finance",
            subjectArea: "Ad Yield",
            level: "",
            processMapping: ["media-adops-2"],
            unit: "INR",
            industryBenchmark: "India CTV / OTT 120-350 INR",
          },
          {
            name: "Ad Completion Rate",
            desc: "Share of served ads viewed to 100% quartile; indicator of creative fatigue and pod length.",
            active: true,
            seed: true,
            businessAreaId: "finance",
            subjectArea: "Ad Yield",
            level: "",
            processMapping: ["media-adops-1"],
            unit: "%",
            industryBenchmark: ">85% for pre-roll",
          },
          {
            name: "Yield per Impression",
            desc: "Realized ad revenue per delivered impression after fees; use to compare demand-partner mix.",
            active: true,
            seed: true,
            businessAreaId: "finance",
            subjectArea: "Ad Yield",
            level: "",
            processMapping: ["media-adops-2"],
            unit: "INR",
            industryBenchmark: "Track vs. house-sold blend",
          },
          {
            name: "ARPU (Paid)",
            desc: "Average revenue per paying subscriber per month, blended across mobile-only, standard, and premium plans.",
            active: true,
            seed: true,
            businessAreaId: "support",
            subjectArea: "Plans & Bundles",
            level: "",
            processMapping: ["media-sub-1"],
            unit: "INR",
            industryBenchmark: "India SVOD 60-180 INR",
          },
        ],
      },
      {
        name: "Market Signals",
        kpis: [
          {
            name: "Share of Voice (Trade + Social)",
            desc: "Share of relevant conversation (trade press + social) attributable to our brand vs. named competitors.",
            active: true,
            seed: true,
            businessAreaId: "e-com",
            subjectArea: "Market Signals",
            level: "",
            processMapping: ["media-mkt-1"],
            unit: "%",
            industryBenchmark: "Rank vs. top 5 platforms",
          },
          {
            name: "Competitor Title Overlap",
            desc: "Share of our library also carried by named competitors; higher overlap = weaker exclusivity moat.",
            active: true,
            seed: true,
            businessAreaId: "e-com",
            subjectArea: "Market Signals",
            level: "",
            processMapping: ["media-mkt-1", "media-cnt-1"],
            unit: "%",
            industryBenchmark: "Target < 25% on top 500",
          },
          {
            name: "Category Watch-Hour Share",
            desc: "Modeled share of Indian OTT watch hours by genre / language cohort vs. competing platforms.",
            active: true,
            seed: true,
            businessAreaId: "e-com",
            subjectArea: "Market Signals",
            level: "",
            processMapping: ["media-mkt-1"],
            unit: "%",
            industryBenchmark: "Track quarterly",
          },
          {
            name: "Search Trend Index",
            desc: "Google Trends + platform search index for our brand and top titles vs. competitors, normalized 0-100.",
            active: true,
            seed: true,
            businessAreaId: "e-com",
            subjectArea: "Market Signals",
            level: "",
            processMapping: ["media-mkt-1", "media-mkt-2"],
            unit: "Index",
            industryBenchmark: "Weekly delta > 5 pts = signal",
          },
        ],
      },
      {
        name: "AEO / GEO",
        kpis: [
          {
            name: "AI Answer Presence Score",
            desc: "Share of tracked prompts across ChatGPT, Perplexity, Google AI Overviews, and Gemini where our brand or titles surface.",
            active: true,
            seed: true,
            businessAreaId: "e-com",
            subjectArea: "AEO / GEO",
            level: "",
            processMapping: ["media-mkt-2"],
            unit: "%",
            industryBenchmark: "Emerging metric; baseline this quarter",
          },
          {
            name: "Generative Search Referrals",
            desc: "Sessions attributed to referrer domains such as chat.openai.com, perplexity.ai, and Google AI Overviews.",
            active: true,
            seed: true,
            businessAreaId: "e-com",
            subjectArea: "AEO / GEO",
            level: "",
            processMapping: ["media-mkt-2"],
            unit: "Sessions",
            industryBenchmark: "Growth curve 3-8% mo/mo",
          },
          {
            name: "Brand Mention Rate (AI Answers)",
            desc: "Rate at which our brand is cited in generative answers per 100 tracked queries in our category.",
            active: true,
            seed: true,
            businessAreaId: "e-com",
            subjectArea: "AEO / GEO",
            level: "",
            processMapping: ["media-mkt-2"],
            unit: "Rate",
            industryBenchmark: "Rank vs. named competitors",
          },
          {
            name: "Content Discoverability Index",
            desc: "Composite of organic SERP visibility, AI-answer presence, and on-platform search CTR for tracked titles.",
            active: true,
            seed: true,
            businessAreaId: "e-com",
            subjectArea: "AEO / GEO",
            level: "",
            processMapping: ["media-mkt-2", "media-eng-2"],
            unit: "Index",
            industryBenchmark: "Track weekly",
          },
        ],
      },
    ],
  },
];
