use std::{
    mem,
    path::{Path, PathBuf},
    process::{Child, Command},
    sync::Arc,
    thread::{sleep, JoinHandle},
    time::Duration,
};

#[cfg(feature = "log")]
use chrono::{FixedOffset, Utc};

use customgen::CustomGenerator;
use libafl::{
    corpus::CorpusId,
    inputs::{BytesInput, Input},
    state::{HasMaxSize, HasRand},
};
use libafl_bolts::{
    current_nanos,
    rands::{Rand, StdRand},
};
use tempfile::{tempdir, TempDir};
use testlang::TestLangAst;
use tokio::runtime::Runtime;

use crate::{
    common::Error as UniaflError,
    input_gen::{
        server::{InputGenWorker, Output, Outputs},
        testlang::{
            generators::{testlang_generators, TestLangGenerators},
            mutators::{
                testlang_ast_free_mutators, testlang_mutators, TestLangAstFreeMutators,
                TestLangMutators,
            },
            service::pool::TestLangLocalId,
        },
    },
    msa::manager::MsaSeed,
};

#[cfg(feature = "log")]
use crate::input_gen::testlang::node_to_bytes;

use super::{
    pool::{AstStorage, TestLangHandle},
    reverser::HarnessReverser,
};

pub struct TestLangState {
    pub testlang: TestLangHandle,
    pub codegen_path: PathBuf,
    pub customgen_runtime: Option<CustomGenRuntime>,
    pub rand: StdRand,
    pub gen_only_valid: bool,
    max_bytes_size: usize,
    #[cfg(feature = "log")]
    pub log: String,
}

impl TestLangState {
    pub fn new(
        testlang: TestLangHandle,
        codegen_path: impl AsRef<Path>,
        rand: StdRand,
        max_bytes_size: usize,
    ) -> Self {
        Self {
            testlang,
            codegen_path: codegen_path.as_ref().to_owned(),
            customgen_runtime: CustomGenRuntime::new().ok(),
            rand,
            gen_only_valid: false,
            max_bytes_size,
            #[cfg(feature = "log")]
            log: String::new(),
        }
    }

    #[cfg(feature = "log")]
    pub fn log(&mut self, message: String) {
        let kst = FixedOffset::east_opt(9 * 3600).unwrap();
        let kst_time = Utc::now().with_timezone(&kst);
        self.log.push_str(&format!(
            "[{}] {}\n",
            kst_time.format("%Y-%m-%d %H:%M:%S"),
            message
        ));
    }
}

impl Clone for TestLangState {
    fn clone(&self) -> Self {
        Self {
            testlang: self.testlang.clone(),
            codegen_path: self.codegen_path.clone(),
            customgen_runtime: CustomGenRuntime::new().ok(),
            rand: self.rand,
            gen_only_valid: self.gen_only_valid,
            max_bytes_size: self.max_bytes_size,
            #[cfg(feature = "log")]
            log: self.log.clone(),
        }
    }
}

impl HasRand for TestLangState {
    type Rand = StdRand;

    fn rand(&self) -> &Self::Rand {
        &self.rand
    }

    fn rand_mut(&mut self) -> &mut Self::Rand {
        &mut self.rand
    }
}

impl HasMaxSize for TestLangState {
    fn max_size(&self) -> usize {
        self.max_bytes_size
    }

    fn set_max_size(&mut self, max_size: usize) {
        self.max_bytes_size = max_size
    }
}

pub struct CustomGenRuntime {
    pub client: CustomGenerator,
    #[allow(unused)]
    endpoint_ns: TempDir,
    pub runtime: Runtime,
    daemon: Child,
}

impl CustomGenRuntime {
    pub fn new() -> Result<Self, UniaflError> {
        let endpoint_ns = tempdir()?;
        let endpoint = endpoint_ns.path().join("customgen.sock");
        let mut daemon = Command::new("python")
            .args(["-m", "customgen.daemon", &endpoint.to_string_lossy()])
            .spawn()?;

        let mut grace = 50;
        while !endpoint.exists() && grace > 0 {
            sleep(Duration::from_millis(100));
            grace -= 1;
        }

        let runtime = Runtime::new().inspect_err(|_| {
            let _ = daemon.kill();
        })?;
        let client = runtime
            .block_on(CustomGenerator::new(endpoint))
            .inspect_err(|_| {
                let _ = daemon.kill();
            })?;

        Ok(Self {
            client,
            endpoint_ns,
            runtime,
            daemon,
        })
    }
}

impl Drop for CustomGenRuntime {
    fn drop(&mut self) {
        let _ = self.daemon.kill();
    }
}

