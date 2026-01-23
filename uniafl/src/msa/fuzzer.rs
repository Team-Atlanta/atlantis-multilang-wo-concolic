use std::path::PathBuf;
use std::sync::Arc;

use super::{
    manager::MsaManager,
    stage::{InputGenStage, LoadStage, MsaStage, MsaStagesTuple},
    state::UniState,
};
use crate::{
    executor::Executor,
    input_gen::{
        dict::service::DictPool, server::InputGenPool,
        testlang::service::pool::TestLangPool,
    },
};

pub struct MsaFuzzer<ST: MsaStagesTuple> {
    state: Arc<UniState>,
    msa_mgr: MsaManager,
    config_path: PathBuf,
    given_fuzzer_dir: String,
    stages: Arc<ST>,
}
#[derive(Debug)]
pub struct SendablePtr(pub *const u8);

impl SendablePtr {
    pub fn get(&self) -> *const u8 {
        self.0
    }
}
unsafe impl Send for SendablePtr {}

macro_rules! dummy_seeds {
    () => {
        vec![vec![b'\n']]
    };
}

impl<ST: MsaStagesTuple> MsaFuzzer<ST> {
    pub fn new(
        msa_mgr: MsaManager,
        config_path: &PathBuf,
        given_fuzzer_dir: String,
        state: UniState,
        stages: ST,
    ) -> Self {
        Self {
            given_fuzzer_dir,
            state: Arc::new(state),
            msa_mgr,
            config_path: config_path.clone(),
            stages: Arc::new(stages),
        }
    }

    fn run_seed(&self, worker_idx: i32, executor: &mut Executor) {
        if let Some(seed_load_stage) = LoadStage::check_new(&self.config_path, true) {
            seed_load_stage
                .perform(&self.msa_mgr, &self.state, worker_idx, executor)
                .ok();
        }
    }

    pub fn run_worker(&self, worker_idx: i32) {
        self.msa_mgr.set_affinity(worker_idx);
        let mut executor = Executor::new(
            &self.config_path,
            &self.msa_mgr,
            &self.given_fuzzer_dir,
            worker_idx,
        );
        self.run_seed(worker_idx, &mut executor);
        if worker_idx == 0 {
            self.state
                .try_add_dummy_seeds(&self.msa_mgr, worker_idx, dummy_seeds!());
        }

        self.stages.filtered_perform_all_forever(
            &self.msa_mgr,
            &self.state,
            worker_idx,
            &mut executor,
            |stage| {
                InputGenStage::stage_filter(
                    self.msa_mgr.worker_cnt,
                    worker_idx as u32,
                    stage.name(),
                )
            },
        );
    }

    pub fn run(&self) {
        let arc_self = Arc::new(self);
        let raw_ptr = Arc::as_ptr(&arc_self) as *const u8;
        let mut workers = Vec::new();
        for worker_idx in 0..self.msa_mgr.worker_cnt {
            let ptr = SendablePtr(raw_ptr);
            let handle = std::thread::spawn(move || unsafe {
                let this = *(ptr.get() as *const &Self);
                this.run_worker(worker_idx as i32);
            });
            workers.push(handle);
        }
        for worker in workers {
            if let Err(e) = worker.join() {
                eprintln!("Error in worker thread: {:?}", e);
            }
        }
    }

    #[cfg(test)]
    pub fn test_worker(&self, worker_idx: i32) -> usize {
        self.msa_mgr.set_affinity(worker_idx);
        let mut executor = Executor::new(
            &self.config_path,
            &self.msa_mgr,
            &self.given_fuzzer_dir,
            worker_idx,
        );
        self.run_seed(worker_idx, &mut executor);
        if worker_idx == 0 {
            self.state
                .try_add_dummy_seeds(&self.msa_mgr, worker_idx, dummy_seeds!());
        }
        let prev = executor.stats.total();
        for idx in 0..2 {
            println!("[worker {}] Run {}", worker_idx, idx);
            loop {
                match self
                    .stages
                    .perform_all(&self.msa_mgr, &self.state, worker_idx, &mut executor)
                {
                    Ok(false) => continue,
                    Ok(true) => break,
                    Err(err) => {
                        println!("Error {:?}", err);
                        break;
                    }
                }
            }
        }
        let post = executor.stats.total();
        post - prev
    }

    #[cfg(test)]
    pub fn test(&self) {
        let arc_self = Arc::new(self);
        let raw_ptr = Arc::as_ptr(&arc_self) as *const u8;
        let mut workers = Vec::new();
        for worker_idx in 0..self.msa_mgr.worker_cnt {
            let ptr = SendablePtr(raw_ptr);
            let handle = std::thread::spawn(move || unsafe {
                let this = *(ptr.get() as *const &Self);
                this.test_worker(worker_idx as i32)
            });
            workers.push(handle);
        }
        let timeout: i32 = std::env::var("TEST_TIMEOUT")
            .expect("Fail to get TEST_TIMEOUT")
            .parse()
            .expect("Fai to get TEST_TIMEOUT");
        for _ in 0..timeout {
            std::thread::sleep(std::time::Duration::from_secs(1));
            if workers.iter().all(|w| w.is_finished()) {
                break;
            }
        }
        let all_ended = workers.iter().all(|w| w.is_finished());
        let mut total = 0;
        for worker in workers {
            if worker.is_finished() {
                match worker.join() {
                    Ok(n) => total += n,
                    _ => panic!("worker fail"),
                }
            }
        }
        assert!(all_ended, "Timeout! likely bug in the testing target");
        assert!(total > 0);
        self.state.check_coverage(&self.config_path);
    }
}
