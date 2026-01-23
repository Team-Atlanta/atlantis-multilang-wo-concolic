#![allow(unused)]
use libafl_bolts::core_affinity::Cores;
use serde::{Deserialize, Serialize};
use std::mem::ManuallyDrop;
use std::path::PathBuf;
use std::sync::Arc;

use super::manager::{InputGenCmd, InputGenManager, InputGenResult, InputGenStatus};
use crate::{
    common::{utils, Error as UniaflError},
    msa::fuzzer::SendablePtr,
    msa::manager::{MsaInput, MsaManager, MsaSeed, NO_SEED_ID},
};

pub trait InputGenPool {
    type Worker: InputGenWorker;
    fn name() -> &'static str;
    fn is_on(config_path: &PathBuf) -> bool {
        true
    }
    fn is_testlang_stage() -> bool {
        false
    }
    fn has_generator() -> bool;
    fn has_mutator() -> bool;
    fn has_cb_if_added_into_corpus() -> bool {
        Self::Worker::has_cb_if_added_into_corpus()
    }
    fn new(config_path: &PathBuf) -> Self;
    fn new_worker(&self, worker_idx: usize) -> Self::Worker;
}

pub trait InputGenWorker {
    type Metadata: Clone;
    fn generate(&mut self, outputs: &mut Outputs<Self::Metadata>) -> Result<bool, UniaflError>;
    fn mutate(
        &mut self,
        seed: &MsaSeed,
        outputs: &mut Outputs<Self::Metadata>,
    ) -> Result<bool, UniaflError>;

    fn has_cb_if_added_into_corpus() -> bool {
        false
    }
    fn cb_if_added_into_corpus(
        &mut self,
        output: &Output<Self::Metadata>,
        corpus_id: usize,
    ) -> Result<(), UniaflError> {
        unreachable!()
    }
}

pub struct Output<T> {
    msa_input: MsaInput,
    pub buf: ManuallyDrop<Vec<u8>>,
    pub metadata: Option<T>,
}

impl<T> Output<T> {
    pub fn new(msa_input: MsaInput, buf: ManuallyDrop<Vec<u8>>, metadata: Option<T>) -> Self {
        Self {
            msa_input,
            buf,
            metadata,
        }
    }
}

#[derive(Clone)]
pub struct Remain<T: Clone> {
    pub buf: Vec<u8>,
    pub metadata: Option<T>,
}

impl<T: Clone> Remain<T> {
    pub fn new(buf: Vec<u8>, metadata: Option<T>) -> Self {
        Self { buf, metadata }
    }
}

pub struct Outputs<'a, T: Clone> {
    msa_mgr: &'a MsaManager,
    worker_idx: i32,
    outputs: Vec<Output<T>>,
    remains: Vec<Remain<T>>,
    seed_id: usize,
}

impl<'a, T: Clone> Outputs<'a, T> {
    fn new(msa_mgr: &'a MsaManager, worker_idx: i32, seed_id: usize) -> Self {
        Self {
            msa_mgr,
            worker_idx,
            outputs: Vec::new(),
            remains: Vec::new(),
            seed_id,
        }
    }

    pub fn next(&mut self) -> Option<&mut Output<T>> {
        match self.msa_mgr.alloc(self.worker_idx) {
            Some(mut new_input) => {
                let buf = new_input.to_vec();
                let output = Output::new(new_input, buf, None);
                self.outputs.push(output);
                Some(self.outputs.last_mut().unwrap())
            }
            None => None,
        }
    }

    pub fn bump_blobs(&mut self, inputs: Vec<Vec<u8>>) {
        let inputs = inputs.into_iter().map(|i| Remain::new(i, None)).collect();
        self.bump(inputs);
    }

    pub fn bump(&mut self, inputs: Vec<Remain<T>>) {
        let mut idx = 0;
        for input in &inputs {
            if let Some(output) = self.next() {
                output.buf.extend_from_slice(&input.buf);
            } else {
                break;
            }
            idx += 1;
        }
        self.remains.extend_from_slice(&inputs[idx..]);
    }

