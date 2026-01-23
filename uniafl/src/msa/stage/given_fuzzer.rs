use libafl::Error;
use libafl_bolts::Named;
use std::{borrow::Cow, path::PathBuf, sync::Arc};

use crate::{
    executor::Executor,
    input_gen::client::InputGenClient,
    msa::{
        manager::{ExecMode, MsaManager},
        stage::MsaStage,
        state::UniState,
    },
};
pub struct GivenFuzzerStage {
    name: Cow<'static, str>,
}

impl GivenFuzzerStage {
    pub fn new(config_path: &PathBuf) -> Option<Self> {
        let name = Cow::Borrowed("given_fuzzer");
        if InputGenClient::is_on_config(&name.to_string(), config_path, true) {
            Some(Self { name })
        } else {
            None
        }
    }
}

impl Named for GivenFuzzerStage {
    fn name(&self) -> &Cow<'static, str> {
        &self.name
    }
}

impl MsaStage for GivenFuzzerStage {
    fn perform(
        &self,
        msa_mgr: &MsaManager,
        state: &Arc<UniState>,
        worker_idx: i32,
        executor: &mut Executor,
    ) -> Result<bool, Error> {
        #[cfg(feature = "log")]
        msa_mgr.log(self.name(), worker_idx, "Mutate seeds".to_string());
        msa_mgr.set_mode(worker_idx, ExecMode::RunFuzzerWithSeed, false);
        if state
            .schedule_seed(msa_mgr, &mut executor.rand, worker_idx, false)
            .is_ok()
        {
            executor.execute_loaded_inputs(self.name(), state, false)?;
        }
        executor.stats.num_normal_inputs += 1;
        #[cfg(feature = "log")]
        msa_mgr.log(self.name(), worker_idx, "Let fuzzer go".to_string());
        msa_mgr.set_mode(worker_idx, ExecMode::RunFuzzer, false);
        msa_mgr.set_iter_cnt(worker_idx, msa_mgr.input_per_worker);
        executor.execute_loaded_inputs(self.name(), state, false)?;
        executor.stats.num_normal_inputs += msa_mgr.input_per_worker as usize;
        Ok(true)
    }
}
