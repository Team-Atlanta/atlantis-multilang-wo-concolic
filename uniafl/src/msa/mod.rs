use base64::Engine;
use libafl_bolts::tuples::{tuple_list, IntoVec};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

use crate::{
    common::utils,
    executor::Executor,
    input_gen::{
        dict::service::DictPool, mock_service::MockPool,
        testlang::service::pool::TestLangPool,
    },
};

pub mod corpus;
pub mod fuzzer;
pub mod manager;
mod scheduler;
pub mod stage;
pub mod state;

#[cfg(test)]
mod tests;

use fuzzer::MsaFuzzer;
use manager::MsaManager;
use stage::{GivenFuzzerStage, InputGenStage, LoadStage, MllaStage, SeedShareStage, TestStage};
use state::UniState;

#[derive(Serialize, Deserialize)]
pub struct ConfigJson {
    corpus_dir: String,
    given_corpus_dir: String,
    given_fuzzer_dir: String,
    pov_dir: String,
    workdir: String,
    core_ids: Vec<usize>,
}

pub fn start_fuzz_loop(config_path: &PathBuf) {
    let config: ConfigJson = utils::load_json::<ConfigJson>(config_path)
        .unwrap_or_else(|e| panic!("Error in load_json: {}", e));
    let corpus_dir = PathBuf::from(config.corpus_dir);
    let pov_dir = PathBuf::from(config.pov_dir);
    let msa_mgr = MsaManager::new(config_path, true);
    let state = UniState::new(config_path, &msa_mgr.harness_name, &corpus_dir, &pov_dir);
    let stages = tuple_list!(
        //LoadStage::new(config_path),
        TestStage::new(config_path),
        InputGenStage::new_with_input_gen_pool::<MockPool>(config_path, msa_mgr.worker_cnt),
        InputGenStage::new_with_input_gen_pool::<TestLangPool>(config_path, msa_mgr.worker_cnt),
        InputGenStage::new_with_input_gen_pool::<DictPool>(config_path, msa_mgr.worker_cnt),
        GivenFuzzerStage::new(config_path),
        MllaStage::new(config_path),
        SeedShareStage::new(config_path),
    )
    .into_vec();
    assert!(!stages.is_empty());
    let msa_fuzzer = MsaFuzzer::new(msa_mgr, config_path, config.given_fuzzer_dir, state, stages);

    #[cfg(not(test))]
    {
        msa_fuzzer.run()
    }

    #[cfg(test)]
    {
        msa_fuzzer.test()
    }
}

pub fn execute_one_by_one(config_path: &PathBuf) {
    let config: ConfigJson = utils::load_json::<ConfigJson>(config_path)
        .unwrap_or_else(|e| panic!("Error in load_json: {}", e));
    assert!(config.core_ids.len() == 1);
    std::env::set_var("SILENT_MODE", "TRUE");
    std::env::set_var("EXECUTOR_MODE", "TRUE");
    std::env::set_var("ALWAYS_GET_COV", "TRUE");
    let log_path = format!("{}/execute_log_{}", config.workdir, config.core_ids[0]);
    let msa_mgr = MsaManager::new_with(config_path, true, 1);
    let mut executor = Executor::new_with(config_path, &msa_mgr, &config.given_fuzzer_dir, 0, true);
    loop {
        let mut tmp_path = String::new();
        if std::io::stdin().read_line(&mut tmp_path).is_err() {
            continue;
        }
        std::fs::remove_file(&log_path).ok();
        let path = PathBuf::from(tmp_path.trim());
        if !path.exists() {
            std::fs::write(&log_path, "{}").ok();
        } else {
            let (stdout, stderr, cov, crash) = executor.execute_one_file(&path);
            let data = format!(
                "{{\"stdout\":\"{}\", \"stderr\":\"{}\"",
                base64::engine::general_purpose::STANDARD.encode(&stdout),
                base64::engine::general_purpose::STANDARD.encode(&stderr)
            );
            let data = if let Some(cov) = cov {
                format!(
                    "{},\"coverage\":\"{}\"",
                    data,
                    base64::engine::general_purpose::STANDARD.encode(&cov)
                )
            } else {
                data
            };
            let data = if let Some(crash) = crash {
                format!(
                    "{},\"crash_log\":\"{}\"",
                    data,
                    base64::engine::general_purpose::STANDARD.encode(&crash)
                )
            } else {
                data
            };
            let data = format!("{}}}", data);
            std::fs::write(&log_path, data).ok();
        }
        println!("{}", &log_path);
    }
}
