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
Auth on means a JWKS document is present. The framework reads a file only; a URL key would be a
placeholder the pods cannot honour -- they would mount a ConfigMap that never rendered.
*/}}
{{- define "egpt.authGuard" -}}
{{- if and .Values.auth.enabled (not .Values.auth.jwks) -}}
{{- fail "auth.enabled is true but auth.jwks is empty. Pass the JWKS document: --set-file auth.jwks=<jwks.json> (devauth keygen locally; Entra's discovery/v2.0/keys document on AKS)." -}}
{{- end -}}
{{- end -}}
