// Playground home with library sidebar open on the left
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { X, Search, Package, ShoppingCart, Store, Headphones, TrendingUp, AlertCircle, Clock, Star } from "lucide-react";
import { Badge, Card } from "./ui";
import { playgroundCategoryToSlug } from "../domain/playground-areas";

const LIBRARY_ITEMS = [
  { category: "Inventory", icon: Package, count: 8, items: [
    { title: "Stock Level Monitor", desc: "Real-time inventory tracking across all locations", starred: true },
    { title: "Reorder Point Alert", desc: "Automatic alerts when stock falls below threshold" },
    { title: "Dead Stock Analysis", desc: "Identify slow-moving inventory for markdown" },
  ]},
  { category: "Store Ops", icon: Store, count: 12, items: [
    { title: "Foot Traffic Forecaster", desc: "Predict store foot traffic by hour and day" },
    { title: "Staff Schedule Optimizer", desc: "AI-powered shift planning based on demand" },
  ]},
  { category: "E-Commerce", icon: ShoppingCart, count: 6, items: [
    { title: "Cart Abandonment Tracker", desc: "Monitor and reduce checkout drop-offs" },
    { title: "Product Recommendation Engine", desc: "Personalized product suggestions" },
  ]},
];

export default function PlaygroundLibrarySidebar() {
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState("Inventory");
  const navigate = useNavigate();

  return (
    <div className="flex min-h-[calc(100vh-72px)]">
      {/* Sidebar */}
      <aside className="w-[320px] shrink-0 border-r border-border bg-surface overflow-y-auto">
        <div className="flex items-center justify-between border-b border-border px-4 py-4">
          <h2 className="text-lg font-semibold text-text-primary">Use Case Library</h2>
          <button onClick={() => navigate("/playground")} className="rounded p-1 text-text-muted hover:bg-surface-secondary">
            <X size={18} />
          </button>
        </div>

        <div className="px-4 py-3">
          <div className="relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
            <input
              type="text"
              placeholder="Search use cases..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full rounded-lg border border-border-light bg-surface py-2 pl-9 pr-3 text-sm placeholder-text-muted outline-none focus:border-primary focus:ring-1 focus:ring-primary"
            />
          </div>
        </div>

        <div className="px-2 pb-4">
          {LIBRARY_ITEMS.map((cat) => (
            <div key={cat.category} className="mt-1">
              <button
                onClick={() => setExpanded(expanded === cat.category ? "" : cat.category)}
                className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm font-semibold text-text-primary hover:bg-surface-secondary"
              >
                <div className="flex items-center gap-2">
                  <cat.icon size={16} className="text-text-muted" />
                  {cat.category}
                </div>
                <Badge variant="primary">{cat.count}</Badge>
              </button>
              {expanded === cat.category && (
                <div className="ml-6 mt-1 space-y-1">
                  {cat.items.map((item, i) => (
                    <button
                      key={i}
                      onClick={() => {
                        const slug = playgroundCategoryToSlug(cat.category);
                        if (slug) navigate(`/playground/area/${slug}`);
                      }}
                      className="flex w-full items-start gap-2 rounded-lg px-3 py-2 text-left hover:bg-surface-secondary"
                    >
                      <div className="flex-1">
                        <p className="text-sm font-medium text-text-primary">{item.title}</p>
                        <p className="text-xs text-text-muted">{item.desc}</p>
                      </div>
                      {item.starred && <Star size={14} className="mt-0.5 text-amber-400" fill="currentColor" />}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </aside>

      {/* Main content (same as PlaygroundHome but dimmed) */}
      <div className="flex flex-1 flex-col items-center px-8 pb-16 opacity-60">
        <div className="mt-16 mb-8 text-center">
          <h1 className="text-[40px] font-semibold leading-[48px] text-text-primary">
            Unlock Data Insights with EnterpriseGPT
          </h1>
        </div>
        <div className="w-full max-w-[640px]">
          <div className="rounded-xl border border-border-light bg-surface p-4 shadow-sm">
            <input type="text" placeholder="Ask anything" className="w-full text-base text-text-primary placeholder-text-muted outline-none" readOnly />
          </div>
        </div>
      </div>
    </div>
  );
}
