{{- define "egpt.labels" -}}
app.kubernetes.io/name: egpt
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}

{{- define "egpt.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end }}

{{/*
The consolidator runs one per customer. There is no replica-count value for it, and this guard
refuses to render if someone adds one. It cannot stop `kubectl scale` -- nothing in a chart can --
which is why the entrypoint holds a Postgres advisory lock and a second instance exits 3.
*/}}
{{- define "egpt.consolidatorGuard" -}}
{{- if hasKey .Values.consolidator "replicaCount" }}
{{- fail "consolidator.replicaCount is not a setting: the consolidator runs one per customer. Remove the key." }}
{{- end }}
{{- if hasKey .Values.consolidator "replicas" }}
{{- fail "consolidator.replicas is not a setting: the consolidator runs one per customer. Remove the key." }}
{{- end }}
{{- end }}

{{/* The environment every process shares, from the ConfigMap and the Secret. */}}
{{- define "egpt.envFrom" -}}
envFrom:
  - configMapRef: {name: {{ .Release.Name }}-env}
  - secretRef: {name: {{ .Release.Name }}-secrets}
{{- end }}

{{/*
Every workload runs the same image as USER 10001 (deploy/Dockerfile) already; declaring it here
costs nothing and is what an AKS policy add-on (Azure Policy / Pod Security admission) will ask
for on day one. Shared by api, pipelines, consolidator and the migrate hook Job.
*/}}
{{- define "egpt.securityContext" -}}
runAsNonRoot: true
runAsUser: 10001
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities: {drop: ["ALL"]}
{{- end }}

{{/*
Auth on means a JWKS document is present. The framework reads a file only; a URL key would be a
placeholder the pods cannot honour -- they would mount a ConfigMap that never rendered.

An empty auth.audience is caught here too: the product's build_agent_os makes the same refusal
at runtime (EGPT_RETAIL_AUTH_AUDIENCE unset with a JWKS file configured silently disables
verify_audience, so every RS256 token the JWKS can verify would be accepted regardless of `aud`)
-- this guard is belt-and-braces so a values file with the mistake never reaches the pod.
*/}}
{{- define "egpt.authGuard" -}}
{{- if and .Values.auth.enabled (not .Values.auth.jwks) -}}
{{- fail "auth.enabled is true but auth.jwks is empty. Pass the JWKS document: --set-file auth.jwks=<jwks.json> (devauth keygen locally; Entra's discovery/v2.0/keys document on AKS)." -}}
{{- end -}}
{{- if and .Values.auth.enabled (not .Values.auth.audience) -}}
{{- fail "auth.enabled is true but auth.audience is empty. Every RS256 token signed by the configured JWKS would be accepted regardless of its `aud` claim. Set auth.audience." -}}
{{- end -}}
{{- end -}}