    fn finalize(mut self) -> (InputGenResult, Vec<Output<T>>, Vec<Remain<T>>, usize) {
        let seed_id = self.seed_id;
        let mut is_empty = true;
        for output in &mut self.outputs {
            let len = output.buf.len();
            if len > 0 {
                is_empty = false;
            }
            output.msa_input.set_metadata(len, seed_id);
        }
        let result = if !self.remains.is_empty() {
            InputGenResult::Remain
        } else if is_empty {
            InputGenResult::Empty
        } else {
            InputGenResult::Done
        };
        (result, self.outputs, self.remains, self.seed_id)
    }

    #[cfg(feature = "log")]
    pub fn log(&self, name: &str, msg: String) {
        self.msa_mgr.log(name, self.worker_idx, msg);
    }
}

pub struct InputGenServerWorker<I: InputGenWorker> {
    implement: I,
    msa_mgr: MsaManager,
    worker_idx: i32,
}

impl<I: InputGenWorker> InputGenServerWorker<I> {
    pub fn new(implement: I, msa_mgr: MsaManager, worker_idx: i32) -> Self {
        Self {
            implement,
            msa_mgr,
            worker_idx,
        }
    }

    pub fn mutate_loop(
        &mut self,
    ) -> (
        InputGenResult,
        Vec<Output<I::Metadata>>,
        Vec<Remain<I::Metadata>>,
        usize,
    ) {
        let seed = self.msa_mgr.get_seed(self.worker_idx);
        let mut outputs = Outputs::new(&self.msa_mgr, self.worker_idx, seed.id);
        let ret = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            self.implement.mutate(&seed, &mut outputs)
        }));
        #[cfg(feature = "log")]
        self.msa_mgr.log(
            "InputGenServerWorker.mutate_loop",
            self.worker_idx,
            match ret {
                Ok(ret) => match ret {
                    Ok(ok) => format!("Ok: {}", ok),
                    Err(err) => format!("Err: {}", err),
                },
                Err(err) => {
                    if let Some(err) = err.downcast_ref::<&str>() {
                        format!("Panic: {}", err)
                    } else if let Some(err) = err.downcast_ref::<String>() {
                        format!("Panic: {}", err)
                    } else {
                        "Panic (Failed to display the exception)".to_string()
                    }
                }
            },
        );
        outputs.finalize()
    }

    pub fn generate_loop(
        &mut self,
    ) -> (
        InputGenResult,
        Vec<Output<I::Metadata>>,
        Vec<Remain<I::Metadata>>,
        usize,
    ) {
        let mut outputs = Outputs::new(&self.msa_mgr, self.worker_idx, NO_SEED_ID);
        let ret = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            self.implement.generate(&mut outputs)
        }));
        #[cfg(feature = "log")]
        self.msa_mgr.log(
            "InputGenServerWorker.generate_loop",
            self.worker_idx,
            match ret {
                Ok(ret) => match ret {
                    Ok(ok) => format!("Ok: {}", ok),
                    Err(err) => format!("Err: {}", err),
                },
                Err(err) => {
                    if let Some(err) = err.downcast_ref::<&str>() {
                        format!("Panic: {}", err)
                    } else if let Some(err) = err.downcast_ref::<String>() {
                        format!("Panic: {}", err)
                    } else {
                        "Panic (Failed to display the exception)".to_string()
                    }
                }
            },
        );
        outputs.finalize()
    }

    fn get_remain(
        &mut self,
        seed_id: usize,
        remains: Vec<Remain<I::Metadata>>,
    ) -> (
        InputGenResult,
        Vec<Output<I::Metadata>>,
        Vec<Remain<I::Metadata>>,
        usize,
    ) {
        let mut outputs = Outputs::new(&self.msa_mgr, self.worker_idx, seed_id);
        outputs.bump(remains);
        outputs.finalize()
    }

    fn invoke_callbacks(
        &mut self,
        outputs: Vec<Output<I::Metadata>>,
        remains: Vec<Remain<I::Metadata>>,
        seed_id: usize,
    ) -> (
        InputGenResult,
        Vec<Output<I::Metadata>>,
        Vec<Remain<I::Metadata>>,
        usize,
    ) {
        #[cfg(feature = "log")]
        self.msa_mgr.log(
            "InputGenServerWorker.invoke_callbacks",
            self.worker_idx,
            format!("Check.."),
        );
        for mut output in &outputs {
            if let Some(corpus_id) = output.msa_input.is_interesting() {
                #[cfg(feature = "log")]
                self.msa_mgr.log(
                    "InputGenServerWorker.invoke_callbacks",
                    self.worker_idx,
                    format!("Invoke callback"),
                );
                std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    self.implement.cb_if_added_into_corpus(output, corpus_id.0);
                }))
                .ok();
            }
        }
        (InputGenResult::Empty, outputs, remains, seed_id)
    }

    pub fn process(&mut self, mgr: &InputGenManager) {
        let worker_idx = self.worker_idx;
        mgr.set_status(worker_idx as usize, InputGenStatus::READY);
        let mut remains = Vec::new();
        let mut seed_id = NO_SEED_ID;
        let mut outputs = Vec::new();
        let has_cb = I::has_cb_if_added_into_corpus();
        loop {
            let result = match mgr.wait_cmd(worker_idx) {
                InputGenCmd::Mutate => self.mutate_loop(),
                InputGenCmd::Generate => self.generate_loop(),
                InputGenCmd::GetRemain => self.get_remain(seed_id, remains),
                InputGenCmd::ExecCB => self.invoke_callbacks(outputs, remains, seed_id),
            };
            mgr.done_cmd(worker_idx, result.0);
            outputs = result.1;
            remains = result.2;
            seed_id = result.3;
        }
    }
}

