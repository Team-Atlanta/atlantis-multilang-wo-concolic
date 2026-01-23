use fuzzdb::FuzzDB;
use libafl_bolts::{
    current_nanos,
    rands::{Rand, StdRand},
};
use std::path::PathBuf;

use super::client::InputGenClient;
use super::server::{InputGenPool, InputGenWorker, Output, Outputs};
use crate::{common::Error as UniaflError, msa::manager::MsaSeed};

pub struct MockPool {
    db: FuzzDB,
}

impl InputGenPool for MockPool {
    type Worker = MockWorker;
    fn is_on(config_path: &PathBuf) -> bool {
        let name = Self::name().to_string();
        InputGenClient::is_on_config(&name, config_path, false)
    }

    fn name() -> &'static str {
        "mock_input_gen"
    }
    fn has_generator() -> bool {
        true
    }

    fn has_mutator() -> bool {
        true
    }

    fn new(config_path: &PathBuf) -> Self {
        Self {
            db: FuzzDB::new(config_path),
        }
    }

    fn new_worker(&self, _worker_idx: usize) -> Self::Worker {
        MockWorker::new(self.db.clone())
    }
}

#[derive(Clone)]
pub struct MockWorker {
    db: FuzzDB,
    rand: StdRand,
}

impl MockWorker {
    pub fn new(db: FuzzDB) -> Self {
        Self {
            db,
            rand: StdRand::with_seed(current_nanos()),
        }
    }
    fn rand_gen(&mut self) -> Vec<u8> {
        let mut buf = Vec::new();
        let len = self.rand.below(0x100);
        for _ in 0..len {
            buf.push(self.rand.below(256) as u8);
        }
        buf
    }
}

impl InputGenWorker for MockWorker {
    type Metadata = ();
    fn generate(&mut self, outputs: &mut Outputs<Self::Metadata>) -> Result<bool, UniaflError> {
        while let Some(output) = outputs.next() {
            let out_buf = &mut output.buf;
            let len = self.rand.below(0x100);
            assert!(out_buf.capacity() > len);
            for _ in 0..len {
                out_buf.push(self.rand.below(256) as u8);
            }
            output.metadata = Some(());
        }

        let mut new_inputs = Vec::new();
        for _ in 0..self.rand.below(0x1000) {
            new_inputs.push(self.rand_gen());
        }
        outputs.bump_blobs(new_inputs);

        Ok(true)
    }

    fn mutate(
        &mut self,
        seed: &MsaSeed,
        outputs: &mut Outputs<Self::Metadata>,
    ) -> Result<bool, UniaflError> {
        println!("{:?}", self.db.load_cov(&seed.fname));
        while let Some(output) = outputs.next() {
            let out_buf = &mut output.buf;
            out_buf.extend(&seed.bytes);
            let mut_idx = self.rand.below(out_buf.len());
            out_buf[mut_idx] = self.rand.below(256) as u8;
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
        println!("ADDED {:?} {:?}", output.metadata, corpus_id);
        Ok(())
    }
}
