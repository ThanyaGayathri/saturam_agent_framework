import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Link2, Plus, Pencil, Trash2, Mail, ChevronRight } from "lucide-react";
import { useConfigureWorkspace } from "../../context/ConfigureWorkspaceContext";
import { useDemoProfile } from "../../context/DemoProfileContext";
import { externalIntegrationsForProfile } from "../../domain/configure-catalogs";
import shopifySvg from "../../assets/shopify.svg";
import slackSvg from "../../assets/slack.svg";
import salesforceSvg from "../../assets/salesforce.svg";
import gmailSvg from "../../assets/gmail.svg";

const BRAND_SVG = {
  shopify: shopifySvg,
  slack:   slackSvg,
  sfcc:    salesforceSvg,
};

/* Per-provider color + initial for providers without an SVG asset */
const BRAND_COLOR = {
  klaviyo: { bg: "#00b8d9", initial: "K" },
  stripe:  { bg: "#635bff", initial: "S" },
  magento: { bg: "#ee6723", initial: "M" },
  braze:   { bg: "#09c",    initial: "B" },
  teams:   { bg: "#5059c9", initial: "T" },
  fedex:   { bg: "#4d148c", initial: "F" },
  webhook: { bg: "#535862", initial: "W" },
};

function IntegrationLogo({ providerId }) {
  const src = BRAND_SVG[providerId];
  if (src) {
    return (
      <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-border bg-surface p-1.5">
        <img src={src} alt="" className="h-full w-full object-contain" />
      </div>
    );
  }
  const branded = BRAND_COLOR[providerId];
  if (branded) {
    return (
      <div
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-sm font-bold text-white"
        style={{ backgroundColor: branded.bg }}
      >
        {branded.initial}
      </div>
    );
  }
  return (
    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[#f0eeff]">
      <Link2 size={16} className="text-primary" />
    </div>
  );
}

export default function ConfigureExternal() {
  const navigate = useNavigate();
  const { data, hydrated, removeExternal } = useConfigureWorkspace();
  const { profileId, isMediaProfile } = useDemoProfile();
  const saved = data.externalIntegrations || [];

  const platformIntegrations = useMemo(() => {
    const list = externalIntegrationsForProfile(profileId);
    return list.filter((e) => e.id !== "slack");
  }, [profileId]);
  const platformsHeading = isMediaProfile
    ? "Enabled Media & OTT Platforms"
    : "Enabled Retail Platforms";
  const platformsSubheading = isMediaProfile
    ? "Connect to the platforms that power OTT engagement, ad-ops, competitor intel, and AEO / GEO visibility."
    : "Connect to industry standard platforms.";

  if (!hydrated) return <p className="text-sm text-text-secondary">Loading…</p>;

  const savedByProvider = Object.fromEntries(saved.map((ex) => [ex.providerId, ex]));
  const slackSaved = savedByProvider.slack;

  return (
    <div className="space-y-6">
      {/* Page heading */}
      <div>
        <h2 className="text-xl font-bold text-text-primary">Integrations</h2>
        <p className="mt-1 text-sm text-text-secondary">Manage your data sources and integrations.</p>
      </div>

      <hr className="border-border" />

      {/* Enabled platforms — profile-aware */}
      <div className="space-y-4">
        <div>
          <h3 className="text-base font-bold text-text-primary">{platformsHeading}</h3>
          <p className="mt-0.5 text-sm text-text-secondary">{platformsSubheading}</p>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {platformIntegrations.map((e) => {
            const existing = savedByProvider[e.id];
            return (
              <div
                key={e.id}
                className="flex items-center gap-3 rounded-xl border border-border bg-surface px-4 py-3.5 transition hover:border-[#c7d0ff] hover:shadow-sm"
              >
                <IntegrationLogo providerId={e.id} />
                <div className="min-w-0 flex-1">
                  <p className="font-semibold text-text-primary">{e.label}</p>
                  <p className="text-xs text-text-secondary">{e.category}</p>
                </div>

                {existing ? (
                  <div className="flex shrink-0 items-center gap-2">
                    <span className="rounded-full border border-[#12b76a] px-2.5 py-0.5 text-xs font-medium text-[#027a48]">
                      Enabled
                    </span>
                    <button
                      type="button"
                      onClick={() => navigate(`/configure/external/edit/${encodeURIComponent(existing.id)}`)}
                      className="rounded p-1 text-text-muted hover:bg-surface-tertiary"
                      aria-label="Edit"
                    >
                      <Pencil size={13} />
                    </button>
                    <button
                      type="button"
                      onClick={() => { if (window.confirm("Remove this integration?")) removeExternal(existing.id); }}
                      className="rounded p-1 text-text-muted hover:bg-[#fff1f0] hover:text-[#d92d20]"
                      aria-label="Remove"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => navigate(`/configure/external/new?provider=${e.id}`)}
                    className="flex shrink-0 items-center gap-1.5 rounded-lg border border-primary bg-surface px-3 py-1.5 text-xs font-semibold text-primary transition hover:border-[#7f56d9] hover:bg-primary-light dark:bg-primary/15"
                  >
                    <Plus size={13} />
                    Configure
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <hr className="border-border" />

      {/* Alerts & Notifications */}
      <div className="space-y-4">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-base font-bold text-text-primary">Alerts & Notifications</h3>
          <button
            type="button"
            onClick={() => navigate("/profile")}
            className="text-sm font-semibold text-primary transition-colors hover:text-[#7f56d9]"
          >
            Manage
          </button>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="flex items-center gap-3 rounded-xl border border-border bg-surface px-4 py-3.5">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-border bg-surface p-1.5">
              <img src={slackSvg} alt="" className="h-full w-full object-contain" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="font-semibold text-text-primary">Slack</p>
              <p className="text-xs text-text-secondary">Channel notifications</p>
            </div>
            {slackSaved ? (
              <div className="flex shrink-0 items-center gap-2">
                <span className="rounded-full border border-[#12b76a] px-2.5 py-0.5 text-xs font-medium text-[#027a48]">
                  Enabled
                </span>
                <button
                  type="button"
                  onClick={() =>
                    navigate(`/configure/external/edit/${encodeURIComponent(slackSaved.id)}`)
                  }
                  className="rounded p-1 text-text-muted hover:bg-surface-tertiary"
                  aria-label="Edit"
                >
                  <Pencil size={13} />
                </button>
                <button
                  type="button"
                  onClick={() => {
                    if (window.confirm("Remove this integration?")) removeExternal(slackSaved.id);
                  }}
                  className="rounded p-1 text-text-muted hover:bg-[#fff1f0] hover:text-[#d92d20]"
                  aria-label="Remove"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => navigate("/configure/external/new?provider=slack")}
                className="flex shrink-0 items-center gap-1.5 rounded-lg border border-primary bg-surface px-3 py-1.5 text-xs font-semibold text-primary transition hover:border-[#7f56d9] hover:bg-primary-light dark:bg-primary/15"
              >
                <Plus size={13} />
                Configure
              </button>
            )}
          </div>

          <div className="flex items-center gap-3 rounded-xl border border-border bg-surface px-4 py-3.5">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-border bg-surface p-1.5">
              <img src={gmailSvg} alt="" className="h-full w-full object-contain" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="font-semibold text-text-primary">Gmail</p>
              <p className="text-xs text-text-secondary">Direct alerts</p>
            </div>
            <button
              type="button"
              className="shrink-0 rounded-lg bg-[#181d27] px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-[#252b37]"
            >
              Set up
            </button>
          </div>

          <div className="flex items-center gap-3 rounded-xl border border-border bg-surface px-4 py-3.5">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-border bg-surface-tertiary">
              <Mail size={18} className="text-text-secondary" aria-hidden />
            </div>
            <div className="min-w-0 flex-1">
              <p className="font-semibold text-text-primary">Email</p>
              <p className="text-xs text-text-secondary">Direct alerts</p>
            </div>
            <button
              type="button"
              className="shrink-0 rounded-lg bg-[#181d27] px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-[#252b37]"
            >
              Set up
            </button>
          </div>

          <button
            type="button"
            onClick={() => navigate("/integrations/retail-platforms")}
            className="flex items-center gap-3 rounded-xl border border-border bg-surface px-4 py-3.5 text-left transition hover:border-[#c7d0ff] hover:shadow-sm"
          >
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-dashed border-border-light bg-surface-secondary">
              <Plus size={20} className="text-text-muted" aria-hidden />
            </div>
            <div className="min-w-0 flex-1">
              <p className="font-semibold text-text-primary">Browse connectors</p>
              <p className="text-xs text-text-secondary">Explore more notification channels.</p>
            </div>
            <ChevronRight size={18} className="shrink-0 text-text-muted" aria-hidden />
          </button>
        </div>
      </div>
    </div>
  );
}
