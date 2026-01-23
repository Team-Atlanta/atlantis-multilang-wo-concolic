use std::{
    collections::{HashMap, HashSet},
    env,
    fs::{self, File, OpenOptions},
    path::{Path, PathBuf},
    process::Command,
    sync::{
        atomic::{self, AtomicBool, AtomicI64, AtomicUsize},
        Arc,
    },
    thread::{self, JoinHandle},
};

use chrono::Utc;
use dashmap::{DashMap, DashSet};
use fslock::LockFile;
use moka::sync::Cache;
use testlang::TestLang;

use crate::{common::Error, input_gen::testlang::service::pool::TestLangLocalId};

pub type TestLangStorage = Cache<usize, Arc<TestLang>>;
pub type CorpusToTestLangMap = Arc<DashMap<String, TestLangLocalId>>;
pub type TestLangSet = Arc<DashSet<TestLangLocalId>>;

const RERUN_DELAY_MS: i64 = 1000 * 60 * 30;
const EARLY_RERUN_DELAY_MS: i64 = 1000 * 60 * 10;

#[derive(Clone)]
pub struct HarnessReverser {
    bin_path: PathBuf,
    config_path: PathBuf,
    testlang_cores: usize,
    work_dir: PathBuf,
    codegen_dir: PathBuf,
    corpus_map_path: PathBuf,
    used_testlangs_path: PathBuf,
    deprioritized_testlangs_path: PathBuf,
    pub lock_path: PathBuf,
    pub outputs: PathBuf,
    testlangs: TestLangStorage,
    rerun_state: Arc<HarnessReverserRerunState>,
    corpus_map: CorpusToTestLangMap,
    used_testlangs: TestLangSet,
    deprioritized_testlangs: TestLangSet,
}

impl HarnessReverser {
    pub fn new(
        config_path: impl AsRef<Path>,
        module_path: impl AsRef<Path>,
        work_dir: impl AsRef<Path>,
        testlang_cores: usize,
    ) -> Self {
        let work_dir = work_dir.as_ref().join("harness-reverser");
        let codegen_dir = work_dir.join("processors");
        let lock_path = work_dir.join("testlang.lock");
        let corpus_map_path = work_dir.join("corpus_map.json");
        let used_testlangs_path = work_dir.join("used_testlangs.json");
        let deprioritized_testlangs_path = work_dir.join("deprioritized_testlangs.json");
        let outputs = work_dir.join("intermediate_outputs");
        HarnessReverser {
            bin_path: module_path.as_ref().join("run.py"),
            config_path: config_path.as_ref().into(),
            testlang_cores,
            work_dir,
            codegen_dir,
            corpus_map_path,
            used_testlangs_path,
            deprioritized_testlangs_path,
            lock_path,
            outputs,
            testlangs: TestLangStorage::new(1024),
            rerun_state: Arc::new(HarnessReverserRerunState::new(0, 10)),
            corpus_map: Arc::new(DashMap::new()),
            used_testlangs: Arc::new(DashSet::new()),
            deprioritized_testlangs: Arc::new(DashSet::new()),
        }
    }

    pub fn run_on_side(
        &self,
        wait_for_used_testlangs: bool,
        extra_context: Option<String>,
    ) -> Result<JoinHandle<Result<(), Error>>, Error> {
        let bin_path = self.bin_path.clone();
        let config_path = self.config_path.clone();
        let work_dir = self.work_dir.clone();
        let testlang_cores = self.testlang_cores;
        let codegen_dir = self.codegen_dir.clone();
        let corpus_map_path = self.corpus_map_path.clone();
        let used_testlangs_path = self.used_testlangs_path.clone();
        let deprioritized_testlangs_path = self.deprioritized_testlangs_path.clone();
        let lock_path = self.lock_path.clone();
        let outputs = self.outputs.clone();

        // This includes `work_dir`.
        fs::create_dir_all(&outputs)?;
        fs::create_dir_all(&codegen_dir)?;
        File::create(&lock_path)?;
        self.push_corpus_map();
        self.push_used_testlangs();

        let handle = thread::spawn(move || {
            let mut err_log = String::new();
            let extra_context_path = extra_context.and_then(|c| {
                let extra_context_path = work_dir.join("extra_context");
                fs::write(&extra_context_path, c).ok()?;
                Some(extra_context_path)
            });

            for i in 0..10 {
                let mut cmd = Command::new("python");
                cmd.args([
                    &bin_path.to_string_lossy(),
                    "--config-path",
                    &config_path.to_string_lossy(),
                    "--workdir",
                    &work_dir.to_string_lossy(),
                    "--codegendir",
                    &codegen_dir.to_string_lossy(),
                    "--corpus-map",
                    &corpus_map_path.to_string_lossy(),
                    "--used-testlangs",
                    &used_testlangs_path.to_string_lossy(),
                    "--deprioritized-testlangs",
                    &deprioritized_testlangs_path.to_string_lossy(),
                    "--outputs",
                    &outputs.to_string_lossy(),
                    "--lock",
                    &lock_path.to_string_lossy(),
                ]);
                if let Some(ref extra_context_path) = extra_context_path {
                    cmd.arg("--extra").arg(extra_context_path);
                }
                // Check CI test / small ncpus.
                let used_testlangs_timeout = if wait_for_used_testlangs && testlang_cores > 1 {
                    env::var("TEST_TIMEOUT").map(|_| 180).unwrap_or(600)
                } else {
                    0
                };
                cmd.arg("--used-testlangs-timeout")
                    .arg(used_testlangs_timeout.to_string());
                let cmd_output = match cmd.output() {
                    Ok(cmd_output) => cmd_output,
                    Err(err) => {
                        err_log += &format!(
                                "[HarnessReverser::run]\n[Try #{i}]\nFailed to run Harness-Reverser:\n{err}\n",
                            );
                        continue;
                    }
                };
                if cmd_output.status.success() {
                    return Ok(());
                } else {
                    err_log += &format!(
                        "[HarnessReverser::run]\n[Try #{i}]\n[stdout]\n{}\n[stderr]\n{}\n",
                        String::from_utf8_lossy(&cmd_output.stdout),
                        String::from_utf8_lossy(&cmd_output.stderr),
                    );
                }
            }
            Err(Error::testlang_error(err_log))
        });
        Ok(handle)
    }

