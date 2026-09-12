// Playground - Full connectors configuration panel
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Database, Snowflake, Server, Cloud, Check, Settings, Plus, RefreshCw, Trash2 } from "lucide-react";
import { Button, Badge, Card, PageHeader, Tabs } from "./ui";

const CONNECTED = [
  { name: "Enterprise Warehouse", type: "Snowflake", host: "xy12345.snowflakecomputing.com", status: "active", lastSync: "2 mins ago", tables: 142 },
  { name: "Main Orchestrator", type: "Airflow", host: "airflow.internal.corp", status: "active", lastSync: "5 mins ago", tables: 12 },
  { name: "Data Lake Catalog", type: "AWS Glue", host: "glue-catalog-us-east-1", status: "inactive", lastSync: "2 hours ago", tables: 89 },
];

const AVAILABLE = [
  { name: "Azure Data Factory", desc: "Cloud ETL and data integration", icon: Cloud, color: "text-[#0078d4]" },
  { name: "BigQuery", desc: "Google analytics data warehouse", icon: Database, color: "text-[#4285f4]" },
  { name: "Redshift", desc: "AWS data warehouse", icon: Database, color: "text-[#8c4fff]" },
  { name: "Databricks", desc: "Unified analytics platform", icon: Server, color: "text-[#ff3621]" },
];

export default function PlaygroundConnectorsPanel() {
  const navigate = useNavigate();

  return (
    <div className="px-8 py-8">
      <button onClick={() => navigate("/playground")} className="flex items-center gap-1 text-sm font-medium text-text-tertiary hover:text-text-primary">
        <ArrowLeft size={16} /> Back to Playground
      </button>

      <div className="mt-4">
        <PageHeader
          title="Data Connectors"
          description="Manage your connected data sources and add new ones."
        >
          <Button><Plus size={16} /> Add Connector</Button>
        </PageHeader>
      </div>

      {/* Connected Sources */}
      <div className="mt-8">
        <h2 className="text-lg font-semibold text-text-primary">Connected Sources</h2>
        <div className="mt-4 rounded-lg border border-border bg-surface dark:bg-surface-default">
          <div className="grid grid-cols-[1fr_120px_200px_80px_100px_100px] gap-4 border-b border-border bg-surface-secondary px-4 py-2.5">
            {["Name", "Type", "Host", "Tables", "Last Sync", "Status"].map((h) => (
              <span key={h} className="text-xs font-medium uppercase tracking-wide text-text-muted">{h}</span>
            ))}
          </div>
          {CONNECTED.map((c, i) => (
            <div key={i} className="grid grid-cols-[1fr_120px_200px_80px_100px_100px] gap-4 border-b border-border px-4 py-3 last:border-0 hover:bg-surface-secondary">
              <span className="text-sm font-semibold text-text-primary">{c.name}</span>
              <span className="text-sm text-text-secondary">{c.type}</span>
              <span className="text-sm text-text-muted font-mono">{c.host}</span>
              <span className="text-sm text-text-tertiary">{c.tables}</span>
              <span className="text-sm text-text-muted">{c.lastSync}</span>
              <Badge variant={c.status === "active" ? "success" : "default"} dot>
                {c.status === "active" ? "Active" : "Inactive"}
              </Badge>
            </div>
          ))}
        </div>
      </div>

      {/* Available Connectors */}
      <div className="mt-10">
        <h2 className="text-lg font-semibold text-text-primary">Available Connectors</h2>
        <div className="mt-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
          {AVAILABLE.map((a, i) => (
            <Card key={i} hover className="flex flex-col items-center p-6 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface-secondary">
                <a.icon size={24} className={a.color} />
              </div>
              <p className="mt-3 text-sm font-semibold text-text-primary">{a.name}</p>
              <p className="mt-1 text-xs text-text-muted">{a.desc}</p>
              <Button variant="secondary" size="sm" className="mt-4">Connect</Button>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
