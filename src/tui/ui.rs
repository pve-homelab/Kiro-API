use super::app::{ConfigField, Tab, TuiApp};
use crate::log_buffer::LogLevel;
use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, List, ListItem, Paragraph, Tabs, Wrap};
use ratatui::Frame;

fn accent() -> Style {
    Style::default()
        .fg(Color::Cyan)
        .add_modifier(Modifier::BOLD)
}

fn accent_magenta() -> Style {
    Style::default()
        .fg(Color::Magenta)
        .add_modifier(Modifier::BOLD)
}

fn muted() -> Style {
    Style::default().fg(Color::DarkGray)
}

fn value_style() -> Style {
    Style::default().fg(Color::White)
}

fn panel_block(title: impl Into<String>) -> Block<'static> {
    Block::default()
        .borders(Borders::ALL)
        .border_style(Style::default().fg(Color::Cyan))
        .title(Span::styled(title.into(), accent()))
}

fn kv(label: &str, value: impl AsRef<str>) -> Line<'static> {
    Line::from(vec![
        Span::styled(format!("{label:<12}"), Style::default().fg(Color::Cyan)),
        Span::styled(value.as_ref().to_string(), value_style()),
    ])
}

fn tab_accent(tab: Tab) -> Color {
    match tab {
        Tab::Dashboard => Color::Cyan,
        Tab::Config => Color::Yellow,
        Tab::Logs => Color::Green,
        Tab::Usage => Color::Magenta,
        Tab::Agent => Color::LightCyan,
        Tab::Help => Color::Blue,
        Tab::Cli => Color::LightGreen,
    }
}

pub fn draw(frame: &mut Frame, app: &TuiApp) {
    let root = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(5),
            Constraint::Length(3),
        ])
        .split(frame.area());

    draw_header(frame, root[0], app);
    match app.tab {
        Tab::Dashboard => draw_dashboard(frame, root[1], app),
        Tab::Config => draw_config(frame, root[1], app),
        Tab::Logs => draw_logs(frame, root[1], app),
        Tab::Usage => draw_usage(frame, root[1], app),
        Tab::Agent => draw_agent(frame, root[1], app),
        Tab::Help => draw_help(frame, root[1], app),
        Tab::Cli => draw_cli(frame, root[1], app),
    }
    draw_footer(frame, root[2], app);
}

fn draw_header(frame: &mut Frame, area: Rect, app: &TuiApp) {
    let titles: Vec<Line> = Tab::ALL
        .iter()
        .enumerate()
        .map(|(i, t)| {
            let selected = *t == app.tab;
            let style = if selected {
                Style::default()
                    .fg(tab_accent(*t))
                    .add_modifier(Modifier::BOLD | Modifier::UNDERLINED)
            } else {
                Style::default().fg(Color::DarkGray)
            };
            Line::from(Span::styled(format!(" {} {} ", i + 1, t.title()), style))
        })
        .collect();
    let selected = Tab::ALL.iter().position(|t| *t == app.tab).unwrap_or(0);
    let tabs = Tabs::new(titles)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(Style::default().fg(Color::Magenta))
                .title(Span::styled(" Kiro-API ", accent_magenta())),
        )
        .select(selected)
        .divider(Span::styled("│", muted()))
        .style(Style::default().fg(Color::DarkGray))
        .highlight_style(
            Style::default()
                .fg(tab_accent(app.tab))
                .bg(Color::Black)
                .add_modifier(Modifier::BOLD | Modifier::REVERSED),
        );
    frame.render_widget(tabs, area);
}

