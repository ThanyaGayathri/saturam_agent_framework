import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, Search } from "lucide-react";
import { Button } from "./ui";
import salesforceSvg from "../assets/salesforce.svg";
import shopifySvg from "../assets/shopify.svg";
import netsuiteSvg from "../assets/netsuite.svg";

const PLATFORMS = [
  { name: "Salesforce Commerce", desc: "Enterprise B2C commerce", img: salesforceSvg },
  { name: "Shopify Plus", desc: "D2C commerce platform", img: shopifySvg, setupPath: "/integrations/shopify" },
  { name: "Oracle NetSuite", desc: "ERP, financials & inventory", img: netsuiteSvg },
];

export default function IntegrationsRetailPlatforms() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");

  const filtered = PLATFORMS.filter(
    (p) => !search || p.name.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="px-8 py-12">
      <div className="mx-auto w-full max-w-[1440px]">
        <div className="rounded-2xl border border-border bg-surface p-8 shadow-sm">
          {/* Back link */}
          <button
            type="button"
            onClick={() => navigate("/integrations")}
            className="flex items-center gap-1 text-sm font-semibold text-primary hover:text-primary-hover"
          >
            <ArrowLeft size={18} />
            Integrations
          </button>

          {/* Title row + search (aligned with grid width) */}
          <div className="mt-5 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between lg:gap-6">
            <div className="min-w-0 flex-1">
              <h1 className="text-2xl font-semibold text-text-primary">Browse Enterprise Platforms</h1>
              <p className="mt-1 text-base text-text-secondary">
                Explore and connect to industry-specific platforms.
              </p>
            </div>
            <div className="relative w-full shrink-0 lg:w-[min(100%,320px)]">
              <Search size={18} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-text-muted" />
              <input
                type="text"
                placeholder="Search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full rounded-full border border-border-light bg-surface py-2.5 pl-11 pr-4 text-base text-text-primary placeholder:text-text-muted outline-none transition focus:border-primary focus:ring-1 focus:ring-primary"
              />
            </div>
          </div>

          <div className="mt-6 h-px bg-border" />

          {/* Platform grid - light grey tiles, two columns */}
          <div className="mt-8 grid grid-cols-1 gap-4 md:grid-cols-2">
            {filtered.map((platform) => (
              <div
                key={platform.name}
                role={platform.setupPath ? "button" : undefined}
                tabIndex={platform.setupPath ? 0 : undefined}
                onClick={() => platform.setupPath && navigate(platform.setupPath)}
                onKeyDown={(e) => {
                  if (
                    platform.setupPath &&
                    (e.key === "Enter" || e.key === " ")
                  ) {
                    e.preventDefault();
                    navigate(platform.setupPath);
                  }
                }}
                className={`flex items-center gap-4 rounded-xl border border-border-light bg-[#f0f2f9] p-4 transition ${
                  platform.setupPath
                    ? "cursor-pointer hover:border-primary hover:shadow-sm"
                    : ""
                }`}
              >
                <div className="flex min-w-0 flex-1 items-center gap-4">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-surface/80 shadow-sm">
                    <img
                      src={platform.img}
                      alt=""
                      className="h-7 w-7 rounded object-contain"
                    />
                  </div>
                  <div className="min-w-0">
                    <p className="text-base font-semibold text-text-primary">
                      {platform.name}
                    </p>
                    <p className="text-sm text-text-secondary">{platform.desc}</p>
                  </div>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  className="shrink-0"
                  onClick={(e) => {
                    e.stopPropagation();
                    if (platform.setupPath) navigate(platform.setupPath);
                  }}
                >
                  Connect
                </Button>
              </div>
            ))}
          </div>

          {filtered.length === 0 && (
            <p className="mt-8 text-center text-sm text-text-secondary">
              No platforms match your search.
            </p>
          )}

          {/* Request an integration */}
          <div className="mt-8 flex flex-col gap-4 rounded-xl border border-border-light bg-[#f0f2f9] p-5 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
            <div className="min-w-0">
              <p className="text-base font-semibold text-text-primary">
                Missing an integration?
              </p>
              <p className="mt-1 text-sm text-text-secondary">
                Request new integrations or support the ones you care about.
              </p>
            </div>
            <Button variant="secondary" className="w-full shrink-0 sm:w-auto">
              Request
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
