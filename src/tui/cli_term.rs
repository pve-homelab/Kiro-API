//! Embedded PTY terminal for the Kiro-API CLI tab.

use anyhow::{Context, Result};
use portable_pty::{native_pty_system, CommandBuilder, MasterPty, PtySize};
use std::io::{Read, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, Sender, TryRecvError};
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use vt100::Parser;

pub struct CliTerminal {
    parser: Parser,
    writer: Box<dyn Write + Send>,
    master: Box<dyn MasterPty + Send>,
    rx: Receiver<Vec<u8>>,
    _reader: JoinHandle<()>,
    child_alive: Arc<AtomicBool>,
    _child_wait: JoinHandle<()>,
    cols: u16,
    rows: u16,
    pub status: String,
}

impl CliTerminal {
    /// Generic shell PTY (PowerShell / $SHELL) for the CLI tab.
    pub fn start(cols: u16, rows: u16, cwd: Option<&str>) -> Result<Self> {
        let mut cmd = shell_command();
        if let Some(dir) = cwd {
            if !dir.is_empty() {
                cmd.cwd(dir);
            }
        }
        enrich_path(&mut cmd);
        Self::start_with_command(cols, rows, cmd, "shell started — type agent commands normally")
    }

    /// Interactive Kiro CLI PTY (separate from /v1 headless jobs).
    pub fn start_agent(
        cols: u16,
        rows: u16,
        cwd: Option<&str>,
        program: &std::path::Path,
        prefix_args: &[String],
        _model: &str,
        _trust: bool,
    ) -> Result<Self> {
        let mut cmd = CommandBuilder::new(program);
        for arg in prefix_args {
            cmd.arg(arg);
        }
        // Interactive chat UI (default kiro-cli entry).
        if let Some(dir) = cwd {
            if !dir.is_empty() {
                cmd.cwd(dir);
            }
        }
        enrich_path(&mut cmd);
        Self::start_with_command(
            cols,
            rows,
            cmd,
            "kiro chat started — separate from /v1 API jobs",
        )
    }

    fn start_with_command(
        cols: u16,
        rows: u16,
        cmd: CommandBuilder,
        status: &str,
    ) -> Result<Self> {
        let cols = cols.max(40);
        let rows = rows.max(10);
        let pty_system = native_pty_system();
        let pair = pty_system
            .openpty(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .context("openpty")?;

        let child = pair
            .slave
            .spawn_command(cmd)
            .context("spawn command in pty")?;
        drop(pair.slave);

        let mut reader = pair
            .master
            .try_clone_reader()
            .context("clone pty reader")?;
        let writer = pair
            .master
            .take_writer()
            .context("take pty writer")?;

        let (tx, rx): (Sender<Vec<u8>>, Receiver<Vec<u8>>) = mpsc::channel();
        let reader_handle = thread::spawn(move || {
            let mut buf = [0u8; 8192];
            loop {
                match reader.read(&mut buf) {
                    Ok(0) => break,
                    Ok(n) => {
                        if tx.send(buf[..n].to_vec()).is_err() {
                            break;
                        }
                    }
                    Err(_) => break,
                }
            }
        });

        let child_alive = Arc::new(AtomicBool::new(true));
        let flag = child_alive.clone();
        let wait_handle = thread::spawn(move || {
            let mut child = child;
            let _ = child.wait();
            flag.store(false, Ordering::SeqCst);
        });

        Ok(Self {
            parser: Parser::new(rows, cols, 2000),
            writer,
            master: pair.master,
            rx,
            _reader: reader_handle,
            child_alive,
            _child_wait: wait_handle,
            cols,
            rows,
            status: status.into(),
        })
    }

    pub fn alive(&self) -> bool {
        self.child_alive.load(Ordering::SeqCst)
    }

    pub fn poll(&mut self) {
        loop {
            match self.rx.try_recv() {
                Ok(bytes) => self.parser.process(&bytes),
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    self.child_alive.store(false, Ordering::SeqCst);
                    break;
                }
            }
        }
    }

