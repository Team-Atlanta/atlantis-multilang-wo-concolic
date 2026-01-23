use libafl::Error;
use libafl_bolts::{
    current_nanos,
    rands::{Rand, StdRand},
    tuples::IntoVec,
    Named,
};
use std::sync::{Arc, RwLock};
use std::{borrow::Cow, path::PathBuf};

use crate::{
    executor::Executor,
    input_gen::client::InputGenClient,
    msa::{
        manager::{ExecMode, MsaManager, NO_SEED_ID},
        state::UniState,
    },
};

pub struct TestStage {
    name: Cow<'static, str>,
}

impl TestStage {
    pub fn new(config_path: &PathBuf) -> Option<Self> {
        let name = Cow::Borrowed("test");
        if InputGenClient::is_on_config(&name.to_string(), config_path, false) {
            Some(Self { name })
        } else {
            None
        }
    }
}

impl Named for TestStage {
    fn name(&self) -> &Cow<'static, str> {
        &self.name
    }
}

impl MsaStage for TestStage {
    fn perform(
        &self,
        msa_mgr: &MsaManager,
        state: &Arc<UniState>,
        worker_idx: i32,
        executor: &mut Executor,
    ) -> Result<bool, Error> {
        #[cfg(feature = "log")]
        msa_mgr.log(self.name(), worker_idx, "perform..".to_string());
        let mut rand = StdRand::with_seed(current_nanos());
        msa_mgr.set_mode(worker_idx, ExecMode::ExecuteInput, false);
        while let Some(mut msa_input) = msa_mgr.alloc(worker_idx) {
            let len = rand.below(0x100);
            let out_buf = msa_input.buffer_mut();
            for b in out_buf.iter_mut().take(len) {
                *b = rand.below(256) as u8;
            }
            msa_input.set_metadata(len, NO_SEED_ID);
        }
        executor.execute_loaded_inputs(self.name(), state, false)?;
        Ok(true)
    }
}

pub struct StageCounter {
    pub on: bool,
    pub is_first_run: bool,
    pub remain: u32,
    pub interval: u64,
}

pub fn need_run(counter: &Arc<RwLock<StageCounter>>) -> bool {
    let on = { counter.read().unwrap().on };
    if on {
        let mut counter = counter.write().unwrap();
        if counter.on {
            counter.on = false;
            return true;
        }
    }
    return false;
}

fn decrease_remain(counter: &Arc<RwLock<StageCounter>>) {
    let mut counter = counter.write().unwrap();
    if counter.remain > 0 {
        counter.remain -= 1;
        counter.is_first_run = false;
        counter.on = true;
    }
}

pub fn set_interval(counter: &Arc<RwLock<StageCounter>>, do_not_wait_if_first_run: bool) {
    let counter = counter.clone();
    if do_not_wait_if_first_run && is_first_run(&counter) {
        decrease_remain(&counter);
    } else {
        std::thread::spawn(move || {
            let interval = { counter.read().unwrap().interval };
            std::thread::sleep(std::time::Duration::from_secs(interval));
            decrease_remain(&counter);
        });
    }
}

pub fn is_first_run(counter: &Arc<RwLock<StageCounter>>) -> bool {
    let counter = counter.read().unwrap();
    counter.is_first_run
}

pub trait MsaStage: Named {
    fn perform(
        &self,
        msa_mgr: &MsaManager,
        state: &Arc<UniState>,
        worker_idx: i32,
        executor: &mut Executor,
    ) -> Result<bool, Error>;
}

pub trait MsaStagesTuple {
    fn perform_all(
        &self,
        msa_mgr: &MsaManager,
        state: &Arc<UniState>,
        worker_idx: i32,
        executor: &mut Executor,
    ) -> Result<bool, Error>;

    fn filtered_perform_all_forever<F: Fn(&&Box<dyn MsaStage>) -> bool>(
        &self,
        msa_mgr: &MsaManager,
        state: &Arc<UniState>,
        worker_idx: i32,
        executor: &mut Executor,
        filter: F,
    );
}

impl MsaStagesTuple for Vec<Box<dyn MsaStage>> {
    fn perform_all(
        &self,
        msa_mgr: &MsaManager,
        state: &Arc<UniState>,
        worker_idx: i32,
        executor: &mut Executor,
    ) -> Result<bool, Error> {
        self.iter().try_fold(false, |acc, stage| {
            match stage.perform(msa_mgr, state, worker_idx, executor) {
                Ok(performed) => Ok(acc || performed),
                err => err,
            }
        })
    }

    fn filtered_perform_all_forever<F: Fn(&&Box<dyn MsaStage>) -> bool>(
        &self,
        msa_mgr: &MsaManager,
        state: &Arc<UniState>,
        worker_idx: i32,
        executor: &mut Executor,
        filter: F,
    ) {
        let stages: Vec<&Box<dyn MsaStage>> = self.iter().filter(filter).collect();
        loop {
            for stage in &stages {
                stage.perform(msa_mgr, state, worker_idx, executor).ok();
            }
        }
    }
}

impl<Head, Tail> IntoVec<Box<dyn MsaStage>> for (Option<Head>, Tail)
where
    Head: MsaStage + 'static,
    Tail: IntoVec<Box<dyn MsaStage>>,
{
    fn into_vec_reversed(self) -> Vec<Box<dyn MsaStage>> {
        let mut ret = self.1.into_vec_reversed();
        if let Some(stage) = self.0 {
            ret.push(Box::new(stage));
        }
        ret
    }

    fn into_vec(self) -> Vec<Box<dyn MsaStage>> {
        if self.0.is_some() {
            let mut ret = self.into_vec_reversed();
            ret.reverse();
            ret
        } else {
            self.1.into_vec()
        }
    }
}
