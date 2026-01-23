use libafl::Error;
use libafl_bolts::Named;
use std::{borrow::Cow, path::PathBuf, sync::Arc};

use crate::{
    executor::Executor,
    input_gen::{
        client::InputGenClient, dict::service::DictPool,
        server::InputGenPool, testlang::service::pool::TestLangPool, InputGenResult,
    },
    msa::{
        manager::{ExecMode, MsaManager},
        stage::MsaStage,
        state::UniState,
    },
};

pub struct InputGenStage {
    input_gen_client: InputGenClient,
}

impl InputGenStage {
    pub fn new(input_gen_client: InputGenClient) -> Self {
        Self { input_gen_client }
    }

    fn stage_worker_range(worker_cnt: u32, stage_name: &str) -> (u32, u32) {
        if worker_cnt < 4 {
            return (0, worker_cnt);
        }
        let block = worker_cnt / 4;
        if stage_name == TestLangPool::name() {
            (0, block)
        } else if stage_name == DictPool::name() {
            (2 * block, 3 * block)
        } else {
            (0, worker_cnt)
        }
    }

    pub fn stage_filter(worker_cnt: u32, worker_idx: u32, stage_name: &str) -> bool {
        let (start, end) = Self::stage_worker_range(worker_cnt, stage_name);
        start <= worker_idx && worker_idx < end
    }

    pub fn new_with_input_gen_pool<I: InputGenPool>(
        config_path: &PathBuf,
        worker_cnt: u32,
    ) -> Option<Self> {
        let (start, end) = Self::stage_worker_range(worker_cnt, I::name());
        InputGenClient::new_with_input_gen_pool::<I>(config_path, Some(start), Some(end))
            .map(Self::new)
    }

    fn execute(
        &self,
        state: &Arc<UniState>,
        worker_idx: i32,
        executor: &mut Executor,
    ) -> Result<(), Error> {
        executor.execute_loaded_inputs(
            self.name(),
            state,
            self.input_gen_client.is_testlang_stage,
        )?;
        if self.input_gen_client.has_cb_if_added_into_corpus() {
            self.input_gen_client.execute_cb(worker_idx)?;
        }
        Ok(())
    }

    fn execute_all(
        &self,
        msa_mgr: &MsaManager,
        state: &Arc<UniState>,
        worker_idx: i32,
        executor: &mut Executor,
        mut result: InputGenResult,
    ) -> Result<bool, Error> {
        if result == InputGenResult::Empty {
            return Ok(false);
        }
        self.execute(state, worker_idx, executor)?;
        while result == InputGenResult::Remain {
            msa_mgr.set_mode(
                worker_idx,
                ExecMode::ExecuteInput,
                self.input_gen_client.is_testlang_stage,
            );
            result = self.input_gen_client.get_remain(worker_idx)?;

            #[cfg(feature = "log")]
            msa_mgr.log(
                self.name(),
                worker_idx,
                format!(
                    "Get {} new remain inputs",
                    msa_mgr.get_allocated_input_cnt(worker_idx)
                ),
            );

            if result == InputGenResult::Empty {
                break;
            }

            self.execute(state, worker_idx, executor)?;
        }
        Ok(true)
    }
}

impl Named for InputGenStage {
    fn name(&self) -> &Cow<'static, str> {
        self.input_gen_client.name()
    }
}

impl MsaStage for InputGenStage {
    fn perform(
        &self,
        msa_mgr: &MsaManager,
        state: &Arc<UniState>,
        worker_idx: i32,
        executor: &mut Executor,
    ) -> Result<bool, Error> {
        if !self.input_gen_client.is_ready(worker_idx) {
            return Ok(false);
        }

        let mut performed = false;
        if self.input_gen_client.has_generator() {
            msa_mgr.set_mode(
                worker_idx,
                ExecMode::ExecuteInput,
                self.input_gen_client.is_testlang_stage,
            );

            #[cfg(feature = "log")]
            msa_mgr.log(self.name(), worker_idx, "Generating new inputs".to_string());

            let result = self.input_gen_client.generate(worker_idx)?;
            #[cfg(feature = "log")]
            msa_mgr.log(
                self.name(),
                worker_idx,
                format!(
                    "Generated {} new inputs",
                    msa_mgr.get_allocated_input_cnt(worker_idx)
                ),
            );
            if self.execute_all(msa_mgr, state, worker_idx, executor, result)? {
                performed = true;
            }
        }

        if self.input_gen_client.has_mutator() {
            msa_mgr.set_mode(
                worker_idx,
                ExecMode::ExecuteInput,
                self.input_gen_client.is_testlang_stage,
            );
            match state.schedule_seed(
                msa_mgr,
                &mut executor.rand,
                worker_idx,
                self.input_gen_client.is_testlang_stage,
            ) {
                Ok(_) => {
                    #[cfg(feature = "log")]
                    msa_mgr.log(
                        self.name(),
                        worker_idx,
                        "Mutating existing inputs".to_string(),
                    );

                    let result = self.input_gen_client.mutate(worker_idx)?;

                    #[cfg(feature = "log")]
                    msa_mgr.log(
                        self.name(),
                        worker_idx,
                        format!(
                            "Mutation resulted in {} new inputs",
                            msa_mgr.get_allocated_input_cnt(worker_idx)
                        ),
                    );

                    if self.execute_all(msa_mgr, state, worker_idx, executor, result)? {
                        performed = true;
                    }
                }
                Err(e) => {
                    eprintln!("[{}] Error in schedule_seed: {}", self.name(), e);
                }
            }
        }
        Ok(performed)
    }
}
