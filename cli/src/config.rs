use std::fs;
use std::io::Write;
#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::PathBuf;

use anyhow::{Context, Result, bail};
use reqwest::Url;
use serde::{Deserialize, Serialize};

#[derive(Clone, Default, Deserialize, Serialize)]
pub struct StoredConfig {
    pub url: Option<String>,
    pub token: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tenant: Option<String>,
}

pub struct ResolvedConfig {
    pub url: String,
    pub token: String,
    pub tenant: Option<String>,
    pub server_alias: String,
}

impl StoredConfig {
    pub fn load() -> Result<Self> {
        let path = config_path()?;
        if !path.exists() {
            return Ok(Self::default());
        }
        let contents = fs::read_to_string(&path)
            .with_context(|| format!("failed to read {}", path.display()))?;
        toml::from_str(&contents).with_context(|| format!("invalid config at {}", path.display()))
    }

    pub fn resolve_with_tenant(
        self,
        url: Option<String>,
        token: Option<String>,
        tenant: Option<String>,
    ) -> Result<ResolvedConfig> {
        let resolved_url = self.resolve_url(url)?;
        let token = token
            .or_else(|| std::env::var("DEVBOX_TOKEN").ok())
            .or(self.token)
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| {
                anyhow::anyhow!("not logged in; run `devbox login` or set DEVBOX_TOKEN")
            })?;
        let tenant = tenant
            .or_else(|| std::env::var("DEVBOX_TENANT").ok())
            .or(self.tenant)
            .filter(|value| !value.trim().is_empty())
            .map(|value| normalize_tenant(&value))
            .transpose()?;
        let parsed = Url::parse(&resolved_url).context("invalid Devboxes API URL")?;
        let server_alias = server_alias(&parsed);
        Ok(ResolvedConfig {
            url: resolved_url,
            token,
            tenant,
            server_alias,
        })
    }

    pub fn resolve_url(&self, url: Option<String>) -> Result<String> {
        let url = url
            .or_else(|| std::env::var("DEVBOX_URL").ok())
            .or_else(|| self.url.clone())
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| {
                anyhow::anyhow!(
                    "Devboxes API URL is not configured; run `devbox login --url https://devboxes.example.com` or set DEVBOX_URL"
                )
            })?;
        let parsed = Url::parse(&url).context("invalid Devboxes API URL")?;
        let local_http = parsed.scheme() == "http"
            && matches!(parsed.host_str(), Some("localhost" | "127.0.0.1" | "::1"));
        if parsed.scheme() != "https" && !local_http {
            bail!("refusing non-HTTPS API URL; only localhost may use plain HTTP");
        }
        if !parsed.username().is_empty() || parsed.password().is_some() {
            bail!("the Devboxes API URL must not contain credentials");
        }
        if parsed.query().is_some() || parsed.fragment().is_some() {
            bail!("the Devboxes API URL must not contain a query string or fragment");
        }
        Ok(url.trim_end_matches('/').to_owned())
    }

    pub fn save_with_tenant(url: &str, token: &str, tenant: Option<&str>) -> Result<PathBuf> {
        let path = config_path()?;
        let parent = path.parent().context("config path has no parent")?;
        fs::create_dir_all(parent)
            .with_context(|| format!("failed to create {}", parent.display()))?;
        let content = toml::to_string_pretty(&Self {
            url: Some(url.trim_end_matches('/').to_owned()),
            token: Some(token.to_owned()),
            tenant: tenant.map(str::to_owned),
        })?;

        let mut options = fs::OpenOptions::new();
        options.create(true).truncate(true).write(true);
        #[cfg(unix)]
        options.mode(0o600);
        let mut file = options
            .open(&path)
            .with_context(|| format!("failed to write {}", path.display()))?;
        file.write_all(content.as_bytes())?;
        #[cfg(unix)]
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600))?;
        Ok(path)
    }
}

fn normalize_tenant(value: &str) -> Result<String> {
    let value = value.trim().to_ascii_lowercase();
    let valid_length = (1..=40).contains(&value.len());
    let valid_edges = value
        .as_bytes()
        .first()
        .is_some_and(u8::is_ascii_alphanumeric)
        && value
            .as_bytes()
            .last()
            .is_some_and(u8::is_ascii_alphanumeric);
    let valid_characters = value.bytes().all(|character| {
        character.is_ascii_lowercase() || character.is_ascii_digit() || character == b'-'
    });
    if valid_length && valid_edges && valid_characters {
        Ok(value)
    } else {
        bail!(
            "invalid tenant ID; use 1-40 lowercase letters, digits, or hyphens and start and end alphanumeric"
        )
    }
}

