{{- define "voxdesk.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- define "voxdesk.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "voxdesk.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- define "voxdesk.labels" -}}
app.kubernetes.io/name: {{ include "voxdesk.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}
{{- define "voxdesk.serviceAccountName" -}}
{{- $serviceAccount := .Values.serviceAccount | default dict -}}
{{- if $serviceAccount.name -}}
{{- $serviceAccount.name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- include "voxdesk.fullname" . -}}
{{- end -}}
{{- end }}
