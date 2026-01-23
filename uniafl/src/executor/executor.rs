use fuzzdb::Language;
use libafl::{executors::ExitKind, Error};
use libafl_bolts::{current_nanos, rands::StdRand};
use serde::{Deserialize, Serialize};
use std::ffi::OsStr;
use std::fs;
use std::fs::File;
use std::io::{Read, Write};
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use tokio::runtime::Handle;
use tokio::time::{timeout, Duration};
use walkdir::WalkDir;

use super::exec_runner::ExecRunner;
use crate::{
    common::utils,
    msa::{
        manager::{CovAddr, ExecMode, MsaInput, MsaManager},
        state::UniState,
    },
};

#[derive(Serialize, Deserialize)]
pub struct ExecutorConf {
    pub harness_name: String,
    pub harness_path: String,
    pub redis_url: String,
    pub language: String,
}

pub struct ExecStats {
    pub num_crashed_inputs: usize,
    pub num_normal_inputs: usize,
}

impl ExecStats {
    pub fn new() -> Self {
        Self {
            num_crashed_inputs: 0,
            num_normal_inputs: 0,
        }
    }

    pub fn total(&self) -> usize {
        self.num_crashed_inputs + self.num_normal_inputs
    }
}

pub struct Executor {
    pub worker_idx: i32,
    tmp_fname: String,
    runner: ExecRunner,
    executor_dir: String,
    pub msa_mgr: MsaManager,
    symbolizer: Option<Child>,
    pub stats: ExecStats,
    pub rand: StdRand,
    pub coverage_harness_path: String,
    pub coverage_binary_ready: bool,
}

impl Executor {
    pub fn new(
        config_path: &PathBuf,
        msa_mgr: &MsaManager,
        executor_dir: &String,
        worker_idx: i32,
    ) -> Self {
        Self::new_with(config_path, msa_mgr, executor_dir, worker_idx, false)
    }

    pub fn new_with(
        config_path: &PathBuf,
        msa_mgr: &MsaManager,
        executor_dir: &String,
        worker_idx: i32,
        allow_print: bool,
    ) -> Self {
        let conf = utils::load_json::<ExecutorConf>(config_path)
            .unwrap_or_else(|e| panic!("Error in load_json: {}", e));
        let harness_filename = Path::new(&conf.harness_path)
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap();
        let coverage_harness_path = format!("/coverage-out/{}", harness_filename);
        let coverage_binary_ready = Path::new(&coverage_harness_path).exists();
        let tmp_dir = msa_mgr.workdir.join("executor_tmp");
        std::fs::create_dir(&tmp_dir).ok();
        Self {
            worker_idx,
            tmp_fname: tmp_dir
                .join(format!("tmp_{}_{}", conf.harness_name, worker_idx))
                .display()
                .to_string(),
            runner: ExecRunner::new_with(msa_mgr, &conf, executor_dir, worker_idx, allow_print),
            msa_mgr: msa_mgr.clone(),
            executor_dir: executor_dir.clone(),
            symbolizer: None,
            stats: ExecStats::new(),
            rand: StdRand::with_seed(current_nanos()),
            coverage_harness_path,
            coverage_binary_ready,
        }
    }

    #[cfg(feature = "log")]
    pub fn log(&self, msg: String) {
        self.msa_mgr.log("Executor", self.worker_idx, msg);
    }

    fn run(&mut self) {
        self.runner.let_go();
    }

    fn check_exit_kind(&self, exit_kind: ExitKind) -> Option<ExitKind> {
        match exit_kind {
            ExitKind::Timeout if !self.msa_mgr.allow_timeout_bug => None,
            _ => Some(exit_kind),
        }
    }

