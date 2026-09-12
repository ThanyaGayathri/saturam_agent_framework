import { useCallback, useEffect, useMemo, useState } from "react";
import {
  readReports,
  createReport as storageCreate,
  renameReport as storageRename,
  deleteReport as storageDelete,
  setActiveReport as storageSetActive,
  addBlockToActiveReport as storageAddBlock,
  removeBlockFromReport as storageRemove,
  clearReport as storageClear,
  isBlockInActiveReport as storageIsIn,
} from "../storage/report-drawer";

export function useReport(profileId) {
  const [state, setState] = useState(() => readReports(profileId));

  useEffect(() => {
    setState(readReports(profileId));
  }, [profileId]);

  const refresh = useCallback(() => setState(readReports(profileId)), [profileId]);

  const activeReport = useMemo(
    () => state.reports.find((r) => r.id === state.activeId) || state.reports[0] || null,
    [state]
  );

  const activeItems = activeReport?.items || [];

  const createReport = useCallback(
    ({ title, makeActive = true } = {}) => {
      const r = storageCreate(profileId, { title, makeActive });
      refresh();
      return r;
    },
    [profileId, refresh]
  );

  const renameReport = useCallback(
    (reportId, title) => {
      storageRename(profileId, reportId, title);
      refresh();
    },
    [profileId, refresh]
  );

  const deleteReport = useCallback(
    (reportId) => {
      storageDelete(profileId, reportId);
      refresh();
    },
    [profileId, refresh]
  );

  const setActive = useCallback(
    (reportId) => {
      storageSetActive(profileId, reportId);
      refresh();
    },
    [profileId, refresh]
  );

  const pin = useCallback(
    ({ block, sourceQuestion, catalogId }) => {
      storageAddBlock(profileId, { block, sourceQuestion, catalogId });
      refresh();
    },
    [profileId, refresh]
  );

  const unpin = useCallback(
    (reportId, blockId) => {
      storageRemove(profileId, reportId, blockId);
      refresh();
    },
    [profileId, refresh]
  );

  const clear = useCallback(
    (reportId) => {
      storageClear(profileId, reportId);
      refresh();
    },
    [profileId, refresh]
  );

  const togglePin = useCallback(
    ({ block, sourceQuestion, catalogId }) => {
      if (!block?.id) return;
      if (storageIsIn(profileId, block.id)) {
        // Remove from the active report specifically.
        const currentActiveId = readReports(profileId).activeId;
        storageRemove(profileId, currentActiveId, block.id);
      } else {
        storageAddBlock(profileId, { block, sourceQuestion, catalogId });
      }
      refresh();
    },
    [profileId, refresh]
  );

  const isPinned = useCallback(
    (blockId) => activeItems.some((it) => it.block.id === blockId),
    [activeItems]
  );

  return {
    reports: state.reports,
    activeReport,
    activeReportId: state.activeId,
    activeItems,
    // legacy alias for chat code that used `items`
    items: activeItems,
    createReport,
    renameReport,
    deleteReport,
    setActive,
    pin,
    unpin,
    clear,
    togglePin,
    isPinned,
    refresh,
  };
}
