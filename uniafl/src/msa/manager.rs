#![allow(dead_code)]
use fuzzdb::Language;
use libafl::{corpus::CorpusId, executors::ExitKind};
use libafl_bolts::core_affinity::Cores;
use num_enum::TryFromPrimitive;
use serde::{Deserialize, Serialize};
use std::ffi::{c_void, CString};
use std::fs::File;
use std::io::{Read, Write};
use std::os::raw::c_char;
use std::path::PathBuf;

#[cfg(feature = "log")]
use {fs2::FileExt, std::fs::OpenOptions};

use super::state::UniInput;
use crate::common::utils;

pub type CovAddr = u64;

#[derive(Debug)]
pub enum ExecResult {
    NewCov(Vec<CovAddr>),
    Crash(Vec<u8>),
    Other(ExitKind),
}

#[derive(Serialize, Deserialize)]
pub struct MsaConfigJson {
    core_ids: Vec<usize>,
    corpus_dir: String,
    cov_dir: String,
    workdir: String,
    language: String,
    harness_name: String,
    ms_per_exec: u32,
    max_len: usize,
    allow_timeout_bug: bool,
}

#[repr(C)]
#[derive(Debug)]
struct InputMetadata {
    input_size: i32,
    result: i32,
    cov_size: i32,
    crash_size: i32,
    id: i64,
    new_normal_feature: i64,
    fname: [c_char; 16],
}

#[allow(improper_ctypes)]
extern "C" {
    fn init_mgr(name: *const i8, create: bool) -> *const c_void;

    fn alloc_input(mgr: *const c_void, worker_idx: i32) -> i32;
    fn get_seed_idx(mgr: *const c_void, worker_idx: i32) -> i32;

    fn set_input_metadata(mgr: *const c_void, idx: i32, size: u32, id: usize);
    fn get_input_metadata(mgr: *const c_void, idx: i32) -> *mut InputMetadata;
    fn get_input_buffer(mgr: *const c_void, idx: i32) -> *mut u8;
    fn get_input_size(mgr: *const c_void, idx: i32) -> u32;
    fn get_id(mgr: *const c_void, idx: i32) -> usize;
    fn get_result(mgr: *const c_void, idx: i32) -> i32;
    fn get_cov_buffer(mgr: *const c_void, idx: i32) -> *mut CovAddr;
    fn get_cov_size(mgr: *const c_void, idx: i32) -> u32;
    fn get_crash_log(mgr: *const c_void, idx: i32) -> *mut u8;
    fn get_crash_size(mgr: *const c_void, idx: i32) -> u32;
    fn get_fname(mgr: *const c_void, idx: i32) -> *mut u8;

    fn set_mode(mgr: *const c_void, worker_idx: i32, mode: i32, testlang_feature: bool);
    fn set_iter_cnt(mgr: *const c_void, worker_idx: i32, iter_cnt: i32);

    fn get_start_input_idx(mgr: *const c_void, worker_idx: i32) -> i32;
    fn get_alloc_input_idx(mgr: *const c_void, worker_idx: i32) -> i32;
    fn get_execute_input_idx(mgr: *const c_void, worker_idx: i32) -> i32;
}
const CRASH_LOG_SIZE: usize = 16 * 1024;

#[derive(Clone)]
pub struct MsaManager {
    mgr_ptr: *const c_void,
    pub harness_name: String,
    pub input_per_worker: u32,
    pub cores: Cores,
    pub worker_cnt: u32,
    pub corpus_dir: String,
    cov_dir: PathBuf,
    pub workdir: PathBuf,
    pub language: Language,
    pub config_path: PathBuf,
    pub max_len: usize,
    pub allow_timeout_bug: bool,
}
unsafe impl Send for MsaManager {}

static mut INPUT_BUFFER_SIZE: usize = 0;
#[derive(Debug, Clone)]
pub struct MsaInput {
    mgr_ptr: *const c_void,
    idx: i32,
}

const FNAME_MAX_LEN: usize = 16;
#[derive(Clone)]
pub struct MsaSeed {
    pub bytes: Vec<u8>,
    pub fname: String,
    pub id: usize,
}
pub const NO_SEED_ID: usize = usize::MAX;

pub fn trim_seed_fname(fname: &str) -> &str {
    if fname.len() > FNAME_MAX_LEN {
        &fname[..FNAME_MAX_LEN]
    } else {
        fname
    }
}

#[derive(Debug, TryFromPrimitive)]
#[repr(i32)]
pub enum InputStatus {
    Free = 0,
    Allocated = 1,
    Ready = 2,
    Executing = 3,
    Executed = 4,
}

