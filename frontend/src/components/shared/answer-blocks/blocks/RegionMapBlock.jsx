// Real US-states heat-map block. Uses react-simple-maps + the us-atlas TopoJSON
// so audiences see the actual outline of the United States, not a grid
// approximation. Hover a state → detail card on the right; click → lock the
// detail and show the top cities/dealers for that state.

import { useMemo, useState } from "react";
import {
  ComposableMap,
  Geographies,
  Geography,
} from "react-simple-maps";
import { scaleQuantize } from "d3-scale";
import BlockShell from "../BlockShell";
import statesTopoJson from "us-atlas/states-10m.json";

// Postal-code → readable state name for tooltips + narrative copy.
const STATE_NAMES = {
  "01": "Alabama", "02": "Alaska", "04": "Arizona", "05": "Arkansas", "06": "California",
  "08": "Colorado", "09": "Connecticut", "10": "Delaware", "11": "Washington DC", "12": "Florida",
  "13": "Georgia", "15": "Hawaii", "16": "Idaho", "17": "Illinois", "18": "Indiana", "19": "Iowa",
  "20": "Kansas", "21": "Kentucky", "22": "Louisiana", "23": "Maine", "24": "Maryland",
  "25": "Massachusetts", "26": "Michigan", "27": "Minnesota", "28": "Mississippi", "29": "Missouri",
  "30": "Montana", "31": "Nebraska", "32": "Nevada", "33": "New Hampshire", "34": "New Jersey",
  "35": "New Mexico", "36": "New York", "37": "North Carolina", "38": "North Dakota", "39": "Ohio",
  "40": "Oklahoma", "41": "Oregon", "42": "Pennsylvania", "44": "Rhode Island", "45": "South Carolina",
  "46": "South Dakota", "47": "Tennessee", "48": "Texas", "49": "Utah", "50": "Vermont",
  "51": "Virginia", "53": "Washington", "54": "West Virginia", "55": "Wisconsin", "56": "Wyoming",
};

const POSTAL_TO_FIPS = {
  AL: "01", AK: "02", AZ: "04", AR: "05", CA: "06", CO: "08", CT: "09", DE: "10", DC: "11", FL: "12",
  GA: "13", HI: "15", ID: "16", IL: "17", IN: "18", IA: "19", KS: "20", KY: "21", LA: "22", ME: "23",
  MD: "24", MA: "25", MI: "26", MN: "27", MS: "28", MO: "29", MT: "30", NE: "31", NV: "32", NH: "33",
  NJ: "34", NM: "35", NY: "36", NC: "37", ND: "38", OH: "39", OK: "40", OR: "41", PA: "42", RI: "44",
  SC: "45", SD: "46", TN: "47", TX: "48", UT: "49", VT: "50", VA: "51", WA: "53", WV: "54", WI: "55",
  WY: "56",
};

const FIPS_TO_POSTAL = Object.fromEntries(
  Object.entries(POSTAL_TO_FIPS).map(([postal, fips]) => [fips, postal])
);

// Purple gradient matching the app theme. Light → dark = low → high value.
const COLOR_STOPS = ["#F9F5FF", "#EFDDFF", "#D6BBFB", "#B692F6", "#9E77ED", "#7F56D9", "#6941C6"];
const NO_DATA_FILL = "#F2F4F7";
const HOVER_STROKE = "#7F56D9";
const SELECTED_STROKE = "#6941C6";
const DEFAULT_STROKE = "#E4E7EC";

function formatMetric(value, format) {
  if (value == null) return " - ";
  if (format === "currency") return `$${new Intl.NumberFormat().format(value)}`;
  if (format === "percent") return `${value}%`;
  return new Intl.NumberFormat().format(value);
}

