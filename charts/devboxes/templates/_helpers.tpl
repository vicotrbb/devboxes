{{- define "devboxes.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "devboxes.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "devboxes.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "devboxes.labels" -}}
helm.sh/chart: {{ include "devboxes.chart" . }}
app.kubernetes.io/name: {{ include "devboxes.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: devboxes
{{- end }}

{{- define "devboxes.selectorLabels" -}}
app.kubernetes.io/name: {{ include "devboxes.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: controller
{{- end }}

{{- define "devboxes.controllerServiceAccountName" -}}
{{- include "devboxes.fullname" . }}
{{- end }}

{{- define "devboxes.workspaceServiceAccountName" -}}
{{- if .Values.workspace.serviceAccount.create }}
{{- default (printf "%s-workspace" (include "devboxes.fullname" .)) .Values.workspace.serviceAccount.name }}
{{- else }}
{{- required "workspace.serviceAccount.name is required when serviceAccount.create is false" .Values.workspace.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "devboxes.controllerImage" -}}
{{- printf "%s:%s" .Values.controller.image.repository (default .Chart.AppVersion .Values.controller.image.tag) }}
{{- end }}

{{- define "devboxes.workspaceImage" -}}
{{- printf "%s:%s" .Values.workspace.image.repository (default .Chart.AppVersion .Values.workspace.image.tag) }}
{{- end }}

{{- define "devboxes.insightsClaimName" -}}
{{- default (printf "%s-insights" (include "devboxes.fullname" .)) .Values.insights.storage.existingClaim }}
{{- end }}
