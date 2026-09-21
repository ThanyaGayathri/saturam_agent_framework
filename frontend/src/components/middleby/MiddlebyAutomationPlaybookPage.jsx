import { useParams, useSearchParams, useNavigate } from "react-router-dom";
import { useMemo } from "react";
import { ArrowLeft } from "lucide-react";
import { useUseCaseCatalog } from "../../hooks/useUseCaseCatalog";
import MiddlebyAutomationFlow from "./MiddlebyAutomationFlow";

export default function MiddlebyAutomationPlaybookPage() {
  const { patternId } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const catalogId = searchParams.get("catalogId");
  const entry = searchParams.get("entry");
  const returnTo = searchParams.get("returnTo");
  const returnLabel = searchParams.get("returnLabel") || "Use case";
  const { entries } = useUseCaseCatalog();
  const useCaseTitle = useMemo(
    () => entries.find((e) => e.id === catalogId)?.title,
    [entries, catalogId]
  );

  const entryPoint =
    entry === "monitor" || entry === "insight" || entry === "automate" ? entry : "hub";

  return (
    <>
      {/* The strip uses the same gutter as the page below it, so the button starts at the
          page's left edge instead of floating in from a narrower column. */}
      {returnTo ? (
        <div className="mx-auto w-full max-w-[1440px] px-8 pt-8">
          <button
            type="button"
            onClick={() => navigate(returnTo)}
            className="inline-flex items-center gap-2 rounded-lg border border-primary/30 bg-primary-light px-3 py-2 text-sm font-semibold text-primary hover:bg-primary/15 dark:bg-primary/10"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden />
            Back to {returnLabel}
          </button>
        </div>
      ) : null}
      <MiddlebyAutomationFlow
        patternId={patternId}
        catalogId={catalogId}
        useCaseTitle={useCaseTitle}
        entryPoint={entryPoint}
      />
    </>
  );
}
