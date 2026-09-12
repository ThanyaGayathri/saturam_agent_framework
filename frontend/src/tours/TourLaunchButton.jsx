import { HelpCircle } from "lucide-react";
import { useTour } from "./TourContext";

/**
 * Small "?" button that launches a specific tour when clicked. Meant to sit
 * in each page's header, near the page title or an existing action button.
 *
 * Usage:
 *   <TourLaunchButton tourId="playground" label="Take a quick tour" />
 *
 * The `label` shows on hover (title + aria-label). Icon-only in the UI to
 * stay out of the way. Optionally pass `size` for a larger hit target.
 */
export default function TourLaunchButton({
  tourId,
  label = "Take a quick tour",
  size = 20,
  className = "",
}) {
  const { startTour } = useTour();
  if (!tourId) return null;
  return (
    <button
      type="button"
      onClick={() => startTour(tourId)}
      title={label}
      aria-label={label}
      className={`inline-flex h-9 w-9 items-center justify-center rounded-full border border-border text-text-secondary transition hover:border-primary hover:bg-primary-light/40 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 ${className}`}
    >
      <HelpCircle size={size} strokeWidth={1.75} aria-hidden />
    </button>
  );
}
