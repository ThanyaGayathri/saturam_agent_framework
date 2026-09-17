/**
 * The shipped `grain` vocabulary (`product/egpt_retail/ontology/vocabularies.json`), for the
 * manual "Add KPI" form's select. The contract types `grain` as a runtime-validated string,
 * not an enum, because a deployment's overlay may add terms -- there is no endpoint listing
 * them yet, so the form offers the shipped set and the server remains the judge: an unknown
 * term is refused with a 422 naming it, which the form shows as-is.
 */
export const GRAIN_OPTIONS = [
 { value: "overall", label: "Overall", description: "One row for the whole deployment. No breakdown." },
 { value: "customer", label: "Customer", description: "One row per individual customer or contact." },
 { value: "customer_segment", label: "Customer segment", description: "One row per defined segment of customers." },
 { value: "product", label: "Product", description: "One row per individual product or SKU." },
 { value: "product_category", label: "Product category", description: "One row per category in the product hierarchy." },
 { value: "brand", label: "Brand", description: "One row per brand." },
 { value: "supplier", label: "Supplier", description: "One row per supplier or vendor." },
 { value: "location", label: "Location", description: "One row per store, warehouse, region or other physical place." },
 { value: "channel", label: "Channel", description: "One row per sales or marketing channel." },
 { value: "order", label: "Order", description: "One row per order or transaction." },
 { value: "time_period", label: "Time period", description: "One row per period, at whatever calendar grain the KPI declares." },
];

export const LEVEL_OPTIONS = [
 { value: "", label: "Not set" },
 { value: "strategic", label: "Strategic" },
 { value: "operational", label: "Operational" },
 { value: "tactical", label: "Tactical" },
];
