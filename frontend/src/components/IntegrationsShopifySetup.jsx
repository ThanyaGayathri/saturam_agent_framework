import { useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Button, Input } from "./ui";
import shopifySvg from "../assets/shopify.svg";

export default function IntegrationsShopifySetup() {
  const navigate = useNavigate();

  return (
    <div className="px-8 py-12">
      <div className="mx-auto w-full max-w-[1440px]">
      <div className="rounded-2xl border border-border bg-surface p-8 dark:bg-surface-default">
        {/* Back */}
        <div className="space-y-4">
          <button
            onClick={() => navigate("/integrations/retail-platforms")}
            className="flex items-center gap-1 text-sm font-semibold text-primary hover:text-primary-hover"
          >
            <ArrowLeft size={18} />
            Integrations
          </button>

          {/* Title with icon */}
          <div className="flex items-center gap-4">
            <img src={shopifySvg} alt="Shopify" className="h-12 w-12 rounded-lg" />
            <div>
              <h1 className="text-2xl font-semibold text-text-primary">Create a Shopify connection</h1>
              <p className="text-base text-text-secondary">Enterprise B2C commerce</p>
            </div>
          </div>
        </div>

        {/* Form */}
        <div className="mt-12 space-y-8">
          <Input
            label="Display name"
            required
            placeholder="Enter a Title"
          />
          <Input
            label="Shopify Store URL"
            required
            placeholder="your-store.myshopify.com"
          />
          <Input
            label="API Key"
            required
            placeholder="Enter your API Key"
          />
          <Input
            label="API Secret"
            required
            placeholder="Enter your API Secret"
            type="password"
          />
          <Input
            label="Access Token"
            required
            placeholder="Enter your access token"
            type="password"
          />
        </div>

        {/* Actions */}
        <div className="mt-12 flex items-center justify-between border-t border-border pt-6">
          <Button variant="secondary" onClick={() => navigate("/integrations/retail-platforms")}>
            Cancel
          </Button>
          <div className="flex items-center gap-3">
            <Button variant="secondary">Test Connection</Button>
            <Button>Save Connection</Button>
          </div>
        </div>
      </div>
      </div>
    </div>
  );
}