fn draw_footer(frame: &mut Frame, area: Rect, app: &TuiApp) {
    let hint = match app.tab {
        Tab::Dashboard => "s start/stop · r health · t smoke · 1-7 tabs · q quit",
        Tab::Config => "↑/↓ · Enter edit · p profile · j json · w force · k trust",
        Tab::Logs => "↑/↓ scroll · c clear",
        Tab::Usage => "↑/↓ scroll · x reset",
        Tab::Agent => "keys → agent chat · Shift+R restart · F1–F7 / Ctrl+←→ tabs · Ctrl+Q quit",
        Tab::Help => "↑/↓ PgUp/PgDn scroll · Home/End · 1-7 tabs",
        Tab::Cli => "keys → shell · Shift+R restart · F1–F7 / Ctrl+←→ tabs · Ctrl+Q quit",
    };
    let text = if app.editing {
        Line::from(vec![
            Span::styled("Editing ", Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
            Span::styled(app.config_field.label().to_string(), accent()),
            Span::raw(": "),
            Span::styled(format!("{}▌", app.edit_buffer), value_style()),
        ])
    } else if app.smoke_running {
        Line::from(vec![
            Span::styled(
                "Smoke test running…  ",
                Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD),
            ),
            Span::styled(hint, muted()),
        ])
    } else {
        Line::from(vec![
            Span::styled(app.status_message.clone(), Style::default().fg(Color::Green)),
            Span::styled("  |  ", muted()),
            Span::styled(hint.to_string(), muted()),
        ])
    };
    let p = Paragraph::new(text)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(Style::default().fg(tab_accent(app.tab)))
                .title(Span::styled(
                    format!(" status · {} ", app.tab.title()),
                    Style::default()
                        .fg(tab_accent(app.tab))
                        .add_modifier(Modifier::BOLD),
                )),
        )
        .wrap(Wrap { trim: true });
    frame.render_widget(p, area);
}

