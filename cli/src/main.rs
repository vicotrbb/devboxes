mod client;
mod config;
mod login;
mod models;

use std::io::{self, Write};
use std::process::Stdio;
use std::time::Duration;

use anyhow::{Context, Result, bail};
use chrono::Utc;
use clap::{Args, Parser, Subcommand};
use tokio::process::Command;
use tokio::time::{Instant, sleep};

use client::ApiClient;
use config::StoredConfig;
use models::{CreateDevbox, Devbox, Preset};

#[derive(Parser)]
#[command(
    name = "devbox",
    version,
    about = "Create and use development environments on Kubernetes"
)]
struct Cli {
    /// Override the Devboxes API URL.
    #[arg(long, global = true)]
    url: Option<String>,

    /// Override the API token (prefer `DEVBOX_TOKEN` to avoid shell history).
    #[arg(long, global = true)]
    token: Option<String>,

    /// Print machine-readable JSON where supported.
    #[arg(long, global = true)]
    json: bool,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Store and verify API credentials.
    Login(LoginArgs),
    /// Create a prepared devbox.
    Create(CreateArgs),
    /// List current devboxes.
    List,
    /// Show one devbox.
    Status(NameArgs),
    /// Connect over SSH and attach to tmux.
    Ssh(SshArgs),
    /// Start stopped compute while retaining the same volume.
    Start(NameArgs),
    /// Stop compute while preserving the home volume.
    Stop(NameArgs),
    /// Delete compute and optionally purge the home volume.
    Delete(DeleteArgs),
}

#[derive(Args)]
struct LoginArgs {
    /// Print the authorization URL without opening a browser.
    #[arg(long)]
    no_open: bool,

    /// Seconds to wait for browser authorization.
    #[arg(long, default_value_t = 300, value_parser = clap::value_parser!(u64).range(10..=900))]
    timeout: u64,
}

#[derive(Args)]
struct CreateArgs {
    #[arg(value_parser = validate_name)]
    name: String,

    #[arg(long, value_enum, default_value_t = Preset::Small)]
    preset: Preset,

    /// Hours before compute is automatically stopped (1-168).
    #[arg(long, default_value_t = 24, value_parser = clap::value_parser!(u16).range(1..=168))]
    ttl: u16,

    /// GitHub owner/repository or HTTPS URL to clone on first boot.
    #[arg(long)]
    repo: Option<String>,

    /// Return immediately instead of waiting for SSH readiness.
    #[arg(long)]
    no_wait: bool,

    /// Connect as soon as the devbox becomes ready.
    #[arg(long)]
    ssh: bool,
}

#[derive(Args)]
struct NameArgs {
    #[arg(value_parser = validate_name)]
    name: String,
}

#[derive(Args)]
struct SshArgs {
    #[arg(value_parser = validate_name)]
    name: String,

    /// Additional options passed to ssh after `--`.
    #[arg(last = true, trailing_var_arg = true)]
    ssh_args: Vec<String>,
}

#[derive(Args)]
struct DeleteArgs {
    #[arg(value_parser = validate_name)]
    name: String,

    /// Permanently delete the home volume as well.
    #[arg(long)]
    purge: bool,

    /// Skip the purge confirmation prompt.
    #[arg(long)]
    yes: bool,
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();
    if let Commands::Login(args) = &cli.command {
        return login(cli.url.as_deref(), cli.token.as_deref(), args, cli.json).await;
    }

    let resolved = StoredConfig::load()?.resolve(cli.url.clone(), cli.token.clone())?;
    let server_alias = resolved.server_alias.clone();
    let client = ApiClient::new(resolved.url, resolved.token)?;

    match cli.command {
        Commands::Login(_) => unreachable!(),
        Commands::Create(args) => create(&client, args, cli.json, &server_alias).await,
        Commands::List => list(&client, cli.json).await,
        Commands::Status(args) => status(&client, &args.name, cli.json).await,
        Commands::Ssh(args) => ssh(&client, &args.name, &args.ssh_args, &server_alias).await,
        Commands::Start(args) => lifecycle(&client, &args.name, true, cli.json).await,
        Commands::Stop(args) => lifecycle(&client, &args.name, false, cli.json).await,
        Commands::Delete(args) => delete(&client, args).await,
    }
}

