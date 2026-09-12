/**
 * Open a catalog use case or explore prompt in Playground chat.
 * Writes a structured handoff to sessionStorage and navigates to /playground/chat
 * so PlaygroundActiveChat picks it up and auto-sends the prompt.
 */
import { PLAYGROUND_HUB_CHAT_MESSAGE_KEY, USE_CASE_LAUNCH_SUMMARY_KEY } from "../storage/keys";
import { serializePlaygroundHubHandoff } from "../domain/playground-hub-handoff";
import { buildLaunchSummaryPayloadFromCatalogRow } from "./use-case-launch-handoff";

function firstLineFromCatalogRow(useCase) {
  const fromProblem = useCase?.problemSignal && String(useCase.problemSignal).trim();
  if (fromProblem) return fromProblem;
  const items = Array.isArray(useCase?.items) ? useCase.items : [];
  if (items[0]) return String(items[0]).trim();
  if (useCase?.title) return String(useCase.title).trim();
  return "";
}

export function openCatalogUseCaseInBuilderChat(navigate, useCase, trail) {
  const text =
    firstLineFromCatalogRow(useCase) ||
    (useCase?.title && String(useCase.title).trim()) ||
    "";
  if (text) {
    try {
      const payload =
        useCase?.source === "seed" && useCase?.id
          ? serializePlaygroundHubHandoff({ text, catalogId: useCase.id })
          : text;
      sessionStorage.setItem(PLAYGROUND_HUB_CHAT_MESSAGE_KEY, payload);
    } catch (_e) {
      /* ignore */
    }
  }
  navigate("/playground/chat");
}

export function openBuilderChatWithExplorePrompt(navigate, catalogId, prompt, trail) {
  const text = (prompt?.text || "").trim();
  if (text) {
    try {
      sessionStorage.setItem(
        PLAYGROUND_HUB_CHAT_MESSAGE_KEY,
        serializePlaygroundHubHandoff({
          text,
          lens: prompt.lens,
          horizon: prompt.horizon,
          catalogId,
        })
      );
    } catch (_e) {
      /* ignore */
    }
  }
  navigate("/playground/chat");
}

/** Same end state as Step 10 "Launch Use Case": sessionStorage payload + `/use-cases/launch-summary`. */
export function openCatalogUseCaseAsLaunchSummary(navigate, useCase) {
  const payload = buildLaunchSummaryPayloadFromCatalogRow(useCase);
  if (!payload) return;
  try {
    sessionStorage.setItem(USE_CASE_LAUNCH_SUMMARY_KEY, JSON.stringify(payload));
  } catch (_e) {
    return;
  }
  navigate("/use-cases/launch-summary");
}
