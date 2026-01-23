use libafl::{corpus::CorpusId, Error};
use libafl_bolts::core_affinity::Cores;
use serde::{Deserialize, Serialize};
use std::borrow::Cow;
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};

use super::{
    manager::{InputGenCmd, InputGenManager, InputGenResult},
    server::InputGenPool,
};
use crate::{
    common::utils,
    msa::manager::{ExecMode, MsaManager},
};

pub struct InputGenClient {
    name: Cow<'static, str>,
    has_generator: bool,
    has_mutator: bool,
    has_cb_if_added_into_corpus: bool,
    runner: Child,
    mgr: InputGenManager,
    pub is_testlang_stage: bool,
}

#[derive(Serialize, Deserialize)]
pub struct InputGenClientConfig {
    harness_name: String,
    core_ids: Vec<usize>,
    input_gens: Option<Vec<String>>,
}

impl InputGenClient {
    pub fn is_on_config(name: &String, config_path: &PathBuf, default: bool) -> bool {
        let config = utils::load_json::<InputGenClientConfig>(config_path)
            .unwrap_or_else(|e| panic!("Error in load_json: {}", e));
        if let Some(input_gens) = config.input_gens {
            input_gens.contains(name)
        } else {
            default
        }
    }

    fn is_on<I: InputGenPool>(config_path: &PathBuf) -> bool {
        let name = I::name().to_string();
        Self::is_on_config(&name, config_path, true) && I::is_on(config_path)
    }

    fn find_runner(base: &PathBuf) -> Option<PathBuf> {
        let ret = base.with_file_name("input_gen_runner");
        if ret.exists() {
            Some(ret)
        } else {
            None
        }
    }

    pub fn new_with_input_gen_pool<I: InputGenPool>(
        config_path: &PathBuf,
        start_worker_idx: Option<u32>,
        end_worker_idx: Option<u32>,
    ) -> Option<Self> {
        if Self::is_on::<I>(config_path) {
            let base = std::env::current_exe().expect("Fail to get current executable");
            let runner = Self::find_runner(&base)
                .or_else(|| Self::find_runner(&PathBuf::from(base.parent().unwrap())))
                .expect("Fail to find input_gen_runner");
            let runner = runner.to_str().expect("Fail to convert to str");
            let config_path_str = config_path.to_str().expect("Fail to convert to str");
            let name = I::name();
            let cmd = format!("{} -c {} -s {}", runner, config_path_str, name);
            let cmd = if let Some(start_worker_idx) = start_worker_idx {
                format!("{} -w {}", cmd, start_worker_idx)
            } else {
                cmd
            };
            let cmd = if let Some(end_worker_idx) = end_worker_idx {
                format!("{} -e {}", cmd, end_worker_idx)
            } else {
                cmd
            };
            Some(Self::new(
                config_path,
                name,
                &cmd,
                I::has_generator(),
                I::has_mutator(),
                I::has_cb_if_added_into_corpus(),
                I::is_testlang_stage(),
            ))
        } else {
            None
        }
    }

    pub fn new(
        config_path: &PathBuf,
        name: &'static str,
        cmd: &String,
        has_generator: bool,
        has_mutator: bool,
        has_cb_if_added_into_corpus: bool,
        is_testlang_stage: bool,
    ) -> Self {
        let config = utils::load_json::<InputGenClientConfig>(config_path)
            .unwrap_or_else(|e| panic!("Error in load_json: {}", e));
        let cores = Cores::from(config.core_ids);
        let log_path = config_path
            .parent()
            .unwrap()
            .join(format!("input_gen_{}.log", name));
        let mgr = InputGenManager::new(name, config.harness_name, &cores, true);
        Self {
            name: Cow::Borrowed(name),
            has_generator,
            has_mutator,
            has_cb_if_added_into_corpus,
            runner: Self::boot_up(cmd, &log_path),
            is_testlang_stage,
            mgr,
        }
    }