    pub fn execute_one_file(
        &mut self,
        path: &PathBuf,
    ) -> (Vec<u8>, Vec<u8>, Option<Vec<u8>>, Option<Vec<u8>>) {
        self.msa_mgr
            .set_mode(self.worker_idx, ExecMode::ExecuteInput, false);
        self.msa_mgr.load_file_input(self.worker_idx, path).ok();
        let (stdout, stderr) = self.runner.get_outputs();
        let idx = 0;
        let exit_kind = self.msa_mgr.get_result(idx);
        let (cov, crash_log) = match self.check_exit_kind(exit_kind) {
            Some(ExitKind::Ok) => {
                let core = self.msa_mgr.cores.ids[self.worker_idx as usize].0;
                let tmp = format!("tmp_{}", core);
                let fpath = PathBuf::from(format!("{}/{}", self.msa_mgr.corpus_dir, tmp));
                std::fs::copy(path, &fpath).ok();
                self.save_coverage(&fpath, self.msa_mgr.get_input(idx).get_cov());
                (self.read_coverage(&tmp).ok(), None)
            }
            Some(ExitKind::Crash | ExitKind::Timeout | ExitKind::Oom) => {
                let msa_input = self.msa_mgr.get_input(idx);
                self.finalize_crash_log_in_fuzzer(&msa_input, exit_kind == ExitKind::Timeout);
                let core = self.msa_mgr.cores.ids[self.worker_idx as usize].0;
                let tmp = format!("tmp_{}", core);
                let fpath = PathBuf::from(format!("{}/{}", self.msa_mgr.corpus_dir, tmp));
                std::fs::copy(path, &fpath).ok();
                self.save_coverage(&fpath, msa_input.get_cov());
                (
                    self.read_coverage(&tmp).ok(),
                    msa_input.get_crash_log().map(|x| x.to_vec()),
                )
            }
            _ => (None, None),
        };
        (stdout, stderr, cov, crash_log)
    }

    pub fn process_results(
        &mut self,
        stage_name: &str,
        state: &Arc<UniState>,
        is_testlang_stage: bool,
    ) -> Result<(), Error> {
        let worker_idx = self.worker_idx;
        let start_idx = self.msa_mgr.get_start_input_idx(worker_idx);
        let execute_input_idx = self.msa_mgr.get_execute_input_idx(worker_idx);
        let mut ok_inputs = Vec::new();
        let mut crash_inputs = Vec::new();
        for idx in start_idx..execute_input_idx {
            let exit_kind = self.msa_mgr.get_result(idx);
            match self.check_exit_kind(exit_kind) {
                Some(ExitKind::Ok) => {
                    ok_inputs.push(self.msa_mgr.get_input(idx));
                }
                Some(ExitKind::Crash | ExitKind::Timeout | ExitKind::Oom) => {
                    let msa_input = self.msa_mgr.get_input(idx);
                    self.finalize_crash_log_in_fuzzer(&msa_input, exit_kind == ExitKind::Timeout);
                    crash_inputs.push(msa_input);
                }
                _ => (),
            }
        }
        self.stats.num_normal_inputs += ok_inputs.len();
        self.stats.num_crashed_inputs += crash_inputs.len();
        state.add_if_interesting_seed(stage_name, self, ok_inputs, is_testlang_stage);
        state.add_if_interesting_crash(stage_name, self, crash_inputs);
        Ok(())
    }

    pub fn execute_loaded_inputs(
        &mut self,
        stage_name: &str,
        state: &Arc<UniState>,
        is_testlang_stage: bool,
    ) -> Result<(), Error> {
        self.run();
        self.process_results(stage_name, state, is_testlang_stage)
    }

    pub fn save_coverage(&mut self, fpath: &PathBuf, cov: &[CovAddr]) {
        let fname = fpath.file_name().unwrap().to_str().unwrap().to_string();
        if let Some(cov_path) = self.msa_mgr.save_coverage(&fname, cov) {
            self.symbolize_coverage(&fpath.display().to_string(), cov, &cov_path);
        }
    }

    pub fn save_crash_log(&self, fpath: &PathBuf, crash_log: &[u8]) {
        if let Some(fname) = fpath.file_name().and_then(|n| n.to_str()) {
            let crash_log_fname = format!(".{}.crash_log", fname);
            let mut crash_log_fpath = fpath.clone();
            crash_log_fpath.set_file_name(crash_log_fname);

            if let Ok(mut file) = File::create(&crash_log_fpath) {
                let _ = file.write_all(crash_log);
            }
        }
    }

    pub fn save_call_stack(&self, fpath: &PathBuf, crash_log: &[u8]) {
        if let Some(fname) = fpath.file_name().and_then(|n| n.to_str()) {
            let callstack_fname = format!(".{}.callstack", fname);
            let mut callstack_fpath = fpath.clone();
            callstack_fpath.set_file_name(callstack_fname);

            let callstack = self.extract_call_stack(crash_log);
            if let Ok(mut file) = File::create(&callstack_fpath) {
                let _ = file.write_all(callstack.as_bytes());
            }
        }
    }

