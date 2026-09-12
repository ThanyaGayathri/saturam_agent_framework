import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { clearSpecDraft } from "../domain/use-case-draft-spec";
import { useNavigate, useLocation } from "react-router-dom";
import Breadcrumb from "./shared/Breadcrumb";
import { Paperclip, Database, LayoutGrid } from "lucide-react";
import { PaperPlaneSendIcon } from "./icons/PaperPlaneSendIcon";
import PlaygroundAssistantMessage from "./shared/PlaygroundAssistantMessage";
import { useDemoProfile } from "../context/DemoProfileContext";
import { useChatSession } from "../hooks/useChatSession";
import { CHAT_SESSIONS } from "../storage/idb/chat-db";
import { getPlaygroundChatSeed } from "../storage/chat-seeds";
import { BUILDER_HUB_CHAT_MESSAGE_KEY, HUB_CHAT_ATTACH_HANDOFF_KEY } from "../storage/keys";
import { lensDisplayToHorizon, parsePlaygroundHubHandoff } from "../domain/playground-hub-handoff";
import { resolvePlaygroundAssistantReplyAsync } from "../domain/resolve-playground-assistant";
import { collapseAdjacentDuplicateUserMessages } from "../domain/chat-message-utils";
import {
  useChatAttachments,
  persistHandoffAttachments,
  discardAttachmentsHandoff,
} from "../hooks/useChatAttachments";
import { PendingAttachmentChips, SentAttachmentChips } from "./shared/ChatAttachmentChips";
import { useDictation } from "../hooks/useDictation";
import { MicButton, MicErrorMessage } from "./shared/MicButton";
import ChatComposerTextarea from "./shared/ChatComposerTextarea";

/** Same as Playground chat: stop short of true bottom so the user bubble (lens + prompt) stays in view above the assistant reply. */
const BUILDER_CHAT_LENS_HEADROOM_PX = 140;

