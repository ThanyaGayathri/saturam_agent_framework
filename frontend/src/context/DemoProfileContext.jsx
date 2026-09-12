import { createContext, useCallback, useContext, useMemo, useState } from "react";
import {
  DEFAULT_DEMO_PROFILE_ID,
  DEMO_PROFILES,
  DEMO_ROLES,
  demoProfileLabel,
  demoRoleLabel,
  normalizeDemoProfileId,
  scopedChatSessionId,
} from "../domain/demo-profiles";
import {
  homePathForProfile,
  isAnalyticsProfile,
  isGfiProfile,
  isMediaProfile,
  isMiddlebyProfile,
  isRetailAutomationProfile,
  navItemsForProfile,
} from "../domain/profile-capabilities";
import {
  readActiveDemoProfileId,
  writeActiveDemoProfileId,
  readActiveDemoRole,
  writeActiveDemoRole,
} from "../storage/demo-profile-preference";
import { storageKeysForProfile } from "../storage/keys";

const DemoProfileContext = createContext(null);

export function DemoProfileProvider({ children }) {
  const [profileId, setProfileIdState] = useState(() => readActiveDemoProfileId());
  const [role, setRoleState] = useState(() => readActiveDemoRole());

  const setProfileId = useCallback((next) => {
    const id = writeActiveDemoProfileId(next);
    setProfileIdState(id);
  }, []);

  const setRole = useCallback((next) => {
    const id = writeActiveDemoRole(next);
    setRoleState(id);
  }, []);

  const storageKeys = useMemo(() => storageKeysForProfile(profileId), [profileId]);

  const sessionForChat = useCallback((baseId) => scopedChatSessionId(baseId, profileId), [profileId]);

  const value = useMemo(
    () => ({
      profileId,
      profileLabel: demoProfileLabel(profileId),
      profiles: DEMO_PROFILES,
      setProfileId,
      role,
      roleLabel: demoRoleLabel(role),
      roles: DEMO_ROLES,
      setRole,
      storageKeys,
      sessionForChat,
      navItems: navItemsForProfile(profileId),
      homePath: homePathForProfile(profileId),
      isDefaultProfile: profileId === DEFAULT_DEMO_PROFILE_ID,
      isGfiProfile: isGfiProfile(profileId),
      isMiddlebyProfile: isMiddlebyProfile(profileId),
      isMediaProfile: isMediaProfile(profileId),
      isRetailAutomationProfile: isRetailAutomationProfile(profileId),
      isAnalyticsProfile: isAnalyticsProfile(profileId),
    }),
    [profileId, setProfileId, role, setRole, storageKeys, sessionForChat]
  );

  return <DemoProfileContext.Provider value={value}>{children}</DemoProfileContext.Provider>;
}

export function useDemoProfile() {
  const ctx = useContext(DemoProfileContext);
  if (!ctx) {
    throw new Error("useDemoProfile must be used within DemoProfileProvider");
  }
  return ctx;
}