pub enum ExecMode {
    RunFuzzer = 0,
    RunFuzzerWithSeed = 1,
    ExecuteInput = 2,
}

impl MsaManager {
    fn calculate_input_per_worker(ms_per_exec: u32) -> u32 {
        const MAX: u32 = 256;

        if ms_per_exec == 0 {
            MAX
        } else {
            let n = (1_000 / ms_per_exec / 2).max(1);
            (n * 2).min(MAX)
        }
    }

    pub fn new(config_path: &PathBuf, create: bool) -> Self {
        let config: MsaConfigJson = utils::load_json::<MsaConfigJson>(config_path)
            .unwrap_or_else(|e| panic!("Error in load_json: {}", e));
        let input_per_worker = Self::calculate_input_per_worker(config.ms_per_exec);
        Self::new_with(config_path, create, input_per_worker)
    }

    pub fn new_with(config_path: &PathBuf, create: bool, input_per_worker: u32) -> Self {
        let config: MsaConfigJson = utils::load_json::<MsaConfigJson>(config_path)
            .unwrap_or_else(|e| panic!("Error in load_json: {}", e));
        let cores = Cores::from(config.core_ids);
        let worker_cnt = cores.ids.len() as u32;
        std::env::set_var("MAX_INPUT_SIZE", format!("{}", config.max_len));
        std::env::set_var("INPUT_PER_WORKER", format!("{}", input_per_worker));
        std::env::set_var("WORKER_CNT", format!("{}", worker_cnt));
        if config.allow_timeout_bug {
            std::env::set_var("ALLOW_TIMEOUT_BUG", "True");
        }
        let c_harness_name = CString::new(config.harness_name.clone()).unwrap();
        let mgr_ptr = unsafe { init_mgr(c_harness_name.as_ptr(), create) };
        unsafe {
            INPUT_BUFFER_SIZE = config.max_len;
        }
        let cov_dir_trimmed = config.cov_dir.trim();
        let corpus_dir_trimmed = config.corpus_dir.trim();
        let workdir_trimmed = config.workdir.trim();

        if cov_dir_trimmed == corpus_dir_trimmed
            || cov_dir_trimmed == workdir_trimmed
            || corpus_dir_trimmed == workdir_trimmed
        {
            panic!("cov_dir, corpus_dir, and workdir must all be different!");
        }
        Self {
            mgr_ptr,
            cores: cores.clone(),
            input_per_worker,
            worker_cnt,
            config_path: config_path.clone(),
            corpus_dir: config.corpus_dir,
            cov_dir: PathBuf::from(config.cov_dir),
            workdir: PathBuf::from(config.workdir),
            language: Language::from(config.language),
            harness_name: config.harness_name,
            max_len: config.max_len,
            allow_timeout_bug: config.allow_timeout_bug,
        }
    }

    pub fn set_affinity(&self, worker_idx: i32) {
        self.cores.ids[worker_idx as usize].set_affinity().ok();
    }

    pub fn set_mode(&self, worker_idx: i32, mode: ExecMode, testlang_feature: bool) {
        unsafe { set_mode(self.mgr_ptr, worker_idx, mode as i32, testlang_feature) }
    }

    pub fn set_iter_cnt(&self, worker_idx: i32, iter_cnt: u32) {
        unsafe { set_iter_cnt(self.mgr_ptr, worker_idx, iter_cnt as i32) }
    }

    pub fn clear_seed(&self, worker_idx: i32) {
        let idx = unsafe { get_seed_idx(self.mgr_ptr, worker_idx) };
        let mut msa_input = MsaInput::new(self.mgr_ptr, idx);
        msa_input.set_metadata(0, NO_SEED_ID);
    }

    pub fn set_seed(&self, worker_idx: i32, id: CorpusId, fname: &Option<String>, bytes: &[u8]) {
        let idx = unsafe { get_seed_idx(self.mgr_ptr, worker_idx) };
        let mut msa_input = MsaInput::new(self.mgr_ptr, idx);
        let buffer = msa_input.buffer_mut();
        buffer[..bytes.len()].copy_from_slice(bytes);
        msa_input.set_metadata(bytes.len(), id.0);
        if let Some(fname) = fname {
            msa_input.set_fname(fname);
        }
    }

    pub fn get_seed(&self, worker_idx: i32) -> MsaSeed {
        let idx = unsafe { get_seed_idx(self.mgr_ptr, worker_idx) };
        MsaSeed::from(MsaInput::new(self.mgr_ptr, idx))
    }

