use std::env;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::Manager;

const BACKEND_NAME: &str = "wattpad-crawler-desktop-backend";
const BACKEND_URL_PREFIX: &str = "WATTPAD_CRAWLER_DESKTOP_URL=";

struct BackendProcess(Arc<Mutex<Option<Child>>>);

impl Drop for BackendProcess {
    fn drop(&mut self) {
        if let Ok(mut child) = self.0.lock() {
            if let Some(mut child) = child.take() {
                let _ = child.kill();
            }
        }
    }
}

fn backend_exe_name() -> &'static str {
    if cfg!(windows) {
        "wattpad-crawler-desktop-backend.exe"
    } else {
        BACKEND_NAME
    }
}

fn candidate_backend_paths() -> Vec<PathBuf> {
    let mut paths = Vec::new();

    if let Ok(current_exe) = env::current_exe() {
        if let Some(app_dir) = current_exe.parent() {
            paths.push(app_dir.join(backend_exe_name()));
            paths.push(app_dir.join("bin").join(backend_exe_name()));

            if cfg!(target_os = "macos") {
                paths.push(app_dir.join("../Resources").join(backend_exe_name()));
                paths.push(app_dir.join("../Resources/bin").join(backend_exe_name()));
            }
        }
    }

    if let Ok(project_dir) = env::current_dir() {
        paths.push(project_dir.join("src-tauri").join("bin").join(backend_exe_name()));
    }

    paths
}

fn backend_command() -> Command {
    if let Ok(command) = env::var("WATTPAD_CRAWLER_BACKEND") {
        return Command::new(command);
    }

    for path in candidate_backend_paths() {
        if path.exists() {
            return Command::new(path);
        }
    }

    Command::new(BACKEND_NAME)
}

fn start_backend() -> Result<(String, BackendProcess), String> {
    let mut child = backend_command()
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|err| format!("failed to start backend: {err}"))?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "backend stdout was not captured".to_string())?;
    let reader = BufReader::new(stdout);
    let deadline = Instant::now() + Duration::from_secs(20);

    for line in reader.lines() {
        let line = line.map_err(|err| format!("failed to read backend startup: {err}"))?;
        if let Some(url) = line.strip_prefix(BACKEND_URL_PREFIX) {
            let process = BackendProcess(Arc::new(Mutex::new(Some(child))));
            return Ok((url.to_string(), process));
        }
        if Instant::now() > deadline {
            break;
        }
    }

    let _ = child.kill();
    Err("backend did not report a startup URL".to_string())
}

fn focus_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn main() {
    let (backend_url, backend_process) =
        start_backend().expect("Wattpad Crawler backend failed to start");

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            focus_main_window(app);
        }))
        .manage(backend_process)
        .setup(move |app| {
            tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::External(backend_url.parse().expect("valid backend URL")),
            )
            .title("Wattpad Crawler")
            .inner_size(1200.0, 800.0)
            .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Tauri application");
}