    fn extract_call_stack(&self, crash_log: &[u8]) -> String {
        let log_str = match std::str::from_utf8(crash_log) {
            Ok(s) => s,
            Err(_) => return String::new(),
        };

        let mut result = Vec::new();
        let mut collecting = false;

        let mut in_main_thread = false;
        let mut in_java_exception = false;
        let mut in_native_stack = false;

        for line in log_str.lines() {
            let trimmed = line.trim();

            if trimmed.starts_with('#') {
                if !collecting {
                    collecting = true;
                    in_native_stack = true;
                }
                if in_native_stack {
                    result.push(trimmed.to_string());
                }
                continue;
            }

            if in_native_stack && !trimmed.starts_with('#') {
                break;
            }

            if trimmed.contains("== Java Exception:") {
                in_java_exception = true;
                continue;
            }

            if in_java_exception {
                if trimmed.starts_with("at ") {
                    collecting = true;
                    result.push(trimmed.to_string());
                    continue;
                } else if collecting {
                    break;
                }
            }

            if trimmed.starts_with("Thread[main") {
                in_main_thread = true;
                collecting = true;
                continue;
            }

            if in_main_thread {
                if trimmed.starts_with("at ") || trimmed.starts_with("at\t") {
                    result.push(trimmed.to_string());
                } else if !trimmed.starts_with("at") && !trimmed.is_empty() {
                    break;
                }
            }
        }

        result.join("\n")
    }

    fn read_coverage(&self, fname: &str) -> Result<Vec<u8>, Error> {
        let json_path = self.msa_mgr.get_cov_path(fname);
        let mut f = std::fs::File::open(json_path)?;
        let mut data = Vec::new();
        f.read_to_end(&mut data)?;
        Ok(data)
    }

    fn symbolize_coverage(&mut self, fpath: &String, _cov: &[CovAddr], cov_path: &PathBuf) {
        match self.msa_mgr.language {
            Language::C | Language::Cpp | Language::Rust | Language::Go => {
                self.run_symbolizer(fpath, cov_path)
            }
            Language::Jvm => self.run_symbolizer(fpath, cov_path),
            _ => todo!(),
        };
    }

    fn run_symbolizer(&mut self, fpath: &String, cov_path: &PathBuf) {
        #[cfg(feature = "log")]
        self.log(format!(
            "run_symbolizer: fpath {:?} cov_path {:?}",
            fpath, cov_path
        ));
        self.ensure_running_symbolizer();
        let child = self.symbolizer.as_mut().unwrap();
        let stdin = child.stdin.as_mut().expect("Fail to get symbolizer stdin");
        let stdout = child
            .stdout
            .as_mut()
            .expect("Fail to get symbolizer stdout");
        stdin.write_all(fpath.as_bytes()).ok();
        stdin.write_all(b"\n").ok();
        stdin.write_all(cov_path.to_str().unwrap().as_bytes()).ok();
        stdin.write_all(b"\n").ok();
        stdin.flush().ok();

        let rt = tokio::runtime::Runtime::new().unwrap();
        let fut = async move {
            let mut buf = vec![0; 5]; // "DONE\n"
            match timeout(Duration::from_secs(600), async {
                stdout.read_exact(&mut buf)
            })
            .await
            {
                Ok(result) => match result {
                    Ok(_) => Ok(()),
                    Err(_) => Err("read failed"),
                },
                Err(_) => Err("timeout"),
            }
        };
        let result = rt.block_on(fut);

        if let Err(reason) = result {
            #[cfg(feature = "log")]
            self.log(format!("Symbolizer failed: {}", reason));
            self.write_empty_cov(cov_path);
        }
        #[cfg(feature = "log")]
        self.log(format!("run_symbolizer"));
    }

    fn write_empty_cov(&self, cov_path: &PathBuf) {
        let mut fallback = cov_path.clone();
        fallback.set_extension("cov");
        let _ = fs::write(&fallback, b"{}");
    }

