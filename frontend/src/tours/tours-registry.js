import { WHOLE_PRODUCT_TOUR } from "./whole-product-tour";
import { PLAYGROUND_TOUR } from "./playground-tour";
import { KPI_REGISTRY_TOUR } from "./kpi-registry-tour";
import { KPI_GALLERY_TOUR } from "./kpi-gallery-tour";
import { KPI_DASHBOARD_TOUR } from "./kpi-dashboard-tour";
import { USE_CASE_TEMPLATES_TOUR } from "./use-case-templates-tour";
import { USE_CASE_GALLERY_TOUR } from "./use-case-gallery-tour";
import { PROCESSES_TOUR } from "./processes-tour";
import { MONITOR_TOUR } from "./monitor-tour";
import { ALERTS_TOUR } from "./alerts-tour";
import { CONFIGURE_TOUR } from "./configure-tour";
import { FREQUENT_QUERIES_TOUR } from "./frequent-queries-tour";
import { MIDDLEBY_HOME_TOUR } from "./middleby-home-tour";
import { MIDDLEBY_USE_CASES_TOUR } from "./middleby-use-cases-tour";
import { MIDDLEBY_PERFORMANCE_TOUR } from "./middleby-performance-tour";
import { MIDDLEBY_MONITOR_TOUR } from "./middleby-monitor-tour";
import { MIDDLEBY_AUTOMATION_TOUR } from "./middleby-automation-tour";

/**
 * Registry of guided tours available in the product.
 *
 * Add a tour: create a new file returning `{ id, title, subtitle, steps }`
 * and import + register here. The TourProvider looks up tours by `id`
 * (matches the `data-tour-launch` attribute on launcher buttons OR the
 * `tourId` prop passed to <TourLaunchButton />).
 */

export const TOURS = [
  WHOLE_PRODUCT_TOUR,
  PLAYGROUND_TOUR,
  KPI_REGISTRY_TOUR,
  KPI_GALLERY_TOUR,
  KPI_DASHBOARD_TOUR,
  USE_CASE_TEMPLATES_TOUR,
  USE_CASE_GALLERY_TOUR,
  PROCESSES_TOUR,
  MONITOR_TOUR,
  ALERTS_TOUR,
  CONFIGURE_TOUR,
  FREQUENT_QUERIES_TOUR,
  MIDDLEBY_HOME_TOUR,
  MIDDLEBY_USE_CASES_TOUR,
  MIDDLEBY_PERFORMANCE_TOUR,
  MIDDLEBY_MONITOR_TOUR,
  MIDDLEBY_AUTOMATION_TOUR,
];
