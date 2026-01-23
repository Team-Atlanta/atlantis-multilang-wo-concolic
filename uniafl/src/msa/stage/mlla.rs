use glob::glob;
use libafl::{corpus::CorpusId, Error};
use libafl_bolts::{
    rands::{Rand, StdRand},
    Named,
};
use serde::{Deserialize, Serialize};
use std::process::{Command, Stdio};
use std::{
    borrow::Cow,
    path::PathBuf,
    sync::{Arc, RwLock},
};

use super::{
    stage::{is_first_run, need_run, set_interval},
    LoadStage, StageCounter,
};
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
use fuzzdb::LinePos;

#[derive(Serialize, Deserialize)]
pub struct MllaConf {
    given_corpus_dir: String,
    workdir: String,
    harness_name: String,
    core_ids: Vec<usize>,
    mlla_iter_cnt: u32,
    mlla_interval: u64,
}
pub struct MllaStage {
    harness_name: String,
    mlla_outdir: PathBuf,
    mlla_workdir: PathBuf,
    mlla_blob_dir: PathBuf,
    loader: LoadStage,
    counter: Arc<RwLock<StageCounter>>,
    blob_gen: Arc<RwLock<MllaBlobGen>>,
}

struct MllaBlobGen {
    mutators_dir: PathBuf,
    generators_dir: PathBuf,
    generator: Vec<MllaScript>,
    mutator: Vec<MllaScript>,
}

#[derive(Debug, Clone)]
struct MllaScript {
    name: String,
    path: String,
    src_func: Option<LinePos>,
    dst_func: Option<LinePos>,
}

impl MllaScript {
    pub fn load_script(
        script_path: &PathBuf,
        new_script_dir: &PathBuf,
        is_generator: bool,
    ) -> Option<Self> {
        let json_path = script_path.with_extension("json");
        if !json_path.exists() {
            return None;
        }
        let done_path = script_path.with_extension("done");
        if !done_path.exists() {
            return None;
        }
        std::fs::remove_file(&done_path).ok();
        let json_content = std::fs::read_to_string(&json_path).ok()?;
        let json_value: serde_json::Value = serde_json::from_str(&json_content).ok()?;
        let src_func = if let Some(src_func_json) = json_value.get("src_func") {
            LinePos::from_json(src_func_json)
        } else {
            None
        };
        let dst_func = if let Some(dst_func_json) = json_value.get("dst_func") {
            LinePos::from_json(dst_func_json)
        } else {
            None
        };

        let md5_hash = format!("{:x}", md5::compute(&std::fs::read(script_path).ok()?));

        let new_script_path = new_script_dir.join(format!("{}.py", md5_hash));
        let new_json_path = new_script_path.with_extension("json");
        std::fs::rename(&script_path, &new_script_path).ok()?;
        std::fs::rename(&json_path, &new_json_path).ok()?;
        Some(Self {
            name: format!(
                "mlla.{}.{}",
                if is_generator { "gen" } else { "mut" },
                md5_hash
            ),
            path: new_script_path.display().to_string(),
            src_func,
            dst_func,
        })
    }

    pub fn generate(&self, harness_name: &String, worker_idx: i32, num_blobs: u32) {
        std::process::Command::new("timeout")
            .args(["10", "run_mlla_gen.py", &self.path, &num_blobs.to_string()])
            .env("CUR_WORKER", format!("{}", worker_idx))
            .env("HARNESS_NAME", harness_name)
            .env(
                "MANAGER_LIB_PATH",
                std::env::current_exe()
                    .expect("Failed to get current exe path")
                    .parent()
                    .unwrap()
                    .join("libmanager.so"),
            )
            .output()
            .ok();
    }

    pub fn mutate(&self, harness_name: &String, worker_idx: i32, num_blobs: u32) {
        std::process::Command::new("timeout")
            .args(["10", "run_mlla_mut.py", &self.path, &num_blobs.to_string()])
            .env("CUR_WORKER", format!("{}", worker_idx))
            .env("HARNESS_NAME", harness_name)
            .env(
                "MANAGER_LIB_PATH",
                std::env::current_exe()
                    .expect("Failed to get current exe path")
                    .parent()
                    .unwrap()
                    .join("libmanager.so"),
            )
            .output()
            .ok();
    }
}

