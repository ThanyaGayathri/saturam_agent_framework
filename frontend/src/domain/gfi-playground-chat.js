/**
 * GFI (Gourmet Foods International) - creative scripted replies for Playground chat.
 * Focus: Tier 1 use cases - (1) perishable demand forecasting, (2) inter-DC rebalancing.
 */

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

/** Rich panels reuse existing Playground topic keys where the narrative fits. */
const GFI_RULES = [
  // ── Perishable demand forecasting (Tier 1 #1) ──
  {
    keywords: ["perishable forecast", "forecast cheese", "forecast for cheese", "charcuterie forecast", "short shelf", "sell through forecast"],
    topic: "inventory",
    intro:
      "Across GFI’s Southeast DCs, blended models are flagging a +6% upside vs. last month on soft-ripened cheese after two regional tasting events, while prepared deli salads show a -3% bias you may want to trim before Tuesday’s cut.",
  },
  {
    keywords: ["thanksgiving", "holiday pull", "q4 peak", "seasonal spike"],
    topic: "inventory",
    intro:
      "Holiday pull-forward is hitting 11-14 days earlier than the five-year specialty average for your turkey-adjacent SKU cluster, recommend staging incremental reefers at Atlanta and Dallas before the rush locks carriers.",
  },
  {
    keywords: ["forecast accuracy", "wmape", "bias", "forecast vs actual"],
    topic: "inventory",
    intro:
      "Network WMAPE on perishables improved to 14.2% after vendor calendar enrichment (was 17.8%). Biggest residual error is on small-batch imports with <90-day history; those SKUs are still on a conservative intercept until NPI scores stabilize.",
  },
  {
    keywords: ["organic almond milk", "almond milk", "velocity outliers"],
    topic: "inventory",
    intro:
      "That plant-based dairy cluster is an outlier: two DCs show 2.1-weeks cover while Charlotte is chasing air fills; suggest a cross-dock from Decatur before you authorize a spot buy.",
  },
  {
    keywords: ["11340", "eleven thousand", "sku count", "six thousand brands"],
    topic: "inventory",
    intro:
      "With 11,340+ active SKUs and 6,000+ brands, GFI’s forecast mesh auto-clusters slow-history artisans with peer curves by temperature lane and region, reducing cold-start error without drowning buyers in manual overrides.",
  },
  {
    keywords: ["institut du fromage", "fromage", "education", "training accounts"],
    topic: "inventory",
    intro:
      "Accounts touched by education programming are running 8% higher true demand on affinage SKUs; your forecast engine is ingesting that cohort separately so you don’t understock post-class ordering waves.",
  },
  {
    keywords: ["supplier delay", "late shipment", "fill rate risk", "artisan small batch"],
    topic: "inventory",
    intro:
      "Upstream jitter from two artisan creameries is pushing a 40-hour lag on replenishment; forecasts have already shifted safety stock at NJ and Denver through Friday; no customer fill-rate breach projected if transfers release tonight.",
  },
  {
    keywords: ["waste", "spoilage forecast", "shrink prediction"],
    topic: "inventory",
    intro:
      "Probabilistic spoilage curves suggest $180K recovery if you pull forward markdowns on the blue-vein set aging past day-45, versus ~$60K write-off if inventory ages through the weekend without action.",
  },
  {
    keywords: ["machine learning", "model ensemble", "ai forecast"],
    topic: "inventory",
    intro:
      "GFI’s ensemble weights weather, regional foodservice indices, and your B2B order cadence; on perishables it favors gradient boosting for 1-14 day horizons and simpler baselines beyond that where noise dominates.",
  },
  {
    keywords: ["atlanta", "decatur", "southeast dc"],
    topic: "inventory",
    intro:
      "Southeast spine (Decatur hub outward) is tracking 1.2σ above baseline on specialty cheese, a good moment to publish a capacity note to field sales so opportunistic deals don’t blow daily pick ceilings.",
  },
  // ── Inter-DC inventory rebalancing (Tier 1 #2) ──
  {
    keywords: ["inter-dc", "inter dc", "dc transfer", "transfer between dc", "rebalance", "rebalancing"],
    topic: "inventory",
    intro:
      "Rebalancing engine sees 3,400 cases of misaligned cover: Denver has 31 DIOH on the Brie program while Edison is at 4.2; proposal is a chilled transfer on tomorrow night’s milk run with a net margin save of ~$42K vs. spot PO.",
  },
  {
    keywords: ["brie", "denver", "new jersey", "edison", "nj stockout"],
    topic: "inventory",
    intro:
      "Classic footprint mismatch: Front Range is long soft-ripened, Mid-Atlantic is chasing allocation. I’ve staged a two-stop consolidated move (Denver → Kansas City → Edison) within temperature spec; ETA protects both DC cut times.",
  },
  {
    keywords: ["own fleet", "gfi fleet", "gfi truck", "driver tenure"],
    topic: "orders",
    intro:
      "Your owned-fleet constraint set is respected; 10+ year driver averages on core lanes mean we’re not over-promising backhauls. Tonight’s plan uses only verified reefer hours; anything else queues to broker backup with surcharge flags.",
  },
  {
    keywords: ["temperature zone", "multi temp", "multi-temperature", "reefer", "frozen chill"],
    topic: "orders",
    intro:
      "Mixed temp transfers are split at pick: frozen never rides with chilled except on approved dual-compartment rigs; this rebalance pack is 100% compatible with your compliance matrix and FSMA chain-of-custody logging.",
  },
  {
    keywords: ["emergency po", "split shipment", "split ship"],
    topic: "orders",
    intro:
      "Routing everything from one DC would force a split on 12% of lines due to OOS cold-chain; your optimal answer is a primary DC ship plus a single-leg transfer for the gap SKUs; that clears the customer in one appointment window.",
  },
  {
    keywords: ["kc", "kansas city", "cross dock", "cross-dock"],
    topic: "inventory",
    intro:
      "KC is acting as swing capacity this week: inbound from Denver lands pre-dawn, cross-dock window is 90 minutes, outbound to Edison locks before the lunch rush; no inventory ages past freshness guardrails.",
  },
  {
    keywords: ["working capital", "cash tied up", "stranded inventory"],
    topic: "inventory",
    intro:
      "Network working-capital snapshot: ~$7.4M sits in >21-day cover on specialty SKUs eligible for transfer; executing the top 18 lanes this week frees roughly $2.1M cash without touching service levels.",
  },
  {
    keywords: ["sysco", "chefs warehouse", "broadline", "competitor steal"],
    topic: "inventory",
    intro:
      "Where you’re long and a competitor is tight regionally, proactive rebalance reduces the odds of a swap-out on the account, especially on hero SKUs where Chef’s Warehouse has been promotional in the last 30 days.",
  },
  {
    keywords: ["14 dc", "fourteen dc", "14 distribution", "network dc"],
    topic: "inventory",
    intro:
      "With 14 DCs, the graph search space gets big fast; your solver is capping feasible paths at three hops, weighted by temp compliance, labor slack, and customer promise time. Top suggestion batch updates every 15 minutes during peaks.",
  },
  // ── Cross-cutting GFI context ──
  {
    keywords: ["fsma", "traceability", "lot trace", "204"],
    topic: "inventory",
    intro:
      "Every transfer lot I’m proposing carries pre-linked trace IDs; release notes for compliance can attach to the move manifest so FSMA readiness stays continuous, not a Friday spreadsheet scramble.",
  },
  {
    keywords: ["infra", "independent retailer", "co-op"],
    topic: "inventory",
    intro:
      "INFRA-facing doors are overweight on slow movers in two regions; before you cascade transfers, flag any SKU tied to co-op promos so we don’t starve a member flyer.",
  },
  {
    keywords: ["tariff", "import cost", "canada mexico", "border"],
    topic: "inventory",
    intro:
      "Tariff shock scenarios are folded into reorder points on affected import families; forecast bias is nudged +4% safety on high-risk harmonized codes until your trade desk publishes new landed costs.",
  },
];

/**
 * @param {string} userText
 * @returns {{ text: string, meta: { variant: string, topic: string } } | null}
 */
export function tryMatchGfiPlaygroundReply(userText) {
  const lower = userText.toLowerCase();
  for (const rule of GFI_RULES) {
    if (rule.keywords.some((k) => textMatchesKeyword(lower, k))) {
      return {
        text: rule.intro,
        meta: { variant: "playground-rich", topic: rule.topic },
      };
    }
  }
  return null;
}
