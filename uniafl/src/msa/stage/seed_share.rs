use libafl::Error;
use libafl_bolts::Named;
use serde::{Deserialize, Serialize};
use std::{
    borrow::Cow,
    path::PathBuf,
    sync::{Arc, RwLock},
};

use super::{
    stage::{need_run, set_interval},
    LoadStage, StageCounter,
};
use crate::{
    common::utils,
    executor::Executor,
    input_gen::client::InputGenClient,
    msa::{manager::MsaManager, stage::MsaStage, state::UniState},
};

#[derive(Serialize, Deserialize)]
pub struct SeedShareConf {
    given_corpus_dir: String,
}

pub struct SeedShareStage {
    loader: LoadStage,
    counter: Arc<RwLock<StageCounter>>,
}

impl SeedShareStage {
    pub fn new(config_path: &PathBuf) -> Option<Self> {
        let name = "share";
        if InputGenClient::is_on_config(&name.to_string(), config_path, true) {
            let config = utils::load_json::<SeedShareConf>(config_path)
                .unwrap_or_else(|e| panic!("Error in load_json: {}", e));
            let blob_dir = PathBuf::from(&config.given_corpus_dir);
            Some(Self {
                loader: LoadStage::new_with(name, config_path, &blob_dir),
                counter: Arc::new(RwLock::new(StageCounter {
                    on: true,
                    is_first_run: true,
                    remain: 0xffffffff,
                    interval: 300,
                })),
            })
        } else {
            None
        }
    }
}

impl Named for SeedShareStage {
    fn name(&self) -> &Cow<'static, str> {
        self.loader.name()
    }
}
impl MsaStage for SeedShareStage {
    fn perform(
        &self,
        msa_mgr: &MsaManager,
        state: &Arc<UniState>,
        worker_idx: i32,
        executor: &mut Executor,
    ) -> Result<bool, Error> {
        if need_run(&self.counter) {
            self.loader
                .perform(msa_mgr, state, worker_idx, executor)
                .ok();
            set_interval(&self.counter, false);
        }
        Ok(true)
    }
}