impl MllaBlobGen {
    pub fn new(workdir: &PathBuf, worker_cnt: usize) -> Self {
        let mutators_dir = workdir.join("mutators");
        let generators_dir = workdir.join("generators");
        std::fs::create_dir_all(&mutators_dir).ok();
        std::fs::create_dir_all(&generators_dir).ok();
        Self {
            mutators_dir,
            generators_dir,
            generator: Vec::new(),
            mutator: Vec::new(),
        }
    }

    fn load_scripts(
        &self,
        script_dir: &PathBuf,
        new_script_dir: &PathBuf,
        is_generator: bool,
    ) -> Vec<MllaScript> {
        match std::fs::read_dir(script_dir) {
            Ok(entries) => entries
                .into_iter()
                .filter_map(|entry| entry.ok())
                .filter_map(|entry| {
                    let path = entry.path();
                    match path.extension() {
                        Some(ext) if ext == "py" => {
                            MllaScript::load_script(&path, new_script_dir, is_generator)
                        }
                        _ => None,
                    }
                })
                .collect(),
            Err(_) => Vec::new(),
        }
    }

    pub fn load_mlla_scripts(&mut self, output_dir: &PathBuf) {
        let generator =
            self.load_scripts(&output_dir.join("generators"), &self.generators_dir, true);
        let mutator = self.load_scripts(&output_dir.join("mutators"), &self.mutators_dir, false);
        self.generator.extend(generator);
        self.mutator.extend(mutator);
    }

    fn pick_generator_by_acc_cov(
        &self,
        state: &Arc<UniState>,
        rand: &mut StdRand,
    ) -> Option<&MllaScript> {
        let generators: Vec<&MllaScript> = self
            .generator
            .iter()
            .filter(|generator| {
                if let Some(src_func) = &generator.src_func {
                    match state.acc_cov_in_src_range(&src_func.path, src_func.start, src_func.end) {
                        Some(true) => false, // skip if already covered
                        _ => true,
                    }
                } else {
                    false
                }
            })
            .collect();
        let len = generators.len();
        if len == 0 {
            None
        } else {
            let rng = rand.below(len);
            Some(generators[rng])
        }
    }

    fn pick_generator(
        &self,
        _msa_mgr: &MsaManager,
        _worker_idx: i32,
        state: &Arc<UniState>,
        rand: &mut StdRand,
    ) -> Option<&MllaScript> {
        let len = self.generator.len();
        if len == 0 {
            None
        } else {
            let prob = rand.below(100);
            if prob < 75 {
                let generator = self.pick_generator_by_acc_cov(state, rand);
                #[cfg(feature = "log")]
                _msa_mgr.log(
                    "pick_generator",
                    _worker_idx,
                    format!("Pick generator by acc cov: {:?}", generator),
                );
                if generator.is_some() {
                    return generator;
                }
            }
            let rng = rand.below(len);
            #[cfg(feature = "log")]
            _msa_mgr.log(
                "pick_generator",
                _worker_idx,
                format!("Pick generator by random: {:?}", &self.generator[rng]),
            );
            Some(&self.generator[rng])
        }
    }

    fn pick_mutator_by_cov(
        &self,
        state: &Arc<UniState>,
        rand: &mut StdRand,
        corpus_id: CorpusId,
    ) -> Option<&MllaScript> {
        let mutators: Vec<&MllaScript> = self
            .mutator
            .iter()
            .filter(|mutator| {
                if let Some(src_func) = &mutator.src_func {
                    match state.corpus_cov_in_src_range(
                        corpus_id,
                        &src_func.path,
                        src_func.start,
                        src_func.end,
                    ) {
                        Some(true) => true,
                        _ => false,
                    }
                } else {
                    false
                }
            })
            .collect();
        let len = mutators.len();
        if len == 0 {
            None
        } else {
            let rng = rand.below(len);
            Some(mutators[rng])
        }
    }

    fn pick_mutator(
        &self,
        _msa_mgr: &MsaManager,
        _worker_idx: i32,
        state: &Arc<UniState>,
        rand: &mut StdRand,
        corpus_id: CorpusId,
    ) -> Option<&MllaScript> {
        let len = self.mutator.len();
        if len == 0 {
            None
        } else {
            let prob = rand.below(100);
            if prob < 75 {
                let mutator = self.pick_mutator_by_cov(state, rand, corpus_id);
                #[cfg(feature = "log")]
                _msa_mgr.log(
                    "pick_mutator",
                    _worker_idx,
                    format!("Pick mutator by cov: {:?}", mutator),
                );
                if mutator.is_some() {
                    return mutator;
                }
            }
            let rng = rand.below(len);
            #[cfg(feature = "log")]
            _msa_mgr.log(
                "pick_mutator",
                _worker_idx,
                format!("Pick mutator by random: {:?}", &self.mutator[rng]),
            );
            Some(&self.mutator[rng])
        }
    }

