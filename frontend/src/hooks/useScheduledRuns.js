import { useCallback, useEffect, useState } from "react";
import {
  readScheduledRuns,
  addScheduledRun as storageAdd,
  removeScheduledRun as storageRemove,
  pauseScheduledRun as storagePause,
} from "../storage/scheduled-runs";

export function useScheduledRuns(profileId) {
  const [runs, setRuns] = useState(() => readScheduledRuns(profileId));

  useEffect(() => {
    setRuns(readScheduledRuns(profileId));
  }, [profileId]);

  const refresh = useCallback(() => setRuns(readScheduledRuns(profileId)), [profileId]);

  const scheduleRun = useCallback(
    (payload) => {
      const entry = storageAdd(profileId, payload);
      refresh();
      return entry;
    },
    [profileId, refresh]
  );

  const removeRun = useCallback(
    (id) => {
      storageRemove(profileId, id);
      refresh();
    },
    [profileId, refresh]
  );

  const togglePause = useCallback(
    (id, paused) => {
      storagePause(profileId, id, paused);
      refresh();
    },
    [profileId, refresh]
  );

  return { runs, scheduleRun, removeRun, togglePause, refresh };
}
