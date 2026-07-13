use std::fs;
use std::io::Write;
#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::PathBuf;

use anyhow::{Context, Result, bail};
use reqwest::Url;
use serde::{Deserialize, Serialize};

#[derive(Default, Deserialize, Serialize)]
pub struct StoredConfig {
    pub url: Option<String>,
    pub token: Option<String>,
}

pub struct ResolvedConfig {
    pub url: String,
    pub token: String,
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

    pub fn resolve(self, url: Option<String>, token: Option<String>) -> Result<ResolvedConfig> {
        let url = url
            .or_else(|| std::env::var("DEVBOX_URL").ok())
            .or(self.url)
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| {
                anyhow::anyhow!(
                    "Devboxes API URL is not configured; run `devbox login --url https://devboxes.example.com` or set DEVBOX_URL"
                )
            })?;
        let token = token
            .or_else(|| std::env::var("DEVBOX_TOKEN").ok())
            .or(self.token)
            .filter(|value| !value.trim().is_empty())
            .ok_or_else(|| {
                anyhow::anyhow!("not logged in; run `devbox login` or set DEVBOX_TOKEN")
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
        let server_alias = server_alias(&parsed);
        Ok(ResolvedConfig {
            url: url.trim_end_matches('/').to_owned(),
            token,
            server_alias,
        })
    }

    pub fn save(url: &str, token: &str) -> Result<PathBuf> {
        let path = config_path()?;
        let parent = path.parent().context("config path has no parent")?;
        fs::create_dir_all(parent)
            .with_context(|| format!("failed to create {}", parent.display()))?;
        let content = toml::to_string_pretty(&Self {
            url: Some(url.trim_end_matches('/').to_owned()),
            token: Some(token.to_owned()),
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
        };
        assert!(config.resolve(None, None).is_err());

        let lookalike = StoredConfig {
            url: Some("http://localhost.example.com".to_owned()),
            token: Some("secret".to_owned()),
        };
        assert!(lookalike.resolve(None, None).is_err());

        let local = StoredConfig {
            url: Some("http://localhost:8000".to_owned()),
            token: Some("secret".to_owned()),
        };
        assert!(local.resolve(None, None).is_ok());

        let loopback = StoredConfig {
            url: Some("http://127.0.0.1:8000".to_owned()),
            token: Some("secret".to_owned()),
        };
        assert!(loopback.resolve(None, None).is_ok());
    }

    #[test]
    fn api_url_is_required_when_none_is_configured() {
        let config = StoredConfig {
            url: None,
            token: Some("secret".to_owned()),
        };

        assert!(config.resolve(None, None).is_err());
    }

    #[test]
    fn server_alias_distinguishes_installations() {
        let first = StoredConfig {
            url: Some("https://devboxes-one.example.com".to_owned()),
            token: Some("secret".to_owned()),
        }
        .resolve(None, None)
        .unwrap();
        let second = StoredConfig {
            url: Some("https://devboxes-two.example.com".to_owned()),
            token: Some("secret".to_owned()),
        }
        .resolve(None, None)
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
            };
            assert!(config.resolve(None, None).is_err());
        }
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