fn draw_dashboard(frame: &mut Frame, area: Rect, app: &TuiApp) {
    let cfg = app.state.config.read().clone();
    let running = app.state.is_running();
    let healthy = app.state.is_healthy();
    let agent = app
        .state
        .agent_version
        .read()
        .clone()
        .unwrap_or_else(|| "unknown".into());
    let usage = app.state.usage.totals();
    let last_error = app.state.last_error.read().clone();
    let binary = app
        .state
        .backend
        .resolve_launch()
        .map(|l| l.display)
        .unwrap_or_else(|e| format!("MISSING ({e})"));

    let status_color = if running && healthy {
        Color::Green
    } else if running {
        Color::Yellow
    } else {
        Color::Red
    };

    let lines = vec![
        Line::from(vec![
            Span::styled("Service:     ", Style::default().fg(Color::Cyan)),
            Span::styled(
                if running { "RUNNING" } else { "STOPPED" },
                Style::default()
                    .fg(status_color)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled("   Health: ", Style::default().fg(Color::Cyan)),
            Span::styled(
                if healthy { "OK" } else { "DOWN" },
                Style::default()
                    .fg(status_color)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled("   Uptime: ", Style::default().fg(Color::Cyan)),
            Span::styled(format!("{}s", app.state.uptime_secs()), value_style()),
        ]),
        Line::from(""),
        kv("Listen:", cfg.listen_addr()),
        kv("Base URL:", cfg.base_url()),
        kv("OpenAI /v1:", cfg.v1_url()),
        Line::from(vec![
            Span::styled("Profile:     ", Style::default().fg(Color::Cyan)),
            Span::styled(cfg.cursor.profile.clone(), Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
            Span::styled("   json_mode=", muted()),
            Span::styled(cfg.cursor.json_mode.to_string(), value_style()),
            Span::styled("   flatten=", muted()),
            Span::styled(cfg.cursor.message_flatten_mode.clone(), value_style()),
        ]),
        Line::from(""),
        kv("Agent bin:", binary),
        kv("Agent ver:", agent),
        Line::from(vec![
            Span::styled("Mode:        ", Style::default().fg(Color::Cyan)),
            Span::styled(cfg.cursor.mode.clone(), Style::default().fg(Color::Magenta).add_modifier(Modifier::BOLD)),
            Span::styled("   model=", muted()),
            Span::styled(cfg.cursor.default_model.clone(), Style::default().fg(Color::Yellow)),
            Span::styled("   force=", muted()),
            Span::styled(cfg.cursor.force.to_string(), value_style()),
            Span::styled("   trust=", muted()),
            Span::styled(cfg.cursor.trust.to_string(), value_style()),
        ]),
        Line::from(vec![
            Span::styled("Context:     ", Style::default().fg(Color::Cyan)),
            Span::styled("max_tokens=", muted()),
            Span::styled(
                cfg.cursor.max_context_tokens.to_string(),
                Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD),
            ),
            Span::styled("   truncate_over=", muted()),
            Span::styled(cfg.cursor.truncate_over_context.to_string(), value_style()),
        ]),
        Line::from(vec![
            Span::styled("Concurrency: ", Style::default().fg(Color::Cyan)),
            Span::styled(format!("max={}", cfg.server.max_concurrency), value_style()),
            Span::styled("   reject_when_busy=", muted()),
            Span::styled(cfg.server.reject_when_busy.to_string(), value_style()),
            Span::styled("   timeout=", muted()),
            Span::styled(format!("{}s", cfg.server.request_timeout_secs), value_style()),
        ]),
        Line::from(""),
        Line::from(vec![
            Span::styled("Active req:  ", Style::default().fg(Color::Cyan)),
            Span::styled(
                app.state.usage.active().to_string(),
                Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                format!(
                    "   Totals: {} ok / {} err / {} calls   avg {}ms",
                    usage.successes, usage.failures, usage.requests, usage.avg_latency_ms
                ),
                value_style(),
            ),
        ]),
        Line::from(vec![
            Span::styled("Tokens≈      ", Style::default().fg(Color::Cyan)),
            Span::styled(
                format!(
                    "prompt {} · completion {} · total {}",
                    usage.prompt_tokens, usage.completion_tokens, usage.total_tokens
                ),
                Style::default().fg(Color::LightMagenta),
            ),
        ]),
        Line::from(vec![
            Span::styled("Last error:  ", Style::default().fg(Color::Cyan)),
            Span::styled(
                last_error.unwrap_or_else(|| "(none)".into()),
                if app.state.last_error.read().is_some() {
                    Style::default().fg(Color::Red)
                } else {
                    muted()
                },
            ),
        ]),
        Line::from(""),
        Line::from(Span::styled(
            "Point any OpenAI SDK client at the /v1 URL. Send X-Request-ID to correlate logs.",
            muted(),
        )),
    ];

    let p = Paragraph::new(lines)
        .block(panel_block(" Kiro-API · service dashboard "))
        .wrap(Wrap { trim: false });
    frame.render_widget(p, area);
}

fn draw_config(frame: &mut Frame, area: Rect, app: &TuiApp) {
    let cfg = app.state.config.read().clone();
    let path = app.state.config_path.read().clone();
    let items: Vec<ListItem> = ConfigField::ALL
        .iter()
        .map(|field| {
            let value = match field {
                ConfigField::Host => cfg.server.host.clone(),
                ConfigField::Port => cfg.server.port.to_string(),
                ConfigField::Profile => cfg.cursor.profile.clone(),
                ConfigField::Model => cfg.cursor.default_model.clone(),
                ConfigField::Mode => cfg.cursor.mode.clone(),
                ConfigField::Workspace => {
                    if cfg.cursor.workspace.is_empty() {
                        "(process cwd)".into()
                    } else {
                        cfg.cursor.workspace.clone()
                    }
                }
                ConfigField::FlattenMode => cfg.cursor.message_flatten_mode.clone(),
                ConfigField::JsonMode => cfg.cursor.json_mode.to_string(),
                ConfigField::MaxContextTokens => cfg.cursor.max_context_tokens.to_string(),
                ConfigField::ApiKey => mask_secret(&cfg.auth.api_key),
                ConfigField::CursorApiKey => mask_secret(&cfg.cursor.cursor_api_key),
                ConfigField::MaxConcurrency => cfg.server.max_concurrency.to_string(),
                ConfigField::Timeout => cfg.server.request_timeout_secs.to_string(),
                ConfigField::RejectWhenBusy => cfg.server.reject_when_busy.to_string(),
            };
            let selected = *field == app.config_field;
            let label_style = if selected {
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD | Modifier::REVERSED)
            } else {
                Style::default().fg(Color::Cyan)
            };
            let val_style = if selected {
                Style::default()
                    .fg(Color::White)
                    .add_modifier(Modifier::BOLD)
            } else {
                value_style()
            };
            let marker = if selected { "▶" } else { " " };
            ListItem::new(Line::from(vec![
                Span::styled(format!("{marker} "), label_style),
                Span::styled(format!("{:<28} ", field.label()), label_style),
                Span::styled(value, val_style),
            ]))
        })
        .collect();

    let list = List::new(items).block(panel_block(format!(
        " configuration · {} ",
        path.display()
    )));
    frame.render_widget(list, area);
}

fn mask_secret(value: &str) -> String {
    if value.is_empty() {
        "(empty)".into()
    } else if value.len() <= 4 {
        "****".into()
    } else {
        format!("{}…{}", &value[..2], &value[value.len() - 2..])
    }
}

fn draw_logs(frame: &mut Frame, area: Rect, app: &TuiApp) {
    let entries = app.state.logs.snapshot();
    let height = area.height.saturating_sub(2) as usize;
    let total = entries.len();
    let scroll = app.log_scroll as usize;
    let end = total.saturating_sub(scroll);
    let start = end.saturating_sub(height);
    let slice = if start < end {
        &entries[start..end]
    } else {
        &[]
    };

    let items: Vec<ListItem> = slice
        .iter()
        .map(|e| {
            let color = match e.level {
                LogLevel::Error => Color::Red,
                LogLevel::Warn => Color::Yellow,
                LogLevel::Info => Color::Green,
                LogLevel::Debug => Color::Blue,
                LogLevel::Trace => Color::DarkGray,
            };
            ListItem::new(Line::from(vec![
                Span::styled(
                    format!("{} ", e.time.format("%H:%M:%S")),
                    Style::default().fg(Color::DarkGray),
                ),
                Span::styled(
                    format!("{:<5} ", e.level.as_str()),
                    Style::default().fg(color),
                ),
                Span::raw(e.message.clone()),
            ]))
        })
        .collect();

    let list = List::new(items).block(panel_block(format!(" logs ({total}) ")));
    frame.render_widget(list, area);
}

fn draw_usage(frame: &mut Frame, area: Rect, app: &TuiApp) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(8), Constraint::Min(3)])
        .split(area);

    let totals = app.state.usage.totals();
    let summary = Paragraph::new(vec![
        Line::from(vec![
            Span::styled("Requests: ", Style::default().fg(Color::Cyan)),
            Span::styled(totals.requests.to_string(), value_style()),
            Span::styled("   Success: ", Style::default().fg(Color::Cyan)),
            Span::styled(totals.successes.to_string(), Style::default().fg(Color::Green).add_modifier(Modifier::BOLD)),
            Span::styled("   Failures: ", Style::default().fg(Color::Cyan)),
            Span::styled(totals.failures.to_string(), Style::default().fg(Color::Red).add_modifier(Modifier::BOLD)),
            Span::styled("   Active: ", Style::default().fg(Color::Cyan)),
            Span::styled(app.state.usage.active().to_string(), Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)),
        ]),
        Line::from(vec![
            Span::styled("Tokens≈ ", Style::default().fg(Color::Cyan)),
            Span::styled(
                format!(
                    "prompt {} · completion {} · total {} · avg {}ms",
                    totals.prompt_tokens,
                    totals.completion_tokens,
                    totals.total_tokens,
                    totals.avg_latency_ms
                ),
                Style::default().fg(Color::LightMagenta),
            ),
        ]),
        Line::from(vec![
            Span::styled("Budget: ", Style::default().fg(Color::Cyan)),
            Span::styled(
                {
                    let cfg = app.state.config.read();
                    format!(
                        "max_context_tokens={} (soft warn; truncate_over={})",
                        cfg.cursor.max_context_tokens, cfg.cursor.truncate_over_context
                    )
                },
                Style::default().fg(Color::Yellow),
            ),
        ]),
        Line::from(""),
        Line::from(Span::styled(
            "Token counts are estimates (chars/4). Response previews shown for completed requests.",
            muted(),
        )),
    ])
    .block(panel_block(" totals "));
    frame.render_widget(summary, chunks[0]);

    let recent = app.state.usage.recent();
    let height = chunks[1].height.saturating_sub(2) as usize;
    let scroll = app.usage_scroll as usize;
    let end = recent.len().saturating_sub(scroll);
    let start = end.saturating_sub(height);
    let slice = if start < end { &recent[start..end] } else { &[] };

    let items: Vec<ListItem> = slice
        .iter()
        .map(|r| {
            let color = match r.status.as_str() {
                "ok" => Color::Green,
                "error" => Color::Red,
                _ => Color::Yellow,
            };
            let preview = r
                .response_preview
                .clone()
                .unwrap_or_else(|| r.error.clone().unwrap_or_default());
            ListItem::new(vec![
                Line::from(vec![
                    Span::styled(
                        format!("{} ", r.started_at.format("%H:%M:%S")),
                        Style::default().fg(Color::DarkGray),
                    ),
                    Span::styled(format!("{:<7} ", r.status), Style::default().fg(color)),
                    Span::raw(format!(
                        "req={} model={} p≈{} c≈{} {}ms",
                        r.request_id, r.model, r.prompt_tokens, r.completion_tokens, r.duration_ms
                    )),
                ]),
                Line::from(Span::styled(
                    preview,
                    Style::default().fg(Color::DarkGray),
                )),
            ])
        })
        .collect();

    let list = List::new(items).block(panel_block(" recent requests "));
    frame.render_widget(list, chunks[1]);
}

