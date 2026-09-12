import { useNavigate } from "react-router-dom";
import { Link2, ChevronRight } from "lucide-react";
import { PageHeader, Card, Badge, Button } from "./ui";
import salesforceSvg from "../assets/salesforce.svg";
import sapSvg from "../assets/sap.svg";
import netsuiteSvg from "../assets/netsuite.svg";
import postgresSvg from "../assets/postgres.svg";
import linearSvg from "../assets/linear.svg";
import slackSvg from "../assets/slack.svg";
import gmailSvg from "../assets/gmail.svg";

const RETAIL_PLATFORMS = [
  { name: "Salesforce Commerce", desc: "Enterprise B2C commerce", img: salesforceSvg, enabled: true },
  { name: "PostgreSQL", desc: "Relational database platform", img: postgresSvg, enabled: true },
  { name: "Linear", desc: "Link pull requests and automate workflows.", img: linearSvg, enabled: true },
  { name: "SAP Commerce Cloud", desc: "Enterprise omnichannel commerce", img: sapSvg, enabled: true },
  { name: "NetSuite", desc: "ERP, financials & inventory", img: netsuiteSvg, enabled: true },
  { name: "Browse connectors", desc: "Explore and connect more platforms.", icon: Link2, iconBg: "bg-surface-secondary border border-border", iconColor: "text-text-secondary", browse: true, browsePath: "/integrations/retail-platforms" },
];

const ALERTS = [
  { name: "Slack", desc: "Channel notifications", img: slackSvg, enabled: true, toggle: true },
  { name: "Gmail", desc: "Direct alerts", img: gmailSvg, enabled: false },
  { name: "Email", desc: "Direct alerts", icon: Link2, iconBg: "bg-surface-secondary", iconColor: "text-text-secondary", enabled: false },
  { name: "Browse connectors", desc: "Explore and connect more platforms.", icon: Link2, iconBg: "bg-surface-secondary border border-border", iconColor: "text-text-secondary", browse: true, browsePath: "/integrations/retail-platforms" },
];

function IntegrationCard({ item }) {
  const navigate = useNavigate();
  const goBrowse = item.browse && item.browsePath ? () => navigate(item.browsePath) : undefined;

  return (
    <Card
      hover={!!goBrowse}
      className={`flex items-center gap-4 p-4 ${item.browse ? "" : "bg-surface-tertiary"}`}
      onClick={goBrowse}
      {...(goBrowse
        ? {
            role: "button",
            tabIndex: 0,
            onKeyDown: (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                navigate(item.browsePath);
              }
            },
          }
        : {})}
    >
      <div className="flex flex-1 items-center gap-4">
        {item.img ? (
          <img src={item.img} alt={item.name} className="h-10 w-10 rounded-lg" />
        ) : (
          <div className={`flex h-10 w-10 items-center justify-center rounded-lg ${item.iconBg}`}>
            <item.icon size={20} className={item.iconColor} />
          </div>
        )}
        <div>
          <p className="text-base font-semibold text-text-primary">{item.name}</p>
          <p className="text-sm text-text-secondary">{item.desc}</p>
        </div>
      </div>
      {item.browse ? (
        <ChevronRight size={20} className="text-text-muted" />
      ) : item.toggle ? (
        <div className="flex h-6 w-11 items-center justify-end rounded-full bg-primary p-0.5">
          <div className="h-5 w-5 rounded-full bg-surface shadow" />
        </div>
      ) : item.enabled ? (
        <Badge variant="success">Enabled</Badge>
      ) : (
        <Button variant="secondary" size="sm">Set up</Button>
      )}
    </Card>
  );
}

function SectionHeader({ title, action }) {
  return (
    <div className="flex items-center justify-between">
      <h2 className="text-lg font-semibold text-text-primary">{title}</h2>
      {action && (
        <button className="text-sm font-semibold text-primary hover:text-primary-hover">
          {action}
        </button>
      )}
    </div>
  );
}

export default function IntegrationsBrowse() {
  return (
    <div className="px-8 py-12">
      <div className="mx-auto w-full max-w-[1440px]">
      <PageHeader
        title="Integrations"
        description="Manage your data sources and integrations."
      />

      <div className="mt-8 space-y-16">
        {/* Enabled Retail Platforms */}
        <div className="space-y-6">
          <div>
            <div className="h-px bg-border" />
            <div className="mt-5">
              <h2 className="text-lg font-semibold text-text-primary">Enabled Enterprise Platforms</h2>
              <p className="text-sm text-text-secondary">Connect to industry standard platforms.</p>
            </div>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {RETAIL_PLATFORMS.map((item, i) => (
              <IntegrationCard key={i} item={item} />
            ))}
          </div>
        </div>

        {/* Alerts & Notifications */}
        <div className="space-y-6">
          <SectionHeader title="Alerts & Notifications" action="Manage" />
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {ALERTS.map((item, i) => (
              <IntegrationCard key={i} item={item} />
            ))}
          </div>
        </div>
      </div>
      </div>
    </div>
  );
}
