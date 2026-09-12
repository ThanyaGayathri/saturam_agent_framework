// Dictation mic button. Shares visual state and error copy across chats.
import { Mic, MicOff } from "lucide-react";

function humanErrorMessage(error) {
  switch (error) {
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone permission was blocked. Enable it in your browser settings and try again.";
    case "no-speech":
      return "No speech was detected. Try again a bit closer to the microphone.";
    case "audio-capture":
      return "No microphone was found on this device.";
    case "network":
      return "Voice input needs a network connection.";
    case "start-failed":
      return "Voice input could not start. Try again.";
    default:
      return error ? "Voice input is not available right now." : null;
  }
}

/**
 * Visual states:
 *   - unsupported: dimmed, not clickable, tooltip explains browser requirement.
 *   - idle: neutral, click to start.
 *   - listening: filled red with subtle pulse, click to stop.
 */
export function MicButton({ supported, listening, onToggle, size = 18, className = "" }) {
  if (!supported) {
    return (
      <button
        type="button"
        disabled
        aria-label="Voice input not supported in this browser"
        title="Voice input works in Chrome or Edge on Windows, and Safari on iOS."
        className={`rounded p-1.5 text-[#d5d7da] ${className}`}
      >
        <MicOff size={size} />
      </button>
    );
  }

  if (listening) {
    return (
      <div className={`relative flex items-center justify-center ${className}`}>
        <span
          className="absolute rounded-full bg-red-300"
          style={{
            width: size + 20,
            height: size + 20,
            animation: "mic-ring 1.4s ease-out infinite",
          }}
        />
        <span
          className="absolute rounded-full bg-red-200"
          style={{
            width: size + 20,
            height: size + 20,
            animation: "mic-ring 1.4s ease-out 0.5s infinite",
          }}
        />
        <button
          type="button"
          onClick={onToggle}
          aria-pressed
          aria-label="Stop voice input"
          title="Stop voice input"
          className="relative z-10 rounded-full bg-[#fee4e2] p-2 text-[#b42318] transition-colors hover:bg-[#fecdca]"
        >
          <Mic size={size} />
        </button>
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={false}
      aria-label="Start voice input"
      title="Speak to type"
      className={`rounded p-1.5 text-text-muted transition-colors hover:bg-surface-secondary hover:text-primary ${className}`}
    >
      <Mic size={size} />
    </button>
  );
}

export function MicErrorMessage({ error }) {
  const msg = humanErrorMessage(error);
  if (!msg) return null;
  return (
    <p className="mb-2 text-xs text-[#b42318]" role="status">
      {msg}
    </p>
  );
}