export default function UseCaseBuilderChat() {
  const [message, setMessage] = useState("");
  const [inputFocused, setInputFocused] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const breadcrumbTrail = location.state?.trail ?? null;
  const scrollRef = useRef(null);
  const messagesEndRef = useRef(null);
  const inputBoxRef = useRef(null);
  const { profileId, sessionForChat } = useDemoProfile();
  const sessionId = sessionForChat(CHAT_SESSIONS.BUILDER);
  const chatSeed = useMemo(() => getPlaygroundChatSeed(profileId), [profileId]);
  const { messages, ready, send } = useChatSession(sessionId, chatSeed);
  const attachments = useChatAttachments();

  const appendDictation = useCallback((text) => {
    setMessage((prev) => (prev ? `${prev.trimEnd()} ${text}` : text));
  }, []);
  const dictation = useDictation({ onFinalText: appendDictation });

  const pinchScrollToBottom = useCallback(() => {
    const scrollEl = scrollRef.current;
    if (!scrollEl) return;
    const maxTop = scrollEl.scrollHeight - scrollEl.clientHeight;
    scrollEl.scrollTop = Math.max(0, maxTop - BUILDER_CHAT_LENS_HEADROOM_PX);
  }, []);

  const seedUserLine = chatSeed.find((s) => s.role === "user")?.text;

  useEffect(() => {
    if (!ready) return;
    const attachHandoffId = (() => {
      try {
        return sessionStorage.getItem(HUB_CHAT_ATTACH_HANDOFF_KEY);
      } catch {
        return null;
      }
    })();
    const clearAttachHandoff = () => {
      try {
        sessionStorage.removeItem(HUB_CHAT_ATTACH_HANDOFF_KEY);
      } catch {
        /* ignore */
      }
      discardAttachmentsHandoff(attachHandoffId);
    };
    try {
      const raw = sessionStorage.getItem(BUILDER_HUB_CHAT_MESSAGE_KEY);
      if (!raw) {
        if (attachHandoffId) clearAttachHandoff();
        return;
      }
      const parsed = parsePlaygroundHubHandoff(raw);
      if (!parsed?.text?.trim()) {
        sessionStorage.removeItem(BUILDER_HUB_CHAT_MESSAGE_KEY);
        clearAttachHandoff();
        return;
      }
      const qText = parsed.text.trim();
      const { lens, horizon, catalogId } = parsed;
      const effectiveHorizon =
        lensDisplayToHorizon(lens) ?? (horizon === "week" || horizon === "quarter" ? horizon : undefined);
      const lensLabel =
        lens ??
        (effectiveHorizon === "week" ? "This week" : effectiveHorizon === "quarter" ? "This quarter" : undefined);
      const baseUserMeta =
        lensLabel || catalogId || effectiveHorizon
          ? {
              source: "builder-catalog",
              ...(lensLabel ? { lens: lensLabel } : {}),
              ...(effectiveHorizon ? { horizon: effectiveHorizon } : {}),
              ...(catalogId ? { catalogId } : {}),
            }
          : undefined;
      const exploreHub =
        catalogId && (effectiveHorizon || lens)
          ? {
              catalogId,
              ...(effectiveHorizon ? { horizon: effectiveHorizon } : {}),
              ...(lens ? { lens } : {}),
            }
          : undefined;
      if (seedUserLine && qText === seedUserLine && !lens && !effectiveHorizon) {
        sessionStorage.removeItem(BUILDER_HUB_CHAT_MESSAGE_KEY);
        clearAttachHandoff();
        return;
      }
      sessionStorage.removeItem(BUILDER_HUB_CHAT_MESSAGE_KEY);
      try {
        sessionStorage.removeItem(HUB_CHAT_ATTACH_HANDOFF_KEY);
      } catch {
        /* ignore */
      }
      (async () => {
        try {
          let userMeta = baseUserMeta;
          let sendOptions;
          if (attachHandoffId) {
            const userMsgId =
              crypto.randomUUID?.() ?? `m-${Date.now()}-${Math.random().toString(16).slice(2)}`;
            const persisted = await persistHandoffAttachments(attachHandoffId, sessionId, userMsgId);
            if (persisted.length) {
              userMeta = { ...(baseUserMeta ?? {}), attachments: persisted };
              sendOptions = { id: userMsgId };
            }
          }
          const rows = await send(qText, "user", userMeta, sendOptions);
          const { text, meta } = await resolvePlaygroundAssistantReplyAsync(
            qText,
            profileId,
            exploreHub,
            userMeta,
            rows ?? undefined
          );
          await send(text, "assistant", meta);
        } catch (e) {
          console.error("[UseCaseBuilderChat] templates handoff failed", e);
          discardAttachmentsHandoff(attachHandoffId);
        }
      })();
    } catch {
      sessionStorage.removeItem(BUILDER_HUB_CHAT_MESSAGE_KEY);
      clearAttachHandoff();
    }
  }, [ready, send, seedUserLine, profileId, sessionId]);

  useLayoutEffect(() => {
    if (!ready) return;
    pinchScrollToBottom();
    const raf = requestAnimationFrame(pinchScrollToBottom);
    return () => cancelAnimationFrame(raf);
  }, [ready, messages, pinchScrollToBottom]);

  useEffect(() => {
    if (!ready) return;
    const t1 = window.setTimeout(pinchScrollToBottom, 120);
    const t2 = window.setTimeout(pinchScrollToBottom, 320);
    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
    };
  }, [ready, messages, pinchScrollToBottom]);

  /** Rich assistant panels resize after paint; keep scroll pinned when the user is already following the thread. */
  useEffect(() => {
    if (!ready) return;
    const pane = scrollRef.current;
    const inner = pane?.firstElementChild;
    if (!inner || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      const el = scrollRef.current;
      if (!el) return;
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
      const nearBottom = dist < BUILDER_CHAT_LENS_HEADROOM_PX + 160;
      if (nearBottom) pinchScrollToBottom();
    });
    ro.observe(inner);
    return () => ro.disconnect();
  }, [ready, pinchScrollToBottom]);

  const handleSend = async () => {
    const trimmed = message.trim();
    const hasStaged = attachments.staged.length > 0;
    if (!trimmed && !hasStaged) return;
    if (dictation.listening) dictation.stop();
    setMessage("");
    const textToSend = trimmed || (hasStaged ? "(attached files)" : "");
    try {
      let userMeta;
      let sendOptions;
      if (hasStaged) {
        const userMsgId = crypto.randomUUID?.() ?? `m-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        const persisted = await attachments.persistAll(sessionId, userMsgId);
        if (persisted.length) {
          userMeta = { attachments: persisted };
          sendOptions = { id: userMsgId };
        }
      }
      const rows = await send(textToSend, "user", userMeta, sendOptions);
      const { text, meta } = await resolvePlaygroundAssistantReplyAsync(
        trimmed || textToSend,
        profileId,
        undefined,
        userMeta,
        rows ?? undefined
      );
      await send(text, "assistant", meta);
    } catch (e) {
      console.error("[UseCaseBuilderChat] send failed", e);
      setMessage(trimmed);
    }
  };

  return (
    <div className="flex h-[calc(100vh-4.5rem)] min-h-0 w-full flex-col overflow-hidden">
      {breadcrumbTrail && breadcrumbTrail.length > 0 ? (
        <div className="shrink-0 border-b border-[#f2f2f5] bg-surface px-6 py-2">
          <Breadcrumb items={[...breadcrumbTrail, { label: "Chat" }]} />
        </div>
      ) : null}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-6 py-3">
          <div className="mx-auto flex w-full max-w-[768px] flex-col items-center">
            <div className="flex w-full items-center gap-3">
              <div className="h-px flex-1 bg-border" />
              <span className="text-xs font-medium text-text-muted">Today · EnterpriseGPT</span>
              <div className="h-px flex-1 bg-border" />
            </div>

            <div className="mt-6 w-full space-y-6">
              {!ready ? (
                <p className="text-sm text-text-secondary">Loading conversation…</p>
              ) : (
                collapseAdjacentDuplicateUserMessages(messages).map((msg, msgIdx, arr) => {
                  const userLensLine =
                    msg.role === "user"
                      ? (msg.meta?.lens ??
                        (msg.meta?.horizon === "week"
                          ? "This week"
                          : msg.meta?.horizon === "quarter"
                            ? "This quarter"
                            : null))
                      : null;
                  return (
                  <div key={msg.id} className="space-y-6">
                    {msg.role === "user" ? (
                      <div className="flex w-full justify-end">
                        <div className="min-w-0 max-w-[78%] rounded-2xl bg-primary-light dark:bg-primary/20 px-4 py-2.5">
                          {userLensLine ? (
                            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-primary">
                              {userLensLine}
                            </p>
                          ) : null}
                          <p className="whitespace-pre-wrap break-words text-[15px] leading-relaxed text-text-primary">
                            {msg.text}
                          </p>
                          <SentAttachmentChips attachments={msg.meta?.attachments} />
                        </div>
                      </div>
                    ) : (
                      <PlaygroundAssistantMessage
                        msg={msg}
                        onBuildUseCase={() => { clearSpecDraft(); navigate("/use-cases/builder/describe"); }}
                        onRetry={async () => {
                          const prevUser = arr.slice(0, msgIdx).reverse().find((m) => m.role === "user");
                          if (!prevUser?.text) return;
                          const { text: rText, meta: rMeta } = await resolvePlaygroundAssistantReplyAsync(prevUser.text, profileId);
                          await send(rText, "assistant", rMeta);
                        }}
                      />
                    )}
                  </div>
                  );
                })
              )}
              <div ref={messagesEndRef} className="h-px w-full shrink-0" aria-hidden />
            </div>
          </div>
        </div>

        <div className="shrink-0 px-6 pt-3 pb-4">
          <div className="mx-auto max-w-[768px]">
            {attachments.lastError ? (
              <p className="mb-2 text-xs text-[#b42318]" role="status">
                {attachments.lastError}
              </p>
            ) : null}
            <MicErrorMessage error={dictation.error} />
            <div
              ref={inputBoxRef}
              className={`w-full shadow-[0px_9px_9px_0px_rgba(0,0,0,0.01)] ${
                inputFocused ? "playground-gradient-border" : "rounded-2xl border-2 border-border"
              }`}
              onFocusCapture={() => setInputFocused(true)}
              onBlurCapture={(e) => {
                if (!inputBoxRef.current?.contains(e.relatedTarget)) setInputFocused(false);
              }}
            >
              <div className="flex flex-col justify-between rounded-[14px] bg-surface px-4 py-4 sm:px-[18px]">
                <PendingAttachmentChips
                  staged={attachments.staged}
                  onRemove={attachments.removeStaged}
                />
                <ChatComposerTextarea
                  value={message}
                  overlayValue={
                    dictation.listening && dictation.interim
                      ? `${message ? `${message.trimEnd()} ` : ""}${dictation.interim}`
                      : undefined
                  }
                  onChange={setMessage}
                  onSend={() => void handleSend()}
                  placeholder={dictation.listening ? "Listening..." : "Ask anything"}
                  ariaLabel="Ask anything"
                />
                <div className="mt-2 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <label
                      className="flex cursor-pointer items-center rounded-xl p-2 text-text-muted transition hover:bg-surface-secondary hover:text-primary"
                      aria-label="Attach files"
                      title="Attach files"
                      tabIndex={0}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          attachments.openPicker();
                        }
                      }}
                    >
                      <input ref={attachments.inputRef} {...attachments.fileInputProps} />
                      <Paperclip size={18} className="pointer-events-none" />
                    </label>
                    <button
                      type="button"
                      aria-label="Data sources"
                      title="Data sources"
                      className="rounded p-1.5 text-text-muted transition hover:bg-surface-secondary hover:text-primary"
                    >
                      <Database size={18} />
                    </button>
                    <button
                      type="button"
                      aria-label="Prompt library"
                      title="Prompt library"
                      className="flex items-center gap-1.5 rounded-lg border border-border-light bg-surface-secondary px-2 py-1.5 text-sm text-text-muted transition hover:border-border hover:text-text-tertiary"
                    >
                      <LayoutGrid size={18} />
                      <span className="font-medium tabular-nums">1 of 10</span>
                    </button>
                  </div>
                  <div className="flex items-center gap-2">
                    <MicButton
                      supported={dictation.supported}
                      listening={dictation.listening}
                      onToggle={dictation.toggle}
                      size={24}
                    />
                    <button
                      type="button"
                      onClick={handleSend}
                      aria-label="Send message"
                      title="Send"
                      className={`flex h-10 w-10 items-center justify-center rounded-[8px] transition-colors ${
                        message.trim() || attachments.staged.length > 0
                          ? "bg-primary text-white hover:bg-primary-hover"
                          : "bg-surface-tertiary text-[#c8cdd8] cursor-default"
                      }`}
                    >
                      <PaperPlaneSendIcon size={24} />
                    </button>
                  </div>
                </div>
              </div>
            </div>
            <p className="mx-auto mt-2 max-w-[530px] text-center text-xs text-text-muted">
              AI-generated insights are based on connected data sources. Critical business decisions should be verified by domain experts.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
