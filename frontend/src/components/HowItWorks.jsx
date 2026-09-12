import { Link } from "react-router-dom";
import {
  Archive,
  ArrowRight,
  Blocks,
  Brain,
  Building2,
  CheckCircle2,
  Cog,
  Gauge,
  Map,
  Play,
  Route,
  Search,
  ShieldCheck,
  Sparkles,
  UserCheck,
} from "lucide-react";
import Breadcrumb from "./shared/Breadcrumb";
import { useBreadcrumbTrail } from "../hooks/useBreadcrumbTrail";
import { useTour } from "../tours/TourContext";

/**
 * How Enterprise GPT Works.
 *
 * Four visual metaphors from docs/storytelling-ideas.md, one per section:
 *   1. Brain vs. Filing Cabinet
 *   2. Subway map vs. Street map
 *   3. Two brains on every decision
 *   4. Private brain per company
 *
 * Followed by a short "where this shows up in the product" map.
 *
 * Plain language only. No ontology, hypergraph, ODKE, SLM, Guard, LLM,
 * SKU, sell-through, or any other jargon in body copy.
 */

const PRODUCT_MAP = [
  {
    icon: Cog,
    label: "Configure",
    href: "/configure/internal-sources",
    body: "Where your data sources, models, and integrations get set up.",
  },
  {
    icon: Blocks,
    label: "Business Areas",
    href: "/business-areas",
    body: "The parts of the business the AI understands.",
  },
  {
    icon: Gauge,
    label: "KPIs",
    href: "/kpis/registry",
    body: "The numbers the AI tracks against your targets.",
  },
  {
    icon: Sparkles,
    label: "Playground",
    href: "/playground",
    body: "Where you ask business questions in plain English.",
  },
];

function SectionHeader({ number, kicker, title }) {
  return (
    <div className="mb-8">
      <div className="flex items-center gap-3 text-[11px] font-semibold uppercase tracking-wide text-primary">
        <span className="rounded-md bg-primary-light px-2 py-0.5 text-primary dark:bg-primary/20">
          {number}
        </span>
        <span>{kicker}</span>
      </div>
      <h2 className="mt-3 max-w-3xl text-3xl font-semibold tracking-tight text-text-primary sm:text-4xl">
        {title}
      </h2>
    </div>
  );
}

function MetaphorPanel({ leftIcon, leftLabel, leftHeadline, leftBody, rightIcon, rightLabel, rightHeadline, rightBody }) {
  const LeftIcon = leftIcon;
  const RightIcon = rightIcon;
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <div className="rounded-2xl border-2 border-status-danger/25 bg-status-danger-light/25 p-6 dark:bg-status-danger/10">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-status-danger-light text-status-danger dark:bg-status-danger/25">
            <LeftIcon className="h-5 w-5" strokeWidth={1.75} aria-hidden />
          </div>
          <span className="rounded-full bg-status-danger-light px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-status-danger dark:bg-status-danger/25">
            {leftLabel}
          </span>
        </div>
        <h3 className="mt-4 text-lg font-semibold text-text-primary">{leftHeadline}</h3>
        <p className="mt-2 text-sm leading-relaxed text-text-secondary">{leftBody}</p>
      </div>
      <div className="rounded-2xl border-2 border-[#a9f0d1] bg-[#ecfdf3]/40 p-6 dark:bg-[#067647]/12">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-[#ecfdf3] text-[#067647] dark:bg-[#067647]/25 dark:text-[#6ce9a6]">
            <RightIcon className="h-5 w-5" strokeWidth={1.75} aria-hidden />
          </div>
          <span className="rounded-full bg-[#ecfdf3] px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[#067647] dark:bg-[#067647]/25 dark:text-[#6ce9a6]">
            {rightLabel}
          </span>
        </div>
        <h3 className="mt-4 text-lg font-semibold text-text-primary">{rightHeadline}</h3>
        <p className="mt-2 text-sm leading-relaxed text-text-secondary">{rightBody}</p>
      </div>
    </div>
  );
}

// ─── Section 1: Filing cabinet vs. brain ─────────────────────────────

function SectionFilingCabinet() {
  return (
    <section className="mt-16" data-tour-how="brain">
      <SectionHeader number="01" kicker="How it thinks" title="A brain, not a filing cabinet." />
      <MetaphorPanel
        leftIcon={Archive}
        leftLabel="Generic AI"
        leftHeadline="A filing cabinet with sticky notes."
        leftBody="Finds text that looks relevant to your question. Doesn't actually understand what your business calls things. Right most of the time. Wrong when it matters."
        rightIcon={Brain}
        rightLabel="Enterprise GPT"
        rightHeadline="A brain that knows your business."
        rightBody="Learns your language on Day 1. Knows what your team means by 'active customer' or 'on-time delivery.' Every answer references the specific rule it used."
      />
    </section>
  );
}

