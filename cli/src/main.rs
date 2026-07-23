mod client;
mod config;
mod login;
mod models;

use std::io::{self, Write};
use std::process::Stdio;
use std::time::Duration;

use anyhow::{Context, Result, bail};
use chrono::Utc;
use clap::{Args, Parser, Subcommand, ValueEnum};
use tokio::process::Command;
use tokio::time::{Instant, sleep};

use client::ApiClient;
use config::StoredConfig;
use models::{
    CollectorStatus, CreateDevbox, CustomImageCapabilities, Devbox, GpuCapabilities, GpuRequest,
    InsightsActivity, InsightsActivityData, InsightsEnvelope, InsightsStatusData, InsightsSummary,
    Preset,
};

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
    /// Inspect privacy-preserving AI and repository metrics.
    Metrics(MetricsArgs),
    /// Inspect GPU acceleration profiles configured by the operator.
    Gpu(GpuArgs),
    /// Inspect custom image profiles configured by the operator.
    Image(ImageArgs),
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

    /// Request an operator-approved image profile or exact approved image reference.
    #[arg(long, value_name = "PROFILE_OR_IMAGE", value_parser = validate_image_selector)]
    image: Option<String>,

    /// Request the operator's default GPU profile.
    #[arg(long)]
    gpu: bool,

    /// Request a specific operator-approved GPU profile (implies --gpu).
    #[arg(long, value_name = "PROFILE", value_parser = validate_name)]
    gpu_profile: Option<String>,

    /// Return immediately instead of waiting for SSH readiness.
    #[arg(long)]
    no_wait: bool,

    /// Connect as soon as the devbox becomes ready.
    #[arg(long)]
    ssh: bool,
}

#[derive(Args)]
struct GpuArgs {
    #[command(subcommand)]
    command: Option<GpuCommand>,
}

#[derive(Args)]
struct ImageArgs {
    #[command(subcommand)]
    command: Option<ImageCommand>,
}

#[derive(Subcommand)]
enum GpuCommand {
    /// List the GPU profiles available for new devboxes.
    Profiles,
}

#[derive(Subcommand)]
enum ImageCommand {
    /// List the custom image profiles available for new devboxes.
    Profiles,
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

#[derive(Args)]
struct MetricsArgs {
    #[command(flatten)]
    filters: MetricsFilters,

    #[command(subcommand)]
    command: Option<MetricsCommand>,
}

#[derive(Args, Clone)]
struct MetricsFilters {
    /// Relative range such as 24h, 7d, or 30d, or an RFC 3339 timestamp.
    #[arg(long, global = true, default_value = "7d")]
    since: String,

    /// Inclusive RFC 3339 range end (defaults to now).
    #[arg(long, global = true)]
    until: Option<String>,

    /// Restrict results to one devbox name.
    #[arg(long = "box", global = true, value_parser = validate_name)]
    box_name: Option<String>,

    /// Restrict results to codex or claude.
    #[arg(long, global = true, value_parser = ["codex", "claude", "all"])]
    provider: Option<String>,

    /// Restrict results to one provider-reported model.
    #[arg(long, global = true)]
    model: Option<String>,

    /// Restrict Git results to one normalized repository identifier.
    #[arg(long, global = true)]
    repo: Option<String>,

    /// Request one stable grouping dimension.
    #[arg(long, global = true, value_enum)]
    group_by: Option<MetricsGroupBy>,
}

#[derive(Clone, Copy, ValueEnum)]
enum MetricsGroupBy {
    Provider,
    Model,
    Box,
    Repository,
}

impl std::fmt::Display for MetricsGroupBy {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(match self {
            Self::Provider => "provider",
            Self::Model => "model",
            Self::Box => "box",
            Self::Repository => "repository",
        })
    }
}

#[derive(Subcommand)]
enum MetricsCommand {
    /// Show collector freshness, queue size, and known loss.
    Status,
    /// Show aggregate commit activity.
    Activity {
        /// Maximum activity rows to return.
        #[arg(long, default_value_t = 50, value_parser = clap::value_parser!(u16).range(1..=200))]
        limit: u16,
    },
    /// Export the filtered summary to stdout.
    Export {
        #[arg(long, value_enum, default_value_t = MetricsExportFormat::Json)]
        format: MetricsExportFormat,
    },
    /// Permanently purge central Insights data for --box.
    Purge {
        /// Skip the destructive confirmation prompt.
        #[arg(long)]
        yes: bool,
    },
}

