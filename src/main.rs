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
    use std::os::unix::ffi::OsStrExt;
    let mut args = std::env::args_os().skip(1).peekable();
    let mut root = std::env::var_os("CODE_FLOW_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| backend::home().join("plancraft"));
    let Some(command) = args.next() else {
        return Ok((Mode::Review, root));
    };
    if command == "--help" || command == "-h" {
        return Ok((Mode::Help, root));
    }
    let validate = command == "--validate-flow";
    anyhow::ensure!(
        validate || command == "--flow",
        "Unknown command. Run code-flow --help."
    );
    let mut file = None;
    let mut positional_only = false;
    while let Some(arg) = args.next() {
        if !positional_only {
            if arg == "--" {
                positional_only = true;
                continue;
            }
            if arg == "--help" || arg == "-h" {
                return Ok((Mode::Help, root));
            }
            if arg == "--root" {
                root = PathBuf::from(
                    args.next()
                        .ok_or_else(|| anyhow::anyhow!("Expected a source checkout path"))?,
                );
                continue;
            }
            if let Some(path) = arg.as_os_str().as_bytes().strip_prefix(b"--root=") {
                anyhow::ensure!(!path.is_empty(), "Expected a source checkout path");
                root = std::ffi::OsStr::from_bytes(path).into();
                continue;
            }
            anyhow::ensure!(
                !arg.as_os_str().as_bytes().starts_with(b"-"),
                "Unknown option: {}",
                arg.to_string_lossy()
            );
        }
        anyhow::ensure!(file.is_none(), "Expected a single flow JSON file");
        file = Some(PathBuf::from(arg));
    }
    let mode = if validate {
        Mode::Validate(file.ok_or_else(|| anyhow::anyhow!("Expected a flow JSON file"))?)
    } else {
        Mode::Flow(file.or_else(|| std::env::var_os("CODE_FLOW_FILE").map(PathBuf::from)))
    };
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
