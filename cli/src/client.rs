use std::time::Duration;

use anyhow::{Context, Result, bail};
use reqwest::{Client, Method, StatusCode};
use serde::Serialize;
use serde::de::DeserializeOwned;
use serde_json::Value;

use crate::models::{
    CliTokenRequest, CliTokenResponse, CreateDevbox, DeleteResult, Devbox, DevboxList,
    InsightsActivityData, InsightsEnvelope, InsightsPurgeResult, InsightsStatusData,
    InsightsSummary, WhoAmI,
};

pub struct ApiClient {
    base_url: String,
    token: String,
    http: Client,
}

impl ApiClient {
    pub fn new(base_url: String, token: String) -> Result<Self> {
        Ok(Self {
            base_url,
            token,
            http: http_client()?,
        })
    }

    pub async fn exchange_cli_code(
        base_url: &str,
        code: &str,
        code_verifier: &str,
        redirect_uri: &str,
    ) -> Result<CliTokenResponse> {
        let response = http_client()?
            .post(format!("{base_url}/api/v1/auth/cli/token"))
            .header("Accept", "application/json")
            .json(&CliTokenRequest {
                grant_type: "authorization_code",
                code,
                code_verifier,
                client_id: "devbox-cli",
                redirect_uri,
            })
            .send()
            .await
            .context("failed to exchange browser authorization")?;
        decode_response(response).await
    }

    pub async fn whoami(&self) -> Result<WhoAmI> {
        self.request(Method::GET, "/api/v1/whoami", Option::<&()>::None)
            .await
    }

    pub async fn list(&self) -> Result<Vec<Devbox>> {
        Ok(self
            .request::<(), DevboxList>(Method::GET, "/api/v1/devboxes", None)
            .await?
            .items)
    }

    pub async fn get(&self, name: &str) -> Result<Devbox> {
        self.request(
            Method::GET,
            &format!("/api/v1/devboxes/{name}"),
            Option::<&()>::None,
        )
        .await
    }

    pub async fn create(&self, payload: &CreateDevbox<'_>) -> Result<Devbox> {
        self.request(Method::POST, "/api/v1/devboxes", Some(payload))
            .await
    }

    pub async fn start(&self, name: &str) -> Result<Devbox> {
        self.request(
            Method::POST,
            &format!("/api/v1/devboxes/{name}/start"),
            Option::<&()>::None,
        )
        .await
    }

    pub async fn stop(&self, name: &str) -> Result<Devbox> {
        self.request(
            Method::POST,
            &format!("/api/v1/devboxes/{name}/stop"),
            Option::<&()>::None,
        )
        .await
    }

    pub async fn delete(&self, name: &str, purge: bool) -> Result<DeleteResult> {
        self.request(
            Method::DELETE,
            &format!("/api/v1/devboxes/{name}?purge={purge}"),
            Option::<&()>::None,
        )
        .await
    }

    pub async fn insights_summary(
        &self,
        query: &[(String, String)],
    ) -> Result<InsightsEnvelope<InsightsSummary>> {
        self.request_query(Method::GET, "/api/v1/insights/summary", query)
            .await
    }

    pub async fn insights_status(
        &self,
        query: &[(String, String)],
    ) -> Result<InsightsEnvelope<InsightsStatusData>> {
        self.request_query(Method::GET, "/api/v1/insights/capabilities", query)
            .await
    }

    pub async fn insights_activity(
        &self,
        query: &[(String, String)],
    ) -> Result<InsightsEnvelope<InsightsActivityData>> {
        self.request_query(Method::GET, "/api/v1/insights/activity", query)
            .await
    }

    pub async fn insights_export(&self, query: &[(String, String)]) -> Result<String> {
        let response = self
            .http
            .get(self.url("/api/v1/insights/export", query)?)
            .bearer_auth(&self.token)
            .header("Accept", "application/json, text/csv")
            .send()
            .await
            .context("failed to reach Devboxes API")?;
        let status = response.status();
        if !status.is_success() {
            let payload = response.json::<Value>().await.unwrap_or(Value::Null);
            bail!(
                "Devboxes API returned {status}: {}",
                api_error_detail(&payload)
            );
        }
        response
            .text()
            .await
            .context("Devboxes API returned an invalid export")
    }

    pub async fn purge_insights(&self, name: &str) -> Result<InsightsPurgeResult> {
        self.request_query(
            Method::DELETE,
            "/api/v1/insights",
            &[("box".to_owned(), name.to_owned())],
        )
        .await
    }

    async fn request<B, R>(&self, method: Method, path: &str, body: Option<&B>) -> Result<R>
    where
        B: Serialize + Sync + ?Sized,
        R: DeserializeOwned,
    {
        let mut request = self
            .http
            .request(method, self.url(path, &[])?)
            .bearer_auth(&self.token)
            .header("Accept", "application/json");
        if let Some(body) = body {
            request = request.json(body);
        }
        let response = request
            .send()
            .await
            .context("failed to reach Devboxes API")?;
        decode_response(response).await
    }

    async fn request_query<R>(
        &self,
        method: Method,
        path: &str,
        query: &[(String, String)],
    ) -> Result<R>
    where
        R: DeserializeOwned,
    {
        let response = self
            .http
            .request(method, self.url(path, query)?)
            .bearer_auth(&self.token)
            .header("Accept", "application/json")
            .send()
            .await
            .context("failed to reach Devboxes API")?;
        decode_response(response).await
    }

    fn url(&self, path: &str, query: &[(String, String)]) -> Result<reqwest::Url> {
        let mut url = reqwest::Url::parse(&format!("{}{}", self.base_url, path))
            .context("failed to build Devboxes API URL")?;
        if !query.is_empty() {
            url.query_pairs_mut()
                .extend_pairs(query.iter().map(|(key, value)| (key, value)));
        }
        Ok(url)
    }
}

fn http_client() -> Result<Client> {
    Client::builder()
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(30))
        .user_agent(concat!("devbox-cli/", env!("CARGO_PKG_VERSION")))
        .build()
        .context("failed to initialize HTTP client")
}

async fn decode_response<R: DeserializeOwned>(response: reqwest::Response) -> Result<R> {
    let status = response.status();
    if status == StatusCode::NO_CONTENT {
        bail!("the API returned no content where a response was expected");
    }
    if !status.is_success() {
        let payload = response.json::<Value>().await.unwrap_or(Value::Null);
        let detail = api_error_detail(&payload);
        bail!("Devboxes API returned {status}: {detail}");
    }
    response
        .json::<R>()
        .await
        .context("Devboxes API returned an invalid response")
}

fn api_error_detail(payload: &Value) -> String {
    match payload.get("detail") {
        Some(Value::String(message)) => message.clone(),
        Some(Value::Array(errors)) => {
            let messages = errors
                .iter()
                .filter_map(|error| error.get("msg").and_then(Value::as_str))
                .collect::<Vec<_>>()
                .join("; ");
            if messages.is_empty() {
                "request failed".to_owned()
            } else {
                messages
            }
        }
        _ => "request failed".to_owned(),
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::api_error_detail;

    #[test]
    fn validation_errors_are_human_readable() {
        let payload = json!({
            "detail": [
                {"msg": "name is invalid"},
                {"msg": "ttl is too large"}
            ]
        });

        assert_eq!(
            api_error_detail(&payload),
            "name is invalid; ttl is too large"
        );
    }
}
