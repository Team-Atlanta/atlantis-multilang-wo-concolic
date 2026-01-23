use libafl::Error;
use libafl_bolts::Named;
use serde::{Deserialize, Serialize};
use std::{borrow::Cow, path::PathBuf, sync::Arc};

use crate::{
    common::utils,
    executor::Executor,
    input_gen::client::InputGenClient,
    msa::{
        manager::{ExecMode, MsaManager},
        stage::MsaStage,
        state::UniState,
    },
};

#[derive(Serialize, Deserialize)]
pub struct LoadStageConf {
    given_corpus_dir: String,
    workdir: String,
    core_ids: Vec<usize>,
}

pub struct LoadStage {
    name: Cow<'static, str>,
    seed_dir: PathBuf,
    tmp_paths: Vec<PathBuf>,
}

impl LoadStage {
    pub fn check_new(config_path: &PathBuf, always_create: bool) -> Option<Self> {
        let name = "Load";
        if always_create || InputGenClient::is_on_config(&name.to_string(), config_path, true) {
            let config = utils::load_json::<LoadStageConf>(config_path)
                .unwrap_or_else(|e| panic!("Error in load_json: {}", e));
            Some(Self::new_with(
                name,
                config_path,
                &PathBuf::from(config.given_corpus_dir),
            ))
        } else {
            None
        }
    }

    pub fn new(config_path: &PathBuf) -> Option<Self> {
        Self::check_new(config_path, false)
    }

    pub fn new_with(name: &'static str, config_path: &PathBuf, target_dir: &PathBuf) -> Self {
        let config = utils::load_json::<LoadStageConf>(config_path)
            .unwrap_or_else(|e| panic!("Error in load_json: {}", e));
        let worker_cnt = config.core_ids.len();
        let workdir = PathBuf::from(config.workdir).join(name);
        std::fs::create_dir_all(&workdir).ok();
        let tmp_paths = (0..worker_cnt)
            .map(|i| workdir.join(i.to_string()))
            .collect();
        Self {
            name: Cow::Borrowed(name),
            seed_dir: target_dir.clone(),
            tmp_paths,
        }
    }

    pub fn load(
        &self,
        msa_mgr: &MsaManager,
        mut max_load: u32,
        worker_idx: i32,
        seeds: &[PathBuf],
    ) -> (bool, usize) {
        let mut i = 0;
        let mut loaded = false;
        let tmp_path = &self.tmp_paths[worker_idx as usize];
        while i < seeds.len() {
            if std::fs::rename(&seeds[i], tmp_path).is_ok() {
                msa_mgr.load_file_input(worker_idx, tmp_path).ok();
                max_load -= 1;
                loaded = true;
            }
            i += 1;
            if max_load == 0 {
                break;
            }
        }
        (loaded, i)
    }

    pub fn load_and_perform(
        &self,
        msa_mgr: &MsaManager,
        state: &Arc<UniState>,
        worker_idx: i32,
        executor: &mut Executor,
        seeds: &[PathBuf],
    ) -> Result<bool, Error> {
        let mut idx = 0;
        let mut performed = false;
        while idx < seeds.len() {
            msa_mgr.set_mode(worker_idx, ExecMode::ExecuteInput, false);
            let (loaded, next) =
                self.load(msa_mgr, msa_mgr.input_per_worker, worker_idx, &seeds[idx..]);
            idx += next;
            if loaded {
                if let Err(e) = executor.execute_loaded_inputs(self.name(), state, false) {
                    eprintln!("Error in execute_loaded_inputs: {}", e);
                };
                performed = true;
            }
        }
        Ok(performed)
    }
}

impl Named for LoadStage {
    fn name(&self) -> &Cow<'static, str> {
        &self.name
    }
}

impl MsaStage for LoadStage {
    fn perform(
        &self,
        msa_mgr: &MsaManager,
        state: &Arc<UniState>,
        worker_idx: i32,
        executor: &mut Executor,
    ) -> Result<bool, Error> {
        let seeds: Vec<PathBuf> = std::fs::read_dir(&self.seed_dir)?
            .filter_map(|entry| entry.ok().map(|e| e.path()))
            .collect();
        self.load_and_perform(msa_mgr, state, worker_idx, executor, &seeds)
    }
}
