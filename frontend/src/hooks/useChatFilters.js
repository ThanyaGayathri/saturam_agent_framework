import { useCallback, useEffect, useState } from "react";
import {
  readChatFilters,
  writeChatFilters,
  getPeriodOptions,
  DEFAULT_PERIOD,
  validateCustomRange,
  describeCustomRange,
  displayFilterLabel,
} from "../storage/chat-filters";

export function useChatFilters(profileId) {
  const [filters, setFilters] = useState(() => readChatFilters(profileId));

  useEffect(() => {
    setFilters(readChatFilters(profileId));
  }, [profileId]);

  const setPeriod = useCallback(
    (period) => {
      const next = { ...filters, period: period || DEFAULT_PERIOD };
      // Clear custom dates when switching away from custom.
      if (next.period !== "custom") {
        next.fromDate = null;
        next.toDate = null;
      }
      writeChatFilters(profileId, next);
      setFilters(next);
    },
    [profileId, filters]
  );

  const setCustomRange = useCallback(
    (fromDate, toDate) => {
      const validation = validateCustomRange(fromDate, toDate);
      if (!validation.ok) return validation;
      const next = { period: "custom", fromDate, toDate };
      writeChatFilters(profileId, next);
      setFilters(next);
      return validation;
    },
    [profileId]
  );

  return {
    period: filters.period,
    fromDate: filters.fromDate,
    toDate: filters.toDate,
    periods: getPeriodOptions(),
    setPeriod,
    setCustomRange,
    customRangeLabel:
      filters.period === "custom" ? describeCustomRange(filters.fromDate, filters.toDate) : null,
    displayLabel: displayFilterLabel(filters),
  };
}
