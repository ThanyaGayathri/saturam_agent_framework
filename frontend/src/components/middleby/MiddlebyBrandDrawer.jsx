import SlidePanel from "../ui/SlidePanel";
import { ExternalLink } from "lucide-react";

export default function MiddlebyBrandDrawer({ brand, onClose }) {
  if (!brand) return null;

  return (
    <SlidePanel
      title={brand.brand_name || "Brand"}
      subtitle="Middleby Marketplace catalog"
      onClose={onClose}
      widthClass="max-w-md"
    >
      <div className="space-y-4 px-6 py-5 text-sm">
        <dl className="space-y-3">
          <div>
            <dt className="text-xs font-semibold uppercase tracking-wide text-text-muted">Brand</dt>
            <dd className="mt-1 font-medium text-text-primary">{brand.brand_name}</dd>
          </div>
          {brand.brand_slug ? (
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-text-muted">Slug</dt>
              <dd className="mt-1 text-text-secondary">{brand.brand_slug}</dd>
            </div>
          ) : null}
        </dl>
        {brand.brand_url ? (
          <a
            href={brand.brand_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 rounded-lg border border-primary/30 bg-primary-light px-3 py-2 text-sm font-semibold text-primary hover:bg-primary/15"
          >
            View on Middleby shop
            <ExternalLink className="h-4 w-4" aria-hidden />
          </a>
        ) : null}
        <p className="text-xs text-text-muted">
          Sample row from the shop brand directory bundled with this analytics demo.
        </p>
      </div>
    </SlidePanel>
  );
}