pub struct TestLangWorker {
    reverser: HarnessReverser,
    reverser_handle: Option<JoinHandle<Result<(), UniaflError>>>,
    state: TestLangState,
    ast_storage: AstStorage,
    generators: TestLangGenerators,
    mutators: TestLangMutators,
    ast_free_mutators: TestLangAstFreeMutators,
    gen_last_used_testlang_id: Option<TestLangLocalId>,
}

impl TestLangWorker {
    pub fn new(
        reverser: HarnessReverser,
        reverser_handle: Option<JoinHandle<Result<(), UniaflError>>>,
        ast_storage: AstStorage,
        max_bytes_size: usize,
    ) -> Self {
        // WARN: consider here when you are changing behavior of reverser output retreival.
        let testlang_id = 0;
        let testlang = reverser.get_testlang_from_id(0).unwrap();
        let state = TestLangState::new(
            testlang,
            reverser.get_testlang_codegen_path_from_id(testlang_id),
            StdRand::with_seed(current_nanos()),
            max_bytes_size,
        );
        Self {
            reverser,
            reverser_handle,
            state,
            ast_storage,
            generators: testlang_generators(),
            mutators: testlang_mutators(),
            ast_free_mutators: testlang_ast_free_mutators(),
            gen_last_used_testlang_id: None,
        }
    }

    pub fn check_reverser_run<T: Clone>(
        &mut self,
        #[allow(unused)] outputs: &mut Outputs<T>,
    ) -> Result<bool, UniaflError> {
        if let Some(handle) = mem::take(&mut self.reverser_handle) {
            let finished = handle.is_finished();
            self.reverser.sync_with_reverser();
            if !finished {
                self.reverser_handle = Some(handle);
                return Ok(true);
            }
            self.reverser.rerun_complete();

            match handle.join() {
                #[allow(unused)]
                Ok(Err(e)) => {
                    #[cfg(feature = "log")]
                    {
                        let log = format!("[Reverser] FAIL: {:?}", e);
                        outputs.log("reverser", log);
                    }
                    return Ok(false);
                }
                #[allow(unused)]
                Err(e) => {
                    #[cfg(feature = "log")]
                    {
                        let log = format!("[Reverser] PANIC: {:?}", e);
                        outputs.log("reverser", log);
                    }
                    return Ok(false);
                }
                _ => (),
            }
        }

        if self.reverser.evaluate_rerun() {
            let testlang_pending_handle = self.reverser.run_on_side(true, None)?;
            self.reverser_handle = Some(testlang_pending_handle);
            return Ok(true);
        }
        Ok(false)
    }

    pub fn pick_testlang_idx(&mut self) -> Option<TestLangLocalId> {
        if let Some(unused_id) = self.reverser.pick_unused_testlang_id() {
            return Some(unused_id);
        }

        let testlang_count = self.reverser.get_testlang_count();
        match testlang_count.checked_ilog2() {
            None => None,       // testlang_count == 0
            Some(0) => Some(0), // testlang_count == 1
            Some(priority_count) => {
                if self.state.rand.coinflip(0.5) {
                    // priority_count > 0, testlang_count > 1, priority_count < testlang_count
                    Some(testlang_count - (self.state.rand.below(priority_count as usize) + 1))
                } else {
                    let weighted_testlang_seq = self.reverser.get_testlang_weighted_sequence();
                    self.state.rand.choose(weighted_testlang_seq)
                }
            }
        }
    }
}

impl InputGenWorker for TestLangWorker {
    type Metadata = TestLangAst;
    fn generate(&mut self, outputs: &mut Outputs<Self::Metadata>) -> Result<bool, UniaflError> {
        if self.check_reverser_run(outputs)? {
            return Ok(false);
        }
        let Some(chosen_id) = self.pick_testlang_idx() else {
            return Ok(false);
        };
        let Some(chosen_testlang) = self.reverser.get_testlang_from_id(chosen_id) else {
            return Ok(false);
        };

        self.gen_last_used_testlang_id = Some(chosen_id);
        self.state.testlang = chosen_testlang;
        self.state.codegen_path = self.reverser.get_testlang_codegen_path_from_id(chosen_id);
        while let Some(output) = outputs.next() {
            #[cfg(feature = "log")]
            self.state.log.clear();
            let Some(generator) = self.state.rand.choose(&mut self.generators) else {
                return Err(UniaflError::other("No generators available"));
            };
            #[allow(unused)]
            let result = generator.generate(&mut self.state, &mut output.buf, &mut output.metadata);
            if let Some(ast) = &mut output.metadata {
                ast.metadata.local_testlang_id = chosen_id;
            }
            #[cfg(feature = "log")]
            {
                let mut log_msg =
                    format!("[Generator] {}\n[Result] {:?}", generator.name(), result);
                #[cfg(feature = "testlang-debug")]
                {
                    log_msg += &format!(
                        "\n[Log]\n{}\n[AST]\n{:#?}\n[Bytes]\n{:?}",
                        &self.state.log,
                        &output.metadata.as_ref().map(|ast| &ast.root),
                        *output.buf,
                    );
                }
                outputs.log("generate", log_msg);
            }
        }
        Ok(true)
    }

