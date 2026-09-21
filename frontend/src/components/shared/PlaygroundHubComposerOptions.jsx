// Shared "Ask anything" toolbar: attach, data sources, connectors, mic, send. Matches Playground home hub.
//
// The Database and Connectors menus moved into `ComposerSourceMenus`, which every composer
// shares. The "New database Connection" slide-over that used to live here went with them: it
// was a form whose Confirm button only closed it, so "Add New Database" now opens the real
// Configure form instead of a look-alike.
import { Paperclip } from "lucide-react";
import { MicButton } from "./MicButton";
import { PaperPlaneSendIcon } from "../icons/PaperPlaneSendIcon";
import ComposerSourceMenus from "./ComposerSourceMenus";

export default function PlaygroundHubComposerOptions({
  attachments,
  dictation,
  onSend,
  actionsRowClassName = "mt-2",
  sendButtonTitle = "Send",
  /** "up" when the composer sits at the bottom of the screen, as it does in chat. */
  menuDirection = "down",
}) {
  return (
    <div className={`flex items-center justify-between ${actionsRowClassName}`}>
      <div className="flex items-center gap-2">
        <label
          className="flex cursor-pointer items-center rounded p-1.5 text-text-muted transition hover:bg-surface-secondary hover:text-primary"
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

        <ComposerSourceMenus direction={menuDirection} />
      </div>

      <div className="flex items-center gap-2">
        <MicButton
          supported={dictation.supported}
          listening={dictation.listening}
          onToggle={dictation.toggle}
        />
        <button
          type="button"
          onClick={onSend}
          className="rounded p-1.5 text-text-muted transition hover:bg-surface-secondary hover:text-primary"
          title={sendButtonTitle}
          aria-label={sendButtonTitle}
        >
          <PaperPlaneSendIcon size={18} />
        </button>
      </div>
    </div>
  );
}