fn server_alias(url: &Url) -> String {
    let identity = format!(
        "{}-{}{}",
        url.host_str().unwrap_or("server"),
        url.port_or_known_default().unwrap_or(0),
        url.path()
    );
    let normalized = identity
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() {
                character.to_ascii_lowercase()
            } else {
                '-'
            }
        })
        .collect::<String>();
    format!("devboxes-{}", normalized.trim_matches('-'))
}

fn config_path() -> Result<PathBuf> {
    if let Some(path) = std::env::var_os("DEVBOX_CONFIG").filter(|value| !value.is_empty()) {
        return Ok(PathBuf::from(path));
    }
    let base = dirs::config_dir().context("could not determine the user config directory")?;
    Ok(base.join("devbox").join("config.toml"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_https_or_local_http_is_allowed() {
        let config = StoredConfig {
            url: Some("http://devboxes.example.com".to_owned()),
            token: Some("secret".to_owned()),
            tenant: None,
        };
        assert!(config.resolve_with_tenant(None, None, None).is_err());

        let lookalike = StoredConfig {
            url: Some("http://localhost.example.com".to_owned()),
            token: Some("secret".to_owned()),
            tenant: None,
        };
        assert!(lookalike.resolve_with_tenant(None, None, None).is_err());

        let local = StoredConfig {
            url: Some("http://localhost:8000".to_owned()),
            token: Some("secret".to_owned()),
            tenant: None,
        };
        assert!(local.resolve_with_tenant(None, None, None).is_ok());

        let loopback = StoredConfig {
            url: Some("http://127.0.0.1:8000".to_owned()),
            token: Some("secret".to_owned()),
            tenant: None,
        };
        assert!(loopback.resolve_with_tenant(None, None, None).is_ok());
    }

    #[test]
    fn api_url_is_required_when_none_is_configured() {
        let config = StoredConfig {
            url: None,
            token: Some("secret".to_owned()),
            tenant: None,
        };

        assert!(config.resolve_with_tenant(None, None, None).is_err());
    }

    #[test]
    fn server_alias_distinguishes_installations() {
        let first = StoredConfig {
            url: Some("https://devboxes-one.example.com".to_owned()),
            token: Some("secret".to_owned()),
            tenant: None,
        }
        .resolve_with_tenant(None, None, None)
        .unwrap();
        let second = StoredConfig {
            url: Some("https://devboxes-two.example.com".to_owned()),
            token: Some("secret".to_owned()),
            tenant: None,
        }
        .resolve_with_tenant(None, None, None)
        .unwrap();

        assert_ne!(first.server_alias, second.server_alias);
    }

    #[test]
    fn api_url_rejects_query_and_fragment_components() {
        for url in [
            "https://devboxes.example.com?token=bad",
            "https://devboxes.example.com#api",
        ] {
            let config = StoredConfig {
                url: Some(url.to_owned()),
                token: Some("secret".to_owned()),
                tenant: None,
            };
            assert!(config.resolve_with_tenant(None, None, None).is_err());
        }
    }

    #[test]
    fn tenant_selection_is_normalized_and_validated() {
        let selected = StoredConfig {
            url: Some("https://devboxes.example.com".to_owned()),
            token: Some("secret".to_owned()),
            tenant: Some("platform".to_owned()),
        }
        .resolve_with_tenant(None, None, Some(" Research ".to_owned()))
        .unwrap();

        assert_eq!(selected.tenant.as_deref(), Some("research"));
        assert!(
            StoredConfig {
                url: Some("https://devboxes.example.com".to_owned()),
                token: Some("secret".to_owned()),
                tenant: None,
            }
            .resolve_with_tenant(None, None, Some("../other".to_owned()))
            .is_err()
        );
    }

    #[test]
    fn config_file_permissions_are_private_on_unix() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("config.toml");
        let mut options = fs::OpenOptions::new();
        options.create(true).truncate(true).write(true);
        #[cfg(unix)]
        options.mode(0o600);
        options
            .open(&path)
            .unwrap()
            .write_all(b"token='x'")
            .unwrap();
        #[cfg(unix)]
        assert_eq!(
            fs::metadata(path).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }
}