fn draw_agent(frame: &mut Frame, area: Rect, app: &TuiApp) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(3), Constraint::Min(5)])
        .split(area);

    let alive = app.agent.as_ref().is_some_and(|c| c.alive());
    let banner = if alive {
        "Interactive Kiro chat — separate from /v1 headless API jobs. Shift+R restarts."
    } else {
        "Agent not running — press r to start, or Shift+R to restart"
    };
    let banner_p = Paragraph::new(Span::styled(banner, muted()))
        .block(panel_block(" Kiro-API · Agent "));
    frame.render_widget(banner_p, chunks[0]);

    let lines: Vec<Line> = if let Some(term) = app.agent.as_ref() {
        term.screen_lines()
            .into_iter()
            .map(Line::from)
            .collect()
    } else {
        vec![Line::from(Span::styled("Starting agent chat…", accent()))]
    };

    let title = if alive {
        " agent chat "
    } else {
        " agent chat (dead) "
    };
    let term = Paragraph::new(lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(Style::default().fg(Color::LightCyan))
                .title(Span::styled(title, accent())),
        )
        .style(Style::default().fg(Color::Cyan));
    frame.render_widget(term, chunks[1]);
}

fn draw_cli(frame: &mut Frame, area: Rect, app: &TuiApp) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(3), Constraint::Min(5)])
        .split(area);

    let alive = app.cli.as_ref().is_some_and(|c| c.alive());
    let banner = if alive {
        "Embedded shell (PowerShell / $SHELL). Far-right tab — leave with F1–F7 / Ctrl+←. Try: kiro-cli --version"
    } else {
        "Shell not running — press r to start, or Shift+R to restart"
    };
    let banner_p = Paragraph::new(Span::styled(banner, muted()))
        .block(panel_block(" Kiro-API · CLI "));
    frame.render_widget(banner_p, chunks[0]);

    let lines: Vec<Line> = if let Some(cli) = app.cli.as_ref() {
        cli.screen_lines()
            .into_iter()
            .map(Line::from)
            .collect()
    } else {
        vec![Line::from(Span::styled("Starting embedded CLI…", accent()))]
    };

    let title = if alive {
        " terminal "
    } else {
        " terminal (dead) "
    };
    let term = Paragraph::new(lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(Style::default().fg(Color::LightGreen))
                .title(Span::styled(
                    title,
                    Style::default()
                        .fg(Color::LightGreen)
                        .add_modifier(Modifier::BOLD),
                )),
        )
        .style(Style::default().fg(Color::Green));
    frame.render_widget(term, chunks[1]);
}

fn draw_help(frame: &mut Frame, area: Rect, app: &TuiApp) {
    let lines = super::help::help_lines();
    let total = lines.len();
    let height = area.height.saturating_sub(2) as usize;
    let max_scroll = total.saturating_sub(height.max(1));
    let scroll = (app.help_scroll as usize).min(max_scroll);

    let p = Paragraph::new(lines)
        .block(panel_block(format!(
            " Kiro-API · help  (lines {}–{} of {}) ",
            scroll + 1,
            (scroll + height).min(total).max(scroll + 1),
            total
        )))
        .wrap(Wrap { trim: false })
        .scroll((scroll as u16, 0));
    frame.render_widget(p, area);
}
