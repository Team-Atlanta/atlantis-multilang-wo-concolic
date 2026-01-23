use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::Arc;

use super::{executor::ExecStats, Executor};
use crate::{
    common::utils,
    msa::{
        manager::{ExecMode, MsaManager},
        stage::LoadStage,
        state::UniState,
    },
};

#[derive(Serialize, Deserialize)]
pub struct TestConf {
    workdir: String,
    given_fuzzer_dir: String,
    povs: Vec<String>,
    seeds: Vec<String>,
    core_ids: Vec<usize>,
    pov_dir: String,
    corpus_dir: String,
}

fn config_path() -> PathBuf {
    let path = std::env::var("UNIAFL_CONFIG").expect("Fail to get UNIAFL_CONFIG in env");
    PathBuf::from(&path)
}

fn run_inputs(conf: &TestConf, input_paths: &[PathBuf], is_pov: bool) -> ExecStats {
    let conf_path = config_path();
    let corpus_dir = PathBuf::from(&conf.corpus_dir);
    let pov_dir = PathBuf::from(&conf.pov_dir);
    let msa_mgr = MsaManager::new(&conf_path, true);
    let state = UniState::new(&conf_path, &msa_mgr.harness_name, &corpus_dir, &pov_dir);
    let state = Arc::new(state);
    let loader = LoadStage::check_new(&conf_path, true).unwrap();
    let mut executor = Executor::new(&conf_path, &msa_mgr, &conf.given_fuzzer_dir, 0);
    let mut idx = 0;
    while idx < input_paths.len() {
        msa_mgr.set_mode(0, ExecMode::ExecuteInput, false);
        let (loaded, next) =
            loader.load(&msa_mgr, msa_mgr.input_per_worker, 0, &input_paths[idx..]);
        assert!(loaded);
        idx = next;
        executor
            .execute_loaded_inputs("executor_test", &state, false)
            .ok();
    }
    if input_paths.len() > 0 {
        state.check_corpus_coverage(&conf_path, is_pov);
    }
    executor.stats
}

#[test]
#[ignore]
fn check_povs() {
    let conf = config_path();
    let conf = utils::load_json::<TestConf>(&conf).expect("Fail to parse TestConf");
    let povs: Vec<PathBuf> = conf.povs.iter().map(PathBuf::from).collect();
    let stats = run_inputs(&conf, &povs, true);
    assert!(povs.len() == stats.num_crashed_inputs);
}

#[test]
#[ignore]
fn check_seeds() {
    let conf = config_path();
    let conf = utils::load_json::<TestConf>(&conf).expect("Fail to parse TestConf");
    let seeds: Vec<PathBuf> = conf.seeds.iter().map(PathBuf::from).collect();
    let seeds = seeds[..seeds.len().min(25)].to_vec();
    let stats = run_inputs(&conf, &seeds, false);
    assert!(seeds.len() == stats.num_normal_inputs);
}