// ─── Section 2: Street map vs. Subway map ─────────────────────────────

function SectionSubwayMap() {
  return (
    <section className="mt-24" data-tour-how="subway">
      <SectionHeader number="02" kicker="How it reasons" title="A subway map, not a street map." />
      <MetaphorPanel
        leftIcon={Map}
        leftLabel="Generic AI"
        leftHeadline="Hands you a street map."
        leftBody="You can see every road. You still have to figure out the route yourself. Ask why sales dropped and it hands back a document to read."
        rightIcon={Route}
        rightLabel="Enterprise GPT"
        rightHeadline="Shows you the route."
        rightBody="Follows the connections in your business the way a subway map shows lines between stations. Ask why sales dropped and it walks you through the cause and effect."
      />
    </section>
  );
}

// ─── Section 3: Two brains ────────────────────────────────────────────

function SectionTwoBrains() {
  return (
    <section className="mt-24" data-tour-how="two-brains">
      <SectionHeader number="03" kicker="How it stays safe" title="Two brains on every decision." />

      <div className="rounded-2xl border border-border bg-surface p-8">
        <div className="grid gap-6 lg:grid-cols-[1fr_auto_1fr]">
          <div className="rounded-xl border-2 border-primary/25 bg-primary-light/25 p-5 dark:bg-primary/12">
            <div className="flex items-center gap-2">
              <Sparkles className="h-5 w-5 text-primary" strokeWidth={1.75} aria-hidden />
              <span className="text-[10px] font-semibold uppercase tracking-wide text-primary">
                Brain 1 · Proposer
              </span>
            </div>
            <h3 className="mt-3 text-lg font-semibold text-text-primary">Suggests what to do.</h3>
            <p className="mt-2 text-sm leading-relaxed text-text-secondary">
              Puts together an answer or an action based on what it knows about your business.
            </p>
          </div>
          <div className="flex items-center justify-center" aria-hidden>
            <ArrowRight className="h-6 w-6 text-text-muted" strokeWidth={2} />
          </div>
          <div className="rounded-xl border-2 border-[#a9f0d1] bg-[#ecfdf3]/40 p-5 dark:bg-[#067647]/15">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-5 w-5 text-[#067647] dark:text-[#6ce9a6]" strokeWidth={1.75} aria-hidden />
              <span className="text-[10px] font-semibold uppercase tracking-wide text-[#067647] dark:text-[#6ce9a6]">
                Brain 2 · Checker
              </span>
            </div>
            <h3 className="mt-3 text-lg font-semibold text-text-primary">Checks every claim.</h3>
            <p className="mt-2 text-sm leading-relaxed text-text-secondary">
              A second, independent brain verifies each fact against your data before you ever see it.
            </p>
          </div>
        </div>
        <div className="mt-6 flex items-start gap-3 rounded-xl border border-border bg-surface-secondary/60 p-4 dark:bg-surface-tertiary/40">
          <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-primary" strokeWidth={1.75} aria-hidden />
          <p className="text-sm leading-relaxed text-text-primary">
            Nothing reaches you unless both brains agree. If the checker can't back it up, the AI holds.
          </p>
        </div>
      </div>
    </section>
  );
}

// ─── Section 4: Private brain per company ─────────────────────────────

function SectionPrivateBrain() {
  const buildings = [
    { name: "Your business", isYou: true },
    { name: "Another business", isYou: false },
    { name: "Another business", isYou: false },
  ];
  return (
    <section className="mt-24" data-tour-how="private">
      <SectionHeader number="04" kicker="How it stays private" title="Your own brain. Nothing shared." />

      <div className="rounded-2xl border border-border bg-surface p-8">
        <div className="grid gap-6 sm:grid-cols-3">
          {buildings.map((b, i) => (
            <div
              key={i}
              className={`rounded-xl border-2 p-5 ${
                b.isYou
                  ? "border-primary/40 bg-primary-light/25 dark:bg-primary/12"
                  : "border-border bg-surface-secondary/40 dark:bg-surface-tertiary/30"
              }`}
            >
              <div className="flex items-center gap-2">
                <Building2
                  className={`h-5 w-5 ${b.isYou ? "text-primary" : "text-text-muted"}`}
                  strokeWidth={1.75}
                  aria-hidden
                />
                <span
                  className={`text-[10px] font-semibold uppercase tracking-wide ${
                    b.isYou ? "text-primary" : "text-text-muted"
                  }`}
                >
                  {b.isYou ? "You" : "Someone else"}
                </span>
              </div>
              <p className="mt-3 text-lg font-semibold text-text-primary">{b.name}</p>
              <div
                className={`mt-4 flex items-center gap-2 rounded-lg border p-3 ${
                  b.isYou ? "border-primary/25 bg-surface" : "border-border bg-surface"
                }`}
              >
                <Brain
                  className={`h-5 w-5 ${b.isYou ? "text-primary" : "text-text-muted"}`}
                  strokeWidth={1.75}
                  aria-hidden
                />
                <span className="text-xs font-medium text-text-primary">Its own brain</span>
              </div>
            </div>
          ))}
        </div>

        <p className="mt-6 text-center text-sm leading-relaxed text-text-secondary">
          Each business gets its own brain. What yours learns from your data never touches anyone else's.
        </p>
      </div>
    </section>
  );
}

// ─── Product map ──────────────────────────────────────────────────────

function ProductMapSection() {
  return (
    <section className="mt-24">
      <SectionHeader number="05" kicker="Where this shows up" title="How the ideas land in the product." />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {PRODUCT_MAP.map((item) => (
          <Link
            key={item.label}
            to={item.href}
            className="group flex items-start gap-4 rounded-xl border border-border bg-surface p-5 transition hover:-translate-y-0.5 hover:border-primary hover:shadow-sm"
          >
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary-light text-primary dark:bg-primary/20">
              <item.icon className="h-5 w-5" strokeWidth={1.75} aria-hidden />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-base font-semibold text-text-primary group-hover:text-primary">
                {item.label}
              </p>
              <p className="mt-1 text-sm leading-relaxed text-text-secondary">{item.body}</p>
            </div>
          </Link>
        ))}
      </div>

      <div className="mt-8 flex flex-wrap items-center gap-3 rounded-2xl border border-primary/20 bg-primary-light/20 p-5 dark:bg-primary/10">
        <Blocks className="h-5 w-5 shrink-0 text-primary" strokeWidth={1.75} aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-text-primary">Want to see the pieces up close?</p>
          <p className="text-sm text-text-secondary">
            The six building blocks — Business Area, Process, KPI, Use Case, Alert, Subject Area — are defined one by one on the next page.
          </p>
        </div>
        <Link
          to="/hierarchy"
          className="inline-flex items-center gap-1.5 rounded-lg border border-primary bg-surface px-4 py-2 text-sm font-semibold text-primary transition hover:bg-primary-light dark:bg-transparent"
        >
          See the building blocks
          <ArrowRight className="h-4 w-4" strokeWidth={2} aria-hidden />
        </Link>
      </div>
    </section>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────

export default function HowItWorks() {
  const trail = useBreadcrumbTrail("How Enterprise GPT works", [
    { label: "Playground", to: "/playground" },
  ]);
  const { startTour } = useTour();

  return (
    <div className="mx-auto min-h-[calc(100vh-72px)] max-w-6xl px-6 py-10">
      <Breadcrumb items={trail} />

      {/* Hero */}
      <header className="mt-4 rounded-3xl border border-primary/25 bg-gradient-to-br from-primary-light/40 via-surface to-surface p-8 shadow-sm sm:p-10 dark:from-primary/15 dark:via-surface-default dark:to-surface-default">
        <div className="inline-flex items-center gap-2 rounded-full bg-primary-light px-3 py-1 text-xs font-semibold uppercase tracking-wide text-primary dark:bg-primary/25">
          <Sparkles className="h-3.5 w-3.5" strokeWidth={2.5} aria-hidden />
          How Enterprise GPT works
        </div>
        <h1 className="mt-4 max-w-3xl text-4xl font-semibold tracking-tight text-text-primary sm:text-5xl">
          Not a chatbot. A system that runs the decision.
        </h1>
        <p className="mt-3 max-w-2xl text-base leading-relaxed text-text-secondary">
          Four ideas explain what makes this different from a generic AI. No jargon, no diagrams,
          no technical setup required.
        </p>
        <div className="mt-6 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => startTour("whole-product")}
            data-tour-launch="whole-product"
            className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-primary-hover"
          >
            <Play className="h-4 w-4" strokeWidth={2} aria-hidden />
            Start guided tour · 5 min
          </button>
          <Link
            to="/hierarchy"
            className="inline-flex items-center gap-2 rounded-lg border border-border bg-surface px-5 py-2.5 text-sm font-semibold text-text-primary transition hover:border-primary hover:text-primary"
          >
            See the building blocks
            <ArrowRight className="h-4 w-4" strokeWidth={2} aria-hidden />
          </Link>
        </div>
      </header>

      <SectionFilingCabinet />
      <SectionSubwayMap />
      <SectionTwoBrains />
      <SectionPrivateBrain />
      <ProductMapSection />
    </div>
  );
}
