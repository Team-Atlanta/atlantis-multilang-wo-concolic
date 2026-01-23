use libafl::{
    corpus::{CorpusId, Testcase},
    inputs::{BytesInput, HasMutatorBytes},
    Error,
};
use libafl_bolts::rands::StdRand;
use std::{collections::HashMap, path::PathBuf, sync::RwLock};

use super::{
    corpus::UniCorpus,
    manager::{MsaInput, MsaManager},
    scheduler::UniScheduler,
};
use crate::executor::{CovObserver, CrashObserver, Executor};

pub type UniInput = BytesInput;
pub struct UniState {
    harness_name: String,
    corpus: RwLock<UniCorpus>,
    solutions: RwLock<UniCorpus>,
    scheduler: RwLock<UniScheduler>,
    cov_observer: RwLock<CovObserver>,
    testlang_cov_observer: RwLock<CovObserver>,
    crash_observer: RwLock<CrashObserver>,
}

impl UniState {
    pub fn new(
        config_path: &PathBuf,
        harness_name: &str,
        corpus_dir: &PathBuf,
        pov_dir: &PathBuf,
    ) -> Self {
        Self {
            harness_name: harness_name.to_owned(),
            corpus: RwLock::new(UniCorpus::new(corpus_dir)),
            solutions: RwLock::new(UniCorpus::new(pov_dir)),
            scheduler: RwLock::new(UniScheduler::new(config_path)),
            cov_observer: RwLock::new(CovObserver::new()),
            testlang_cov_observer: RwLock::new(CovObserver::new()),
            crash_observer: RwLock::new(CrashObserver::new()),
        }
    }

    fn submit_pov(&self, finder: &str, pov_path: &PathBuf, crash_log: &[u8]) {
        self.scheduler
            .write()
            .unwrap()
            .notify_cpv_found(pov_path, crash_log);
        let crash_log = String::from_utf8_lossy(crash_log);
        let finder = format!("UniAFL.{}", finder);
        let args = [
            "-m",
            "libCRS.submit",
            "submit_vd",
            "--harness",
            &self.harness_name,
            "--pov",
            &pov_path.display().to_string(),
            "--sanitizer-output",
            &crash_log,
            "--finder",
            &finder,
        ];
        std::process::Command::new("python")
            .args(args)
            .output()
            .ok();
    }

    pub fn schedule_seed(
        &self,
        msa_mgr: &MsaManager,
        rand: &mut StdRand,
        worker_idx: i32,
        is_testlang_stage: bool,
    ) -> Result<CorpusId, Error> {
        msa_mgr.clear_seed(worker_idx);
        let corpus = self.corpus.read().unwrap();
        let corpus_id = self
            .scheduler
            .read()
            .unwrap()
            .next(&corpus, rand, is_testlang_stage)?;
        let tc = corpus.get(corpus_id)?;
        msa_mgr.set_seed(
            worker_idx,
            corpus_id,
            tc.filename(),
            tc.input().as_ref().unwrap().bytes(),
        );
        #[cfg(feature = "log")]
        msa_mgr.log(
            "UniAFL.schedule_seed",
            worker_idx,
            format!(
                "Scheduled seed, is_testlang_stage: {}, id: {}, fname: {}",
                is_testlang_stage,
                corpus_id,
                tc.filename().as_ref().unwrap().to_string()
            ),
        );

        Ok(corpus_id)
    }

    pub fn add_if_interesting_seed(
        &self,
        stage_name: &str,
        executor: &mut Executor,
        msa_inputs: Vec<MsaInput>,
        is_testlang_stage: bool,
    ) {
        let mut tmp_seed_map = HashMap::new();
        self.__add_if_interesting_seed(stage_name, executor, &msa_inputs, &mut tmp_seed_map, false);
        if is_testlang_stage {
            self.__add_if_interesting_seed(
                stage_name,
                executor,
                &msa_inputs,
                &mut tmp_seed_map,
                true,
            );
        }
    }

    fn __add_if_interesting_seed(
        &self,
        stage_name: &str,
        executor: &mut Executor,
        msa_inputs: &[MsaInput],
        tmp_seed_map: &mut HashMap<i32, CorpusId>,
        is_testlang_stage: bool,
    ) {
        let interesting_seeds = {
            let cov_observer = if is_testlang_stage {
                self.testlang_cov_observer.read().unwrap()
            } else {
                self.cov_observer.read().unwrap()
            };
            // Note that these are candidates
            msa_inputs
                .iter()
                .cloned()
                .filter(|i| cov_observer.is_interesting(i.get_cov()))
                .collect()
        };
        self.process_new_seeds(
            stage_name,
            executor,
            interesting_seeds,
            tmp_seed_map,
            is_testlang_stage,
        );
    }

