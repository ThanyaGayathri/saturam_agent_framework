"""PA07 -- curate the Middleby RFQ extracts into the world Middleby lives in (K26).

The four extracts as delivered are a generator's: Indian territories and states, Indian
rep names, Dutch customer names, consumer brands (Toyota, Levi), placeholder products
("Widget Quantum"), word-shuffled labels ("Chosen Competitor" / "Competitor Chosen",
"India East" / "East India"), a ``quote_month`` that never matches ``quote_date`` and a
``quote_status`` that contradicts ``pipeline_stage``. Middleby is a US commercial-kitchen
manufacturer (Elgin, Illinois) selling through reps and dealers.

This rewrites the four files deterministically -- every choice keyed on an id, so two runs
give the same world -- and nothing else moves: ids, dates, values, stages, outcomes, the
links between sessions, chats, quotes and accounts all stay. What changes:

* territories -> five US regions (Northeast, Southeast, Midwest, West, Central), one per
  account, with a US state inside it; the quote's territory and state follow the account,
  and the two spellings of each Indian territory fold into one region;
* reps -> twenty US names, four per region; an account keeps one rep;
* customer names -> business names by segment (a dealer, a regional chain, a hospital
  dining service); consumers keep a person's name;
* brands -> Middleby brands by category (Pitco fryers, Blodgett and TurboChef ovens,
  Southbend ranges, Star griddles, Follett refrigeration, Middleby Marshall conveyors);
  products named brand + model; SKUs prefixed by category;
* lost reasons, chat types and channels -> one canonical spelling each;
* ``quote_month`` = the month of ``quote_date``; ``quote_status`` = the stage's status.

    .venv/bin/python cookbook/94_retail/PA07_middleby_us_curation.py --dir ~/Downloads --out ~/Downloads/middleby_us
    .venv/bin/python cookbook/94_retail/PA04_middleby_rfq_load.py --dir ~/Downloads/middleby_us

Then recompute the KPIs (PA05 / PA06 ``--compute-only``) and rerun the use cases.
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
from pathlib import Path
from typing import Dict, List, Sequence

TABLES = ("dc_rfq_quotes", "dc_chat_inquiries", "dc_web_sessions", "dc_customers")

REGIONS = ("Northeast", "Southeast", "Midwest", "West", "Central")
TERRITORY_FROM_INDIA = {
    "india east": "Northeast",
    "east india": "Northeast",
    "india north": "Midwest",
    "north india": "Midwest",
    "india west": "West",
    "west india": "West",
    "india south": "Southeast",
    "south india": "Southeast",
}
CUSTOMER_TERRITORY = {
    "north": "Midwest",
    "south": "Southeast",
    "east": "Northeast",
    "west": "West",
    "central": "Central",
}
STATES = {
    "Northeast": ("NY", "NJ", "PA", "MA", "CT"),
    "Southeast": ("FL", "GA", "NC", "TN", "VA"),
    "Midwest": ("IL", "OH", "MI", "WI", "MN"),
    "West": ("CA", "WA", "OR", "AZ", "CO"),
    "Central": ("TX", "OK", "MO", "KS", "LA"),
}
REPS = {
    "Northeast": ("M. Sullivan", "J. Romano", "A. Fitzgerald", "K. Brennan"),
    "Southeast": ("D. Whitaker", "S. Carter", "L. Hernandez", "T. Boone"),
    "Midwest": ("R. Novak", "P. Lindqvist", "C. Okafor", "B. Schroeder"),
    "West": ("E. Nakamura", "J. Alvarez", "M. Chen", "H. Delgado"),
    "Central": ("W. Travis", "N. Beaumont", "G. Kowalski", "R. Ortega"),
}

BRANDS = {
    "Ovens": ("Blodgett", "TurboChef", "Middleby Marshall", "Lincoln"),
    "Fryers": ("Pitco", "Pitco", "Perfect Fry", "Pitco"),
    "Ranges": ("Southbend", "Jade", "Southbend", "Bakers Pride"),
    "Griddles": ("Star", "Toastmaster", "Wells", "Star"),
    "Steamers": ("Southbend", "Market Forge", "Southbend", "Market Forge"),
    "Refrigeration": ("Follett", "Carter-Hoffmann", "Taylor", "Follett"),
    "Conveyor Systems": ("Middleby Marshall", "Lincoln", "CTX", "Nieco"),
}
MODELS = {
    "Ovens": (
        "DFG-100 Convection Oven",
        "i5 Rapid Cook Oven",
        "PS540 Conveyor Oven",
        "1600 Series Impinger",
    ),
    "Fryers": (
        "SG14 Gas Fryer",
        "SE14 Electric Fryer",
        "PFC 570 Ventless Fryer",
        "SSH55 Solstice Fryer",
    ),
    "Ranges": (
        "S-Series 6-Burner Range",
        "JTRH Titan Range",
        "P-Series Platinum Range",
        "BPHP Countertop Range",
    ),
    "Griddles": (
        "Ultra-Max Gas Griddle",
        "TMGM Griddle",
        "G-Series Griddle",
        "Star-Max Griddle",
    ),
    "Steamers": (
        "SimpleSteam Convection Steamer",
        "ST-10E Sterilmatic Steamer",
        "EZ-3 Steamer",
        "Altair II Steamer",
    ),
    "Refrigeration": (
        "Symphony Plus Ice & Water",
        "CD Series Holding Cabinet",
        "C712 Soft Serve",
        "Horizon Elite Ice Machine",
    ),
    "Conveyor Systems": (
        "PS360 Series Conveyor",
        "1100 Series Conveyor",
        "DZ33 Conveyor",
        "Broiler Conveyor 1021",
    ),
}
SKU_PREFIX = {
    "Ovens": "OVN",
    "Fryers": "FRY",
    "Ranges": "RNG",
    "Griddles": "GRD",
    "Steamers": "STM",
    "Refrigeration": "REF",
    "Conveyor Systems": "CNV",
}

BUSINESS_NAMES = {
    "Regional Chain": (
        "Harbor Grill Group",
        "Copper Kettle Kitchens",
        "Blue Ridge Eateries",
        "Prairie Table Restaurants",
        "Sunbelt Burger Co.",
        "Lakeshore Diners",
        "Redwood Taphouse Group",
        "Magnolia Kitchen Collective",
        "Northwind Brewhouse",
        "Cedar & Salt Restaurants",
    ),
    "Independent Restaurant": (
        "Maple & Vine Bistro",
        "The Iron Skillet",
        "Rosa's Trattoria",
        "Pier 9 Seafood",
        "Juniper Kitchen",
        "Old Mill Steakhouse",
        "Saffron Table",
        "The Corner Smokehouse",
        "Willow Creek Café",
        "Bayou Blue Kitchen",
    ),
    "Dealer / Distributor": (
        "Summit Foodservice Equipment",
        "Keystone Restaurant Supply",
        "Gulf Coast Kitchen Supply",
        "Great Lakes Equipment Co.",
        "Pacific Foodservice Distributors",
        "Heartland Restaurant Equipment",
        "Atlantic Commercial Kitchens",
        "Frontier Supply Partners",
        "Metro Culinary Equipment",
        "Bluewater Distribution",
    ),
    "Foodservice Consultant": (
        "Cornerstone Foodservice Design",
        "Culinary Systems Advisory",
        "Kitchen Works Consulting",
        "Hearth & Line Design Group",
        "Northstar Foodservice Planning",
        "Vantage Kitchen Consultants",
        "Meridian Hospitality Design",
        "Pinnacle Foodservice Studio",
        "Compass Culinary Advisors",
        "Trident Kitchen Planning",
    ),
    "Institutional (Healthcare/Education)": (
        "Lakeview Medical Center Dining",
        "Riverbend University Dining",
        "St. Anne's Hospital Nutrition",
        "Oakmont School District Nutrition",
        "Mercy General Food Services",
        "Crestview College Dining",
        "Valley Regional Hospital Dining",
        "Pine Hill Senior Living",
        "Harborview Community College",
        "Summit Ridge Medical Dining",
    ),
    "National Account": (
        "Northstar Hospitality",
        "Evergreen Hotels & Resorts",
        "Liberty Stadium Concessions",
        "Grand Meridian Hotels",
        "Coastline Cruise Catering",
        "Summit Peak Resorts",
        "Apex Entertainment Venues",
        "Horizon Airport Concessions",
        "Redstone Casino Group",
        "Beacon Hospitality Partners",
    ),
    "Corporate": (
        "Meridian Corporate Dining",
        "Ironclad Campus Café",
        "Tallgrass Employee Dining",
        "Skyline Tower Cafeteria",
        "Granite Corporate Kitchens",
        "Beacon Hill Office Dining",
        "Clearwater Campus Catering",
        "Silverline Corporate Services",
        "Cascade Corporate Café",
        "Foundry Workplace Dining",
    ),
    "SMB": (
        "Riverside Café",
        "Main Street Deli",
        "Sunrise Bakery",
        "The Daily Grind",
        "Little Oak Pizzeria",
        "Harbor Point Grill",
        "Mesa Verde Taqueria",
        "Birchwood Bagels",
        "Cottonwood Diner",
        "Pebble Beach Snack Bar",
    ),
}
FIRST_NAMES = (
    "James",
    "Maria",
    "Robert",
    "Linda",
    "Michael",
    "Patricia",
    "David",
    "Jennifer",
    "William",
    "Elizabeth",
    "Daniel",
    "Susan",
    "Joseph",
    "Karen",
    "Thomas",
    "Nancy",
    "Christopher",
    "Lisa",
    "Matthew",
    "Sandra",
)
LAST_NAMES = (
    "Anderson",
    "Bennett",
    "Carlson",
    "Dawson",
    "Ellison",
    "Foster",
    "Gallagher",
    "Hayes",
    "Ingram",
    "Jensen",
    "Keller",
    "Lawson",
    "Mercer",
    "Nolan",
    "Osborne",
    "Parker",
    "Quinn",
    "Reyes",
    "Sutton",
    "Turner",
)

LOST_REASONS = (
    (("competitor", "chosen"), "Chosen Competitor"),
    (("budget", "constraints"), "Budget Constraints"),
    (("response",), "No Response"),
    (("specification", "alternative"), "Alternative Specification"),
    (("decision", "delayed"), "Decision Delayed"),
    (("price", "high"), "Price Too High"),
)
CHAT_TYPES = (
    (("buy",), "Buy Now Online"),
    (("availability", "check"), "Check Availability"),
    (("support", "service"), "Support / Service"),
    (("question", "product", "general"), "General Product Question"),
)
STATUS_OF_STAGE = {"Closed Won": "Closed-Won", "Closed Lost": "Closed-Lost"}


def _pick(seq: Sequence[str], key: str) -> str:
    digest = hashlib.sha1(key.encode()).digest()
    return seq[int.from_bytes(digest[:4], "big") % len(seq)]


def _canonical(value: str, table: Sequence) -> str:
    words = {w.strip().lower() for w in str(value or "").replace("/", " ").split()}
    for keys, canonical in table:
        if any(k in words for k in keys):
            return canonical
    return value


def _channel(value: str) -> str:
    v = str(value or "").lower()
    if "portal" in v:
        return "Dealer Portal"
    if "shop" in v:
        return "Middleby Shop"
    if "division" in v:
        return "Middleby Division"
    return "Dealer"


def _latest(directory: Path, table: str) -> Path:
    matches = sorted(glob.glob(str(directory / f"{table}_*.csv")))
    if not matches:
        raise SystemExit(f"no {table}_*.csv in {directory}")
    return Path(matches[-1])


def _read(path: Path):
    with open(path, newline="") as handle:
        rows = list(csv.reader(handle))
    return rows[0], rows[1:]


def _write(path: Path, header: List[str], rows: List[List[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(header)
        writer.writerows(rows)


def curate(directory: Path, out: Path) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    # Accounts first: region, state and rep are the account's; the quotes follow.
    header, rows = _read(_latest(directory, "dc_customers"))
    col = {c: i for i, c in enumerate(header)}
    account: Dict[str, Dict[str, str]] = {}
    for r in rows:
        cid = r[col["customer_id"]]
        segment = r[col["customer_segment"]]
        region = CUSTOMER_TERRITORY.get(
            str(r[col["customer_territory"]]).lower(), _pick(REGIONS, "region:" + cid)
        )
        state = _pick(STATES[region], "state:" + cid)
        rep = _pick(REPS[region], "rep:" + cid)
        if segment == "Consumer":
            name = f"{_pick(FIRST_NAMES, 'first:' + cid)} {_pick(LAST_NAMES, 'last:' + cid)}"
        else:
            pool = BUSINESS_NAMES.get(segment, BUSINESS_NAMES["SMB"])
            base = _pick(pool, "name:" + cid)
            # Ten names per segment for hundreds of accounts: the city tells them apart.
            suffix = _pick(
                (
                    "",
                    " – " + state,
                    " – "
                    + _pick(
                        (
                            "Downtown",
                            "Airport",
                            "Riverside",
                            "Uptown",
                            "Midtown",
                            "Harbor",
                            "Old Town",
                            "Lakeside",
                        ),
                        "site:" + cid,
                    ),
                ),
                "suffix:" + cid,
            )
            name = base + suffix
        account[cid] = {
            "name": name,
            "region": region,
            "state": state,
            "rep": rep,
            "segment": segment,
        }
        r[col["customer_name"]] = name
        r[col["customer_state"]] = state
        r[col["customer_territory"]] = region
    _write(out / _latest(directory, "dc_customers").name, header, rows)
    counts["dc_customers"] = len(rows)

    header, rows = _read(_latest(directory, "dc_rfq_quotes"))
    col = {c: i for i, c in enumerate(header)}
    for r in rows:
        cid = r[col["customer_id"]]
        a = account.get(cid)
        category = r[col["pim_category"]]
        qid = r[col["quote_id"]]
        if a:
            r[col["customer_name"]] = a["name"]
            r[col["customer_state"]] = a["state"]
            r[col["sales_rep_territory"]] = a["region"]
            r[col["sales_rep_name"]] = a["rep"]
            r[col["customer_segment"]] = a["segment"]
        else:
            region = TERRITORY_FROM_INDIA.get(
                str(r[col["sales_rep_territory"]]).lower(),
                _pick(REGIONS, "region:" + cid),
            )
            r[col["sales_rep_territory"]] = region
            r[col["customer_state"]] = _pick(STATES[region], "state:" + cid)
            r[col["sales_rep_name"]] = _pick(REPS[region], "rep:" + cid)
        brands = BRANDS.get(category, BRANDS["Ovens"])
        models = MODELS.get(category, MODELS["Ovens"])
        slot = int.from_bytes(
            hashlib.sha1(("model:" + qid).encode()).digest()[:2], "big"
        ) % len(brands)
        r[col["brand"]] = brands[slot]
        r[col["product_name"]] = f"{brands[slot]} {models[slot]}"
        digits = "".join(ch for ch in r[col["product_sku"]] if ch.isdigit()) or qid
        r[col["product_sku"]] = f"{SKU_PREFIX.get(category, 'EQP')}-{digits}"
        r[col["audience_channel"]] = _channel(r[col["audience_channel"]])
        if r[col["lost_reason"]]:
            r[col["lost_reason"]] = _canonical(r[col["lost_reason"]], LOST_REASONS)
        r[col["quote_month"]] = str(r[col["quote_date"]])[:7]
        r[col["quote_status"]] = STATUS_OF_STAGE.get(r[col["pipeline_stage"]], "Open")
    _write(out / _latest(directory, "dc_rfq_quotes").name, header, rows)
    counts["dc_rfq_quotes"] = len(rows)

    header, rows = _read(_latest(directory, "dc_chat_inquiries"))
    col = {c: i for i, c in enumerate(header)}
    for r in rows:
        category = r[col["pim_category_discussed"]]
        r[col["chat_type"]] = _canonical(r[col["chat_type"]], CHAT_TYPES)
        if r[col["brand_discussed"]]:
            r[col["brand_discussed"]] = _pick(
                BRANDS.get(category, BRANDS["Ovens"]), "chatbrand:" + r[col["chat_id"]]
            )
        r[col["audience_channel"]] = _channel(r[col["audience_channel"]])
    _write(out / _latest(directory, "dc_chat_inquiries").name, header, rows)
    counts["dc_chat_inquiries"] = len(rows)

    header, rows = _read(_latest(directory, "dc_web_sessions"))
    col = {c: i for i, c in enumerate(header)}
    for r in rows:
        category = r[col["pim_category_viewed"]]
        sid = r[col["session_id"]]
        if r[col["brand_viewed"]]:
            r[col["brand_viewed"]] = _pick(
                BRANDS.get(category, BRANDS["Ovens"]), "webbrand:" + sid
            )
        if "top_pdp_viewed" in col and r[col["top_pdp_viewed"]]:
            brand = r[col["brand_viewed"]] or _pick(
                BRANDS.get(category, BRANDS["Ovens"]), "webbrand:" + sid
            )
            models = MODELS.get(category, MODELS["Ovens"])
            r[col["top_pdp_viewed"]] = f"{brand} {_pick(models, 'pdp:' + sid)}"
        r[col["audience_channel"]] = _channel(r[col["audience_channel"]])
        a = account.get(r[col["customer_id"]]) if "customer_id" in col else None
        if a:
            r[col["customer_state"]] = a["state"]
            r[col["customer_segment"]] = a["segment"]
        elif "customer_state" in col and r[col["customer_state"]]:
            r[col["customer_state"]] = _pick(
                STATES[_pick(REGIONS, "region:" + sid)], "state:" + sid
            )
    _write(out / _latest(directory, "dc_web_sessions").name, header, rows)
    counts["dc_web_sessions"] = len(rows)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dir", default=str(Path.home() / "Downloads"))
    parser.add_argument("--out", default=str(Path.home() / "Downloads" / "middleby_us"))
    args = parser.parse_args()
    counts = curate(Path(args.dir).expanduser(), Path(args.out).expanduser())
    for table, n in counts.items():
        print(f"  {table:<20} {n} rows -> {args.out}")


if __name__ == "__main__":
    main()
