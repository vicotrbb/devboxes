use std::collections::HashMap;
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{Context, Result, bail};
use axum::Router;
use axum::extract::{Query, State};
use axum::http::{HeaderValue, StatusCode, header};
use axum::response::{Html, IntoResponse, Response};
use axum::routing::get;
use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use rand::RngCore;
use reqwest::Url;
use sha2::{Digest, Sha256};
use tokio::net::TcpListener;
use tokio::sync::oneshot;
use tokio::task::JoinHandle;

const CALLBACK_PATH: &str = "/callback";
const CLIENT_ID: &str = "devbox-cli";

pub struct BrowserAuthorization {
    pub code: String,
    pub code_verifier: String,
    pub redirect_uri: String,
}

struct LoginAttempt {
    state: String,
    code_verifier: String,
    code_challenge: String,
}

impl LoginAttempt {
    fn generate() -> Self {
        let state = random_base64url();
        let code_verifier = random_base64url();
        let code_challenge = URL_SAFE_NO_PAD.encode(Sha256::digest(code_verifier.as_bytes()));
        Self {
            state,
            code_verifier,
            code_challenge,
        }
    }
}

enum CallbackResult {
    Code(String),
    Denied,
    Invalid(&'static str),
}

#[derive(Clone)]
struct CallbackState {
    expected_state: String,
    sender: Arc<Mutex<Option<oneshot::Sender<CallbackResult>>>>,
}

struct CallbackListener {
    address: SocketAddr,
    receiver: oneshot::Receiver<CallbackResult>,
    shutdown: oneshot::Sender<()>,
    server: JoinHandle<std::io::Result<()>>,
}

pub async fn authorize(
    base_url: &str,
    login_timeout: Duration,
    no_open: bool,
) -> Result<BrowserAuthorization> {
    authorize_with_opener(base_url, login_timeout, no_open, &|url| {
        webbrowser::open(url).map_err(|error| error.to_string())
    })
    .await
}

async fn authorize_with_opener(
    base_url: &str,
    login_timeout: Duration,
    no_open: bool,
    opener: &(dyn Fn(&str) -> std::result::Result<(), String> + Sync),
) -> Result<BrowserAuthorization> {
    let attempt = LoginAttempt::generate();
    let listener = start_callback_listener(&attempt.state).await?;
    let redirect_uri = format!("http://{}{}", listener.address, CALLBACK_PATH);
    let authorization_url = build_authorization_url(
        base_url,
        &redirect_uri,
        &attempt.state,
        &attempt.code_challenge,
    )?;

    launch_browser(&authorization_url, no_open, opener);

    let CallbackListener {
        receiver,
        shutdown,
        server,
        ..
    } = listener;
    let callback = tokio::select! {
        result = wait_for_callback(receiver, login_timeout) => result,
        result = tokio::signal::ctrl_c() => {
            result.context("failed to listen for cancellation")?;
            bail!("browser authorization was cancelled")
        }
    };
    let _ = shutdown.send(());
    let _ = server.await;

    let code = callback?;
    Ok(BrowserAuthorization {
        code,
        code_verifier: attempt.code_verifier,
        redirect_uri,
    })
}

fn build_authorization_url(
    base_url: &str,
    redirect_uri: &str,
    state: &str,
    code_challenge: &str,
) -> Result<String> {
    let base = Url::parse(base_url).context("invalid Devboxes API URL")?;
    let mut url = base
        .join("/auth/cli/authorize")
        .context("failed to construct browser authorization URL")?;
    url.query_pairs_mut()
        .append_pair("response_type", "code")
        .append_pair("client_id", CLIENT_ID)
        .append_pair("redirect_uri", redirect_uri)
        .append_pair("state", state)
        .append_pair("code_challenge", code_challenge)
        .append_pair("code_challenge_method", "S256");
    Ok(url.into())
}

fn launch_browser(
    authorization_url: &str,
    no_open: bool,
    opener: &(dyn Fn(&str) -> std::result::Result<(), String> + Sync),
) {
    if no_open {
        println!("Open this URL to authorize the Devbox CLI:\n{authorization_url}");
        return;
    }
    if let Err(error) = opener(authorization_url) {
        eprintln!("Could not open the browser ({error}).");
        println!("Open this URL to authorize the Devbox CLI:\n{authorization_url}");
    }
}

async fn start_callback_listener(expected_state: &str) -> Result<CallbackListener> {
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))
        .await
        .context("failed to bind the loopback authorization callback")?;
    let address = listener
        .local_addr()
        .context("failed to inspect the loopback authorization callback")?;
    if address.ip() != IpAddr::V4(Ipv4Addr::LOCALHOST) {
        bail!("authorization callback did not bind to 127.0.0.1");
    }

    let (callback_sender, receiver) = oneshot::channel();
    let state = CallbackState {
        expected_state: expected_state.to_owned(),
        sender: Arc::new(Mutex::new(Some(callback_sender))),
    };
    let app = Router::new()
        .route(CALLBACK_PATH, get(callback))
        .fallback(not_found)
        .with_state(state);
    let (shutdown, shutdown_receiver) = oneshot::channel();
    let server = tokio::spawn(async move {
        axum::serve(listener, app)
            .with_graceful_shutdown(async {
                let _ = shutdown_receiver.await;
            })
            .await
    });

    Ok(CallbackListener {
        address,
        receiver,
        shutdown,
        server,
    })
}

