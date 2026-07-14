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
pub struct CreateDevbox<'a> {
    pub name: &'a str,
    pub preset: Preset,
    pub ttl_hours: u16,
    pub repository: Option<&'a str>,
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