    #[allow(unused)]
    fn boot_up(cmd: &String, log_path: &PathBuf) -> Child {
        #[cfg(not(feature = "log"))]
        {
            let cmd = format!("{} > /dev/null 2>&1; {} --reset", cmd, cmd);
            unsafe {
                Command::new("sh")
                    .args(["-c", &cmd])
                    .stdout(Stdio::piped())
                    .stderr(Stdio::piped())
                    .stdin(Stdio::piped())
                    .pre_exec(|| {
                        libc::setsid();
                        Ok(())
                    })
                    .spawn()
                    .expect("Fail to run_fuzzer")
            }
        }
        #[cfg(feature = "log")]
        {
            let cmd = format!(
                "{} >> {} 2>&1; {} --reset >> {} 2>&1",
                cmd,
                log_path.to_str().unwrap(),
                cmd,
                log_path.to_str().unwrap(),
            );
            Command::new("sh")
                .args(["-c", &cmd])
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .stdin(Stdio::piped())
                .spawn()
                .expect("Fail to run_fuzzer")
        }
    }

    pub fn name(&self) -> &Cow<'static, str> {
        &self.name
    }

    pub fn has_generator(&self) -> bool {
        self.has_generator
    }

    pub fn has_mutator(&self) -> bool {
        self.has_mutator
    }

    pub fn has_cb_if_added_into_corpus(&self) -> bool {
        self.has_cb_if_added_into_corpus
    }

    pub fn mutate(&self, worker_idx: i32) -> Result<InputGenResult, Error> {
        Ok(self.mgr.run_cmd(worker_idx, InputGenCmd::Mutate))
    }

    pub fn generate(&self, worker_idx: i32) -> Result<InputGenResult, Error> {
        Ok(self.mgr.run_cmd(worker_idx, InputGenCmd::Generate))
    }

    pub fn get_remain(&self, worker_idx: i32) -> Result<InputGenResult, Error> {
        Ok(self.mgr.run_cmd(worker_idx, InputGenCmd::GetRemain))
    }

    pub fn execute_cb(&self, worker_idx: i32) -> Result<InputGenResult, Error> {
        Ok(self.mgr.run_cmd(worker_idx, InputGenCmd::ExecCB))
    }

    pub fn is_ready(&self, worker_idx: i32) -> bool {
        self.mgr.is_ready(worker_idx)
    }

    // For debugging only
    #[allow(dead_code)]
    pub fn get_inputs_from_server(&self, msa_mgr: &MsaManager, worker_idx: i32) -> Vec<Vec<u8>> {
        let start_idx = msa_mgr.get_start_input_idx(worker_idx);
        let end_idx = msa_mgr.get_alloc_input_idx(worker_idx);
        (start_idx..end_idx)
            .map(|idx| msa_mgr.get_input(idx).bytes().to_vec())
            .collect()
    }

    #[allow(dead_code)]
    pub fn debug<I: InputGenPool>(config_path: &PathBuf, seed_bytes: &[u8]) {
        let client = InputGenClient::new_with_input_gen_pool::<I>(config_path, None, None).unwrap();
        let msa_mgr = MsaManager::new(config_path, true);
        let worker_idx = 0;
        if client.has_mutator() {
            msa_mgr.set_mode(worker_idx, ExecMode::ExecuteInput, client.is_testlang_stage);
            msa_mgr.set_seed(worker_idx, CorpusId(1337), &None, seed_bytes);
            client.mutate(worker_idx).expect("Fail to mutate");
            let mutated_inputs = client.get_inputs_from_server(&msa_mgr, worker_idx);
            println!("New mutated inputs");
            for input in mutated_inputs {
                println!("{:?}", input);
            }
        } else {
            println!("Do not have mutator");
        }
        if client.has_generator() {
            msa_mgr.set_mode(worker_idx, ExecMode::ExecuteInput, client.is_testlang_stage);
            client.generate(worker_idx).ok();
            let generated_inputs = client.get_inputs_from_server(&msa_mgr, worker_idx);
            println!("New generated inputs");
            for input in generated_inputs {
                println!("{:?}", input);
            }
        }
    }
}

impl Drop for InputGenClient {
    fn drop(&mut self) {
        utils::force_kill(&mut self.runner);
    }
}
