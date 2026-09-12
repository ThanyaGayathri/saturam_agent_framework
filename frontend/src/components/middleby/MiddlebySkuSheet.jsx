import SlidePanel from "../ui/SlidePanel";
import { ExternalLink } from "lucide-react";

export default function MiddlebySkuSheet({ product, onClose }) {
  if (!product) return null;

  return (
    <SlidePanel
      title={product.sku || "SKU"}
      subtitle="Product catalog lookup"
      onClose={onClose}
      widthClass="max-w-md"
    >
      <div className="space-y-4 px-6 py-5 text-sm">
        <dl className="space-y-3">
          <div>
            <dt className="text-xs font-semibold uppercase tracking-wide text-text-muted">SKU</dt>
            <dd className="mt-1 font-mono text-text-primary">{product.sku}</dd>
          </div>
          {product.product_name ? (
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-text-muted">Name</dt>
              <dd className="mt-1 text-text-primary">{product.product_name}</dd>
            </div>
          ) : null}
          {product.brand ? (
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-text-muted">Brand</dt>
              <dd className="mt-1 text-text-primary">{product.brand}</dd>
            </div>
          ) : null}
          {product.l1_category ? (
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-text-muted">Category</dt>
              <dd className="mt-1 text-text-secondary">
                {[product.l1_category, product.l2_category, product.l3_category].filter(Boolean).join(" / ")}
              </dd>
            </div>
          ) : null}
          {product.price_hint ? (
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-text-muted">List price hint</dt>
              <dd className="mt-1 text-text-primary">
                {String(product.price_hint).startsWith("$") ? product.price_hint : `$${product.price_hint}`}
              </dd>
            </div>
          ) : null}
        </dl>
        {product.product_url ? (
          <a
            href={product.product_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 rounded-lg border border-primary/30 bg-primary-light px-3 py-2 text-sm font-semibold text-primary hover:bg-primary/15"
          >
            Open product page
            <ExternalLink className="h-4 w-4" aria-hidden />
          </a>
        ) : null}
      </div>
    </SlidePanel>
  );
}