pub struct InputGenServer<I: InputGenPool> {
    implement: I,
    mgr: InputGenManager,
    msa_mgr: MsaManager,
}

impl<I: InputGenPool> InputGenServer<I> {
    pub fn new(config_path: &PathBuf, mgr: InputGenManager, msa_mgr: MsaManager) -> Self {
        Self {
            implement: I::new(config_path),
            mgr,
            msa_mgr,
        }
    }

    fn name(&self) -> &'static str {
        I::name()
    }

    fn run(&self, start_worker_idx: Option<u32>, end_worker_idx: Option<u32>) {
        let mut handles = Vec::new();
        let mgr = Arc::new(&self.mgr);
        let mgr_ptr = Arc::as_ptr(&mgr) as *const u8;
        let pool = Arc::new(&self.implement);
        let pool_ptr = Arc::as_ptr(&pool) as *const u8;
        let start_worker_idx = start_worker_idx.unwrap_or(0);
        let end_worker_idx = end_worker_idx.unwrap_or(self.msa_mgr.worker_cnt);
        for worker_idx in start_worker_idx..end_worker_idx {
            let msa_mgr = self.msa_mgr.clone();
            let mgr_ptr = SendablePtr(mgr_ptr);
            let pool_ptr = SendablePtr(pool_ptr);
            let handle = std::thread::spawn(move || {
                msa_mgr.set_affinity(worker_idx as i32);
                let mgr = unsafe { *(mgr_ptr.get() as *const &InputGenManager) };
                let pool = unsafe { *(pool_ptr.get() as *const &I) };
                let mut worker = InputGenServerWorker::new(
                    pool.new_worker(worker_idx.try_into().unwrap()),
                    msa_mgr,
                    worker_idx as i32,
                );
                worker.process(mgr);
            });
            handles.push(handle);
        }
        let mgr_ptr = SendablePtr(mgr_ptr);
        let timeout_handle = std::thread::spawn(move || {
            let mgr = unsafe { *(mgr_ptr.get() as *const &InputGenManager) };
            mgr.check_timeout_loop();
        });
        handles.push(timeout_handle);
        for handle in handles {
            if let Err(e) = handle.join() {
                eprintln!("Error in worker thread: {:?}", e);
                break;
            }
        }
    }
}

#[derive(Serialize, Deserialize)]
pub struct InputGenServerConfig {
    core_ids: Vec<usize>,
    harness_name: String,
}

pub fn run_server<I: InputGenPool>(
    config_path: &PathBuf,
    start_worker_idx: Option<u32>,
    end_worker_idx: Option<u32>,
    reset: bool,
) {
    let config = utils::load_json::<InputGenServerConfig>(config_path)
        .unwrap_or_else(|e| panic!("Error in load_json: {}", e));
    let cores = Cores::from(config.core_ids);
    let mgr = InputGenManager::new(I::name(), config.harness_name, &cores, false);
    if reset {
        mgr.reset();
    } else {
        let msa_mgr = MsaManager::new(config_path, false);
        let server = InputGenServer::<I>::new(config_path, mgr, msa_mgr);
        server.run(start_worker_idx, end_worker_idx);
    }
}