    fn turn_on_symbolizer(&mut self) -> Child {
        let command = if self.coverage_binary_ready {
            "harness_coverage_runner.py"
        } else {
            "symbolizer.py"
        };
        #[cfg(feature = "log")]
        self.log(format!("turn_on_symbolizer: command {:?}", command));
        let work_dir = self
            .msa_mgr
            .workdir
            .join("coverage-workdir")
            .join(format!("worker_{}", self.worker_idx));
        #[cfg(feature = "log")]
        let log_dir = self.msa_mgr.workdir.join("coverage-log");
        let args = if self.coverage_binary_ready {
            vec![
                OsStr::new("--config"),
                self.msa_mgr.config_path.as_os_str(),
                OsStr::new("--coverage_harness"),
                self.coverage_harness_path.as_ref(),
                OsStr::new("--work_dir"),
                work_dir.as_ref(),
                OsStr::new("--out_dir"),
                OsStr::new("/coverage-out"),
                #[cfg(feature = "log")]
                OsStr::new("--log_dir"),
                #[cfg(feature = "log")]
                log_dir.as_ref(),
            ]
        } else {
            vec![self.msa_mgr.config_path.as_os_str()]
        };
        #[cfg(feature = "log")]
        self.log(format!("turn_on_symbolizer: args {:?}", args));
        unsafe {
            Command::new(command)
                .args(args)
                .pre_exec(|| {
                    libc::setsid();
                    Ok(())
                })
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .stdin(Stdio::piped())
                .spawn()
                .expect(&format!("Fail to run {:?}", command).to_string())
        }
    }

    fn is_symbolizer_off(&mut self) -> bool {
        if let Some(child) = self.symbolizer.as_mut() {
            match child.try_wait() {
                Ok(None) => false,
                _ => {
                    utils::force_kill(child);
                    true
                }
            }
        } else {
            true
        }
    }

    fn ensure_running_symbolizer(&mut self) {
        if self.is_symbolizer_off() {
            let child = self.turn_on_symbolizer();
            self.symbolizer = Some(child);
        }
    }

    fn extract_path_from_line(line: &str) -> Option<String> {
        let tokens: Vec<&str> = line.split_whitespace().collect();
        if tokens.len() == 5 {
            let last = tokens.last().unwrap();
            if last.starts_with('/') {
                return Some(last.split(':').next()?.to_string());
            }
        }
        None
    }

    fn filter_valid_paths_from_bytes(input: &[u8]) -> Option<Vec<u8>> {
        let s = std::str::from_utf8(input).ok()?;
        let mut output_lines = Vec::new();
        let mut expected_index = 0;

        for line in s.lines() {
            let mut tokens = line.split_whitespace();
            if let Some(first_token) = tokens.next() {
                if first_token.starts_with('#') && first_token[1..].chars().all(|c| c.is_digit(10))
                {
                    if let Some(path_str) = Self::extract_path_from_line(line) {
                        if !path_str.starts_with("/src/llvm-project") {
                            let new_line =
                                line.replacen(first_token, &format!("#{}", expected_index), 1);
                            output_lines.push(new_line);
                            expected_index += 1;
                        }
                    }
                }
            }
        }

        if output_lines.is_empty() {
            None
        } else {
            Some(output_lines.join("\n").into_bytes())
        }
    }

    fn filter_address_in_jazzer_log(log: Vec<u8>) -> Vec<u8> {
        let mut ret = Vec::new();
        for line in log.split(|&b| b == b'\n') {
            if utils::find_subarr(line, b"0x").is_none() {
                ret.extend_from_slice(line);
                ret.push(b'\n');
            }
        }
        ret
    }

    pub fn parse_libfuzzer_crash_log(log: &[u8], parse_err_head: bool) -> Option<Vec<u8>> {
        if let Some(dedup_tokens) = Self::parse_dedup_tokens(log) {
            return Some(dedup_tokens);
        }
        let cur = log;
        let from = if parse_err_head {
            0
        } else {
            utils::find_subarr(cur, "==ERROR: ".as_bytes())?
        };
        let cur = &cur[from..];
        let from = utils::find_subarr(cur, "    #0 ".as_bytes())?;
        let cur = &cur[from..];
        let last = utils::find_subarr(cur, " in LLVMFuzzerTestOneInput".as_bytes())?;
        let last = last + utils::find_subarr(&cur[last..], "\n".as_bytes())?;
        Self::filter_valid_paths_from_bytes(&cur[..last])
    }