    pub fn get_result(&self, idx: i32) -> ExitKind {
        match unsafe { get_result(self.mgr_ptr, idx) } {
            0 | 1 => ExitKind::Ok,
            2 => ExitKind::Crash,
            3 => ExitKind::Timeout,
            4 => ExitKind::Oom,
            0x10 => ExitKind::Ok, // interesting seed in UniAFL side
            _ => ExitKind::Ok,
        }
    }

    pub fn get_start_input_idx(&self, worker_idx: i32) -> i32 {
        unsafe { get_start_input_idx(self.mgr_ptr, worker_idx) }
    }

    pub fn get_alloc_input_idx(&self, worker_idx: i32) -> i32 {
        unsafe { get_alloc_input_idx(self.mgr_ptr, worker_idx) }
    }

    pub fn get_execute_input_idx(&self, worker_idx: i32) -> i32 {
        unsafe { get_execute_input_idx(self.mgr_ptr, worker_idx) }
    }

    pub fn get_raw_cov_path(&self, tc_name: &str) -> PathBuf {
        let tc_name = trim_seed_fname(tc_name);
        self.cov_dir.join(tc_name)
    }

    pub fn get_cov_path(&self, tc_name: &str) -> PathBuf {
        let tc_name = trim_seed_fname(tc_name);
        self.cov_dir.join(format!("{}.cov", tc_name))
    }

    pub fn save_coverage(&self, tc_name: &str, cov: &[CovAddr]) -> Option<PathBuf> {
        let path = self.get_raw_cov_path(tc_name);
        if let Ok(mut file) = File::create(&path) {
            let cov = unsafe {
                std::slice::from_raw_parts(cov.as_ptr() as *const u8, std::mem::size_of_val(cov))
            };
            file.write_all(cov).ok();
            Some(path)
        } else {
            None
        }
    }

    #[cfg(feature = "log")]
    pub fn get_allocated_input_cnt(&self, worker_idx: i32) -> i32 {
        unsafe {
            get_alloc_input_idx(self.mgr_ptr, worker_idx)
                - get_start_input_idx(self.mgr_ptr, worker_idx)
        }
    }

    #[cfg(feature = "log")]
    pub fn log(&self, name: &str, worker_idx: i32, msg: String) {
        let lock = File::create(self.workdir.join("log.lock")).unwrap();
        lock.lock_exclusive().unwrap();
        let mut file = OpenOptions::new()
            .append(true) // Enable appending
            .create(true) // Create the file if it doesn't exist
            .open(self.workdir.join("log"))
            .unwrap();
        file.write_all(format!("[{} at {}] {}\n", name, worker_idx, msg).as_bytes())
            .ok();

        lock.unlock().unwrap();
    }

    pub fn alloc(&self, worker_idx: i32) -> Option<MsaInput> {
        let idx = unsafe { alloc_input(self.mgr_ptr, worker_idx) };
        if idx < 0 {
            None
        } else {
            Some(MsaInput::new(self.mgr_ptr, idx))
        }
    }

    pub fn load_file_input(&self, worker_idx: i32, fname: &PathBuf) -> std::io::Result<bool> {
        match self.alloc(worker_idx) {
            Some(mut input) => {
                let mut file = File::open(fname)?;
                let buffer = input.buffer_mut();
                let size = file.read(buffer)?;
                input.set_metadata(size, NO_SEED_ID);
                Ok(true)
            }
            _ => {
                panic!("alloc fail");
            }
        }
    }

    pub fn get_input(&self, idx: i32) -> MsaInput {
        MsaInput::new(self.mgr_ptr, idx)
    }

    pub fn run_once(&self, worker_idx: i32, dummys: &[Vec<u8>]) -> Vec<(String, String)> {
        let dummy_dir = self.workdir.join("dummy");
        std::fs::create_dir(&dummy_dir).ok();
        let mut seeds = Vec::new();
        let mut cmd = vec![self.harness_name.clone()];
        for (idx, dummy) in dummys.iter().enumerate() {
            let seed = dummy_dir.join(format!("{}", idx)).display().to_string();
            std::fs::write(&seed, dummy).ok();
            cmd.push(seed.clone());
            seeds.push(seed);
        }

        std::process::Command::new("run_once")
            .args(cmd)
            .env("CUR_WORKER", format!("{}", worker_idx))
            .output()
            .ok();

        seeds
            .iter()
            .map(|seed| (format!("{}.raw_cov", seed), format!("{}.cov", seed)))
            .collect()
    }
}

impl MsaInput {
    fn new(mgr_ptr: *const c_void, idx: i32) -> Self {
        Self { mgr_ptr, idx }
    }

    pub fn idx(&self) -> i32 {
        self.idx
    }

