use chrono::{DateTime, Utc};
use clap::ValueEnum;
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, ValueEnum)]
#[serde(rename_all = "lowercase")]
pub enum Preset {
    Small,
    Medium,
    Large,
}

impl std::fmt::Display for Preset {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(match self {
            Self::Small => "small",
            Self::Medium => "medium",
            Self::Large => "large",
        })
    }
}

#[derive(Debug, Serialize)]
pub struct GpuRequest<'a> {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub profile: Option<&'a str>,
}

#[derive(Debug, Serialize)]
pub struct CreateDevbox<'a> {
    pub name: &'a str,
    pub preset: Preset,
    pub ttl_hours: u16,
    pub repository: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub image: Option<&'a str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gpu: Option<GpuRequest<'a>>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct GpuAllocation {
    pub profile: String,
    pub display_name: String,
    pub resource_name: String,
    pub count: u16,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CustomImagePort {
    pub name: String,
    pub container_port: u16,
    pub protocol: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CustomImageAllocation {
    pub profile: String,
    pub display_name: String,
    pub mode: String,
    #[serde(default)]
    pub ports: Vec<CustomImagePort>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Devbox {
    pub name: String,
    pub state: String,
    pub preset: Preset,
    pub created_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
    pub repository: Option<String>,
    pub ssh_host: Option<String>,
    pub ssh_port: u16,
    pub ssh_command: Option<String>,
    pub pod_name: Option<String>,
    pub pod_ready: bool,
    pub restarts: u32,
    pub storage_size: String,
    pub message: Option<String>,
    #[serde(default)]
    pub gpu: Option<GpuAllocation>,
    #[serde(default)]
    pub image: Option<CustomImageAllocation>,
}

#[derive(Debug, Deserialize)]
pub struct DevboxList {
    pub items: Vec<Devbox>,
}

#[derive(Debug, Deserialize)]
pub struct DeleteResult {
    pub message: String,
}

#[derive(Debug, Deserialize)]
pub struct WhoAmI {
    pub user: String,
    pub mode: String,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct Capabilities {
    pub gpu: GpuCapabilities,
    #[serde(default)]
    pub images: CustomImageCapabilities,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct GpuCapabilities {
    pub enabled: bool,
    pub default_profile: Option<String>,
    pub profiles: Vec<GpuProfileSummary>,
}

#[derive(Debug, Default, Deserialize, Serialize)]
pub struct CustomImageCapabilities {
    pub enabled: bool,
    pub profiles: Vec<CustomImageProfileSummary>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct CustomImageProfileSummary {
    pub name: String,
    pub display_name: String,
    pub description: Option<String>,
    pub mode: String,
    #[serde(default)]
    pub ports: Vec<CustomImagePort>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct GpuProfileSummary {
    pub name: String,
    pub display_name: String,
    pub description: Option<String>,
    pub resource_name: String,
    pub count: u16,
    #[serde(rename = "default")]
    pub is_default: bool,
}

#[derive(Debug, Serialize)]
pub struct CliTokenRequest<'a> {
    pub grant_type: &'static str,
    pub code: &'a str,
    pub code_verifier: &'a str,
    pub client_id: &'static str,
    pub redirect_uri: &'a str,
}

#[derive(Debug, Deserialize)]
pub struct CliTokenResponse {
    pub access_token: String,
    pub token_type: String,
    pub expires_in: u64,
    pub scope: String,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct InsightsEnvelope<T> {
    pub schema_version: u32,
    pub generated_at: String,
    pub enabled: bool,
    pub effective_range: Option<InsightsRange>,
    pub coverage: InsightsCoverage,
    pub capabilities: Value,
    pub data: Option<T>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct InsightsRange {
    pub since: String,
    pub until: String,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct InsightsCoverage {
    pub status: String,
    pub freshness_seconds: Option<u64>,
    pub collectors: Vec<CollectorStatus>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct CollectorStatus {
    #[serde(rename = "box")]
    pub box_name: String,
    pub collector: String,
    pub version: String,
    pub status: String,
    pub capability_reason: Option<String>,
    pub last_seen_at: String,
    pub freshness_seconds: u64,
    pub queue_bytes: u64,
    pub dropped_batches: u64,
    pub dropped_points: u64,
    pub provider_versions: std::collections::BTreeMap<String, String>,
    pub last_successful_send_at: Option<String>,
    pub last_error_category: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct InsightsSummary {
    pub ai: AiSummary,
    pub code: CodeSummary,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct AiSummary {
    pub totals: AiTotals,
    pub providers: std::collections::BTreeMap<String, ProviderSummary>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct AiTotals {
    pub sessions: u64,
    pub tokens: u64,
    pub provider_reported_cost_usd: Option<f64>,
    pub active_seconds: Option<f64>,
    pub ai_lines: Option<u64>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct ProviderSummary {
    pub sessions: u64,
    pub tokens: std::collections::BTreeMap<String, u64>,
    pub total_tokens: u64,
    pub cost_usd: Option<f64>,
    pub active_seconds: Option<f64>,
    pub ai_lines: std::collections::BTreeMap<String, u64>,
    pub models: Vec<String>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct CodeSummary {
    pub commits: u64,
    pub additions: u64,
    pub deletions: u64,
    pub files_changed: u64,
    pub binary_files: u64,
    pub working_tree: WorkingTreeSummary,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct WorkingTreeSummary {
    pub staged_additions: u64,
    pub staged_deletions: u64,
    pub staged_files: u64,
    pub unstaged_additions: u64,
    pub unstaged_deletions: u64,
    pub unstaged_files: u64,
    pub binary_files: u64,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct InsightsStatusData {
    pub collectors: Vec<CollectorStatus>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct InsightsActivityData {
    pub items: Vec<InsightsActivity>,
    pub next_cursor: Option<String>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct InsightsActivity {
    pub id: u64,
    #[serde(rename = "type")]
    pub activity_type: String,
    #[serde(rename = "box")]
    pub box_name: String,
    pub repo: String,
    pub observed_at: String,
    pub additions: u64,
    pub deletions: u64,
    pub files_changed: u64,
    pub binary_files: u64,
    pub is_merge: bool,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct InsightsPurgeResult {
    pub schema_version: u32,
    pub generated_at: String,
    #[serde(rename = "box")]
    pub box_name: String,
    pub purged_instances: u64,
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{CreateDevbox, Devbox, GpuRequest, Preset};

    #[test]
    fn create_payload_omits_cpu_only_gpu_state() {
        let payload = CreateDevbox {
            name: "atlas",
            preset: Preset::Medium,
            ttl_hours: 24,
            repository: None,
            image: None,
            gpu: None,
        };

        assert_eq!(
            serde_json::to_value(payload).unwrap(),
            json!({
                "name": "atlas",
                "preset": "medium",
                "ttl_hours": 24,
                "repository": null
            })
        );
    }

    #[test]
    fn create_payload_distinguishes_default_and_named_gpu_profiles() {
        let default_gpu = CreateDevbox {
            name: "inference",
            preset: Preset::Small,
            ttl_hours: 24,
            repository: None,
            image: None,
            gpu: Some(GpuRequest { profile: None }),
        };
        let named_gpu = CreateDevbox {
            name: "training",
            preset: Preset::Large,
            ttl_hours: 72,
            repository: None,
            image: None,
            gpu: Some(GpuRequest {
                profile: Some("nvidia-l4"),
            }),
        };

        assert_eq!(serde_json::to_value(default_gpu).unwrap()["gpu"], json!({}));
        assert_eq!(
            serde_json::to_value(named_gpu).unwrap()["gpu"],
            json!({"profile": "nvidia-l4"})
        );
    }

    #[test]
    fn create_payload_includes_an_explicit_image_selector() {
        let payload = CreateDevbox {
            name: "nginx",
            preset: Preset::Small,
            ttl_hours: 24,
            repository: None,
            image: Some("docker.io/library/nginx:1.27"),
            gpu: None,
        };

        assert_eq!(
            serde_json::to_value(payload).unwrap()["image"],
            json!("docker.io/library/nginx:1.27")
        );
    }

    #[test]
    fn devbox_deserialization_accepts_pre_gpu_controller_responses() {
        let box_info: Devbox = serde_json::from_value(json!({
            "name": "atlas",
            "state": "ready",
            "preset": "medium",
            "created_at": "2026-07-20T12:00:00Z",
            "expires_at": "2026-07-21T12:00:00Z",
            "repository": null,
            "ssh_host": "192.0.2.10",
            "ssh_port": 22,
            "ssh_command": "ssh dev@192.0.2.10",
            "pod_name": "devbox-atlas-example",
            "pod_ready": true,
            "restarts": 0,
            "storage_size": "30Gi",
            "message": null
        }))
        .unwrap();

        assert!(box_info.gpu.is_none());
    }
}