async fn login(
    url: Option<&str>,
    provided_token: Option<&str>,
    args: &LoginArgs,
    json: bool,
) -> Result<()> {
    let stored = StoredConfig::load()?;
    let configured_url = url
        .map(str::to_owned)
        .or_else(|| std::env::var("DEVBOX_URL").ok())
        .or_else(|| stored.url.clone())
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| {
            anyhow::anyhow!(
                "Devboxes API URL is not configured; pass `--url https://devboxes.example.com` or set DEVBOX_URL"
            )
        })?;
    let configured_url = stored.resolve_url(Some(configured_url))?;
    let token = if let Some(token) =
        resolve_login_token(provided_token, std::env::var("DEVBOX_TOKEN").ok())
    {
        token
    } else {
        let authorization = login::authorize(
            &configured_url,
            Duration::from_secs(args.timeout),
            args.no_open,
        )
        .await?;
        let response = ApiClient::exchange_cli_code(
            &configured_url,
            &authorization.code,
            &authorization.code_verifier,
            &authorization.redirect_uri,
        )
        .await?;
        if response.token_type != "Bearer"
            || response.scope != "devboxes:manage"
            || response.expires_in == 0
        {
            bail!("Devboxes API returned an unsupported CLI token");
        }
        response.access_token
    };
    let resolved = stored.resolve(Some(configured_url), Some(token.clone()))?;
    let identity = ApiClient::new(resolved.url.clone(), resolved.token)?
        .whoami()
        .await
        .context("token verification failed")?;
    let path = StoredConfig::save(&resolved.url, &token)?;
    if json {
        println!(
            "{}",
            serde_json::json!({
                "user": identity.user,
                "mode": identity.mode,
                "config": path,
            })
        );
    } else {
        println!(
            "✓ authenticated as {} via {}\n  config: {}",
            identity.user,
            identity.mode,
            path.display()
        );
    }
    Ok(())
}

fn resolve_login_token(
    provided_token: Option<&str>,
    environment_token: Option<String>,
) -> Option<String> {
    provided_token
        .map(str::to_owned)
        .filter(|token| !token.trim().is_empty())
        .or_else(|| environment_token.filter(|token| !token.trim().is_empty()))
}

fn validate_name(value: &str) -> std::result::Result<String, String> {
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
        Ok(value.to_owned())
    } else {
        Err("use 1-40 lowercase letters, digits, or hyphens; start and end alphanumeric".to_owned())
    }
}

async fn create(
    client: &ApiClient,
    args: CreateArgs,
    json: bool,
    server_alias: &str,
) -> Result<()> {
    let payload = CreateDevbox {
        name: &args.name,
        preset: args.preset,
        ttl_hours: args.ttl,
        repository: args.repo.as_deref(),
    };
    let mut box_info = client.create(&payload).await?;
    if !args.no_wait || args.ssh {
        eprintln!("→ preparing {}…", box_info.name);
        box_info = wait_until_ready(client, &box_info.name, Duration::from_mins(4)).await?;
    }
    print_box(&box_info, json)?;
    if args.ssh {
        run_ssh(&box_info, &[], server_alias).await?;
    }
    Ok(())
}

async fn list(client: &ApiClient, json: bool) -> Result<()> {
    let boxes = client.list().await?;
    if json {
        println!("{}", serde_json::to_string_pretty(&boxes)?);
        return Ok(());
    }
    if boxes.is_empty() {
        println!("No devboxes. Create one with `devbox create <name> --ssh`.");
        return Ok(());
    }
    println!(
        "{:<22} {:<11} {:<8} {:<16} SSH",
        "NAME", "STATE", "SIZE", "AUTO-STOP"
    );
    for box_info in boxes {
        println!(
            "{:<22} {:<11} {:<8} {:<16} {}",
            box_info.name,
            box_info.state,
            box_info.preset,
            human_expiry(&box_info),
            box_info.ssh_command.as_deref().unwrap_or("pending"),
        );
    }
    Ok(())
}

async fn status(client: &ApiClient, name: &str, json: bool) -> Result<()> {
    print_box(&client.get(name).await?, json)
}

async fn lifecycle(client: &ApiClient, name: &str, start: bool, json: bool) -> Result<()> {
    let box_info = if start {
        client.start(name).await?
    } else {
        client.stop(name).await?
    };
    print_box(&box_info, json)
}

async fn ssh(
    client: &ApiClient,
    name: &str,
    ssh_args: &[String],
    server_alias: &str,
) -> Result<()> {
    let box_info = client.get(name).await?;
    if box_info.state == "stopped" {
        bail!("{name} is stopped; run `devbox start {name}` first");
    }
    let box_info = if box_info.ssh_host.is_some() {
        box_info
    } else {
        wait_until_ready(client, name, Duration::from_mins(3)).await?
    };
    run_ssh(&box_info, ssh_args, server_alias).await
}

async fn delete(client: &ApiClient, args: DeleteArgs) -> Result<()> {
    if args.purge && !args.yes {
        print!(
            "Permanently purge {} and its home volume? Type the devbox name to continue: ",
            args.name
        );
        io::stdout().flush()?;
        let mut confirmation = String::new();
        io::stdin().read_line(&mut confirmation)?;
        if confirmation.trim() != args.name {
            bail!("confirmation did not match; nothing was deleted");
        }
    }
    let result = client.delete(&args.name, args.purge).await?;
    println!("✓ {}", result.message);
    Ok(())
}