    pub fn get_testlang_path_from_id(&self, id: TestLangLocalId) -> PathBuf {
        self.work_dir.join(format!("testlang_{}.out", id))
    }

    pub fn get_testlang_codegen_path_from_id(&self, id: TestLangLocalId) -> PathBuf {
        self.codegen_dir.join(id.to_string())
    }

    pub fn get_testlang_from_id(&self, id: TestLangLocalId) -> Option<Arc<TestLang>> {
        self.testlangs
            .try_get_with(id, || {
                let testlang_path = self.get_testlang_path_from_id(id);
                TestLang::from_file(testlang_path).map(Arc::new)
            })
            .ok()
    }

    pub fn get_testlang_count(&self) -> usize {
        self.rerun_state.get_testlang_count()
    }

    pub fn insert_corpus_map_entry(&self, corpus_id: String, testlang_id: TestLangLocalId) {
        self.corpus_map.insert(corpus_id, testlang_id);
        self.rerun_state.mark_corpus_map_update();
    }

    pub fn insert_used_testlang_entry(&self, testlang_id: TestLangLocalId) {
        if self.used_testlangs.insert(testlang_id) {
            self.rerun_state.mark_used_testlangs_update();
        }
    }

    pub fn pick_unused_testlang_id(&self) -> Option<TestLangLocalId> {
        let testlang_cnt = self.get_testlang_count();
        if self.used_testlangs.len() == testlang_cnt || 0 == testlang_cnt {
            return None;
        }
        let all_testlangs: HashSet<_> = (0..testlang_cnt).collect();
        let used_testlangs: HashSet<_> = self.used_testlangs.iter().map(|i| *i.key()).collect();
        let mut unused_testlangs: Vec<_> =
            all_testlangs.difference(&used_testlangs).copied().collect();
        unused_testlangs.sort();
        unused_testlangs.last().copied()
    }

    pub fn get_testlang_weighted_sequence(&self) -> Vec<TestLangLocalId> {
        let testlang_cnt = self.get_testlang_count();
        let all_testlangs: HashSet<_> = (0..testlang_cnt).collect();
        let deprioritized_testlangs: HashSet<_> = self
            .deprioritized_testlangs
            .iter()
            .map(|i| *i.key())
            .collect();
        let mut weighted_testlangs: Vec<_> = all_testlangs
            .difference(&deprioritized_testlangs)
            .copied()
            .collect();
        weighted_testlangs.extend(0..testlang_cnt);
        weighted_testlangs
    }

    pub fn sync_with_reverser(&self) {
        let Ok(mut lock) = LockFile::open(&self.lock_path) else {
            return;
        };
        if lock.lock().is_err() {
            return;
        };
        self.pull_testlang();
        self.pull_deprioritized_testlangs();
        self.push_corpus_map();
        self.push_used_testlangs();
    }

    fn pull_testlang(&self) {
        let Ok(outputs) = self.outputs.read_dir() else {
            return;
        };

        let outputs: Vec<_> = outputs
            .filter_map(|x| match x {
                Ok(output) if output.file_type().map(|x| x.is_file()).unwrap_or(false) => {
                    Some(output.path())
                }
                _ => None,
            })
            .collect();

        for output in outputs {
            if let Ok(testlang) = TestLang::from_file(&output) {
                let id = self.rerun_state.get_testlang_count();
                let finalized_path = self.get_testlang_path_from_id(id);
                let codegen_ok = match output.file_stem() {
                    Some(discriminator) => {
                        let codegen_path = self.codegen_dir.join(discriminator);
                        let finalized_codegen_path = self.get_testlang_codegen_path_from_id(id);
                        !codegen_path.exists()
                            || fs::rename(codegen_path, finalized_codegen_path).is_ok()
                    }
                    None => true,
                };
                if codegen_ok && fs::rename(&output, finalized_path).is_ok() {
                    self.testlangs.insert(id, Arc::new(testlang));
                    self.rerun_state.get_next_id();
                    continue;
                }
            }
            let _ = fs::remove_file(output);
        }
    }

