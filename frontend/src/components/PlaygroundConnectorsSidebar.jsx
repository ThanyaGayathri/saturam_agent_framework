// Playground home with connectors sidebar open
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { X, Search, Database, Snowflake, Server, Cloud, Plus, Check, Settings } from "lucide-react";
import { Badge, Button } from "./ui";

const CONNECTORS = [
  { name: "Snowflake", desc: "Enterprise data warehouse", icon: Snowflake, status: "connected", color: "text-[#249edc]" },
  { name: "PostgreSQL", desc: "Production database", icon: Database, status: "connected", color: "text-[#336791]" },
  { name: "Airflow", desc: "Workflow orchestration", icon: Server, status: "connected", color: "text-[#017cee]" },
  { name: "AWS Glue", desc: "Data integration service", icon: Cloud, status: "available", color: "text-[#ff9900]" },
  { name: "Azure Data Factory", desc: "Cloud ETL service", icon: Cloud, status: "available", color: "text-[#0078d4]" },
  { name: "BigQuery", desc: "Google analytics warehouse", icon: Database, status: "available", color: "text-[#4285f4]" },
];

export default function PlaygroundConnectorsSidebar() {
  const [search, setSearch] = useState("");
  const navigate = useNavigate();

  const filtered = CONNECTORS.filter(
    (c) => !search || c.name.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="flex min-h-[calc(100vh-72px)]">
      {/* Main content (dimmed) */}
      <div className="flex flex-1 flex-col items-center px-8 pb-16 opacity-60">
        <div className="mt-16 mb-8 text-center">
          <h1 className="text-[40px] font-semibold leading-[48px] text-text-primary">
            Unlock Data Insights with EnterpriseGPT
          </h1>
        </div>
        <div className="w-full max-w-[640px]">
          <div className="rounded-xl border border-border-light bg-surface p-4 shadow-sm">
            <input type="text" placeholder="Ask anything" className="w-full text-base placeholder-text-muted outline-none" readOnly />
          </div>
        </div>
      </div>

      {/* Right Sidebar */}
      <aside className="w-[360px] shrink-0 border-l border-border bg-surface overflow-y-auto">
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <h2 className="text-lg font-semibold text-text-primary">Data Connectors</h2>
          <button onClick={() => navigate("/playground")} className="rounded p-1 text-text-muted hover:bg-surface-secondary">
            <X size={18} />
          </button>
        </div>

        <div className="px-5 py-3">
          <div className="relative">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
            <input
              type="text"
              placeholder="Search connectors..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full rounded-lg border border-border-light bg-surface py-2 pl-9 pr-3 text-sm placeholder-text-muted outline-none focus:border-primary focus:ring-1 focus:ring-primary"
            />
          </div>
        </div>

        <div className="px-3 pb-4 space-y-1">
          {filtered.map((connector) => (
            <div
              key={connector.name}
              className="flex items-center gap-3 rounded-lg px-3 py-3 hover:bg-surface-secondary cursor-pointer"
              onClick={() => navigate("/playground/connectors-panel")}
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-surface-secondary">
                <connector.icon size={20} className={connector.color} />
              </div>
              <div className="flex-1">
                <p className="text-sm font-semibold text-text-primary">{connector.name}</p>
                <p className="text-xs text-text-muted">{connector.desc}</p>
              </div>
              {connector.status === "connected" ? (
                <Badge variant="success" dot>Connected</Badge>
              ) : (
                <Button variant="secondary" size="sm">Connect</Button>
              )}
            </div>
          ))}
        </div>

        <div className="border-t border-border px-5 py-4">
          <Button variant="outline" className="w-full" onClick={() => navigate("/configure/external")}>
            <Plus size={16} /> Browse All Connectors
          </Button>
        </div>
      </aside>
    </div>
  );
}