async fn wait_until_ready(client: &ApiClient, name: &str, timeout: Duration) -> Result<Devbox> {
    let deadline = Instant::now() + timeout;
    loop {
        let box_info = client.get(name).await?;
        if box_info.state == "ready" && box_info.ssh_host.is_some() {
            return Ok(box_info);
        }
        if box_info.state == "degraded" {
            bail!(
                "{} became degraded: {}",
                name,
                box_info.message.as_deref().unwrap_or("unknown error")
            );
        }
        if Instant::now() >= deadline {
            bail!("timed out waiting for {name} to become ready");
        }
        sleep(Duration::from_secs(2)).await;
    }
}

async fn run_ssh(box_info: &Devbox, extra_args: &[String], server_alias: &str) -> Result<()> {
    let host = box_info
        .ssh_host
        .as_deref()
        .context("devbox does not have an SSH address yet")?;
    let arguments = ssh_arguments(
        server_alias,
        &box_info.name,
        host,
        box_info.ssh_port,
        extra_args,
    );
    let mut command = Command::new("ssh");
    command
        .args(arguments)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());
    let status = command.status().await.context("failed to launch ssh")?;
    if !status.success() {
        bail!("ssh exited with {status}");
    }
    Ok(())
}

fn ssh_arguments(
    server_alias: &str,
    name: &str,
    host: &str,
    port: u16,
    extra_args: &[String],
) -> Vec<String> {
    let mut arguments = vec![
        "-t".to_owned(),
        "-p".to_owned(),
        port.to_string(),
        "-o".to_owned(),
        format!("HostKeyAlias={server_alias}-{name}"),
        "-o".to_owned(),
        "StrictHostKeyChecking=accept-new".to_owned(),
        "-o".to_owned(),
        "ServerAliveInterval=30".to_owned(),
    ];
    arguments.extend(extra_args.iter().cloned());
    arguments.push(format!("dev@{host}"));
    arguments
}

fn print_box(box_info: &Devbox, json: bool) -> Result<()> {
    if json {
        println!("{}", serde_json::to_string_pretty(box_info)?);
        return Ok(());
    }
    println!("{}  {}", box_info.name, box_info.state);
    println!("  preset:     {}", box_info.preset);
    println!("  storage:    {}", box_info.storage_size);
    println!("  auto-stop:  {}", box_info.expires_at.to_rfc3339());
    if let Some(repository) = &box_info.repository {
        println!("  repository: {repository}");
    }
    if let Some(command) = &box_info.ssh_command {
        println!("  ssh:        {command}");
    } else if let Some(message) = &box_info.message {
        println!("  status:     {message}");
    }
    Ok(())
}

fn human_expiry(box_info: &Devbox) -> String {
    if box_info.state == "stopped" {
        return "stopped".to_owned();
    }
    let hours = (box_info.expires_at - Utc::now()).num_hours().max(0);
    if hours < 24 {
        format!("in {hours}h")
    } else {
        format!("in {}d", (hours + 23) / 24)
    }
}

#[cfg(test)]
mod tests {
    use super::{resolve_login_token, ssh_arguments, validate_name};

    #[test]
    fn login_token_prefers_the_flag_and_supports_the_environment() {
        assert_eq!(
            resolve_login_token(Some("flag-token"), Some("environment-token".to_owned())),
            Some("flag-token".to_owned())
        );
        assert_eq!(
            resolve_login_token(None, Some("environment-token".to_owned())),
            Some("environment-token".to_owned())
        );
        assert_eq!(
            resolve_login_token(Some(""), Some("environment-token".to_owned())),
            Some("environment-token".to_owned())
        );
        assert_eq!(resolve_login_token(None, Some("  ".to_owned())), None);
    }

    #[test]
    fn devbox_names_match_the_controller_contract() {
        assert_eq!(validate_name("atlas-2").unwrap(), "atlas-2");
        assert!(validate_name("").is_err());
        assert!(validate_name("Uppercase").is_err());
        assert!(validate_name("-atlas").is_err());
        assert!(validate_name("atlas-").is_err());
        assert!(validate_name("atlas/other").is_err());
        assert!(validate_name(&"a".repeat(41)).is_err());
    }

    #[test]
    fn extra_ssh_options_come_before_the_destination() {
        let extra = vec!["-L".to_owned(), "3000:127.0.0.1:3000".to_owned()];
        let arguments = ssh_arguments("devboxes-cluster-one", "atlas", "192.0.2.10", 22, &extra);
        let forwarding = arguments.iter().position(|item| item == "-L").unwrap();
        let destination = arguments
            .iter()
            .position(|item| item == "dev@192.0.2.10")
            .unwrap();

        assert!(forwarding < destination);
        assert!(arguments.contains(&"HostKeyAlias=devboxes-cluster-one-atlas".to_owned()));
    }
}
