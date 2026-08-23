{{- define "cmul8.name" -}}cmul8{{- end }}
{{- define "cmul8.fullname" -}}{{ printf "%s-cmul8" .Release.Name | trunc 63 | trimSuffix "-" }}{{- end }}
{{- define "cmul8.serviceAccountName" -}}{{ default (include "cmul8.fullname" .) .Values.serviceAccount.name }}{{- end }}
{{- define "cmul8.labels" -}}
app.kubernetes.io/name: {{ include "cmul8.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
cmul8.io/tenant: {{ .Values.tenantId | quote }}
cmul8.io/environment: {{ .Values.environment | quote }}
{{- end }}
{{- define "cmul8.image" -}}
{{- if .Values.image.digest -}}
{{ printf "%s@%s" .Values.image.repository .Values.image.digest }}
{{- else -}}
{{ printf "%s:%s" .Values.image.repository (.Values.image.tag | default .Chart.AppVersion) }}
{{- end -}}
{{- end }}
{{- define "cmul8.env" -}}
- name: CMUL8_DEPLOYMENT_MODE
  value: {{ .Values.deploymentMode | quote }}
- name: CMUL8_TENANT_ID
  value: {{ .Values.tenantId | quote }}
- name: CMUL8_ENVIRONMENT
  value: {{ .Values.environment | quote }}
- name: CMUL8_SECRET_PROVIDER
  value: {{ .Values.external.secretProvider | quote }}
- name: CMUL8_IMAGE_REGISTRY
  value: {{ .Values.image.repository | quote }}
- name: CMUL8_TLS_REQUIRED
  value: "true"
- name: CMUL8_POSTGRES_URL
  valueFrom:
    secretKeyRef: {name: {{ .Values.external.secretName | quote }}, key: {{ .Values.external.postgresKey | quote }}}
- name: CMUL8_REDIS_URL
  valueFrom:
    secretKeyRef: {name: {{ .Values.external.secretName | quote }}, key: {{ .Values.external.redisKey | quote }}}
- name: CMUL8_OBJECT_STORAGE_URL
  valueFrom:
    secretKeyRef: {name: {{ .Values.external.secretName | quote }}, key: {{ .Values.external.objectStorageKey | quote }}}
{{- end }}
