/**
 * Default Media profile — scripted rich-reply panels for Playground chat.
 *
 * Panels implement the "explainability layer" requested by the customer AI
 * Content Intelligence definition PDF:
 *   - what is trending (topic / theme)
 *   - who is driving it (audience segment)
 *   - momentum signal (4-week growth, spike vs sustained)
 * plus a recommendations list that separates evidence, historical parallel,
 * and next-step recommendation.
 *
 * The two hero panels below (content_trend_intelligence and content_scoping)
 * are anchored to the two hero personas from the same PDF: the "Content
 * Intelligence Analyst" (Listen → Understand → Detect → Explain) and the
 * "Content Strategy Advisor" (Anticipate → Advise). Copy references those
 * stages + the Consumer / Culture / Competition dimensions so demo viewers
 * see the same terminology they briefed us with.
 *
 * Shape matches `PLAYGROUND_TOPIC_PANELS` (findings[3] + recommendations[]) so
 * the shared `PlaygroundRichReplyBody` can render these without any bespoke
 * component work.
 */

export const MEDIA_TOPIC_PANELS = {
  /**
   * UC-1 — Content Conversation & Trend Intelligence.
   * Mirrors the mythological-drama example from the AI Content Intelligence
   * definition PDF so the demo response reads exactly like the customer ask.
   */
  content_trend_intelligence: {
    findings: [
      {
        title: "What is trending",
        value: "Mythological drama reboots",
        change: "+4 wks",
        desc: "Sustained conversation growth across open-web and social for four consecutive weeks.",
      },
      {
        title: "Who is driving it",
        value: "Hindi · Tier-2",
        change: "Nostalgia",
        desc: "Hindi-speaking audiences in Tier-2 markets sharing nostalgia-led short-form clips.",
      },
      {
        title: "Signal type",
        value: "Sustained",
        change: "not a spike",
        desc: "Momentum score 78/100; sentiment +0.42; spread across 6 signal sources.",
      },
    ],
    recommendations: [
      "Evidence (Listen + Understand): 4-week growth on YouTube shorts + a rival network's successful mythology relaunch; source-linked chatter tracked across trade press, Instagram Reels, and open-web. Every insight is source-traceable.",
      "Historical parallel: our own mythology and family-drama slate has over-indexed on similar Consumer + Culture audience segments in prior seasons.",
      "Recommendation: brief content-development on a focused mythology-adjacent slate exploration; open a scoping session with the Content Strategy Advisor before commissioning.",
    ],
  },

  /**
   * UC-2 — Future Content Opportunity (Content Scoping).
   * Mirrors the competitive-cooking example from the PDF; separates fact,
   * inferred trend, and recommendation as the customer explicitly asked.
   * Uses "Content Strategy Advisor" persona and Anticipate → Advise stages.
   */
  content_scoping: {
    findings: [
      {
        title: "Emerging theme",
        value: "Competitive cooking formats",
        change: "6-mo rise",
        desc: "Interest in competitive cooking formats has risen steadily for six months on both trade and social signals.",
      },
      {
        title: "Historical parallel",
        value: "25-40 urban female",
        change: "over-index",
        desc: "Similar formats have historically performed strongly with 25-40 urban female audiences in our library.",
      },
      {
        title: "Under-represented",
        value: "3 formats",
        change: "in slate",
        desc: "Three related format variants are absent or thin in our current slate, a gap the Content Strategy Advisor has flagged.",
      },
    ],
    recommendations: [
      "Fact: 6-month sustained rise on competitive-cooking search + social signals; multiple regional-language sub-clusters visible in the Consumer signals.",
      "Inferred trend (Anticipate): trajectory suggests this stays elevated 3-6 months out on the current growth curve, decision support, not a hit prediction.",
      "Recommendation (Advise): mark this a content opportunity worth deeper research; open a scoping brief for content-development and consumer insights.",
    ],
  },

  /**
   * UC-1 quarter view — sustained-momentum watchlist across the 3 dimensions.
   * Distinct tiles from the week panel so users don't see repeated cards
   * when they cycle This week / This quarter on the same UC.
   */
  content_trend_intelligence_quarter: {
    findings: [
      {
        title: "Sustained themes",
        value: "6",
        change: "quarter-to-date",
        desc: "Six themes have cleared the 4-week sustained-momentum threshold across the Consumer, Culture, and Competition dimensions.",
      },
      {
        title: "Top audience segment",
        value: "Hindi Tier-2",
        change: "cross-theme",
        desc: "Hindi-speaking Tier-2 audiences are the strongest signal source across mythology, thriller, and cooking-format themes.",
      },
      {
        title: "Spike vs sustained",
        value: "5 / 1",
        change: "not a spike",
        desc: "5 of 6 themes are sustained; the viral dance-cameos spike is flagged separately as short-lived interest.",
      },
    ],
    recommendations: [
      "Evidence: 12+ weeks of open-web, social, and search signal history per theme; every ranking is source-traceable back to individual clips, posts, and trade articles.",
      "Historical parallel: sustained themes cluster around audience patterns our own mythology, family-drama, and regional catalogs have historically over-indexed on.",
      "Recommendation: hand the sustained watchlist to the Content Strategy Advisor for the Anticipate → Advise step; hold spikes separately for editorial signal, not commissioning.",
    ],
  },

  /**
   * UC-2 quarter view — commissioning-shortlist opportunities (not one hero
   * theme). Different tiles from the week panel so week vs quarter reads as
   * two distinct answers, not the same cooking example twice.
   */
  content_scoping_quarter: {
    findings: [
      {
        title: "Recommend for research",
        value: "3 themes",
        change: "quarter",
        desc: "Regional mythology (Marathi + Tamil), women-led mythology reboots, and youth-led courtroom drama, all sustained AND under-represented in our current slate.",
      },
      {
        title: "Historical fit",
        value: "Strong",
        change: "match slate",
        desc: "All three themes match Consumer + Culture patterns our historically successful shows have over-indexed on.",
      },
      {
        title: "Confidence framing",
        value: "Fact + Inferred",
        change: "labelled",
        desc: "The brief separates observed fact from inferred trajectory and the recommendation, clear decision support for commissioning research.",
      },
    ],
    recommendations: [
      "Fact: three themes cleared both the sustained-momentum threshold AND the slate-gap check this quarter, with 12+ weeks of source-linked evidence.",
      "Inferred trend (Anticipate): trajectory extrapolation suggests all three stay elevated 3-6 months forward on their current growth curves.",
      "Recommendation (Advise): open commissioning research shortlists for all three themes, decision support for the Head of Content Strategy, not a hit prediction.",
    ],
  },

  /**
   * UC-3 / UC-4 — Competitor OTT & market landscape.
   */
  competitor_landscape: {
    findings: [
      {
        title: "Weekly releases",
        value: "12 vs 8",
        change: "our slate",
        desc: "Rival OTT platforms shipped 12 marquee releases vs. our 8 across Hindi + regional slate this week.",
      },
      {
        title: "Genre gap",
        value: "Thriller",
        change: "-3 titles",
        desc: "Thriller-genre depth is three titles behind the top two platforms on Hindi catalog.",
      },
      {
        title: "Pricing move",
        value: "-19% mobile plan",
        change: "1 competitor",
        desc: "One competitor dropped mobile-only plan by 19% mid-week; telco-bundle activity ticked up.",
      },
    ],
    recommendations: [
      "Evidence: release cadence + plan pricing scraped from competitor OTT landing pages, app-store listings, and trade releases in the last 7 days.",
      "Historical parallel: prior mobile-plan cuts triggered a 6-8 week ARPU compression window on affected cohorts.",
      "Recommendation: brief programming on thriller-catalog acquisition; run a scenario in the plan & pricing optimization use case before responding.",
    ],
  },

  /**
   * UC-3 quarter view — category watch-hour share vs. top 5 competitors.
   */
  competitor_landscape_quarter: {
    findings: [
      {
        title: "Category rank",
        value: "#2",
        change: "of 5",
        desc: "Stable at #2 in India OTT category watch-hour share; leader gap steady at 6 pts quarter-to-date.",
      },
      {
        title: "Regional-language gap",
        value: "-5 titles",
        change: "widening",
        desc: "Regional-language originals gap vs. the top 2 rivals has widened from 3 to 5 titles across the quarter.",
      },
      {
        title: "Star-cast pull",
        value: "3 rivals",
        change: "ahead",
        desc: "Three rivals have secured 2-3 star-cast marquee deals apiece in the quarter; our slate has one.",
      },
    ],
    recommendations: [
      "Evidence: quarterly BARC + OTT watch-hour panels + trade-press deal announcements across the last 90 days.",
      "Historical parallel: sustained regional-language investment has historically closed 2-3 pts of share loss per quarter in prior cycles.",
      "Recommendation: prioritise a Marathi + Tamil catalog build this quarter; open a star-cast deal-search brief for 2 tent-pole slots.",
    ],
  },

  /**
   * UC-4 week view — daily landscape scan (distinct from UC-3 which is the
   * benchmark). Focus: pricing + bundle + upcoming-premiere signals.
   */
  market_landscape_week: {
    findings: [
      {
        title: "Pricing move",
        value: "-19% mobile plan",
        change: "same-day",
        desc: "Rival mobile-only plan cut by 19% mid-week; ARPU compression risk on affected cohorts starts within 48 hours.",
      },
      {
        title: "Bundle expansion",
        value: "2 telcos",
        change: "Tier-2",
        desc: "Two telco-bundle expansions announced this week focused on Tier-2 markets where our regional catalog is thinnest.",
      },
      {
        title: "Overlapping premieres",
        value: "3 titles",
        change: "vs our marquee",
        desc: "Three category-leader premieres overlap the launch window of our upcoming marquee release.",
      },
    ],
    recommendations: [
      "Evidence: competitor OTT landing pages, app-store listings, telco press releases, and trade press releases scraped over the last 7 days.",
      "Historical parallel: prior mobile-plan cuts triggered a 6-8 week ARPU compression window on affected cohorts without a defensive counter.",
      "Recommendation: brief marketing on a same-day defensive counter; run a scenario in the plan & pricing optimization use case before responding.",
    ],
  },

  /**
   * UC-4 quarter view — sustained bundling pressure + share trajectory.
   */
  market_landscape_quarter: {
    findings: [
      {
        title: "Bundle pressure",
        value: "6 telco deals",
        change: "quarter",
        desc: "Six telco-bundle deals announced by category leaders this quarter; sustained pressure on our mobile-only ARPU.",
      },
      {
        title: "Category share loss",
        value: "-1.2 pts",
        change: "concentrated",
        desc: "Category watch-hour share loss concentrates on 2 of 5 named competitors, not evenly across the field.",
      },
      {
        title: "Response window",
        value: "6-8 wks",
        change: "before cascade",
        desc: "ARPU compression typically cascades to churn 6-8 weeks after initial pricing move if no counter is deployed.",
      },
    ],
    recommendations: [
      "Evidence: quarterly landscape aggregation of pricing changes, bundle deals, and share movement across the top 5 India OTT platforms.",
      "Historical parallel: prior bundling waves resolved fastest with content differentiation + selective price defence, not blanket price matching.",
      "Recommendation: quarterly plan-mix scenario in the plan & pricing UC; sustained regional-language content investment as the durable moat.",
    ],
  },

  /**
   * UC-5 week view — regional-language demand + acquisition gap.
   */
  content_gap_week: {
    findings: [
      {
        title: "Demand outpacing",
        value: "Marathi + Tamil",
        change: "this week",
        desc: "Regional-language demand this week outpaces our acquisition cadence on Marathi thrillers and Tamil family drama.",
      },
      {
        title: "Rival library depth",
        value: "-3 to -5 titles",
        change: "our slate",
        desc: "Competitor libraries out-index ours by 3 to 5 titles in these two regional-language pockets.",
      },
      {
        title: "Slate gap flag",
        value: "2 themes",
        change: "commissioning",
        desc: "Two themes flagged for content-development scoping in this week's brief.",
      },
    ],
    recommendations: [
      "Evidence: regional-language watch-hour delta by state + competitor library depth crawl.",
      "Historical parallel: closing similar gaps in prior cycles took 6-8 weeks with a mixed acquisition + originals plan.",
      "Recommendation: brief acquisitions on the two flagged themes; open scoping for one regional originals concept.",
    ],
  },

  /**
   * UC-5 quarter view — Hindi thriller depth + women-led mythology.
   */
  content_gap_quarter: {
    findings: [
      {
        title: "Hindi thriller depth",
        value: "-3 titles",
        change: "quarter",
        desc: "Hindi thriller catalog depth 3 titles behind the top two rivals; largest single content gap this quarter.",
      },
      {
        title: "Women-led mythology",
        value: "1 in dev",
        change: "0 in market",
        desc: "Women-led mythology theme thin: one title in development, none in market, matches historical audience over-index.",
      },
      {
        title: "Regional cricket",
        value: "Absent",
        change: "adjacencies",
        desc: "Regional-language cricket-adjacent programming absent from our current slate despite pan-India reach signal.",
      },
    ],
    recommendations: [
      "Evidence: quarterly library-depth comparison + sustained-momentum theme trajectory over 12 weeks.",
      "Historical parallel: prior thriller acquisition sweeps closed 60-70% of comparable gaps within a single commissioning cycle.",
      "Recommendation: commissioning shortlist prioritises the three highest-momentum under-represented themes this quarter.",
    ],
  },

  /**
   * UC-11 week view — churn risk cohort + save-offer window.
   */
  churn_predictor_week: {
    findings: [
      {
        title: "At-risk cohort",
        value: "42K",
        change: "48h window",
        desc: "42K monthly-cohort subscribers at elevated churn risk within their renewal window over the next 48 hours.",
      },
      {
        title: "Renewal delta",
        value: "+3.4 pts",
        change: "vs trailing",
        desc: "Post-marquee-title churn is 3.4 pts hotter than the trailing average on the affected cohort.",
      },
      {
        title: "Save-offer projected",
        value: "68% retain",
        change: "content bundle",
        desc: "Modelled save-offer with content bundle projected to retain 68% of at-risk cohort vs. 34% baseline no-offer.",
      },
    ],
    recommendations: [
      "Evidence: renewal-cohort churn deltas + content-affinity clustering + 12-week prior save-offer response rates.",
      "Historical parallel: post-marquee finish gaps historically compress churn back to baseline within 4 weeks if content pacing recovers.",
      "Recommendation: stage save offers immediately for the 42K cohort; brief content pacing on the next release cadence.",
    ],
  },

  /**
   * UC-11 quarter view — structural churn drivers + levers.
   */
  churn_predictor_quarter: {
    findings: [
      {
        title: "Structural driver 1",
        value: "Content pacing",
        change: "45% of churn",
        desc: "Marquee-title finish gaps account for ~45% of voluntary churn this quarter, the largest structural driver.",
      },
      {
        title: "Structural driver 2",
        value: "Mobile-plan competition",
        change: "30% of churn",
        desc: "Competitor mobile-plan price cuts account for ~30% of voluntary churn on price-sensitive cohorts.",
      },
      {
        title: "Levers with impact",
        value: "2",
        change: "quantified",
        desc: "Content-pacing calendar + save-offer eligibility windows are the two levers with quantified churn-reduction impact this quarter.",
      },
    ],
    recommendations: [
      "Evidence: 12-week structural attribution of voluntary churn across content-pacing, plan-competition, and life-event cohorts.",
      "Historical parallel: closing content-pacing gaps + selective save offers historically reduced quarterly churn by 1-2 pts on affected cohorts.",
      "Recommendation: brief content strategy on release-cadence gaps; extend save-offer eligibility windows for price-sensitive cohorts by 7 days.",
    ],
  },

  /**
   * UC-13 week view — connected TV fill drop.
   */
  ad_yield_week: {
    findings: [
      {
        title: "Connected TV fill",
        value: "72%",
        change: "-8 pts vs target",
        desc: "AVOD fill on connected TV averaged 72% over the last 24 hours vs. an 80% target: largest weekly slippage.",
      },
      {
        title: "Mobile eCPM",
        value: "-5%",
        change: "mid-week",
        desc: "Mobile eCPM slipped 5% mid-week; demand-partner mix skewed after last week's floor adjustment.",
      },
      {
        title: "VPAID error rate",
        value: "3.1%",
        change: "+0.8 pts",
        desc: "VPAID timeout / error rate elevated on 2 demand partners this week, creative fatigue on 3 top campaigns.",
      },
    ],
    recommendations: [
      "Evidence: hourly fill / eCPM by device + demand-partner from the ad server; VPAID timeout logs cross-referenced with creative rotation.",
      "Historical parallel: header-bidding waterfall audits + demand-partner rebalance have recovered similar slips within 48 hours without floor changes.",
      "Recommendation: audit the waterfall + rebalance partner priorities today; hold floor changes until yield stabilises.",
    ],
  },

  /**
   * UC-13 quarter view — quarterly ad-yield trend + structural fixes.
   */
  ad_yield_quarter: {
    findings: [
      {
        title: "Blended eCPM trend",
        value: "Flat",
        change: "vs plan",
        desc: "Blended eCPM has held flat quarter-over-quarter while AVOD watch hours grew 12%, structural yield gap.",
      },
      {
        title: "CTV drift",
        value: "-6 pts",
        change: "sustained",
        desc: "Connected-TV fill has drifted 6 pts below target on a sustained basis, not a one-week spike.",
      },
      {
        title: "House-sold blend",
        value: "22%",
        change: "defensive",
        desc: "House-sold blend at 22% keeps quarterly yield defensible during programmatic dips.",
      },
    ],
    recommendations: [
      "Evidence: quarterly attribution of fill, eCPM, and VPAID health by device + demand partner + creative type.",
      "Historical parallel: quarterly waterfall reviews + creative-fatigue rules have closed 3-5 pt eCPM gaps in prior quarters.",
      "Recommendation: schedule a quarterly demand-partner review + creative-fatigue policy; hold house-sold minimum at 20-25% as yield floor.",
    ],
  },

  /**
   * UC-10 — AEO / GEO brand visibility tracker.
   */
  aeo_geo_visibility: {
    findings: [
      {
        title: "AI answer presence",
        value: "34%",
        change: "+6 pts wk",
        desc: "Our brand or titles cited in 34% of tracked prompts on ChatGPT, Perplexity, Gemini, and Google AI Overviews.",
      },
      {
        title: "Generative referrals",
        value: "18.4K",
        change: "+11% wk",
        desc: "Sessions attributed to chat.openai.com, perplexity.ai, and Google AI Overviews last 7 days.",
      },
      {
        title: "Category rank",
        value: "#2",
        change: "of 5",
        desc: "Second among named India OTT competitors on category prompts; leader gap 9 pts.",
      },
    ],
    recommendations: [
      "Evidence: 240 tracked prompts across four AI engines; source citations logged per query with time-stamped snapshots.",
      "Historical parallel: category leaders that closed AEO gaps in the last 12 months saw 8-14% incremental discovery volume.",
      "Recommendation: brief SEO and editorial on the 9-pt gap; open the content discoverability process to prioritize title-metadata and long-form article work.",
    ],
  },

  /**
   * UC-10 quarter view — AEO / GEO trend + leader gap trajectory.
   */
  aeo_geo_visibility_quarter: {
    findings: [
      {
        title: "Presence trend",
        value: "+6 pts",
        change: "quarter",
        desc: "AI-answer presence score has trended +6 pts over the quarter; growth concentrated on brand + top-10 title prompts.",
      },
      {
        title: "Leader gap",
        value: "9 pts",
        change: "widened from 5",
        desc: "Gap to the category leader has widened from 5 pts to 9 pts over the quarter, the leader is investing faster.",
      },
      {
        title: "Fastest lever",
        value: "Editorial + metadata",
        change: "top 5 titles",
        desc: "Sustained editorial + title-metadata investment on the top 5 under-cited titles closes the gap fastest per prior model runs.",
      },
    ],
    recommendations: [
      "Evidence: 12-week quarterly aggregation of tracked-prompt presence, generative-search referrals, and citation source patterns.",
      "Historical parallel: 3 category leaders that ran sustained metadata + editorial programmes closed 5-8 pt AEO gaps within a single quarter.",
      "Recommendation: commit editorial + metadata sprints on the top 5 under-cited titles this quarter; brief SEO leadership on the widening trajectory.",
    ],
  },
};

