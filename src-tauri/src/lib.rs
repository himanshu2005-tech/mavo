use rand::{rngs::OsRng, RngCore};
use serde::{Deserialize, Serialize};
use std::io::{Read, Write};
use std::time::Duration;
use std::{
    fs::{self, OpenOptions},
    net::TcpListener,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
};
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    WindowEvent,
};
use tauri::{Emitter, Manager, RunEvent, State, WebviewUrl, WebviewWindowBuilder};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

const CREATE_NO_WINDOW: u32 = 0x08000000;

struct BackendState {
    child: Mutex<Option<Child>>,
    port: u16,
    token: String,
    #[cfg(target_os = "windows")]
    _job: BackendJob,
}

// An OS-owned job closes the entire sidecar tree even when a dev rebuild kills the host.
// This also covers the Windows virtualenv launcher and Playwright's owned driver/browser.
#[cfg(target_os = "windows")]
struct BackendJob(*mut std::ffi::c_void);
#[cfg(target_os = "windows")]
unsafe impl Send for BackendJob {}
#[cfg(target_os = "windows")]
unsafe impl Sync for BackendJob {}

#[cfg(target_os = "windows")]
#[repr(C)]
#[derive(Default)]
struct JobBasicLimits {
    process_time: i64,
    job_time: i64,
    flags: u32,
    min_working_set: usize,
    max_working_set: usize,
    active_processes: u32,
    affinity: usize,
    priority: u32,
    scheduling: u32,
}
#[cfg(target_os = "windows")]
#[repr(C)]
#[derive(Default)]
struct JobExtendedLimits {
    basic: JobBasicLimits,
    io: [u64; 6],
    process_memory: usize,
    job_memory: usize,
    peak_process_memory: usize,
    peak_job_memory: usize,
}
#[cfg(target_os = "windows")]
#[link(name = "kernel32")]
extern "system" {
    fn CreateJobObjectW(
        attributes: *mut std::ffi::c_void,
        name: *const u16,
    ) -> *mut std::ffi::c_void;
    fn SetInformationJobObject(
        job: *mut std::ffi::c_void,
        class: i32,
        info: *const std::ffi::c_void,
        length: u32,
    ) -> i32;
    fn AssignProcessToJobObject(job: *mut std::ffi::c_void, process: *mut std::ffi::c_void) -> i32;
    fn CloseHandle(handle: *mut std::ffi::c_void) -> i32;
}
#[cfg(target_os = "windows")]
impl BackendJob {
    fn attach(child: &Child) -> Result<Self, String> {
        use std::os::windows::io::AsRawHandle;
        let job = Self(unsafe { CreateJobObjectW(std::ptr::null_mut(), std::ptr::null()) });
        if job.0.is_null() {
            return Err("Could not create the backend process boundary".into());
        }
        let mut limits = JobExtendedLimits::default();
        // KILL_ON_JOB_CLOSE (0x2000) ends the whole backend tree with Mavo. BREAKAWAY_OK
        // (0x0800) lets a program the person asked for, such as a Blender window opened by a
        // project run, start outside the boundary so quitting Mavo never closes it on unsaved
        // work. Only a process that asks to break away does; everything else stays inside.
        limits.basic.flags = 0x2000 | 0x0800;
        let configured = unsafe {
            SetInformationJobObject(
                job.0,
                9,
                &limits as *const _ as *const _,
                std::mem::size_of::<JobExtendedLimits>() as u32,
            )
        };
        let assigned = if configured != 0 {
            unsafe { AssignProcessToJobObject(job.0, child.as_raw_handle()) }
        } else {
            0
        };
        if assigned == 0 {
            return Err("Could not attach the backend to its Windows process boundary".into());
        }
        Ok(job)
    }
}
#[cfg(target_os = "windows")]
impl Drop for BackendJob {
    fn drop(&mut self) {
        if !self.0.is_null() {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}

#[derive(Clone, Deserialize, Serialize)]
struct ReminderPayload {
    #[serde(default)]
    kind: String,
    id: String,
    #[serde(default)]
    crew_id: Option<String>,
    crew_name: String,
    message: String,
    scheduled_for: String,
    #[serde(default)]
    preview: bool,
    // A one-time task's question: which task and step it is, so the desktop answers it there.
    // Without these, serde dropped them on the way to the popup, and a reply went to a Crew
    // ("Crew not found").
    #[serde(default)]
    task_run_id: Option<String>,
    #[serde(default)]
    task_step_id: Option<String>,
}

struct ReminderOverlayState {
    pending: Mutex<Option<ReminderPayload>>,
}

#[derive(Serialize)]
struct BackendConfig {
    base_url: String,
    token: String,
}

#[tauri::command]
fn backend_config(state: State<'_, BackendState>) -> BackendConfig {
    BackendConfig {
        base_url: format!("http://127.0.0.1:{}", state.port),
        token: state.token.clone(),
    }
}

#[tauri::command]
fn open_github_url(url: String) -> Result<(), String> {
    let parsed = tauri::Url::parse(&url).map_err(|_| "Invalid GitHub URL")?;
    let parts: Vec<_> = parsed.path().split('/').collect();
    let install = parts.len() == 5
        && parts[1] == "apps"
        && !parts[2].is_empty()
        && parts[2]
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
        && parts[3] == "installations"
        && parts[4] == "new";
    if parsed.scheme() != "https"
        || parsed.host_str() != Some("github.com")
        || parsed.port().is_some()
        || parsed.username() != ""
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
        || !(parsed.path() == "/login/device"
            || parsed.path() == "/settings/apps/authorizations"
            || install)
    {
        return Err("Only approved GitHub authorization URLs can be opened".into());
    }
    #[cfg(target_os = "windows")]
    Command::new("rundll32.exe")
        .args(["url.dll,FileProtocolHandler", &url])
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map_err(|_| "Could not open the default browser")?;
    #[cfg(not(target_os = "windows"))]
    return Err("Browser sign-in is currently supported on Windows".into());
    Ok(())
}

#[tauri::command]
fn open_google_url(url: String) -> Result<(), String> {
    let parsed = tauri::Url::parse(&url).map_err(|_| "Invalid Google URL")?;
    let allowed = parsed.scheme() == "https"
        && match parsed.host_str() {
            Some("accounts.google.com") => parsed.path() == "/o/oauth2/v2/auth",
            Some("console.cloud.google.com") => true,
            Some("myaccount.google.com") => parsed.path().starts_with("/connections"),
            _ => false,
        };
    if !allowed || parsed.username() != "" || parsed.password().is_some() {
        return Err("Only approved account sign-in and setup URLs can be opened".into());
    }
    #[cfg(target_os = "windows")]
    Command::new("rundll32.exe")
        .args(["url.dll,FileProtocolHandler", &url])
        .creation_flags(CREATE_NO_WINDOW)
        .spawn()
        .map_err(|_| "Could not open the default browser")?;
    #[cfg(not(target_os = "windows"))]
    return Err("Google browser sign-in is currently supported on Windows".into());
    Ok(())
}

#[tauri::command]
async fn show_reminder_overlay(
    app: tauri::AppHandle,
    state: State<'_, ReminderOverlayState>,
    reminder: ReminderPayload,
) -> Result<(), String> {
    *state
        .pending
        .lock()
        .map_err(|_| "Reminder state is unavailable")? = Some(reminder.clone());
    let window = app
        .get_webview_window("status")
        .ok_or("Status window unavailable")?;
    // A new update arrives as a one-line pill beside the icon, not a window.
    resize_status(&app, true, true, true, false, false, &app.state::<StatusWindowState>())?;
    window.show().map_err(|error| error.to_string())?;
    // Do not steal keyboard focus from the user's current application.
    window
        .emit("reminder-updated", &reminder)
        .map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
fn pending_reminder(
    state: State<'_, ReminderOverlayState>,
) -> Result<Option<ReminderPayload>, String> {
    state
        .pending
        .lock()
        .map(|value| value.clone())
        .map_err(|_| "Reminder state is unavailable".to_string())
}

#[tauri::command]
fn close_reminder_overlay(
    app: tauri::AppHandle,
    state: State<'_, ReminderOverlayState>,
) -> Result<(), String> {
    if let Some(window) = app.get_webview_window("reminder") {
        window.close().map_err(|error| error.to_string())?;
    }
    *state
        .pending
        .lock()
        .map_err(|_| "Reminder state is unavailable")? = None;
    if let Some(window) = app.get_webview_window("status") {
        window
            .emit("reminder-cleared", ())
            .map_err(|error| error.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn quit_mavo(app: tauri::AppHandle) {
    app.exit(0);
}

#[tauri::command]
fn show_main(app: tauri::AppHandle, activity: bool) -> Result<(), String> {
    let main = app
        .get_webview_window("main")
        .ok_or("Main window unavailable")?;
    main.show().map_err(|e| e.to_string())?;
    main.unminimize().map_err(|e| e.to_string())?;
    main.set_focus().map_err(|e| e.to_string())?;
    if activity {
        main.emit("mavo-open-activity", ())
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn status_expanded(
    app: tauri::AppHandle,
    expanded: bool,
    peek: Option<bool>,
    missed: Option<bool>,
    fan: Option<bool>,
    state: State<'_, StatusWindowState>,
    reminders: State<'_, ReminderOverlayState>,
) -> Result<(), String> {
    let has_message = reminders
        .pending
        .lock()
        .map_err(|_| "Reminder state unavailable")?
        .is_some();
    resize_status(
        &app,
        expanded,
        has_message,
        peek.unwrap_or(false),
        missed.unwrap_or(false),
        fan.unwrap_or(false),
        &state,
    )
}

fn resize_status(
    app: &tauri::AppHandle,
    expanded: bool,
    has_message: bool,
    peek: bool,
    missed: bool,
    fan: bool,
    state: &StatusWindowState,
) -> Result<(), String> {
    let window = app
        .get_webview_window("status")
        .ok_or("Status window unavailable")?;
    let size = window.outer_size().map_err(|e| e.to_string())?;
    let position = window.outer_position().map_err(|e| e.to_string())?;
    let scale = window.scale_factor().map_err(|e| e.to_string())?;
    let mut anchor = state
        .anchor
        .lock()
        .map_err(|_| "Status position unavailable")?;
    if expanded && anchor.is_none() {
        *anchor = Some(position);
    }
    if expanded && fan && !has_message {
        // The Crew spread over the whole screen (a hundred agents need the room): the window
        // covers the work area, above the taskbar; the page draws the icon where it was.
        if let Some(monitor) = window.current_monitor().map_err(|e| e.to_string())?.or(
            window.primary_monitor().map_err(|e| e.to_string())?,
        ) {
            let area = *monitor.work_area();
            window.set_size(area.size).map_err(|e| e.to_string())?;
            window.set_position(area.position).map_err(|e| e.to_string())?;
            return Ok(());
        }
    }
    let (width, height) = if expanded && has_message && peek {
        (380.0, 72.0)
    } else if expanded && has_message {
        (400.0, 380.0)
    } else if expanded && fan {
        // The Crew fanning out of the icon, a quarter circle up and to the left of it.
        (300.0, 300.0)
    } else if expanded && missed {
        // The updates that came while the laptop was closed, as a list.
        (320.0, 400.0)
    } else if expanded {
        (248.0, 336.0) // room for the Missed, Crew and Quit rows
    } else {
        (60.0, 60.0)
    };
    if !expanded {
        // Shrinking from the pill once left the page inside at the pill's size, drawing the
        // icon outside its window: a second, real resize makes the page follow.
        window
            .set_size(tauri::LogicalSize::new(width + 1.0, height + 1.0))
            .map_err(|e| e.to_string())?;
    }
    window
        .set_size(tauri::LogicalSize::new(width, height))
        .map_err(|e| e.to_string())?;
    let target = if expanded {
        tauri::PhysicalPosition::new(
            position.x + size.width as i32 - (width * scale) as i32,
            position.y + size.height as i32 - (height * scale) as i32,
        )
    } else {
        anchor.take().unwrap_or(position)
    };
    window.set_position(target).map_err(|e| e.to_string())?;
    keep_status_visible(&window).map_err(|e| e.to_string())?;
    Ok(())
}

#[derive(Default)]
struct StatusWindowState {
    anchor: Mutex<Option<tauri::PhysicalPosition<i32>>>,
}

fn fit_status_position(
    position: tauri::PhysicalPosition<i32>,
    size: tauri::PhysicalSize<u32>,
    area: tauri::PhysicalRect<i32, u32>,
    margin: i32,
) -> tauri::PhysicalPosition<i32> {
    let left = area.position.x.saturating_add(margin);
    let top = area.position.y.saturating_add(margin);
    let right =
        (area.position.x as i64 + area.size.width as i64 - size.width as i64 - margin as i64)
            .max(left as i64)
            .min(i32::MAX as i64) as i32;
    let bottom =
        (area.position.y as i64 + area.size.height as i64 - size.height as i64 - margin as i64)
            .max(top as i64)
            .min(i32::MAX as i64) as i32;
    tauri::PhysicalPosition::new(position.x.clamp(left, right), position.y.clamp(top, bottom))
}

fn keep_status_visible(window: &tauri::WebviewWindow) -> tauri::Result<()> {
    if let Some(monitor) = window.current_monitor()?.or(window.primary_monitor()?) {
        let position = window.outer_position()?;
        let bounded = fit_status_position(
            position,
            window.outer_size()?,
            *monitor.work_area(),
            (8.0 * monitor.scale_factor()).round() as i32,
        );
        if bounded != position {
            window.set_position(bounded)?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod status_position_tests {
    use super::fit_status_position;
    use tauri::{PhysicalPosition, PhysicalRect, PhysicalSize};

    #[test]
    fn panel_fits_at_every_screen_edge_and_above_the_taskbar() {
        let area = PhysicalRect {
            position: PhysicalPosition::new(0, 0),
            size: PhysicalSize::new(1104, 680),
        };
        let size = PhysicalSize::new(310, 278); // 248 x 222 logical at 125% DPI.
        for x in [-4000, 0, 1094, 4000] {
            for y in [-4000, 0, 670, 4000] {
                let point = fit_status_position(PhysicalPosition::new(x, y), size, area, 10);
                assert!(point.x >= 10 && point.y >= 10);
                assert!(point.x + size.width as i32 <= 1094);
                assert!(point.y + size.height as i32 <= 670);
            }
        }
    }

    #[test]
    fn negative_monitor_coordinates_and_safe_dragged_positions_are_preserved() {
        let area = PhysicalRect {
            position: PhysicalPosition::new(-1920, 40),
            size: PhysicalSize::new(1920, 1040),
        };
        let size = PhysicalSize::new(372, 333); // 150% DPI.
        let inside = PhysicalPosition::new(-900, 200);
        assert_eq!(fit_status_position(inside, size, area, 12), inside);
        let outside = fit_status_position(PhysicalPosition::new(80, 1400), size, area, 12);
        assert!(outside.x + size.width as i32 <= -12);
        assert!(outside.y + size.height as i32 <= 1068);
    }
}

fn create_status_window(app: &tauri::AppHandle) -> tauri::Result<()> {
    let window = WebviewWindowBuilder::new(
        app,
        "status",
        WebviewUrl::App("index.html?overlay=status".into()),
    )
    .title("Mavo Status")
    .inner_size(60.0, 60.0)
    .resizable(false)
    .decorations(false)
    .transparent(true)
    .always_on_top(true)
    .skip_taskbar(true)
    .shadow(false)
    .visible(false)
    .build()?;
    // Windows can impose its initial minimum window width during creation.
    // Resize after creation, then dock using the actual physical window size.
    window.set_size(tauri::LogicalSize::new(60.0, 60.0))?;
    if let Some(monitor) = window.current_monitor()?.or(window.primary_monitor()?) {
        let scale = monitor.scale_factor();
        let position = monitor.work_area().position;
        let size = monitor.work_area().size;
        let actual = window.outer_size()?;
        let gap = (16.0 * scale).round() as i32;
        window.set_position(tauri::PhysicalPosition::new(
            position.x + size.width as i32 - actual.width as i32 - gap,
            position.y + size.height as i32 - actual.height as i32 - gap,
        ))?;
    }
    keep_status_visible(&window)?;
    window.show()?;
    Ok(())
}

fn tray_icon() -> tauri::image::Image<'static> {
    let mut rgba = vec![0u8; 32 * 32 * 4];
    let letter = [0b10001u8, 0b11011, 0b10101, 0b10001, 0b10001];
    for y in 0usize..32 {
        for x in 0usize..32 {
            let pixel = (y * 32 + x) * 4;
            let inside = (x as i32 - 16).pow(2) + (y as i32 - 16).pow(2) < 225;
            if inside {
                rgba[pixel..pixel + 4].copy_from_slice(&[30, 30, 32, 255]);
            }
            if (8..23).contains(&x)
                && (8..23).contains(&y)
                && letter[(y - 8) / 3] & (1 << (4 - (x - 8) / 3)) != 0
            {
                rgba[pixel..pixel + 4].copy_from_slice(&[245, 245, 247, 255]);
            }
        }
    }
    tauri::image::Image::new_owned(rgba, 32, 32)
}

fn create_tray(app: &tauri::AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open Mavo", true, None::<&str>)?;
    let desktop = MenuItem::with_id(app, "desktop", "Desktop button", true, None::<&str>)?;
    let pause = MenuItem::with_id(app, "pause", "Pause automations", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Mavo", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &desktop, &pause, &quit])?;
    TrayIconBuilder::with_id("mavo-status")
        .icon(tray_icon())
        .tooltip("Mavo is running")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => {
                let _ = show_main(app.clone(), false);
            }
            "desktop" => {
                if let Some(w) = app.get_webview_window("status") {
                    if w.is_visible().unwrap_or(false) {
                        let _ = w.hide();
                    } else {
                        let _ = w.show();
                    }
                }
            }
            "pause" => {
                let _ = app.emit("mavo-pause-all", ());
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if matches!(
                event,
                TrayIconEvent::Click {
                    button: MouseButton::Left,
                    button_state: MouseButtonState::Up,
                    ..
                }
            ) {
                let _ = show_main(tray.app_handle().clone(), false);
            }
        })
        .build(app)?;
    Ok(())
}

fn close_tools(state: &BackendState) {
    if let Ok(mut stream) = std::net::TcpStream::connect_timeout(
        &std::net::SocketAddr::from(([127, 0, 0, 1], state.port)),
        Duration::from_secs(1),
    ) {
        let _ = stream.set_read_timeout(Some(Duration::from_secs(4)));
        let _ = stream.set_write_timeout(Some(Duration::from_secs(1)));
        let request = format!("POST /api/v1/runtime/close-tools HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", state.token);
        if stream.write_all(request.as_bytes()).is_ok() {
            let mut buffer = [0u8; 1024];
            let _ = stream.read(&mut buffer);
        }
    }
}

fn available_port() -> Result<u16, String> {
    TcpListener::bind(("127.0.0.1", 0))
        .and_then(|listener| listener.local_addr())
        .map(|address| address.port())
        .map_err(|error| format!("Could not allocate a local port: {error}"))
}

fn random_token() -> String {
    let mut bytes = [0_u8; 32];
    OsRng.fill_bytes(&mut bytes);
    hex::encode(bytes)
}

fn development_backend() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("backend")
}

fn start_backend(app: &tauri::AppHandle) -> Result<BackendState, String> {
    let port = available_port()?;
    let token = random_token();
    let app_data = app
        .path()
        .app_local_data_dir()
        .map_err(|error| format!("Could not resolve app data: {error}"))?;
    fs::create_dir_all(app_data.join("logs"))
        .map_err(|error| format!("Could not create app data: {error}"))?;

    let configured = std::env::var_os("MAVO_BACKEND_EXECUTABLE").map(PathBuf::from);
    let packaged = if cfg!(debug_assertions) {
        None
    } else {
        app.path()
            .resource_dir()
            .ok()
            .map(|path| path.join("mavo-backend").join("mavo-backend.exe"))
            .filter(|path| path.exists())
    };
    let backend_root = development_backend();
    let executable = configured.or(packaged).unwrap_or_else(|| {
        backend_root
            .join(".venv")
            .join("Scripts")
            .join("python.exe")
    });

    if !executable.exists() {
        return Err(format!(
            "Backend executable was not found at {}. Run scripts/setup.ps1 first.",
            executable.display()
        ));
    }

    let log_path = app_data.join("logs").join("backend.log");
    let stdout = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|error| format!("Could not open backend log: {error}"))?;
    let stderr = stdout
        .try_clone()
        .map_err(|error| format!("Could not initialize backend logging: {error}"))?;

    let is_python = executable
        .file_stem()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name.eq_ignore_ascii_case("python"));
    let mut command = Command::new(&executable);
    if is_python {
        command.args(["-m", "mavo.main"]);
        command.current_dir(&backend_root);
    }
    command
        .args(["--host", "127.0.0.1", "--port", &port.to_string()])
        .env("MAVO_API_TOKEN", &token)
        .env("MAVO_DATA_DIR", &app_data)
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));
    #[cfg(target_os = "windows")]
    command.creation_flags(CREATE_NO_WINDOW);
    let mut child = command
        .spawn()
        .map_err(|error| format!("Could not start the Mavo backend: {error}"))?;
    #[cfg(target_os = "windows")]
    let job = match BackendJob::attach(&child) {
        Ok(job) => job,
        Err(error) => {
            let _ = Command::new("taskkill.exe")
                .args(["/PID", &child.id().to_string(), "/T", "/F"])
                .creation_flags(CREATE_NO_WINDOW)
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
    };

    Ok(BackendState {
        child: Mutex::new(Some(child)),
        port,
        token,
        #[cfg(target_os = "windows")]
        _job: job,
    })
}

pub fn run() {
    let application = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            backend_config,
            open_google_url,
            open_github_url,
            show_reminder_overlay,
            pending_reminder,
            close_reminder_overlay,
            show_main,
            status_expanded,
            quit_mavo
        ])
        .setup(|app| {
            let state = start_backend(app.handle()).map_err(std::io::Error::other)?;
            app.manage(state);
            app.manage(ReminderOverlayState {
                pending: Mutex::new(None),
            });
            app.manage(StatusWindowState::default());
            create_tray(app.handle())?;
            create_status_window(app.handle())?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() == "status"
                && matches!(
                    event,
                    WindowEvent::Moved(_)
                        | WindowEvent::Resized(_)
                        | WindowEvent::ScaleFactorChanged { .. }
                )
            {
                if let Some(status) = window.app_handle().get_webview_window("status") {
                    if let Err(error) = keep_status_visible(&status) {
                        eprintln!("Could not keep Mavo status on screen: {error}");
                    }
                }
            }
            if window.label() == "main" {
                if let WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = window.hide();
                    if let Some(status) = window.app_handle().get_webview_window("status") {
                        let _ = status.show();
                    }
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build Mavo");

    application.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit) {
            if let Some(state) = app_handle.try_state::<BackendState>() {
                close_tools(&state);
                if let Ok(mut child) = state.child.lock() {
                    if let Some(process) = child.as_mut() {
                        let _ = process.kill();
                        let _ = process.wait();
                    }
                    *child = None;
                }
            }
        }
    });
}