    pub fn add_if_interesting_crash(
        &self,
        stage_name: &str,
        executor: &mut Executor,
        msa_inputs: Vec<MsaInput>,
    ) {
        let mut candidates = Vec::new();
        {
            let crash_observer = self.crash_observer.read().unwrap();
            // Note that these are candidates
            for msa_input in msa_inputs {
                if let Some(log) = msa_input.get_crash_log() {
                    if crash_observer.is_interesting(log) {
                        candidates.push((msa_input, log));
                    }
                } else {
                    candidates.push((msa_input, &[]));
                }
            }
        }

        let mut interesting = Vec::new();
        let mut local_crash_observer = CrashObserver::new();
        for (msa_input, log) in candidates {
            if !local_crash_observer.is_interesting(log) {
                continue;
            }
            let bytes = msa_input.bytes();
            if let Some((parsed_log, reproduce_log)) = executor.run_pov(bytes) {
                if local_crash_observer.add_log(&parsed_log) {
                    interesting.push((msa_input, parsed_log, reproduce_log));
                }
            }
        }

        self.process_povs(stage_name, executor, interesting);
    }

    fn process_new_seeds(
        &self,
        stage_name: &str,
        executor: &mut Executor,
        interesting_seeds: Vec<MsaInput>,
        tmp_seed_map: &mut HashMap<i32, CorpusId>,
        is_testlang_stage: bool,
    ) {
        if interesting_seeds.is_empty() {
            return;
        }
        let mut for_saving = Vec::new();
        {
            let mut corpus = self.corpus.write().unwrap();
            let mut cov_observer = if is_testlang_stage {
                self.testlang_cov_observer.write().unwrap()
            } else {
                self.cov_observer.write().unwrap()
            };
            for seed in interesting_seeds {
                let cov = seed.get_cov();
                if !cov_observer.update_cov(cov) {
                    continue;
                }
                let idx = seed.idx();
                if let Some(corpus_id) = tmp_seed_map.get(&idx) {
                    let tc = corpus.get(*corpus_id).unwrap();
                    let fpath = tc.file_path().clone().unwrap();
                    for_saving.push((*corpus_id, fpath, cov, false));
                } else {
                    let input = UniInput::from(seed.bytes());
                    let tc = Testcase::new(input);
                    if let Ok((corpus_id, is_new)) = corpus.add(stage_name, tc) {
                        tmp_seed_map.insert(idx, corpus_id);
                        let tc = corpus.get(corpus_id).unwrap();
                        let fpath = tc.file_path().clone().unwrap();
                        for_saving.push((corpus_id, fpath, cov, is_new));
                        seed.set_is_interesting(corpus_id);
                    }
                }
            }
        }

        for (corpus_id, fpath, cov, is_new) in for_saving {
            if is_new {
                executor.save_coverage(&fpath, cov);
            }
            let fname = fpath.file_name().unwrap().to_str().unwrap().to_string();
            {
                self.scheduler
                    .write()
                    .unwrap()
                    .update(corpus_id, &fname, cov, is_testlang_stage);
            }
        }
    }

    pub fn try_add_dummy_seeds(&self, msa_mgr: &MsaManager, worker_idx: i32, dummys: Vec<Vec<u8>>) {
        let need_add = { self.corpus.read().unwrap().count() == 0 };
        if need_add {
            let covs = msa_mgr.run_once(worker_idx, &dummys);
            let mut corpus = self.corpus.write().unwrap();
            let mut cov_observer = self.cov_observer.write().unwrap();
            let mut scheduler = self.scheduler.write().unwrap();
            let mut idx = 0;
            for (raw_cov_file, line_cov_file) in covs {
                if let Ok(cov) = std::fs::read(&raw_cov_file) {
                    let cov: Vec<u64> = cov
                        .chunks_exact(std::mem::size_of::<u64>())
                        .map(|chunk| u64::from_le_bytes(chunk.try_into().unwrap()))
                        .collect();
                    if !cov_observer.update_cov(&cov) {
                        continue;
                    }
                    let input = UniInput::from(dummys[idx].as_slice());
                    let tc = Testcase::new(input);
                    if let Ok((corpus_id, true)) = corpus.add("dummy", tc) {
                        let tc = corpus.get(corpus_id).unwrap();
                        let fname = tc.filename().clone().unwrap();
                        scheduler.update(corpus_id, &fname, &cov, false);
                        std::fs::rename(&raw_cov_file, msa_mgr.get_raw_cov_path(&fname)).ok();
                        std::fs::rename(&line_cov_file, msa_mgr.get_cov_path(&fname)).ok();
                    }
                }
                idx += 1;
            }
        }
    }