    pub fn mutate(
        &self,
        msa_mgr: &MsaManager,
        state: &Arc<UniState>,
        worker_idx: i32,
        executor: &mut Executor,
    ) {
        if self.mutator.len() > 0 {
            msa_mgr.set_mode(worker_idx, ExecMode::ExecuteInput, false);
            if let Ok(corpus_id) =
                state.schedule_seed(msa_mgr, &mut executor.rand, worker_idx, false)
            {
                if let Some(mutator) =
                    self.pick_mutator(msa_mgr, worker_idx, state, &mut executor.rand, corpus_id)
                {
                    mutator.mutate(&msa_mgr.harness_name, worker_idx, msa_mgr.input_per_worker);
                    executor
                        .execute_loaded_inputs(&mutator.name, state, false)
                        .ok();
                }
            }
        }
    }
}

impl MllaStage {
    pub fn new(config_path: &PathBuf) -> Option<Self> {
        let name = "mlla";
        if InputGenClient::is_on_config(&name.to_string(), config_path, true) {
            let config = utils::load_json::<MllaConf>(config_path)
                .unwrap_or_else(|e| panic!("Error in load_json: {}", e));
            let workdir = PathBuf::from(config.workdir).join(name);
            let mlla_outdir = workdir.join("output");
            let mlla_workdir = workdir.join("workdir");
            let mlla_blob_dir = mlla_outdir.join("blobs");
            let mlla_blob_gen_workdir = workdir.join("mlla_blob_gen");
            std::fs::create_dir_all(&mlla_workdir).ok();
            std::fs::create_dir_all(&mlla_outdir).ok();
            std::fs::create_dir_all(&mlla_blob_dir).ok();
            std::fs::create_dir_all(&mlla_blob_gen_workdir).ok();
            Some(Self {
                harness_name: config.harness_name,
                mlla_outdir,
                mlla_workdir,
                mlla_blob_dir: mlla_blob_dir.clone(),
                loader: LoadStage::new_with(name, config_path, &mlla_blob_dir),
                blob_gen: Arc::new(RwLock::new(MllaBlobGen::new(
                    &mlla_blob_gen_workdir,
                    config.core_ids.len(),
                ))),
                counter: Arc::new(RwLock::new(StageCounter {
                    on: true,
                    is_first_run: true,
                    remain: config.mlla_iter_cnt - 1,
                    interval: config.mlla_interval,
                })),
            })
        } else {
            None
        }
    }

    fn get_generated_blobs(&self) -> Vec<PathBuf> {
        let pattern = format!("{}/*.done", self.mlla_blob_dir.display());
        if let Ok(entries) = glob(&pattern) {
            entries
                .into_iter()
                .filter_map(|entry| entry.ok())
                .map(|path| {
                    std::fs::remove_file(&path).ok();
                    path.with_extension("blob")
                })
                .filter(|path| path.exists())
                .collect()
        } else {
            Vec::new()
        }
    }

    fn need_run(&self) -> bool {
        need_run(&self.counter)
    }

    fn prepare_mlla_cmd(&self) -> String {
        let cmd = format!(
            "timeout 3h python -m mlla.main --cp /src --harness '{}' --workdir '{}' --output '{}'",
            &self.harness_name,
            self.mlla_workdir.display(),
            self.mlla_outdir.display(),
        );
        let cmd = if is_first_run(&self.counter) {
            format!("{} --agent generator", cmd)
        } else {
            cmd
        };

        let cmd = match std::env::var("CODE_INDEXER_REDIS_URL") {
            Ok(redis) => format!("{} --redis {}", cmd, redis),
            _ => cmd,
        };
        format!("{} >> {}/log 2>&1", cmd, self.mlla_workdir.display())
    }

    fn run_mlla(cmd: &String, worker_idx: i32) {
        Command::new("bash")
            .args(["-c", cmd.as_str()])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .stdin(Stdio::piped())
            .current_dir("/home/crs/blob-gen/multilang-llm-agent")
            .env("CUR_WORKER", format!("{}", worker_idx))
            .output()
            .ok();
    }