#[derive(Clone, Copy, ValueEnum)]
enum MetricsExportFormat {
    Json,
    Csv,
}

impl std::fmt::Display for MetricsExportFormat {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(match self {
            Self::Json => "json",
            Self::Csv => "csv",
        })
    }
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
        Commands::Metrics(args) => metrics(&client, args, cli.json).await,
        Commands::Gpu(args) => gpu(&client, args, cli.json).await,
        Commands::Image(args) => image(&client, args, cli.json).await,
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

fn validate_image_selector(value: &str) -> std::result::Result<String, String> {
    let value = value.trim();
    if value.is_empty()
        || value.len() > 512
        || value.chars().any(char::is_whitespace)
        || value.contains("://")
    {
        Err(
            "use an operator-approved image profile or image reference without whitespace or a URL scheme"
                .to_owned(),
        )
    } else {
        Ok(value.to_owned())
    }
}

async fn create(
    client: &ApiClient,
    args: CreateArgs,
    json: bool,
    server_alias: &str,
) -> Result<()> {
    let gpu = if args.gpu || args.gpu_profile.is_some() {
        Some(GpuRequest {
            profile: args.gpu_profile.as_deref(),
        })
    } else {
        None
    };
    let payload = CreateDevbox {
        name: &args.name,
        preset: args.preset,
        ttl_hours: args.ttl,
        repository: args.repo.as_deref(),
        image: args.image.as_deref(),
        gpu,
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
        "{:<22} {:<11} {:<8} {:<16} {:<18} {:<18} SSH",
        "NAME", "STATE", "SIZE", "AUTO-STOP", "ACCELERATOR", "IMAGE"
    );
    for box_info in boxes {
        println!(
            "{:<22} {:<11} {:<8} {:<16} {:<18} {:<18} {}",
            box_info.name,
            box_info.state,
            box_info.preset,
            human_expiry(&box_info),
            gpu_label(&box_info),
            image_label(&box_info),
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

async fn metrics(client: &ApiClient, args: MetricsArgs, json: bool) -> Result<()> {
    let MetricsArgs { filters, command } = args;
    match command {
        None => {
            let response = client.insights_summary(&metrics_query(&filters)).await?;
            if json {
                println!("{}", serde_json::to_string_pretty(&response)?);
            } else {
                print_metrics_summary(&response);
            }
        }
        Some(MetricsCommand::Status) => {
            let response = client.insights_status(&metrics_query(&filters)).await?;
            if json {
                println!("{}", serde_json::to_string_pretty(&response)?);
            } else {
                print_metrics_status(&response);
            }
        }
        Some(MetricsCommand::Activity { limit }) => {
            let mut query = metrics_query(&filters);
            query.push(("limit".to_owned(), limit.to_string()));
            let response = client.insights_activity(&query).await?;
            if json {
                println!("{}", serde_json::to_string_pretty(&response)?);
            } else {
                print_metrics_activity(&response);
            }
        }
        Some(MetricsCommand::Export { format }) => {
            let mut query = metrics_query(&filters);
            query.push(("format".to_owned(), format.to_string()));
            print!("{}", client.insights_export(&query).await?);
        }
        Some(MetricsCommand::Purge { yes }) => {
            let name = filters
                .box_name
                .as_deref()
                .context("metrics purge requires --box NAME")?;
            if !yes {
                print!(
                    "Permanently purge central Insights data for {name}? Type the devbox name to continue: "
                );
                io::stdout().flush()?;
                let mut confirmation = String::new();
                io::stdin().read_line(&mut confirmation)?;
                if confirmation.trim() != name {
                    bail!("confirmation did not match; no Insights data was deleted");
                }
            }
            let result = client.purge_insights(name).await?;
            if json {
                println!("{}", serde_json::to_string_pretty(&result)?);
            } else {
                println!(
                    "purged {} Insights instance{} for {}",
                    result.purged_instances,
                    if result.purged_instances == 1 {
                        ""
                    } else {
                        "s"
                    },
                    result.box_name
                );
            }
        }
    }
    Ok(())
}

async fn gpu(client: &ApiClient, args: GpuArgs, json: bool) -> Result<()> {
    let GpuArgs { command } = args;
    match command {
        None | Some(GpuCommand::Profiles) => {
            let capabilities = client.capabilities().await?.gpu;
            if json {
                println!("{}", serde_json::to_string_pretty(&capabilities)?);
            } else {
                print_gpu_profiles(&capabilities);
            }
        }
    }
    Ok(())
}

async fn image(client: &ApiClient, args: ImageArgs, json: bool) -> Result<()> {
    let ImageArgs { command } = args;
    match command {
        None | Some(ImageCommand::Profiles) => {
            let capabilities = client.capabilities().await?.images;
            if json {
                println!("{}", serde_json::to_string_pretty(&capabilities)?);
            } else {
                print_image_profiles(&capabilities);
            }
        }
    }
    Ok(())
}

fn print_gpu_profiles(capabilities: &GpuCapabilities) {
    if !capabilities.enabled {
        println!("GPU acceleration is disabled by the operator.");
        return;
    }
    println!(
        "{:<18} {:<26} {:>5} {:<28} DESCRIPTION",
        "PROFILE", "NAME", "COUNT", "RESOURCE"
    );
    for profile in &capabilities.profiles {
        println!(
            "{:<18} {:<26} {:>5} {:<28} {}{}",
            profile.name,
            profile.display_name,
            profile.count,
            profile.resource_name,
            profile.description.as_deref().unwrap_or(""),
            if profile.is_default { " (default)" } else { "" },
        );
    }
}

fn print_image_profiles(capabilities: &CustomImageCapabilities) {
    if !capabilities.enabled {
        println!("Custom images are disabled by the operator.");
        return;
    }
    println!(
        "{:<18} {:<26} {:<10} PORTS DESCRIPTION",
        "PROFILE", "NAME", "MODE"
    );
    for profile in &capabilities.profiles {
        let ports = profile
            .ports
            .iter()
            .map(|port| format!("{}:{}/{}", port.name, port.container_port, port.protocol))
            .collect::<Vec<_>>()
            .join(",");
        println!(
            "{:<18} {:<26} {:<10} {:<16} {}",
            profile.name,
            profile.display_name,
            profile.mode,
            ports,
            profile.description.as_deref().unwrap_or(""),
        );
    }
}

fn metrics_query(filters: &MetricsFilters) -> Vec<(String, String)> {
    let mut query = vec![("since".to_owned(), filters.since.clone())];
    for (key, value) in [
        ("until", filters.until.as_ref()),
        ("box", filters.box_name.as_ref()),
        (
            "provider",
            filters
                .provider
                .as_ref()
                .filter(|value| value.as_str() != "all"),
        ),
        ("model", filters.model.as_ref()),
        ("repository", filters.repo.as_ref()),
    ] {
        if let Some(value) = value {
            query.push((key.to_owned(), value.clone()));
        }
    }
    if let Some(group_by) = filters.group_by {
        query.push(("group_by".to_owned(), group_by.to_string()));
    }
    query
}

fn print_metrics_summary(response: &InsightsEnvelope<InsightsSummary>) {
    if !response.enabled {
        println!("Insights is disabled by the operator.");
        return;
    }
    let Some(summary) = &response.data else {
        println!("No Insights data is available.");
        return;
    };
    let range = response.effective_range.as_ref().map_or_else(
        || "requested range".to_owned(),
        |value| format!("{} to {}", value.since, value.until),
    );
    println!("INSIGHTS  {range}");
    let provider_cost = summary
        .ai
        .totals
        .provider_reported_cost_usd
        .map_or_else(|| "not reported".to_owned(), |value| format!("${value:.4}"));
    let active_time = summary
        .ai
        .totals
        .active_seconds
        .map_or_else(|| "not reported".to_owned(), human_duration);
    let ai_lines = summary
        .ai
        .totals
        .ai_lines
        .map_or_else(|| "not reported".to_owned(), |value| value.to_string());
    println!(
        "AI       {} sessions  {} tokens  {} provider cost  {} active  {} AI lines",
        summary.ai.totals.sessions, summary.ai.totals.tokens, provider_cost, active_time, ai_lines,
    );
    for (provider, values) in &summary.ai.providers {
        let cost = values
            .cost_usd
            .map_or_else(|| "not reported".to_owned(), |value| format!("${value:.4}"));
        println!(
            "  {provider:<7} {:>6} sessions  {:>10} tokens  {cost}",
            values.sessions, values.total_tokens
        );
    }
    let changed = summary.code.working_tree.staged_files + summary.code.working_tree.unstaged_files;
    println!(
        "CODE     {} commits  +{} -{}  {} files changed  {} worktree files",
        summary.code.commits,
        summary.code.additions,
        summary.code.deletions,
        summary.code.files_changed,
        changed,
    );
    println!(
        "COVERAGE {}  {} collector{}",
        response.coverage.status,
        response.coverage.collectors.len(),
        if response.coverage.collectors.len() == 1 {
            ""
        } else {
            "s"
        }
    );
}

fn print_metrics_status(response: &InsightsEnvelope<InsightsStatusData>) {
    if !response.enabled {
        println!("Insights is disabled by the operator.");
        return;
    }
    let collectors: &[CollectorStatus] = response
        .data
        .as_ref()
        .map_or(&[], |data| data.collectors.as_slice());
    if collectors.is_empty() {
        println!("No Insights collectors have checked in.");
        return;
    }
    println!(
        "{:<20} {:<10} {:<10} {:>8} {:>10} LOSS",
        "BOX", "COLLECTOR", "STATUS", "AGE", "QUEUE"
    );
    for collector in collectors {
        println!(
            "{:<20} {:<10} {:<10} {:>7}s {:>10} {}",
            collector.box_name,
            collector.collector,
            collector.status,
            collector.freshness_seconds,
            collector.queue_bytes,
            collector.dropped_points,
        );
    }
}

fn print_metrics_activity(response: &InsightsEnvelope<InsightsActivityData>) {
    if !response.enabled {
        println!("Insights is disabled by the operator.");
        return;
    }
    let items: &[InsightsActivity] = response
        .data
        .as_ref()
        .map_or(&[], |data| data.items.as_slice());
    if items.is_empty() {
        println!("No aggregate Git activity was observed in this range.");
        return;
    }
    for item in items {
        println!(
            "{}  {}  {}  +{} -{}  {} file{}{}",
            item.observed_at,
            item.box_name,
            item.repo,
            item.additions,
            item.deletions,
            item.files_changed,
            if item.files_changed == 1 { "" } else { "s" },
            if item.is_merge { "  merge" } else { "" },
        );
    }
}

fn human_duration(seconds: f64) -> String {
    let safe_seconds = if seconds.is_finite() {
        seconds.max(0.0)
    } else {
        0.0
    };
    let minutes = Duration::from_secs_f64(safe_seconds)
        .as_secs()
        .saturating_add(30)
        / 60;
    if minutes < 60 {
        format!("{minutes}m")
    } else {
        let hours = minutes / 60;
        let remainder = minutes % 60;
        if remainder == 0 {
            format!("{hours}h")
        } else {
            format!("{hours}h {remainder}m")
        }
    }
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
            let detail = box_info
                .message
                .as_deref()
                .map_or_else(String::new, |message| format!(": {message}"));
            bail!("timed out waiting for {name} to become ready{detail}");
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
    if let Some(gpu) = &box_info.gpu {
        println!(
            "  accelerator: {} ({} x {})",
            gpu.display_name, gpu.count, gpu.resource_name
        );
    }
    if let Some(image) = &box_info.image {
        println!("  image:      {} ({})", image.display_name, image.mode);
        if !image.ports.is_empty() {
            let ports = image
                .ports
                .iter()
                .map(|port| format!("{}:{}/{}", port.name, port.container_port, port.protocol))
                .collect::<Vec<_>>()
                .join(", ");
            println!("  image ports: {ports}");
        }
    }
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

fn gpu_label(box_info: &Devbox) -> &str {
    box_info
        .gpu
        .as_ref()
        .map_or("cpu", |gpu| gpu.profile.as_str())
}

fn image_label(box_info: &Devbox) -> &str {
    box_info
        .image
        .as_ref()
        .map_or("prepared", |image| image.profile.as_str())
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
    use clap::Parser;

    use super::{
        Cli, Commands, GpuCommand, ImageCommand, MetricsCommand, human_duration, metrics_query,
        resolve_login_token, ssh_arguments, validate_image_selector, validate_name,
    };

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
    fn image_selectors_match_the_controller_contract() {
        assert_eq!(
            validate_image_selector(" docker.io/library/nginx:1.27 ").unwrap(),
            "docker.io/library/nginx:1.27"
        );
        assert!(validate_image_selector("https://registry.example/image:tag").is_err());
        assert!(validate_image_selector("image with spaces").is_err());
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

    #[test]
    fn metrics_filters_are_stable_before_or_after_subcommands() {
        let summary = Cli::try_parse_from([
            "devbox",
            "metrics",
            "--since",
            "24h",
            "--box",
            "atlas",
            "--provider",
            "codex",
            "--group-by",
            "model",
        ])
        .unwrap();
        let Commands::Metrics(summary) = summary.command else {
            panic!("expected metrics command");
        };
        assert!(summary.command.is_none());
        assert_eq!(
            metrics_query(&summary.filters),
            vec![
                ("since".to_owned(), "24h".to_owned()),
                ("box".to_owned(), "atlas".to_owned()),
                ("provider".to_owned(), "codex".to_owned()),
                ("group_by".to_owned(), "model".to_owned()),
            ]
        );

        let status = Cli::try_parse_from([
            "devbox", "metrics", "status", "--box", "atlas", "--since", "7d",
        ])
        .unwrap();
        let Commands::Metrics(status) = status.command else {
            panic!("expected metrics command");
        };
        assert!(matches!(status.command, Some(MetricsCommand::Status)));
        assert_eq!(status.filters.box_name.as_deref(), Some("atlas"));
    }

    #[test]
    fn metrics_rejects_unknown_providers_and_invalid_box_names() {
        assert!(Cli::try_parse_from(["devbox", "metrics", "--provider", "other"]).is_err());
        assert!(Cli::try_parse_from(["devbox", "metrics", "--box", "Invalid"]).is_err());
    }

    #[test]
    fn create_accepts_default_and_named_gpu_profiles() {
        let default_gpu = Cli::try_parse_from(["devbox", "create", "atlas", "--gpu"]).unwrap();
        let Commands::Create(default_gpu) = default_gpu.command else {
            panic!("expected create command");
        };
        assert!(default_gpu.gpu);
        assert!(default_gpu.gpu_profile.is_none());

        let named_gpu =
            Cli::try_parse_from(["devbox", "create", "atlas", "--gpu-profile", "nvidia-l4"])
                .unwrap();
        let Commands::Create(named_gpu) = named_gpu.command else {
            panic!("expected create command");
        };
        assert!(!named_gpu.gpu);
        assert_eq!(named_gpu.gpu_profile.as_deref(), Some("nvidia-l4"));
        assert!(
            Cli::try_parse_from([
                "devbox",
                "create",
                "atlas",
                "--gpu-profile",
                "Invalid_Profile",
            ])
            .is_err()
        );
    }

    #[test]
    fn gpu_profiles_command_is_discoverable() {
        let root = Cli::try_parse_from(["devbox", "gpu"]).unwrap();
        let Commands::Gpu(root) = root.command else {
            panic!("expected gpu command");
        };
        assert!(root.command.is_none());

        let profiles = Cli::try_parse_from(["devbox", "gpu", "profiles", "--json"]).unwrap();
        assert!(profiles.json);
        let Commands::Gpu(profiles) = profiles.command else {
            panic!("expected gpu command");
        };
        assert!(matches!(profiles.command, Some(GpuCommand::Profiles)));
    }

    #[test]
    fn image_profiles_and_create_image_are_discoverable() {
        let root = Cli::try_parse_from(["devbox", "image"]).unwrap();
        let Commands::Image(root) = root.command else {
            panic!("expected image command");
        };
        assert!(root.command.is_none());

        let profiles = Cli::try_parse_from(["devbox", "image", "profiles", "--json"]).unwrap();
        assert!(profiles.json);
        let Commands::Image(profiles) = profiles.command else {
            panic!("expected image command");
        };
        assert!(matches!(profiles.command, Some(ImageCommand::Profiles)));

        let create = Cli::try_parse_from([
            "devbox",
            "create",
            "nginx",
            "--image",
            "docker.io/library/nginx:1.27",
        ])
        .unwrap();
        let Commands::Create(create) = create.command else {
            panic!("expected create command");
        };
        assert_eq!(
            create.image.as_deref(),
            Some("docker.io/library/nginx:1.27")
        );
    }

    #[test]
    fn active_time_format_is_compact_and_bounded() {
        assert_eq!(human_duration(0.0), "0m");
        assert_eq!(human_duration(90.0), "2m");
        assert_eq!(human_duration(3_600.0), "1h");
        assert_eq!(human_duration(3_900.0), "1h 5m");
        assert_eq!(human_duration(-10.0), "0m");
        assert_eq!(human_duration(f64::NAN), "0m");
    }
}
