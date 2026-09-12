import {
  DEFAULT_DEMO_PROFILE_ID,
  isDemoProfileId,
  normalizeDemoProfileId,
  isDemoRoleId,
  normalizeDemoRoleId,
} from "../domain/demo-profiles";

const ACTIVE_PROFILE_STORAGE_KEY = "retailgpt:demo-profile-active:v1";
const ACTIVE_ROLE_STORAGE_KEY = "retailgpt:demo-role-active:v1";

export function readActiveDemoProfileId() {
  try {
    const raw = localStorage.getItem(ACTIVE_PROFILE_STORAGE_KEY);
    if (raw != null && isDemoProfileId(raw)) return raw;
  } catch {
    /* ignore */
  }
  return DEFAULT_DEMO_PROFILE_ID;
}

export function writeActiveDemoProfileId(profileId) {
  const id = normalizeDemoProfileId(profileId);
  try {
    localStorage.setItem(ACTIVE_PROFILE_STORAGE_KEY, id);
  } catch {
    /* ignore */
  }
  return id;
}

export function readActiveDemoRole() {
  try {
    const raw = localStorage.getItem(ACTIVE_ROLE_STORAGE_KEY);
    if (raw != null && isDemoRoleId(raw)) return raw;
  } catch {
    /* ignore */
  }
  return normalizeDemoRoleId(undefined);
}

export function writeActiveDemoRole(roleId) {
  const id = normalizeDemoRoleId(roleId);
  try {
    localStorage.setItem(ACTIVE_ROLE_STORAGE_KEY, id);
  } catch {
    /* ignore */
  }
  return id;
}

export { ACTIVE_PROFILE_STORAGE_KEY, ACTIVE_ROLE_STORAGE_KEY };
