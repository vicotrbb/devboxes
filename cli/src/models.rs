use chrono::{DateTime, Utc};
use clap::ValueEnum;
use serde::{Deserialize, Serialize};

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