    fn mutate(
        &mut self,
        seed: &MsaSeed,
        outputs: &mut Outputs<Self::Metadata>,
    ) -> Result<bool, UniaflError> {
        if let Some(testlang_id) = mem::take(&mut self.gen_last_used_testlang_id) {
            self.reverser.insert_used_testlang_entry(testlang_id);
        }
        if self.check_reverser_run(outputs)? {
            return Ok(false);
        }
        if let Some(ast) = self.ast_storage.get(&seed.id) {
            let testlang_id = ast.metadata.local_testlang_id;
            if let Some(testlang_handle) = self.reverser.get_testlang_from_id(testlang_id) {
                let testlang = testlang_handle.clone();
                drop(testlang_handle);
                self.state.testlang = testlang;
                self.state.codegen_path =
                    self.reverser.get_testlang_codegen_path_from_id(testlang_id);
                while let Some(output) = outputs.next() {
                    #[cfg(feature = "log")]
                    self.state.log.clear();
                    let Some(mutator) = self.state.rand.choose(&mut self.mutators) else {
                        return Err(UniaflError::other("No mutators available"));
                    };
                    #[allow(unused)]
                    let result = mutator.mutate(
                        &mut self.state,
                        &ast,
                        seed.bytes.len(),
                        &mut output.buf,
                        &mut output.metadata,
                    );
                    if let Some(ast) = &mut output.metadata {
                        ast.metadata.local_testlang_id = testlang_id;
                    }
                    #[cfg(feature = "log")]
                    {
                        let testlang_arc = self.state.testlang.clone();
                        let testlang = testlang_arc.as_ref();
                        let mut log_msg =
                            format!("[Mutator] {}\n[Result] {:?}", mutator.name(), result);
                        #[cfg(feature = "testlang-debug")]
                        {
                            let bytes =
                                node_to_bytes(testlang, &self.state.codegen_path, &ast.root)?;
                            log_msg += &format!(
                                "\n[AST][Before]\n{:#?}\n[Bytes][Before]\n{:?}\n[Log]\n{}\n[AST][After]\n{:#?}\n[Bytes][After]\n{:?}",
                                &ast.root,
                                &bytes,
                                &self.state.log,
                                &output.metadata.as_ref().map(|ast| &ast.root),
                                *output.buf,
                            );
                        }
                        outputs.log("mutate", log_msg);
                    }
                }
                return Ok(true);
            }
        }

        #[cfg(feature = "log")]
        outputs.log(
            "mutate",
            format!(
                "[Mutator] AST not found for corpus id {}. Using AST-free mutators instead.",
                seed.id
            ),
        );

        let Some(chosen_id) = self.pick_testlang_idx() else {
            return Ok(false);
        };
        let Some(chosen_testlang) = self.reverser.get_testlang_from_id(chosen_id) else {
            return Ok(false);
        };

        self.state.testlang = chosen_testlang;
        self.state.codegen_path = self.reverser.get_testlang_codegen_path_from_id(chosen_id);
        while let Some(output) = outputs.next() {
            let Some(mutator) = self.state.rand.choose(&mut self.ast_free_mutators) else {
                return Err(UniaflError::other("No mutators available"));
            };
            #[allow(unused)]
            let result = mutator.mutate(&mut self.state, &seed.bytes, &mut output.buf);
            #[cfg(feature = "log")]
            {
                let mut log_msg = format!("[Mutator] {}\n[Result] {:?}", mutator.name(), result,);
                #[cfg(feature = "testlang-debug")]
                {
                    log_msg += &format!(
                        "\n[Bytes][Before]\n{:?}\n[Log]\n{}\n[Bytes][After]\n{:?}",
                        &seed.bytes, &self.state.log, *output.buf,
                    );
                }
                outputs.log("mutate", log_msg);
            }
        }
        Ok(true)
    }

    fn has_cb_if_added_into_corpus() -> bool {
        true
    }

    fn cb_if_added_into_corpus(
        &mut self,
        output: &Output<Self::Metadata>,
        corpus_id: usize,
    ) -> Result<(), UniaflError> {
        if let Some(ast) = &output.metadata {
            let fuzzdb_id = BytesInput::new(Vec::from(output.buf.as_slice()))
                .generate_name(Some(CorpusId(corpus_id)));
            self.ast_storage.insert(corpus_id, Arc::new(ast.clone()));
            self.reverser
                .insert_corpus_map_entry(fuzzdb_id, ast.metadata.local_testlang_id);
        }
        Ok(())
    }
}
