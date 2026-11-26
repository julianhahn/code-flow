mod app;
mod backend;
mod canvas;
mod chat;
mod flow_model;
mod flow_viewer;
mod theme;
mod widgets;

use std::{
    fs, io,
    os::fd::AsRawFd,
    path::PathBuf,
    time::{SystemTime, UNIX_EPOCH},
};

fn session_log() -> io::Result<PathBuf> {
    let dir = std::env::temp_dir().join("code-flow-logs");
    fs::create_dir_all(&dir)?;
    let stamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let path = dir.join(format!("session-{stamp}-{}.log", std::process::id()));
    let file = fs::File::create_new(&path)?;
    eprintln!("Code Flow session log: {}", path.display());
    // Capture GTK, subprocess and Rust diagnostics for Finder and desktop launches.
    for fd in [libc::STDERR_FILENO, libc::STDOUT_FILENO] {
        if unsafe { libc::dup2(file.as_raw_fd(), fd) } < 0 {
            return Err(io::Error::last_os_error());
        }
    }
    eprintln!(
        "Log file: {}\nPlatform: {}\nPID: {}",
        path.display(),
        std::env::consts::OS,
        std::process::id()
    );
    Ok(path)
}

enum Mode {
    Review,
    Flow(Option<PathBuf>),
    Validate(PathBuf),
    Help,
}

fn arguments() -> anyhow::Result<(Mode, PathBuf)> {
    let mut args = std::env::args_os().skip(1).peekable();
    let mut root = std::env::var_os("CODE_FLOW_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| backend::home().join("plancraft"));
    let mode = match args.peek().and_then(|s| s.to_str()) {
        None => Mode::Review,
        Some("--help" | "-h") => {
            args.next();
            Mode::Help
        }
        Some("--flow") => {
            args.next();
            let file = if args.peek().is_some_and(|arg| arg != "--root") {
                args.next()
            } else {
                None
            };
            Mode::Flow(
                file.or_else(|| std::env::var_os("CODE_FLOW_FILE"))
                    .map(PathBuf::from),
            )
        }
        Some("--validate-flow") => {
            args.next();
            Mode::Validate(
                args.next()
                    .map(PathBuf::from)
                    .ok_or_else(|| anyhow::anyhow!("Expected a flow JSON file"))?,
            )
        }
        _ => anyhow::bail!("Unknown argument. Run code-flow --help."),
    };
    if args.peek().is_some_and(|s| s == "--root") {
        args.next();
        root = args
            .next()
            .map(PathBuf::from)
            .ok_or_else(|| anyhow::anyhow!("Expected a source checkout path"))?;
    }
    anyhow::ensure!(args.next().is_none(), "Unexpected extra argument");
    Ok((mode, root))
}

fn run() -> anyhow::Result<()> {
    let (mode, root) = arguments()?;
    match mode {
        Mode::Help => {
            println!("code-flow\ncode-flow --flow [FILE] [--root CHECKOUT]\ncode-flow --validate-flow FILE [--root CHECKOUT]");
            return Ok(());
        }
        Mode::Validate(file) => {
            let (_, positions, warnings) = flow_model::read(&file, &root)?;
            println!(
                "{}",
                serde_json::to_string_pretty(
                    &serde_json::json!({"positions":positions,"warnings":warnings})
                )?
            );
            return Ok(());
        }
        _ => {}
    }
    if let Err(e) = session_log() {
        eprintln!("Could not start session log: {e}");
    }
    glib::set_prgname(Some("code-flow"));
    glib::set_application_name("Code Flow");
    gtk::init().map_err(|e| anyhow::anyhow!("Could not start GTK: {e}"))?;
    gdk::set_program_class("code-flow");
    match mode {
        Mode::Review => {
            theme::apply();
            let review = app::App::new();
            review.start();
            gtk::main();
        }
        Mode::Flow(path) => {
            let _view = flow_viewer::Viewer::new(root, path);
            gtk::main();
        }
        _ => unreachable!(),
    }
    eprintln!("Code Flow session ended.");
    Ok(())
}

fn main() {
    if let Err(e) = run() {
        eprintln!("Code Flow: {e:#}");
        std::process::exit(1);
    }
}
