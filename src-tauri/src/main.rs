#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::env;
use std::fs;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use tauri::Manager;

const BACKEND_NAME: &str = "local-story-archive-desktop-backend";

struct BackendProcess(Arc<Mutex<Option<Child>>>);

impl BackendProcess {
    fn shutdown(&self) {
        if let Ok(mut child) = self.0.lock() {
            if let Some(mut child) = child.take() {
                terminate_backend(&mut child);
            }
        }
    }
}

impl Drop for BackendProcess {
    fn drop(&mut self) {
        self.shutdown();
    }
}

fn terminate_backend(child: &mut Child) {
    if cfg!(windows) {
        let _ = Command::new("taskkill")
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    } else {
        let _ = child.kill();
    }
    let _ = child.wait();
}

fn backend_exe_name() -> &'static str {
    if cfg!(windows) {
        "local-story-archive-desktop-backend.exe"
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
    if let Ok(command) = env::var("LOCAL_STORY_ARCHIVE_BACKEND") {
        return Command::new(command);
    }

    for path in candidate_backend_paths() {
        if path.exists() {
            return Command::new(path);
        }
    }

    Command::new(BACKEND_NAME)
}

fn startup_url_file() -> PathBuf {
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or_default();
    env::temp_dir().join(format!(
        "local-story-archive-desktop-backend-{timestamp}.url"
    ))
}

fn read_startup_url(path: &PathBuf, deadline: Instant) -> Result<String, String> {
    while Instant::now() <= deadline {
        if let Ok(url) = fs::read_to_string(path) {
            let url = url.trim().to_string();
            if !url.is_empty() {
                let _ = fs::remove_file(path);
                return Ok(url);
            }
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    Err("backend did not report a startup URL".to_string())
}

fn start_backend() -> Result<(String, BackendProcess), String> {
    let startup_url_file = startup_url_file();
    let mut command = backend_command();
    let mut child = command
        .arg("--startup-url-file")
        .arg(&startup_url_file)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|err| format!("failed to start backend: {err}"))?;

    let deadline = Instant::now() + Duration::from_secs(20);
    match read_startup_url(&startup_url_file, deadline) {
        Ok(url) => {
            let process = BackendProcess(Arc::new(Mutex::new(Some(child))));
            Ok((url, process))
        }
        Err(err) => {
            terminate_backend(&mut child);
            let _ = fs::remove_file(startup_url_file);
            Err(err)
        }
    }
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
        start_backend().expect("Local Story Archive backend failed to start");

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            focus_main_window(app);
        }))
        .manage(backend_process)
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                window.state::<BackendProcess>().shutdown();
            }
        })
        .setup(move |app| {
            tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::External(backend_url.parse().expect("valid backend URL")),
            )
            .title("Local Story Archive")
            .inner_size(1200.0, 800.0)
            .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Tauri application");
}
