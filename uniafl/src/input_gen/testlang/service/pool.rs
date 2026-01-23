use std::{
    mem,
    path::PathBuf,
    sync::{Arc, Mutex},
    thread::{sleep, JoinHandle},
    time::Duration,
};

use moka::sync::Cache;
use testlang::{TestLang, TestLangAst};

use crate::{
    common::utils,
    common::Error as UniaflError,
    input_gen::{server::InputGenPool, testlang::TestLangInputGenConfig},
};

use super::{reverser::HarnessReverser, worker::TestLangWorker};

pub type TestLangLocalId = usize;
pub type AstStorage = Cache<usize, Arc<TestLangAst>>;
pub type TestLangHandle = Arc<TestLang>;
pub type ReverserInitHandle = Arc<Mutex<Option<JoinHandle<Result<(), UniaflError>>>>>;

pub struct TestLangPool {
    #[allow(unused)]
    work_dir: PathBuf,
    reverser: HarnessReverser,
    reverser_init_handle: ReverserInitHandle,
    asts: AstStorage,
    max_bytes_size: usize,
}

impl InputGenPool for TestLangPool {
    type Worker = TestLangWorker;
    fn name() -> &'static str {
        "testlang_input_gen"
    }

    fn is_testlang_stage() -> bool {
        true
    }

    fn has_generator() -> bool {
        true
    }

    fn has_mutator() -> bool {
        true
    }

    fn new(config_path: &PathBuf) -> Self {
        let conf = utils::load_json::<TestLangInputGenConfig>(config_path)
            .expect("Failed to open testlang config file");
        let work_dir: PathBuf = conf.workdir.into();
        let full_cores = conf.core_ids.len();
        let testlang_cores = if full_cores < 4 {
            full_cores
        } else {
            full_cores / 4
        };
        let reverser_path: PathBuf = conf.reverser_path.into();
        let reverser = HarnessReverser::new(config_path, reverser_path, &work_dir, testlang_cores);
        let reverser_handle = reverser
            .run_on_side(false, None)
            .expect("Failed to run harness reverser");

        while !reverser_handle.is_finished() {
            reverser.sync_with_reverser();
            if reverser.get_testlang_count() > 0 {
                break;
            }
            sleep(Duration::from_secs(1));
        }

        if reverser.get_testlang_count() == 0 {
            reverser.sync_with_reverser();
            if reverser.get_testlang_count() == 0 {
                let run_result = reverser_handle.join();
                panic!("Failed to create any testlang: {:?}", run_result);
            }
        }

        // Testlang storage should be NOT empty here.
        let max_bytes_size = conf.max_len;
        Self {
            work_dir,
            reverser,
            reverser_init_handle: Arc::new(Mutex::new(Some(reverser_handle))),
            // This capacity was decided with consideration by @DaramG.
            asts: Cache::new(2048),
            max_bytes_size,
        }
    }

    fn new_worker(&self, worker_idx: usize) -> Self::Worker {
        // WARN: see https://github.com/Team-Atlanta/CRS-multilang/issues/903
        if worker_idx == 0 {
            let mut reverser_init_handle = self.reverser_init_handle.lock();
            if let Ok(reverser_init_handle) = &mut reverser_init_handle {
                return TestLangWorker::new(
                    self.reverser.clone(),
                    mem::take(reverser_init_handle),
                    self.asts.clone(),
                    self.max_bytes_size,
                );
            }
        }

        TestLangWorker::new(
            self.reverser.clone(),
            None,
            self.asts.clone(),
            self.max_bytes_size,
        )
    }
}
