import { saveSpecDraft } from "../domain/use-case-draft-spec";
import { activationPayload, draftPayload, specPayload, useCaseWrite } from "../domain/use-case-platform";
import {
 activateUseCase, createUseCase, pollUseCaseRun, saveUseCaseDraft, saveUseCaseSpec, triggerUseCaseRun, updateUseCase,
} from "./use-cases-api";

/**
 * The builder's side of the platform: create-or-update the use case the spec describes,
 * store its spec and draft, activate it, run it. The platform id lives on the local spec
 * draft as `platform.useCaseId`, so every screen after the first sync talks about the same
 * row.
 *
 * Every call here is a network call to the real API; the screens show `err.detail` when one
 * fails and never pretend the platform did something it did not.
 */

/** Ensure the platform holds this spec: create it on first sync, update it after. Returns the
 * spec with `platform` filled in and saved to the draft. */
export async function syncUseCaseToPlatform(spec, sources = [], { signal } = {}) {
 const write = useCaseWrite(spec);
 let useCaseId = spec.platform?.useCaseId || null;
 if (useCaseId) {
  await updateUseCase(useCaseId, write, { signal });
 } else {
  const created = await createUseCase(write, { signal });
  useCaseId = created.id;
 }
 await saveUseCaseDraft(useCaseId, draftPayload(spec), { signal });
 const storedSpec = await saveUseCaseSpec(useCaseId, specPayload(spec, useCaseId, sources), { signal });
 const next = { ...spec, platform: { ...(spec.platform || {}), useCaseId, syncedAt: new Date().toISOString(), parameters: storedSpec.parameters } };
 return saveSpecDraft(next);
}

/** Activate on the platform. Returns `{spec, useCase}`; a 409 rejects with the preflight
 * result in `err.detail` (as text) and `err.status === 409`. */
export async function activateOnPlatform(spec, sources = [], { signal } = {}) {
 const synced = await syncUseCaseToPlatform(spec, sources, { signal });
 const useCase = await activateUseCase(synced.platform.useCaseId, activationPayload(synced), { signal });
 const next = { ...synced, platform: { ...synced.platform, version: useCase.version, status: useCase.status, activatedAt: new Date().toISOString() } };
 return { spec: saveSpecDraft(next), useCase };
}

/** Queue a run and poll it to the end. `onTick` sees each poll. */
export async function runOnPlatform(useCaseId, mode, { signal, onTick, request } = {}) {
 const queued = await triggerUseCaseRun(useCaseId, mode, { signal, request });
 onTick?.(queued);
 return pollUseCaseRun(useCaseId, queued.id, { signal, onTick });
}
