{{- define "kb.name" -}}kb-assistant{{- end -}}

{{- define "kb.fullname" -}}
{{- if contains "kb-assistant" .Release.Name -}}
{{- .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-kb-assistant" .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "kb.labels" -}}
app.kubernetes.io/name: {{ include "kb.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "kb.selector" -}}
app.kubernetes.io/name: {{ include "kb.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "kb.image" -}}
{{- if .digest -}}
{{ .repository }}@{{ .digest }}
{{- else -}}
{{ .repository }}:{{ .tag }}
{{- end -}}
{{- end -}}

{{/*
Restricted Pod Security Standard: non-root, no privilege escalation, no
capabilities. The user is numeric so the kubelet can verify runAsNonRoot; the
backend image runs as 10001, the web image as node (1000). fsGroup lets that
user read mounted secrets.
*/}}
{{- define "kb.podSecurity" -}}
automountServiceAccountToken: false
securityContext:
  runAsNonRoot: true
  runAsUser: {{ .uid }}
  runAsGroup: {{ .uid }}
  fsGroup: {{ .uid }}
  seccompProfile:
    type: RuntimeDefault
{{- end -}}

{{- define "kb.containerSecurity" -}}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: [ALL]
{{- end -}}

{{/* Backend environment: non-secret config, then the secret, then tracing. */}}
{{- define "kb.backendEnv" -}}
envFrom:
  - configMapRef:
      name: {{ include "kb.fullname" . }}-config
  - secretRef:
      name: {{ .Values.secrets.backend }}
env:
  - name: KB_AUTH__PRIVATE_KEY_FILE
    value: /run/secrets/kb/jwt_signing_key.pem
{{- if .Values.tracing.otlpEndpoint }}
  - name: OTEL_EXPORTER_OTLP_ENDPOINT
    value: {{ .Values.tracing.otlpEndpoint | quote }}
{{- end }}
{{- end -}}