    fn process_povs(
        &self,
        stage_name: &str,
        executor: &mut Executor,
        povs: Vec<(MsaInput, Vec<u8>, Vec<u8>)>,
    ) {
        if povs.is_empty() {
            return;
        }
        let mut for_submit = Vec::new();
        {
            let mut solutions = self.solutions.write().unwrap();
            let mut crash_observer = self.crash_observer.write().unwrap();
            for (pov, log, raw_log) in povs {
                if !crash_observer.add_log(&log) {
                    continue;
                }
                let input = UniInput::from(pov.bytes());
                let tc = Testcase::new(input);
                if let Ok((id, true)) = solutions.add(stage_name, tc) {
                    let tc = solutions.get(id).unwrap();
                    if let Some(path) = tc.file_path() {
                        for_submit.push((path.clone(), log, pov.get_cov(), raw_log));
                    }
                }
            }
        }
        for (path, log, cov, raw_log) in for_submit {
            let in_corpus = {
                let corpus = self.corpus.read().unwrap();
                corpus.has_filename(&path)
            };
            if !in_corpus {
                executor.save_coverage(&path, cov);
            }
            executor.save_crash_log(&path, &raw_log);
            executor.save_call_stack(&path, &raw_log);
            self.submit_pov(stage_name, &path, &log);
        }
    }

    pub fn load_bcda_result(&self, path: &PathBuf) -> Option<bool> {
        let mut scheduler = self.scheduler.write().unwrap();
        scheduler.load_bcda_result(path)
    }

    pub fn corpus_cov_in_src_range(
        &self,
        corpus_id: CorpusId,
        src_path: &String,
        start: u32,
        end: u32,
    ) -> Option<bool> {
        let corpus = self.corpus.read().ok()?;
        let tc = corpus.get(corpus_id).ok()?;
        let fname = tc.filename().as_ref()?;
        let cov = self.scheduler.read().ok()?.db.load_cov(&fname)?;
        Some(cov.has_src_cov_in_range(src_path, start, end))
    }

    pub fn acc_cov_in_src_range(&self, src_path: &String, start: u32, end: u32) -> Option<bool> {
        let scheduler = self.scheduler.read().ok()?;
        Some(scheduler.db.has_acc_src_cov_in_range(src_path, start, end))
    }

    #[cfg(test)]
    fn check_corpus_coverage_with_db(&self, db: &fuzzdb::FuzzDB, corpus: &UniCorpus, is_pov: bool) {
        let testcases = corpus.testcases();
        if !is_pov {
            assert!(!testcases.is_empty());
        }
        let mut failed_testcases = Vec::new();
        for tc in testcases {
            let fname = tc.filename().as_ref().unwrap();
            let cov = db.load_cov(fname).unwrap();
            if is_pov {
                if cov.func_names().count() == 0 {
                    failed_testcases.push(fname.clone());
                }
            } else {
                if !cov.func_names().any(|fname| {
                    fname.contains("fuzzerTestOneInput")
                        || fname.contains("LLVMFuzzerTestOneInput")
                        || fname.contains("DEFINE_PROTO_FUZZER")
                }) {
                    failed_testcases.push(fname.clone());
                }
            }
        }
        if !failed_testcases.is_empty() {
            panic!(
                "Some {} did not meet coverage requirements:\n{}",
                if is_pov { "POVs" } else { "seeds" },
                failed_testcases.join("\n")
            );
        }
    }
    #[cfg(test)]
    pub fn check_corpus_coverage(&self, config_path: &PathBuf, is_pov: bool) {
        let db = fuzzdb::FuzzDB::new(config_path);
        let corpus = if is_pov {
            self.solutions.read().unwrap()
        } else {
            self.corpus.read().unwrap()
        };
        self.check_corpus_coverage_with_db(&db, &corpus, is_pov);
    }

    #[cfg(test)]
    pub fn check_coverage(&self, config_path: &PathBuf) {
        let db = fuzzdb::FuzzDB::new(config_path);
        let corpus = self.corpus.read().unwrap();
        self.check_corpus_coverage_with_db(&db, &corpus, false);
        let solutions = self.solutions.read().unwrap();
        self.check_corpus_coverage_with_db(&db, &solutions, true);
    }
}