/** Default assistant acknowledgement when the request doesn't map to a topic. */
export const PLAYGROUND_MEDIA_DEFAULT_ACK =
  "I can analyze audience-conversation trends, competitor moves, OTT engagement, ad-yield, and AEO / GEO visibility. Ask what content themes are trending, which trends are spikes vs sustained, where competitor libraries out-index ours, or how our brand surfaces in AI answer engines.";

function textMatchesKeyword(lower, keyword) {
  const k = keyword.trim().toLowerCase();
  if (!k) return false;
  if (k.includes(" ")) return lower.includes(k);
  try {
    return new RegExp(`\\b${k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`, "i").test(lower);
  } catch {
    return lower.includes(k);
  }
}

const MEDIA_RULES = [
  {
    keywords: [
      "trending",
      "trends",
      "trend",
      "topics",
      "conversation",
      "momentum",
      "spike",
      "sustained",
      "audience talking",
      "mythology",
      "mythological",
      "genre gaining",
      "reality shows",
      "growth",
    ],
    topic: "content_trend_intelligence",
    intro:
      "Conversation around mythological drama reboots has grown for four straight weeks, driven mainly by nostalgia-led social clips and a rival network's successful relaunch. Strongest signals come from Hindi-speaking audiences in Tier-2 markets. Similar themes have historically performed well in our mythology and family-drama slate.",
  },
  {
    keywords: [
      "content opportunity",
      "content scoping",
      "commissioning",
      "should we explore",
      "commissioning research",
      "future content",
      "themes emerging",
      "under-represented",
      "under represented",
      "resemble our",
      "warrant content research",
      "cooking format",
      "trend continues",
      "3-6 months",
    ],
    topic: "content_scoping",
    intro:
      "Interest in competitive cooking formats has risen steadily for six months. Similar formats have historically performed strongly with 25-40 urban female audiences. On the current trajectory this is a potential content opportunity worth deeper research.",
  },
  {
    keywords: [
      "competitor",
      "competing",
      "rival",
      "netflix",
      "prime video",
      "hotstar",
      "jiocinema",
      "sonyliv",
      "market landscape",
      "compare",
      "benchmark",
      "genre gap",
      "star cast",
    ],
    topic: "competitor_landscape",
    intro:
      "Competing OTT platforms shipped 12 marquee releases this week vs. our 8; thriller catalog depth is 3 titles behind the top two rivals on Hindi. One competitor cut their mobile-only plan by 19% mid-week; ARPU compression risk on affected cohorts.",
  },
  {
    keywords: [
      "aeo",
      "geo",
      "ai answer",
      "chatgpt",
      "perplexity",
      "google ai",
      "gemini",
      "generative search",
      "answer engine",
      "brand mention",
      "brand visibility",
      "discoverability",
    ],
    topic: "aeo_geo_visibility",
    intro:
      "Our brand or titles are cited in 34% of tracked prompts across ChatGPT, Perplexity, Gemini, and Google AI Overviews. Generative-search referrals climbed 11% week-over-week. We rank #2 among named India OTT competitors; the category leader is 9 points ahead.",
  },
];

/** Match a user question against media topic panels. Returns null if no match. */
export function tryMatchMediaPlaygroundReply(userText) {
  const lower = String(userText || "").toLowerCase();
  for (const rule of MEDIA_RULES) {
    if (rule.keywords.some((k) => textMatchesKeyword(lower, k))) {
      return {
        text: rule.intro,
        meta: {
          variant: "playground-rich",
          topic: rule.topic,
          panel: MEDIA_TOPIC_PANELS[rule.topic],
        },
      };
    }
  }
  return null;
}
