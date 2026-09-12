// Playground - Inventory detail view
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Package, TrendingUp, TrendingDown, AlertTriangle, BarChart3, RefreshCw } from "lucide-react";
import { Button, Badge, Card, PageHeader } from "./ui";

const METRICS = [
  { label: "Total SKUs", value: "11,340", change: "+2.4%", up: true },
  { label: "Stockout Rate", value: "3.2%", change: "-0.8%", up: false },
  { label: "Inventory Turnover", value: "8.4x", change: "+1.2x", up: true },
  { label: "Days of Stock", value: "14.2", change: "-2.1", up: false },
];

const ALERTS = [
  { sku: "SKU-4521", product: "Organic Almond Milk 1L", store: "Store #142 - Atlanta", level: "12 units", reorder: "50 units", severity: "critical" },
  { sku: "SKU-8934", product: "Premium Greek Yogurt 500g", store: "Store #89 - Denver", level: "28 units", reorder: "40 units", severity: "warning" },
  { sku: "SKU-1156", product: "Artisan Sourdough Loaf", store: "Store #201 - Portland", level: "5 units", reorder: "30 units", severity: "critical" },
];

const TOP_MOVERS = [
  { name: "Plant-Based Protein Bar", category: "Health Foods", change: "+34%", velocity: "High" },
  { name: "Cold Brew Coffee 12oz", category: "Beverages", change: "+28%", velocity: "High" },
  { name: "Organic Baby Spinach 5oz", category: "Produce", change: "+22%", velocity: "Medium" },
];

export default function PlaygroundInventoryDetail() {
  const navigate = useNavigate();

  return (
    <div className="px-8 py-8">
      <button
        type="button"
        onClick={() => navigate("/playground/area/inventory")}
        className="flex items-center gap-1 text-sm font-medium text-text-tertiary hover:text-text-primary"
      >
        <ArrowLeft size={16} /> Back to Inventory use cases
      </button>

      <div className="mt-4 flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-primary-light">
            <Package size={24} className="text-primary" />
          </div>
          <div>
            <h1 className="text-2xl font-semibold text-text-primary">Inventory Intelligence</h1>
            <p className="text-base text-text-secondary">Real-time inventory monitoring and optimization insights</p>
          </div>
        </div>
        <Button variant="secondary"><RefreshCw size={16} /> Refresh Data</Button>
      </div>

      {/* Metrics */}
      <div className="mt-8 grid grid-cols-4 gap-4">
        {METRICS.map((m) => (
          <Card key={m.label} className="p-4">
            <p className="text-xs font-medium text-text-muted">{m.label}</p>
            <div className="mt-1 flex items-baseline gap-2">
              <span className="text-2xl font-semibold text-text-primary">{m.value}</span>
              <span className={`flex items-center gap-0.5 text-xs font-medium ${m.up ? "text-status-success" : "text-red-500"}`}>
                {m.up ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                {m.change}
              </span>
            </div>
          </Card>
        ))}
      </div>

      {/* Stock Alerts */}
      <div className="mt-8">
        <h2 className="text-lg font-semibold text-text-primary">Stock Alerts</h2>
        <div className="mt-4 rounded-lg border border-border bg-surface dark:bg-surface-default">
          <div className="grid grid-cols-[120px_1fr_200px_100px_100px_80px] gap-4 border-b border-border bg-surface-secondary px-4 py-2.5">
            {["SKU", "Product", "Store", "Level", "Reorder", "Status"].map((h) => (
              <span key={h} className="text-xs font-medium uppercase tracking-wide text-text-muted">{h}</span>
            ))}
          </div>
          {ALERTS.map((a, i) => (
            <div key={i} className="grid grid-cols-[120px_1fr_200px_100px_100px_80px] gap-4 border-b border-border px-4 py-3 last:border-0 hover:bg-surface-secondary">
              <span className="text-sm font-medium text-text-primary">{a.sku}</span>
              <span className="text-sm text-text-secondary">{a.product}</span>
              <span className="text-sm text-text-secondary">{a.store}</span>
              <span className="text-sm text-text-primary">{a.level}</span>
              <span className="text-sm text-text-muted">{a.reorder}</span>
              <Badge variant={a.severity === "critical" ? "default" : "default"}>
                <AlertTriangle size={12} className={a.severity === "critical" ? "text-red-500" : "text-amber-500"} />
                {a.severity === "critical" ? "Critical" : "Warning"}
              </Badge>
            </div>
          ))}
        </div>
      </div>

      {/* Top Movers */}
      <div className="mt-8">
        <h2 className="text-lg font-semibold text-text-primary">Top Movers</h2>
        <div className="mt-4 grid grid-cols-3 gap-4">
          {TOP_MOVERS.map((item, i) => (
            <Card key={i} className="p-4">
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-sm font-semibold text-text-primary">{item.name}</p>
                  <p className="text-xs text-text-muted">{item.category}</p>
                </div>
                <span className="text-sm font-semibold text-status-success">{item.change}</span>
              </div>
              <Badge variant="primary" className="mt-2">{item.velocity} Velocity</Badge>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
