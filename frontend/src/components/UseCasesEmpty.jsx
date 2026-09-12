import { Lightbulb } from "lucide-react";
import { EmptyState } from "./ui";

export default function UseCasesEmpty() {
  return (
    <div className="px-8 py-12">
      <EmptyState
        icon={<Lightbulb size={24} className="text-text-secondary" />}
        title="No Use Cases Yet"
        description="Create your first use case or browse the gallery for prebuilt templates."
        actionLabel="Browse Gallery"
      />
    </div>
  );
}