    pub fn id(&self) -> usize {
        unsafe { get_id(self.mgr_ptr, self.idx) }
    }

    pub fn parent_id(&self) -> CorpusId {
        CorpusId(self.id())
    }

    pub fn bytes(&self) -> &[u8] {
        unsafe {
            let buffer = get_input_buffer(self.mgr_ptr, self.idx);
            let size = get_input_size(self.mgr_ptr, self.idx) as usize;
            std::slice::from_raw_parts(buffer, size)
        }
    }

    pub fn buffer_mut(&mut self) -> &mut [u8] {
        unsafe {
            let buffer = get_input_buffer(self.mgr_ptr, self.idx);
            std::slice::from_raw_parts_mut(buffer, INPUT_BUFFER_SIZE)
        }
    }

    pub fn to_vec(&mut self) -> std::mem::ManuallyDrop<Vec<u8>> {
        unsafe {
            let buffer = get_input_buffer(self.mgr_ptr, self.idx);
            let ret = Vec::from_raw_parts(buffer, 0, INPUT_BUFFER_SIZE);
            std::mem::ManuallyDrop::new(ret)
        }
    }

    pub fn set_metadata(&mut self, size: usize, id: usize) {
        unsafe {
            set_input_metadata(self.mgr_ptr, self.idx, size as u32, id);
        }
    }

    pub fn set_fname(&mut self, fname: &str) {
        let fname = trim_seed_fname(fname);
        let fname_buffer = unsafe {
            std::slice::from_raw_parts_mut(get_fname(self.mgr_ptr, self.idx), FNAME_MAX_LEN)
        };
        fname_buffer[..fname.len()].copy_from_slice(fname.as_bytes());
    }

    pub fn get_fname(&self) -> String {
        let fname_buffer =
            unsafe { std::slice::from_raw_parts(get_fname(self.mgr_ptr, self.idx), FNAME_MAX_LEN) };
        let len = fname_buffer
            .iter()
            .position(|&b| b == 0)
            .unwrap_or(FNAME_MAX_LEN);
        String::from_utf8_lossy(&fname_buffer[..len]).to_string()
    }

    pub fn get_cov<'a>(&self) -> &'a [CovAddr] {
        let idx = self.idx;
        unsafe {
            let buffer = get_cov_buffer(self.mgr_ptr, idx);
            let size = get_cov_size(self.mgr_ptr, idx);
            std::slice::from_raw_parts(buffer, size as usize)
        }
    }

    pub fn get_crash_log<'a>(&self) -> Option<&'a [u8]> {
        let idx = self.idx;
        unsafe {
            let buffer = get_crash_log(self.mgr_ptr, idx);
            let size = get_crash_size(self.mgr_ptr, idx);
            if size > 0 {
                Some(std::slice::from_raw_parts(buffer, size as usize))
            } else {
                None
            }
        }
    }

    pub fn set_crash_log(&self, log: &[u8]) {
        let idx = self.idx;
        unsafe {
            let buffer = get_crash_log(self.mgr_ptr, idx);
            let len = std::cmp::min(CRASH_LOG_SIZE, log.len());
            let buffer = std::slice::from_raw_parts_mut(buffer, len);
            buffer.copy_from_slice(log);
            let md = get_input_metadata(self.mgr_ptr, idx);
            (*md).crash_size = len as i32;
        }
    }

    pub fn set_is_interesting(&self, corpus_id: CorpusId) {
        unsafe {
            let md = get_input_metadata(self.mgr_ptr, self.idx);
            (*md).id = corpus_id.0 as i64;
            (*md).result = 0x10;
        }
    }

    pub fn is_interesting(&self) -> Option<CorpusId> {
        unsafe {
            let md = get_input_metadata(self.mgr_ptr, self.idx);
            if (*md).result == 0x10 {
                Some(CorpusId((*md).id as usize))
            } else {
                None
            }
        }
    }
}

impl From<MsaInput> for UniInput {
    fn from(msa_input: MsaInput) -> Self {
        Self::from(msa_input.bytes())
    }
}

impl From<MsaInput> for MsaSeed {
    fn from(msa_input: MsaInput) -> Self {
        Self {
            id: msa_input.id(),
            fname: msa_input.get_fname(),
            bytes: msa_input.bytes().to_vec(),
        }
    }
}

impl From<&ExecResult> for ExitKind {
    fn from(res: &ExecResult) -> Self {
        match res {
            ExecResult::NewCov(_) => ExitKind::Ok,
            ExecResult::Crash(_) => ExitKind::Crash,
            ExecResult::Other(kind) => *kind,
        }
    }
}
