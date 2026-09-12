import { useCallback, useEffect, useState } from "react";
import {
  readSavedPrompts,
  addSavedPrompt as storageAdd,
  removeSavedPrompt as storageRemove,
  markSavedPromptUsed as storageMarkUsed,
  updateSavedPromptLabel as storageUpdateLabel,
  updateSavedPromptText as storageUpdateText,
} from "../storage/saved-prompts";

export function useSavedPrompts(profileId) {
  const [prompts, setPrompts] = useState(() => readSavedPrompts(profileId));

  useEffect(() => {
    setPrompts(readSavedPrompts(profileId));
  }, [profileId]);

  const refresh = useCallback(() => setPrompts(readSavedPrompts(profileId)), [profileId]);

  const savePrompt = useCallback(
    ({ text, label }) => {
      const entry = storageAdd(profileId, { text, label });
      refresh();
      return entry;
    },
    [profileId, refresh]
  );

  const removePrompt = useCallback(
    (id) => {
      storageRemove(profileId, id);
      refresh();
    },
    [profileId, refresh]
  );

  const markUsed = useCallback(
    (id) => {
      storageMarkUsed(profileId, id);
      refresh();
    },
    [profileId, refresh]
  );

  const relabel = useCallback(
    (id, label) => {
      storageUpdateLabel(profileId, id, label);
      refresh();
    },
    [profileId, refresh]
  );

  const updateText = useCallback(
    (id, text) => {
      storageUpdateText(profileId, id, text);
      refresh();
    },
    [profileId, refresh]
  );

  return { prompts, savePrompt, removePrompt, markUsed, relabel, updateText, refresh };
}
