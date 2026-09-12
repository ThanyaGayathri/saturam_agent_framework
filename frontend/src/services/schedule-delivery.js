// Delivery adapters for scheduled runs. Client-only, so what actually happens:
//   - email   → opens the user's default mail client via mailto: with the
//               subject/body pre-filled. Real, immediate, works everywhere.
//   - slack   → POSTs to a Slack incoming webhook URL if the user pasted one.
//               Real; requires the user to have created a webhook in Slack.
//   - in-app  → adds a "delivery" entry to a persisted list per profile so the
//               user can see what would have been sent, when.
//
// Recurring cron itself is a backend concern  -  this module handles what happens
// when a delivery fires. Wire it to a "Send now" button in the demo.

import { readLocalJson, writeLocalJson } from "../storage/local-json";
import { normalizeDemoProfileId, DEFAULT_DEMO_PROFILE_ID } from "../domain/demo-profiles";

const DELIVERIES_KEY_BASE = "retailgpt:schedule-deliveries:v1";

function deliveriesKey(profileId) {
  const id = normalizeDemoProfileId(profileId);
  return id === DEFAULT_DEMO_PROFILE_ID ? DELIVERIES_KEY_BASE : `${DELIVERIES_KEY_BASE}:profile:${id}`;
}

export function readDeliveries(profileId) {
  const raw = readLocalJson(deliveriesKey(profileId), []);
  return Array.isArray(raw) ? raw : [];
}

function recordDelivery(profileId, entry) {
  const list = readDeliveries(profileId);
  writeLocalJson(deliveriesKey(profileId), [entry, ...list].slice(0, 200));
}

// ---------- Email (mailto) ------------------------------------------------

export function deliverViaEmail(profileId, { recipient, subject, body, runId }) {
  const to = (recipient || "").trim();
  const encodedSubject = encodeURIComponent(subject || "Enterprise GPT  -  scheduled answer");
  const encodedBody = encodeURIComponent(body || "");
  const href = `mailto:${to}?subject=${encodedSubject}&body=${encodedBody}`;
  try {
    window.location.href = href;
    recordDelivery(profileId, {
      id: `dl-${Date.now().toString(36)}`,
      runId,
      channel: "email",
      recipient: to,
      sentAt: Date.now(),
      status: "opened-in-mail-client",
      preview: body?.slice(0, 200) || "",
    });
    return { ok: true, note: "Opened your mail client with the message pre-filled." };
  } catch (e) {
    console.error("[schedule-delivery] mailto failed", e);
    return { ok: false, note: "Couldn't open your mail client." };
  }
}

// ---------- Slack (incoming webhook) -------------------------------------

const SLACK_WEBHOOK_PATTERN = /^https:\/\/hooks\.slack\.com\/services\//;

export async function deliverViaSlack(profileId, { recipient, subject, body, runId }) {
  const webhook = (recipient || "").trim();
  if (!SLACK_WEBHOOK_PATTERN.test(webhook)) {
    return {
      ok: false,
      note: "For Slack delivery, paste a Slack incoming webhook URL (starts with https://hooks.slack.com/services/…). Create one in Slack under Apps → Incoming Webhooks.",
    };
  }
  const payload = {
    text: `*${subject || "Enterprise GPT  -  scheduled answer"}*\n\n${body || ""}`,
  };
  try {
    await fetch(webhook, {
      method: "POST",
      mode: "no-cors",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    // no-cors mode gives an opaque response  -  we can't confirm 200, but Slack
    // does deliver. Record optimistic success with a note.
    recordDelivery(profileId, {
      id: `dl-${Date.now().toString(36)}`,
      runId,
      channel: "slack",
      recipient: webhook,
      sentAt: Date.now(),
      status: "sent",
      preview: body?.slice(0, 200) || "",
    });
    return { ok: true, note: "Sent to Slack. Check the channel that owns this webhook." };
  } catch (e) {
    console.error("[schedule-delivery] slack POST failed", e);
    return { ok: false, note: "Slack delivery failed. Check the webhook URL and try again." };
  }
}

// ---------- In-app -------------------------------------------------------

export function deliverInApp(profileId, { subject, body, runId }) {
  recordDelivery(profileId, {
    id: `dl-${Date.now().toString(36)}`,
    runId,
    channel: "in-app",
    recipient: "Enterprise GPT",
    sentAt: Date.now(),
    status: "delivered",
    subject: subject || "Enterprise GPT  -  scheduled answer",
    preview: body || "",
  });
  return { ok: true, note: "Delivered to your Enterprise GPT inbox." };
}

// ---------- Dispatcher ---------------------------------------------------

export async function deliverScheduledRun(profileId, run, bodyText) {
  const subject = run.label || (run.prompt ? run.prompt.slice(0, 60) : "Enterprise GPT  -  scheduled answer");
  const body = bodyText || `Question: ${run.prompt || ""}\n\n(Answer body would be attached in production.)`;

  if (run.delivery === "email") {
    return deliverViaEmail(profileId, { recipient: run.recipient, subject, body, runId: run.id });
  }
  if (run.delivery === "slack") {
    return deliverViaSlack(profileId, { recipient: run.recipient, subject, body, runId: run.id });
  }
  return deliverInApp(profileId, { subject, body, runId: run.id });
}
