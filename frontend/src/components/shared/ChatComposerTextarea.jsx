// Auto-growing textarea used by the Playground and Use Case builder chat
// composers. Dictated speech can get long, and a single-line <input> hides
// everything past the right edge. A textarea lets the transcript wrap so the
// user can see it while they're talking, and Enter still sends (Shift+Enter
// adds a newline).
//
// Props:
//  - value: the committed message text (what will actually be sent)
//  - overlayValue: optional display-only value used for live dictation
//    transcripts; when provided, it is shown in place of `value` but is NOT
//    written back through onChange (the parent controls `value` directly)
//  - onChange(text), onSend(): controlled input + Enter-to-send
//  - placeholder, ariaLabel: standard textarea props
import { useLayoutEffect, useRef, forwardRef } from "react";

const MAX_VISIBLE_LINES = 6;
const LINE_HEIGHT_PX = 24; // matches Tailwind `leading-6`

const ChatComposerTextarea = forwardRef(function ChatComposerTextarea({
  value,
  overlayValue,
  onChange,
  onSend,
  placeholder,
  ariaLabel,
}, forwardedRef) {
  const innerRef = useRef(null);
  const displayValue = overlayValue != null ? overlayValue : value;

  // Resize on every change so dictation interim text grows the box live.
  useLayoutEffect(() => {
    const el = innerRef.current;
    if (!el) return;
    el.style.height = "0px";
    const maxH = LINE_HEIGHT_PX * MAX_VISIBLE_LINES;
    el.style.height = `${Math.min(el.scrollHeight, maxH)}px`;
  }, [displayValue]);

  const handleKeyDown = (e) => {
    if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return;
    e.preventDefault();
    onSend?.();
  };

  // The box is sized by the inline height the effect above sets. It must not be a flex item
  // that grows or shrinks: in a column composer `flex-1` means `flex-basis: 0%`, which wins
  // over that height and pins the field to one line however much is typed.
  return (
    <textarea
      ref={(el) => {
        innerRef.current = el;
        if (typeof forwardedRef === "function") forwardedRef(el);
        else if (forwardedRef) forwardedRef.current = el;
      }}
      rows={1}
      placeholder={placeholder}
      aria-label={ariaLabel}
      value={displayValue}
      onChange={(e) => onChange?.(e.target.value)}
      onKeyDown={handleKeyDown}
      className="block w-full shrink-0 resize-none overflow-y-auto bg-transparent py-1 text-base leading-6 text-[color:var(--app-text-primary)] placeholder:text-text-muted outline-none"
    />
  );
});

export default ChatComposerTextarea;