    fn parse_dedup_tokens(log: &[u8]) -> Option<Vec<u8>> {
        let mut dedup_tokens: Vec<Vec<u8>> = Vec::new();
        let mut cur = log;
        let key = b"DEDUP_TOKEN: ";
        while let Some(from) = utils::find_subarr(cur, key) {
            cur = &cur[from + key.len()..];
            if let Some(to) = utils::find_subarr(cur, b"\n") {
                dedup_tokens.push(cur[..to].to_vec());
                cur = &cur[to..];
            }
        }
        if dedup_tokens.is_empty() {
            None
        } else {
            dedup_tokens.sort();
            Some(dedup_tokens.join(&b"\n"[..]))
        }
    }

    fn parse_jazzer_crash_log(log: &[u8]) -> Option<Vec<u8>> {
        let cur = log;
        let key = "== Java Exception: ".as_bytes();
        let from = utils::find_subarr(cur, key)?;
        let cur = &cur[from + key.len()..];
        let from = utils::find_subarr(cur, b"\tat")?;
        let cur = &cur[from..];
        let cur = if let Some(last) = utils::find_subarr(cur, b"Caused by:") {
            &cur[..last]
        } else {
            cur
        };
        if let Some(last) = utils::find_subarr(cur, b"== libFuzzer crashing input ==") {
            Some(Self::filter_address_in_jazzer_log(cur[..last].to_vec()))
        } else {
            Some(Self::filter_address_in_jazzer_log(cur.to_vec()))
        }
    }

    fn parse_jazzer_timeout_log<'a>(log: &'a [u8]) -> Option<Vec<u8>> {
        let cur = log;
        let key = "Thread[main".as_bytes();
        let from = utils::find_subarr(cur, key)?;
        let cur = &cur[from + key.len()..];
        let from = utils::find_subarr(cur, b"\n")?;
        let cur = &cur[from + 1..];
        let last = utils::find_subarr(cur, "\n\n".as_bytes())?;
        Some(Self::filter_address_in_jazzer_log(cur[..last].to_vec()))
    }

    pub fn run_pov(&self, pov: &[u8]) -> Option<(Vec<u8>, Vec<u8>)> {
        {
            let mut tmp_file = std::fs::File::create(&self.tmp_fname).ok()?;
            tmp_file.write_all(pov).ok()?;
            tmp_file.flush().ok()?;
        }
        let output = unsafe {
            let mut cmd = Command::new("reproduce");
            match self.msa_mgr.language {
                Language::Jvm => cmd.args([&self.msa_mgr.harness_name, "-timeout=150"]),
                _ => cmd.args([&self.msa_mgr.harness_name]),
            };
            cmd.pre_exec(|| {
                libc::setsid();
                // libc::setuid(1000);
                // libc::setgid(1000);
                Ok(())
            })
            .env("TESTCASE", &self.tmp_fname)
            .env("OUT", &self.executor_dir)
            .output()
            .ok()?
        };
        #[cfg(test)]
        {
            println!("Status: {:?}", output.status);
            println!("Stderr: {}", String::from_utf8_lossy(&output.stderr));
        }
        if output.status.success() {
            return None;
        }

        let log = &output.stderr;
        let parsed = Self::parse_libfuzzer_crash_log(log, true)
            .or_else(|| Self::parse_jazzer_crash_log(log))
            .or_else(|| Self::parse_jazzer_timeout_log(log))
            .map(|ret| ret.to_vec())?;

        Some((parsed, log.to_vec()))
    }

    fn finalize_crash_log_in_fuzzer(&self, msa_input: &MsaInput, is_timeout: bool) {
        match self.msa_mgr.language {
            Language::C | Language::Cpp | Language::Rust | Language::Go => {
                if let Some(log) = msa_input.get_crash_log() {
                    let log =
                        Self::parse_libfuzzer_crash_log(log, is_timeout).unwrap_or(Vec::new());
                    msa_input.set_crash_log(&log);
                }
            }
            Language::Jvm => {
                if let Some(log) = msa_input.get_crash_log() {
                    msa_input.set_crash_log(&Self::filter_address_in_jazzer_log(log.to_vec()));
                }
            }
            _ => (),
        };
    }
}