    fn get_bcda_result(&self) -> Option<PathBuf> {
        let pattern = format!("{}/bcda/*.json*", self.mlla_outdir.display());
        if let Ok(entries) = glob(&pattern) {
            for entry in entries {
                match entry {
                    Ok(path) => {
                        let done_path = path.with_extension("done");
                        if done_path.exists() {
                            std::fs::remove_file(&done_path).ok();
                            return Some(path);
                        }
                    }
                    Err(_) => return None,
                }
            }
        }
        None
    }

    fn load_mlla_result(
        &self,
        msa_mgr: &MsaManager,
        state: &Arc<UniState>,
        worker_idx: i32,
        executor: &mut Executor,
    ) {
        if let Some(bcda_result) = self.get_bcda_result() {
            if state.load_bcda_result(&bcda_result).is_some() {
                // Successfully loaded bcda result, remove the file
                #[cfg(feature = "log")]
                msa_mgr.log(
                    self.name(),
                    worker_idx,
                    format!("Load bcda result : {}", bcda_result.display()),
                );
                std::fs::remove_file(&bcda_result).ok();
            }
        }
        {
            let mut blob_gen = self.blob_gen.write().unwrap();
            blob_gen.load_mlla_scripts(&self.mlla_outdir);
        }
        #[cfg(feature = "log")]
        msa_mgr.log(self.name(), worker_idx, "Load blobs from mlla".to_string());

        #[cfg(feature = "log")]
        msa_mgr.log(
            self.name(),
            worker_idx,
            format!("Generator: {:?}", self.blob_gen.read().unwrap().generator),
        );
        #[cfg(feature = "log")]
        msa_mgr.log(
            self.name(),
            worker_idx,
            format!("Mutator: {:?}", self.blob_gen.read().unwrap().mutator),
        );

        let blobs = self.get_generated_blobs();
        self.loader
            .load_and_perform(msa_mgr, state, worker_idx, executor, &blobs)
            .ok();
    }

    pub fn generate(
        &self,
        msa_mgr: &MsaManager,
        state: &Arc<UniState>,
        worker_idx: i32,
        executor: &mut Executor,
    ) {
        let generator = {
            self.blob_gen
                .read()
                .unwrap()
                .pick_generator(msa_mgr, worker_idx, state, &mut executor.rand)
                .cloned()
        };
        if let Some(generator) = generator {
            msa_mgr.set_mode(worker_idx, ExecMode::ExecuteInput, false);
            generator.generate(&msa_mgr.harness_name, worker_idx, msa_mgr.input_per_worker);
            executor
                .execute_loaded_inputs(&generator.name, state, false)
                .ok();
        }
    }

    pub fn mutate(
        &self,
        msa_mgr: &MsaManager,
        state: &Arc<UniState>,
        worker_idx: i32,
        executor: &mut Executor,
    ) {
        let mutator = {
            let blob_gen = self.blob_gen.read().unwrap();
            if blob_gen.mutator.len() > 0 {
                msa_mgr.set_mode(worker_idx, ExecMode::ExecuteInput, false);
                if let Ok(corpus_id) =
                    state.schedule_seed(msa_mgr, &mut executor.rand, worker_idx, false)
                {
                    blob_gen
                        .pick_mutator(msa_mgr, worker_idx, state, &mut executor.rand, corpus_id)
                        .cloned()
                } else {
                    None
                }
            } else {
                None
            }
        };
        if let Some(mutator) = mutator {
            mutator.mutate(&msa_mgr.harness_name, worker_idx, msa_mgr.input_per_worker);
            executor
                .execute_loaded_inputs(&mutator.name, state, false)
                .ok();
        }
    }
}

impl Named for MllaStage {
    fn name(&self) -> &Cow<'static, str> {
        self.loader.name()
    }
}

impl MsaStage for MllaStage {
    fn perform(
        &self,
        msa_mgr: &MsaManager,
        state: &Arc<UniState>,
        worker_idx: i32,
        executor: &mut Executor,
    ) -> Result<bool, Error> {
        if self.need_run() {
            let cmd = self.prepare_mlla_cmd();
            let runner = std::thread::spawn(move || Self::run_mlla(&cmd, worker_idx));
            while !runner.is_finished() {
                std::thread::sleep(std::time::Duration::from_secs(5));
                self.load_mlla_result(msa_mgr, state, worker_idx, executor);
            }
            runner.join().ok();
            set_interval(&self.counter, true);
            self.load_mlla_result(msa_mgr, state, worker_idx, executor);
        }
        self.generate(msa_mgr, state, worker_idx, executor);
        self.mutate(msa_mgr, state, worker_idx, executor);

        Ok(true) // Note: This is special case always returns true.
    }
}