    fn pull_deprioritized_testlangs(&self) {
        if let Ok(file) = OpenOptions::new().read(true).open(&self.corpus_map_path) {
            if let Ok(deprioritized_testlangs) =
                serde_json::from_reader::<_, Vec<TestLangLocalId>>(file)
            {
                for id in deprioritized_testlangs {
                    self.deprioritized_testlangs.insert(id);
                }
            }
        }
    }

    fn push_corpus_map(&self) {
        if !self.rerun_state.take_corpus_map_update() {
            return;
        }

        if let Ok(file) = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&self.corpus_map_path)
        {
            let corpus_map_owned: HashMap<_, _> = self
                .corpus_map
                .iter()
                .map(|i| (i.key().to_owned(), *i.value()))
                .collect();
            let _ = serde_json::to_writer(file, &corpus_map_owned);
        }
    }

    fn push_used_testlangs(&self) {
        if !self.rerun_state.take_used_testlangs_update() {
            return;
        }

        if let Ok(file) = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&self.used_testlangs_path)
        {
            let used_testlangs_owned: HashSet<_> =
                self.used_testlangs.iter().map(|i| *i.key()).collect();
            let _ = serde_json::to_writer(file, &used_testlangs_owned);
        }
    }

    pub fn rerun_complete(&self) {
        self.rerun_state.rerun_complete();
    }

    pub fn evaluate_rerun(&self) -> bool {
        self.rerun_state.evaluate_rerun()
    }
}

pub struct HarnessReverserRerunState {
    last_rerun: AtomicI64,
    early_rerun_time: AtomicI64,
    early_rerun_credit: AtomicUsize,
    next_testlang_id: AtomicUsize,
    corpus_map_updated: AtomicBool,
    used_testlangs_updated: AtomicBool,
}

impl HarnessReverserRerunState {
    pub fn new(testlang_start_id: TestLangLocalId, early_rerun_credit: usize) -> Self {
        Self {
            last_rerun: AtomicI64::new(i64::MAX),
            early_rerun_time: AtomicI64::new(i64::MAX),
            early_rerun_credit: AtomicUsize::new(early_rerun_credit),
            next_testlang_id: AtomicUsize::new(testlang_start_id),
            corpus_map_updated: AtomicBool::new(true),
            used_testlangs_updated: AtomicBool::new(true),
        }
    }

    pub fn get_next_id(&self) -> TestLangLocalId {
        self.next_testlang_id.fetch_add(1, atomic::Ordering::SeqCst)
    }

    pub fn get_testlang_count(&self) -> TestLangLocalId {
        self.next_testlang_id.load(atomic::Ordering::SeqCst)
    }

    pub fn evaluate_rerun(&self) -> bool {
        let now = Utc::now().timestamp_millis();
        let last_rerun = self.last_rerun.swap(i64::MAX, atomic::Ordering::SeqCst);
        if last_rerun == i64::MAX {
            // Safe because you swapped i64::MAX with i64::MAX.
            return false;
        }

        // Critical section && last_rerun < i64::MAX
        if now <= last_rerun.saturating_add(RERUN_DELAY_MS) {
            let early_rerun_allowed = self.early_rerun_credit.load(atomic::Ordering::SeqCst) > 0;
            let early_rerun_elapsed = self.early_rerun_time.load(atomic::Ordering::SeqCst) < now;
            if early_rerun_allowed && early_rerun_elapsed {
                self.early_rerun_credit
                    .fetch_sub(1, atomic::Ordering::SeqCst);
            } else {
                self.last_rerun
                    .fetch_min(last_rerun, atomic::Ordering::SeqCst);
                return false;
            }
        }
        true
    }

    pub fn rerun_complete(&self) {
        let now = Utc::now().timestamp_millis();
        let early_rerun_time = now + EARLY_RERUN_DELAY_MS;
        self.last_rerun.store(now, atomic::Ordering::SeqCst);
        self.early_rerun_time
            .store(early_rerun_time, atomic::Ordering::SeqCst);
    }

    pub fn mark_corpus_map_update(&self) {
        let now = Utc::now().timestamp_millis();
        let early_rerun_time = now + EARLY_RERUN_DELAY_MS;
        self.corpus_map_updated
            .store(true, atomic::Ordering::SeqCst);
        self.early_rerun_time
            .store(early_rerun_time, atomic::Ordering::SeqCst);
    }

    pub fn take_corpus_map_update(&self) -> bool {
        self.corpus_map_updated
            .compare_exchange(
                true,
                false,
                atomic::Ordering::SeqCst,
                atomic::Ordering::SeqCst,
            )
            .is_ok()
    }

    pub fn mark_used_testlangs_update(&self) {
        self.used_testlangs_updated
            .store(true, atomic::Ordering::SeqCst);
    }

    pub fn take_used_testlangs_update(&self) -> bool {
        self.used_testlangs_updated
            .compare_exchange(
                true,
                false,
                atomic::Ordering::SeqCst,
                atomic::Ordering::SeqCst,
            )
            .is_ok()
    }
}