async fn callback(
    State(state): State<CallbackState>,
    Query(query): Query<HashMap<String, String>>,
) -> Response {
    let supplied_state = query.get("state").map(String::as_str).unwrap_or_default();
    if supplied_state != state.expected_state {
        send_callback(
            &state,
            CallbackResult::Invalid("callback state did not match"),
        );
        return callback_response(
            StatusCode::BAD_REQUEST,
            "<h1>Authorization failed</h1><p>The callback state did not match.</p>",
        );
    }

    match (query.get("code"), query.get("error")) {
        (Some(code), None) if valid_code(code) => {
            send_callback(&state, CallbackResult::Code(code.clone()));
            callback_response(
                StatusCode::OK,
                "<h1>Devbox CLI authorized</h1><p>You can close this window and return to the terminal.</p>",
            )
        }
        (None, Some(error)) if error == "access_denied" => {
            send_callback(&state, CallbackResult::Denied);
            callback_response(
                StatusCode::OK,
                "<h1>Authorization denied</h1><p>You can close this window.</p>",
            )
        }
        _ => {
            send_callback(&state, CallbackResult::Invalid("callback was malformed"));
            callback_response(
                StatusCode::BAD_REQUEST,
                "<h1>Authorization failed</h1><p>The callback was malformed.</p>",
            )
        }
    }
}

fn callback_response(status: StatusCode, body: &'static str) -> Response {
    let mut response = (status, Html(body)).into_response();
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response.headers_mut().insert(
        header::REFERRER_POLICY,
        HeaderValue::from_static("no-referrer"),
    );
    response
}

async fn not_found() -> impl IntoResponse {
    (StatusCode::NOT_FOUND, "Not found")
}

fn send_callback(state: &CallbackState, result: CallbackResult) {
    let sender = state
        .sender
        .lock()
        .expect("callback sender mutex was poisoned")
        .take();
    if let Some(sender) = sender {
        let _ = sender.send(result);
    }
}

async fn wait_for_callback(
    receiver: oneshot::Receiver<CallbackResult>,
    login_timeout: Duration,
) -> Result<String> {
    let result = tokio::time::timeout(login_timeout, receiver)
        .await
        .map_err(|_| anyhow::anyhow!("timed out waiting for browser authorization"))?
        .context("browser authorization callback closed unexpectedly")?;
    match result {
        CallbackResult::Code(code) => Ok(code),
        CallbackResult::Denied => bail!("browser authorization was denied"),
        CallbackResult::Invalid(message) => bail!("browser authorization failed: {message}"),
    }
}

fn valid_code(code: &str) -> bool {
    (32..=256).contains(&code.len())
        && code
            .bytes()
            .all(|character| character.is_ascii_alphanumeric() || matches!(character, b'-' | b'_'))
}

