import { useCallback, useEffect, useState } from "react";
import { CHAT_MODES, readChatMode, writeChatMode } from "../storage/chat-mode";

export function useChatMode(profileId) {
  const [mode, setModeState] = useState(() => readChatMode(profileId).mode);

  useEffect(() => {
    setModeState(readChatMode(profileId).mode);
  }, [profileId]);

  const setMode = useCallback(
    (next) => {
      writeChatMode(profileId, next);
      setModeState(next);
    },
    [profileId]
  );

  const toggleBrief = useCallback(() => {
    const next = mode === CHAT_MODES.BRIEF ? CHAT_MODES.FULL : CHAT_MODES.BRIEF;
    writeChatMode(profileId, next);
    setModeState(next);
  }, [profileId, mode]);

  return { mode, setMode, toggleBrief, isBrief: mode === CHAT_MODES.BRIEF };
}