export default function RegionMapBlock({ block, onPinToggle }) {
  const [hovered, setHovered] = useState(null);
  const [selected, setSelected] = useState(null);

  const stateMap = useMemo(() => {
    const map = {};
    for (const r of block.states || []) {
      if (r?.state) map[r.state.toUpperCase()] = r;
    }
    return map;
  }, [block.states]);

  const values = useMemo(
    () => Object.values(stateMap).map((r) => Number(r.value)).filter((n) => Number.isFinite(n)),
    [stateMap]
  );

  const colorScale = useMemo(() => {
    if (!values.length) return () => NO_DATA_FILL;
    const min = Math.min(...values);
    const max = Math.max(...values);
    if (min === max) return () => COLOR_STOPS[COLOR_STOPS.length - 1];
    return scaleQuantize().domain([min, max]).range(COLOR_STOPS);
  }, [values]);

  const [min, max] = useMemo(() => {
    if (!values.length) return [0, 1];
    return [Math.min(...values), Math.max(...values)];
  }, [values]);

  const metricLabel = block.metricLabel || "Value";
  const metricFormat = block.metricFormat || "number";

  const activePostal = selected || hovered;
  const activeState = activePostal ? stateMap[activePostal] : null;
  const activeIsPeek = !selected && !!hovered;

  return (
    <BlockShell
      block={block}
      title={block.title || "Regional performance"}
      subtitle={block.description || `Colour intensity = ${metricLabel.toLowerCase()}`}
      onPinToggle={onPinToggle}
    >
      <div className="flex flex-col gap-3 lg:flex-row">
        {/* Real US map */}
        <div className="flex-shrink-0" style={{ maxWidth: 560 }}>
          <div className="rounded-lg border border-border bg-surface-default p-2">
            <ComposableMap
              projection="geoAlbersUsa"
              width={520}
              height={320}
              style={{ width: "100%", height: "auto" }}
            >
              <Geographies geography={statesTopoJson}>
                {({ geographies }) =>
                  geographies.map((geo) => {
                    const fips = String(geo.id).padStart(2, "0");
                    const postal = FIPS_TO_POSTAL[fips];
                    const data = postal ? stateMap[postal] : null;
                    const value = data?.value != null ? Number(data.value) : null;
                    const fill = value != null ? colorScale(value) : NO_DATA_FILL;
                    const isSelected = postal && selected === postal;
                    const isHovered = postal && hovered === postal;
                    return (
                      <Geography
                        key={geo.rsmKey}
                        geography={geo}
                        fill={fill}
                        stroke={isSelected ? SELECTED_STROKE : isHovered ? HOVER_STROKE : DEFAULT_STROKE}
                        strokeWidth={isSelected ? 1.5 : isHovered ? 1.25 : 0.5}
                        onMouseEnter={() => postal && setHovered(postal)}
                        onMouseLeave={() => setHovered(null)}
                        onClick={() => {
                          if (!postal) return;
                          setSelected((s) => (s === postal ? null : postal));
                        }}
                        style={{
                          default: { outline: "none", cursor: "pointer" },
                          hover: { outline: "none", cursor: "pointer" },
                          pressed: { outline: "none" },
                        }}
                      />
                    );
                  })
                }
              </Geographies>
            </ComposableMap>
          </div>
          {/* Legend */}
          <div className="mt-2 flex items-center gap-2 text-[10px] text-text-muted">
            <span>{formatMetric(min, metricFormat)}</span>
            <div className="flex flex-1 overflow-hidden rounded-full">
              {COLOR_STOPS.map((c) => (
                <div key={c} className="h-2 flex-1" style={{ backgroundColor: c }} aria-hidden />
              ))}
            </div>
            <span>{formatMetric(max, metricFormat)}</span>
          </div>
        </div>

        {/* Detail card */}
        <div className="min-w-0 flex-1 rounded-xl border border-border bg-surface-secondary/40 p-3">
          {activeState ? (
            <StateDetail
              postal={activePostal}
              state={activeState}
              metricLabel={metricLabel}
              metricFormat={metricFormat}
              peek={activeIsPeek}
            />
          ) : (
            <>
              <p className="text-xs font-semibold text-text-primary">Regional overview</p>
              <p className="mt-1 text-xs text-text-secondary">
                Hover any state to see its numbers. Click to lock the detail and see the top cities or dealers there.
              </p>
              {block.summary ? (
                <div className="mt-3 space-y-1 rounded-lg bg-surface-default p-2 text-[11px] text-text-secondary">
                  {block.summary.map((line, i) => (
                    <p key={i}>{line}</p>
                  ))}
                </div>
              ) : null}
            </>
          )}
        </div>
      </div>
    </BlockShell>
  );
}

function StateDetail({ postal, state, metricLabel, metricFormat, peek }) {
  const fips = POSTAL_TO_FIPS[postal];
  const stateName = STATE_NAMES[fips] || postal;
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-semibold text-text-primary">{stateName}</p>
        {peek ? (
          <span className="rounded-full bg-surface-default px-1.5 py-0.5 text-[10px] font-medium text-text-muted">
            hovering  -  click to lock
          </span>
        ) : null}
      </div>
      <div className="mt-2 rounded-lg bg-surface-default p-2">
        <p className="text-[10px] font-medium uppercase tracking-wide text-text-muted">{metricLabel}</p>
        <p className="text-lg font-semibold text-text-primary">{formatMetric(state.value, metricFormat)}</p>
      </div>
      {state.breakdown?.length ? (
        <div className="mt-2 rounded-lg bg-surface-default p-2">
          <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-text-muted">
            {state.breakdownLabel || "Top locations"}
          </p>
          <ul className="space-y-0.5 text-xs">
            {state.breakdown.map((b, i) => (
              <li key={i} className="flex items-center justify-between">
                <span className="min-w-0 flex-1 truncate text-text-primary">{b.name}</span>
                <span className="ml-2 shrink-0 tabular-nums text-text-secondary">{b.value}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {state.note ? (
        <p className="mt-2 text-[11px] italic text-text-muted">{state.note}</p>
      ) : null}
    </div>
  );
}