fn random_base64url() -> String {
    let mut bytes = [0_u8; 32];
    rand::rng().fill_bytes(&mut bytes);
    URL_SAFE_NO_PAD.encode(bytes)
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicUsize, Ordering};

    use super::*;

    #[test]
    fn state_and_pkce_are_high_entropy_and_s256() {
        let first = LoginAttempt::generate();
        let second = LoginAttempt::generate();

        assert_eq!(first.state.len(), 43);
        assert_eq!(first.code_verifier.len(), 43);
        assert_eq!(first.code_challenge.len(), 43);
        assert_ne!(first.state, second.state);
        assert_ne!(first.code_verifier, second.code_verifier);
        assert_eq!(
            first.code_challenge,
            URL_SAFE_NO_PAD.encode(Sha256::digest(first.code_verifier.as_bytes()))
        );
    }

    #[test]
    fn authorization_url_contains_fixed_client_loopback_and_s256() {
        let url = build_authorization_url(
            "https://devboxes.example.com",
            "http://127.0.0.1:49152/callback",
            "state-value",
            "challenge-value",
        )
        .unwrap();
        let parsed = Url::parse(&url).unwrap();
        let query = parsed.query_pairs().collect::<HashMap<_, _>>();

        assert_eq!(parsed.path(), "/auth/cli/authorize");
        assert_eq!(query["response_type"], "code");
        assert_eq!(query["client_id"], CLIENT_ID);
        assert_eq!(query["redirect_uri"], "http://127.0.0.1:49152/callback");
        assert_eq!(query["state"], "state-value");
        assert_eq!(query["code_challenge"], "challenge-value");
        assert_eq!(query["code_challenge_method"], "S256");
    }

    #[test]
    fn opener_is_invoked_unless_no_open_and_failures_do_not_abort() {
        let calls = Arc::new(AtomicUsize::new(0));
        let opener_calls = Arc::clone(&calls);
        let opener = move |_: &str| {
            opener_calls.fetch_add(1, Ordering::Relaxed);
            Err("no browser".to_owned())
        };

        launch_browser("https://example.test/auth", false, &opener);
        assert_eq!(calls.load(Ordering::Relaxed), 1);
        launch_browser("https://example.test/auth", true, &opener);
        assert_eq!(calls.load(Ordering::Relaxed), 1);
    }

    #[tokio::test]
    async fn callback_binds_only_ipv4_loopback_ignores_favicon_and_accepts_code() {
        let listener = start_callback_listener("expected-state").await.unwrap();
        assert_eq!(listener.address.ip(), IpAddr::V4(Ipv4Addr::LOCALHOST));
        let base = format!("http://{}", listener.address);
        assert_eq!(
            reqwest::get(format!("{base}/favicon.ico"))
                .await
                .unwrap()
                .status(),
            StatusCode::NOT_FOUND
        );

        let code = "authorization-code-value-with-enough-entropy";
        assert_eq!(
            reqwest::get(format!("{base}/callback?state=expected-state&code={code}"))
                .await
                .unwrap()
                .status(),
            StatusCode::OK
        );
        assert_eq!(
            wait_for_callback(listener.receiver, Duration::from_secs(1))
                .await
                .unwrap(),
            code
        );
        let _ = listener.shutdown.send(());
        listener.server.await.unwrap().unwrap();
    }

    #[tokio::test]
    async fn callback_reports_denial_wrong_state_malformed_and_timeout() {
        for (query, expected) in [
            (
                "state=expected-state&error=access_denied",
                "browser authorization was denied",
            ),
            (
                "state=wrong&code=authorization-code-value-with-enough-entropy",
                "callback state did not match",
            ),
            ("state=expected-state", "callback was malformed"),
        ] {
            let listener = start_callback_listener("expected-state").await.unwrap();
            let response = reqwest::get(format!(
                "http://{}{CALLBACK_PATH}?{query}",
                listener.address
            ))
            .await
            .unwrap();
            assert!(matches!(
                response.status(),
                StatusCode::OK | StatusCode::BAD_REQUEST
            ));
            let error = wait_for_callback(listener.receiver, Duration::from_secs(1))
                .await
                .unwrap_err();
            assert!(error.to_string().contains(expected));
            let _ = listener.shutdown.send(());
            listener.server.await.unwrap().unwrap();
        }

        let (_sender, receiver) = oneshot::channel();
        let timeout = wait_for_callback(receiver, Duration::from_millis(1))
            .await
            .unwrap_err();
        assert!(timeout.to_string().contains("timed out"));
    }
}
