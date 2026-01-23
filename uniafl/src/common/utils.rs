#![allow(unused)]

use nix::sys::signal::{killpg, Signal};
use nix::unistd::Pid;
use std::{
    fs::File,
    io::{BufReader, Error, ErrorKind},
    path::Path,
    process::Child,
};

#[allow(dead_code)]
pub fn load_json<T: serde::de::DeserializeOwned>(path: impl AsRef<Path>) -> serde_json::Result<T> {
    let file = File::open(&path)
        .unwrap_or_else(|e| panic!("{} has an error: {}", path.as_ref().display(), e));
    let reader = BufReader::new(file);
    serde_json::from_reader(reader)
}

#[allow(dead_code)]
pub fn load_yaml<T: serde::de::DeserializeOwned>(path: impl AsRef<Path>) -> serde_yaml::Result<T> {
    let file = File::open(&path)
        .unwrap_or_else(|e| panic!("{} has an error: {}", path.as_ref().display(), e));
    let reader = BufReader::new(file);
    serde_yaml::from_reader(reader)
}

#[allow(dead_code)]
pub fn is_empty_dir(dir: impl AsRef<Path>) -> std::io::Result<bool> {
    let mut entries = std::fs::read_dir(dir)?;
    Ok(entries.next().is_none())
}

pub fn new_err(s: &str) -> Error {
    Error::new(ErrorKind::Other, s)
}

pub fn force_kill(proc: &mut Child) {
    let pid = proc.id();
    while proc.try_wait().expect("Fail to try wait").is_none() {
        killpg(Pid::from_raw(pid as i32), Signal::SIGTERM).ok();
        proc.kill().ok();
    }
}

async fn async_wait(proc: &mut async_process::Child) {
    proc.status().await;
}

async fn async_force_kill(proc: &mut async_process::Child) {
    let pid = proc.id();
    while proc.try_status().expect("Fail to try wait").is_none() {
        async_process::Command::new("pkill")
            .arg("-P")
            .arg(pid.to_string())
            .output()
            .await
            .ok();
        proc.kill().ok();
    }
}

async fn async_wait_until(proc: &mut async_process::Child, timeout: u64) {
    let duration = tokio::time::Duration::from_secs(timeout);
    match tokio::time::timeout(duration, async_wait(proc)).await {
        Ok(_) => (),
        Err(_) => {
            async_force_kill(proc);
        }
    }
}

pub fn wait_until(proc: &mut async_process::Child, timeout: u64) {
    let rt = tokio::runtime::Runtime::new().expect("Failed to create runtime");
    rt.block_on(async_wait_until(proc, timeout));
}

pub fn find_subarr<T: std::cmp::PartialEq>(arr: &[T], subarr: &[T]) -> Option<usize> {
    arr.windows(subarr.len())
        .position(|window| window == subarr)
}

pub fn sync_wait_coroutine<T>(coroutine: impl std::future::Future<Output = T>) -> T {
    let rt = tokio::runtime::Runtime::new().expect("Failed to create runtime");
    rt.block_on(coroutine)
}