    pub fn resize(&mut self, cols: u16, rows: u16) {
        let cols = cols.max(40);
        let rows = rows.max(10);
        if cols == self.cols && rows == self.rows {
            return;
        }
        self.cols = cols;
        self.rows = rows;
        let _ = self.master.resize(PtySize {
            rows,
            cols,
            pixel_width: 0,
            pixel_height: 0,
        });
        // vt100::Parser does not expose resize in older versions — recreate keeping scrollback is hard;
        // process a clear + note. Simpler: new parser.
        self.parser = Parser::new(rows, cols, 2000);
    }

    pub fn write_bytes(&mut self, data: &[u8]) {
        let _ = self.writer.write_all(data);
        let _ = self.writer.flush();
    }

    pub fn write_str(&mut self, s: &str) {
        self.write_bytes(s.as_bytes());
    }

    pub fn handle_key(&mut self, key: crossterm::event::KeyEvent) {
        use crossterm::event::{KeyCode, KeyModifiers};
        match key.code {
            KeyCode::Char(c) if key.modifiers.contains(KeyModifiers::CONTROL) => {
                let b = (c.to_ascii_lowercase() as u8).saturating_sub(b'a').saturating_add(1);
                if (1..=26).contains(&b) {
                    self.write_bytes(&[b]);
                }
            }
            KeyCode::Char(c) => self.write_str(&c.to_string()),
            KeyCode::Enter => self.write_str("\r"),
            KeyCode::Backspace => self.write_bytes(&[0x7f]),
            KeyCode::Delete => self.write_bytes(&[0x1b, b'[', b'3', b'~']),
            KeyCode::Tab => self.write_bytes(&[b'\t']),
            KeyCode::Esc => self.write_bytes(&[0x1b]),
            KeyCode::Up => self.write_bytes(&[0x1b, b'[', b'A']),
            KeyCode::Down => self.write_bytes(&[0x1b, b'[', b'B']),
            KeyCode::Right => self.write_bytes(&[0x1b, b'[', b'C']),
            KeyCode::Left => self.write_bytes(&[0x1b, b'[', b'D']),
            KeyCode::Home => self.write_bytes(&[0x1b, b'[', b'H']),
            KeyCode::End => self.write_bytes(&[0x1b, b'[', b'F']),
            KeyCode::PageUp => self.write_bytes(&[0x1b, b'[', b'5', b'~']),
            KeyCode::PageDown => self.write_bytes(&[0x1b, b'[', b'6', b'~']),
            _ => {}
        }
    }

    pub fn screen_lines(&self) -> Vec<String> {
        let screen = self.parser.screen();
        let mut out = Vec::with_capacity(self.rows as usize);
        for row in 0..self.rows {
            let mut line = String::new();
            for col in 0..self.cols {
                let cell = screen.cell(row, col);
                if let Some(cell) = cell {
                    let ch = cell.contents();
                    if ch.is_empty() {
                        line.push(' ');
                    } else {
                        line.push_str(&ch);
                    }
                } else {
                    line.push(' ');
                }
            }
            out.push(line.trim_end().to_string());
        }
        out
    }
}

fn shell_command() -> CommandBuilder {
    #[cfg(windows)]
    {
        let mut cmd = CommandBuilder::new("powershell.exe");
        cmd.arg("-NoLogo");
        cmd
    }
    #[cfg(not(windows))]
    {
        let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/bash".into());
        let mut cmd = CommandBuilder::new(shell);
        cmd.arg("-l");
        cmd
    }
}

fn enrich_path(cmd: &mut CommandBuilder) {
    #[cfg(windows)]
    {
        if let Ok(local) = std::env::var("LOCALAPPDATA") {
            prepend_path_env(cmd, &format!("{local}\\cursor-agent"));
        }
    }
    #[cfg(not(windows))]
    {
        if let Some(home) = dirs::home_dir() {
            prepend_path_env(
                cmd,
                &home.join(".local").join("bin").display().to_string(),
            );
        }
    }
}

fn prepend_path_env(cmd: &mut CommandBuilder, dir: &str) {
    let key = if cfg!(windows) { "Path" } else { "PATH" };
    let current = std::env::var_os(key).unwrap_or_default();
    let mut new_path = std::ffi::OsString::from(dir);
    new_path.push(if cfg!(windows) { ";" } else { ":" });
    new_path.push(current);
    cmd.env(key, new_path);
}
