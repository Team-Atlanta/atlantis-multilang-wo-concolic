use fuzzdb::FuzzDB;
use libafl_bolts::{current_nanos, rands::StdRand};
use rand::{seq::IteratorRandom, Rng};
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use crate::{
    common::{utils, Error as UniaflError},
    input_gen::{
        dict::dictgen::{DictGen, Tokens},
        server::{InputGenPool, InputGenWorker, Output, Outputs},
    },
    msa::manager::MsaSeed,
};

#[derive(Serialize, Deserialize)]
pub struct DictInputGenConfig {
    workdir: String,
    dictgen_path: String,
    #[serde(default = "default_max_function_count")]
    max_function_count: usize,
    #[serde(default = "default_use_bcda_result")]
    use_bcda_result: bool,
    #[serde(default = "default_input_hash_size")]
    input_hash_size: usize,
    diff_path: Option<String>,
}

fn default_max_function_count() -> usize {
    50 // TODO: determine a proper default
}

fn default_use_bcda_result() -> bool {
    false
}

fn default_input_hash_size() -> usize {
    3221225472 // 3 GB
}

pub struct DictPool {
    db: Arc<FuzzDB>,
    dictgen: Arc<DictGen>,
    mutation_tracker: Arc<Mutex<HashSet<u64>>>,
    input_hash_size: usize,
    use_bcda_result: bool,
    diff_path: Option<String>,
}

pub struct DictWorker {
    db: Arc<FuzzDB>,
    dictgen: Arc<DictGen>,
    mutator: DictMutator,
    use_bcda_result: bool,
    diff_path: Option<String>,
    log_count: u64,
}

pub struct DictMutator {
    rand: StdRand,
    mutation_tracker: Arc<Mutex<HashSet<u64>>>,
    input_hash_size: usize,
    log_count: u64,
}

impl InputGenPool for DictPool {
    type Worker = DictWorker;

    fn name() -> &'static str {
        "dict_input_gen"
    }

    fn has_generator() -> bool {
        false
    }

    fn has_mutator() -> bool {
        true
    }

    fn new(config_path: &PathBuf) -> Self {
        let conf = utils::load_json::<DictInputGenConfig>(config_path)
            .expect("Failed to open dictgen config file");
        let workdir = format!("{}/dictgen", &conf.workdir);
        let use_bcda_result = conf.use_bcda_result;
        #[cfg(feature = "log")]
        println!("Max function count: {}", conf.max_function_count);
        #[cfg(feature = "log")]
        println!("Use BCDA result: {}", use_bcda_result);
        #[cfg(feature = "log")]
        println!("Diff path: {:?}", conf.diff_path);
        Self {
            db: Arc::new(FuzzDB::new(config_path)),
            dictgen: Arc::new(DictGen::new(
                &conf.dictgen_path,
                &workdir,
                conf.max_function_count,
                use_bcda_result,
            )),
            mutation_tracker: Arc::new(Mutex::new(HashSet::new())),
            input_hash_size: conf.input_hash_size,
            use_bcda_result,
            diff_path: conf.diff_path,
        }
    }

    fn new_worker(&self, _worker_idx: usize) -> Self::Worker {
        Self::Worker::new(
            self.db.clone(),
            self.dictgen.clone(),
            self.mutation_tracker.clone(),
            self.input_hash_size,
            self.use_bcda_result,
            self.diff_path.clone(),
        )
    }
}

impl InputGenWorker for DictWorker {
    type Metadata = ();
    fn generate(&mut self, _outputs: &mut Outputs<Self::Metadata>) -> Result<bool, UniaflError> {
        unreachable!()
    }

    fn mutate(
        &mut self,
        seed: &MsaSeed,
        outputs: &mut Outputs<Self::Metadata>,
    ) -> Result<bool, UniaflError> {
        // self.update_bcda()?;

        let funcs = self.get_interesting_func();

        match self.db.load_cov(&seed.fname) {
            Some(cov) => {
                self.log_new_seed(&format!("Seed ID: {}", seed.id));
                #[cfg(feature = "log")]
                for func_name in cov.func_names() {
                    self.log(&format!("Function Name: {}", func_name));
                }
                let (normal_tokens, diff_tokens) =
                    self.dictgen
                        .get_tokens(&seed.fname, &cov, funcs, self.diff_path.clone());

                #[cfg(feature = "log")]
                self.print_tokens(&normal_tokens, "normal_tokens");
                #[cfg(feature = "log")]
                self.print_tokens(&diff_tokens, "diff_tokens");

                Ok(self
                    .mutator
                    .mutate_seed(seed, &normal_tokens, &diff_tokens, outputs))
            }
            None => Err(anyhow::Error::msg("Failed to load func cov").into()),
        }
    }
}

impl DictWorker {
    pub fn new(
        db: Arc<FuzzDB>,
        dictgen: Arc<DictGen>,
        mutation_tracker: Arc<Mutex<HashSet<u64>>>,
        input_hash_size: usize,
        use_bcda_result: bool,
        diff_path: Option<String>,
    ) -> Self {
        Self {
            db,
            dictgen,
            mutator: DictMutator::new(mutation_tracker.clone(), input_hash_size),
            use_bcda_result,
            diff_path,
            log_count: 0,
        }
    }

    // fn update_bcda(&mut self) -> Result<(), UniaflError> {
    //     // TODO: find the bcda result
    //     let path = "/some/path";
    //     self.db
    //         .load_bcda_result(&PathBuf::from(path))
    //         .ok_or_else(|| UniaflError::from(anyhow::anyhow!("Failed to load BCDA result")))?;
    //     Ok(())
    // }

    fn get_interesting_func(&self) -> Option<HashSet<String>> {
        if !self.use_bcda_result {
            return None;
        }
        let funcs = self.db.collect_interesting_functions();
        if !funcs.is_empty() {
            Some(funcs)
        } else {
            None
        }
    }

    #[cfg(feature = "log")]
    fn print_tokens(&mut self, tokens: &Tokens, name: &str) {
        if tokens.is_empty() {
            return;
        }
        self.log(&format!("Len({}): {}", name, tokens.len()));
        for token in tokens.iter() {
            match std::str::from_utf8(token) {
                Ok(s) => self.log(&format!("Token: {}", s)),
                Err(_) => self.log(&format!("Token (non-UTF8): {:?}", token)),
            }
        }
    }

    fn log(&mut self, msg: &str) {
        #[cfg(feature = "log")]
        {
            if self.log_count % 1000 == 0 {
                println!("[DictWorker] {}", msg);
            }
        }
    }

    fn log_new_seed(&mut self, msg: &str) {
        #[cfg(feature = "log")]
        {
            self.log_count += 1;
            if self.log_count % 1000 == 0 {
                println!("[DictWorker] {}", msg);
            }
        }
    }
}

impl DictMutator {
    pub fn new(mutation_tracker: Arc<Mutex<HashSet<u64>>>, input_hash_size: usize) -> Self {
        Self {
            rand: StdRand::with_seed(current_nanos()),
            mutation_tracker,
            input_hash_size,
            log_count: 0,
        }
    }

    // XXX: This is pub for unit testing. need to fix it.
    pub fn compute_hash(&self, seed: &Vec<u8>) -> u64 {
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};

        let mut hasher = DefaultHasher::new();
        seed.hash(&mut hasher);
        hasher.finish()
    }

    // XXX: This is pub for unit testing. need to fix it.
    pub fn is_unique(&mut self, seed: &Vec<u8>) -> bool {
        let hash = self.compute_hash(seed);
        let mut tracker = self.mutation_tracker.lock().unwrap();
        if tracker.contains(&hash) {
            matches!(self.rand.gen_range(0..10), 0)
        } else {
            if tracker.len() >= self.input_hash_size {
                // If the tracker is full, remove a random entry
                if let Some(entry) = tracker.iter().choose(&mut self.rand).cloned() {
                    tracker.remove(&entry);
                }
            }
            tracker.insert(hash);
            true
        }
    }

    fn choose_token<'a>(
        &mut self,
        input: &'a Vec<u8>,
        tokens: &'a Tokens,
        choice: usize,
    ) -> Option<&'a Vec<u8>> {
        match choice {
            0 | 1 => tokens.iter().choose(&mut self.rand),
            2 => self.find_match(input, tokens),
            _ => unreachable!(),
        }
    }

    pub fn mutate_seed(
        &mut self,
        seed: &MsaSeed,
        normal_tokens: &Tokens,
        diff_tokens: &Tokens,
        outputs: &mut Outputs<()>,
    ) -> bool {
        if normal_tokens.is_empty() && diff_tokens.is_empty() {
            return false;
        }
        if seed.bytes.is_empty() {
            return false;
        }

        self._mutate_seed(seed, normal_tokens, diff_tokens, outputs);
        true
    }

    fn _mutate_seed(
        &mut self,
        seed: &MsaSeed,
        normal_tokens: &Tokens,
        diff_tokens: &Tokens,
        outputs: &mut Outputs<()>,
    ) {
        self.log_new_seed(&format!("Initial input: {:?}", seed.bytes));

        while let Some(mut output) = outputs.next() {
            let use_diff_tokens = diff_tokens.len() != 0 && self.rand.gen_range(0..10) < 7;
            if use_diff_tokens {
                self.log("Using diff tokens");
                self._mutate_seed_with_tokens(seed, diff_tokens, &mut output);
            } else {
                self.log("Using normal tokens");
                self._mutate_seed_with_tokens(seed, normal_tokens, &mut output);
            }
        }
    }

    fn _mutate_seed_with_tokens(
        &mut self,
        seed: &MsaSeed,
        tokens: &Tokens,
        output: &mut Output<()>,
    ) {
        let max_retries = 10;
        for _retries in 0..max_retries {
            if self._try_mutate_seed_with_tokens(seed, tokens, output) {
                return;
            }
        }
        self._mutate_seed_fallback(seed, output);
    }

    fn _try_mutate_seed_with_tokens(
        &mut self,
        seed: &MsaSeed,
        tokens: &Tokens,
        output: &mut Output<()>,
    ) -> bool {
        let choice = self.rand.gen_range(0..3);
        self.log(&format!("Mutation method choice: {}", choice));

        if let Some(rand_token) = self.choose_token(&seed.bytes, tokens, choice) {
            self.log(&format!("Token: {:?}", rand_token));

            let mutation = match choice {
                0 => self.mutate_seed_by_insert_token(seed, rand_token, tokens),
                1 => self.mutate_seed_by_replacing_bytes(seed, rand_token, tokens),
                2 => self.mutate_seed_by_replacing_token(seed, rand_token, tokens),
                _ => unreachable!(), // This case should never happen
            };
            self.log(&format!("Mutated: {:?}", mutation));

            if let Some(mutation) = mutation {
                if self.is_unique(&mutation) {
                    let out_buf = &mut output.buf;
                    let remaining = out_buf.capacity() - out_buf.len();
                    if mutation.len() > remaining {
                        out_buf.extend(&mutation[..remaining]);
                    } else {
                        out_buf.extend(&mutation);
                    }
                    return true;
                } else {
                    self.log("Redundant input");
                }
            }
        }
        return false;
    }

    fn mutate_seed_by_insert_token(
        &mut self,
        seed: &MsaSeed,
        token: &Vec<u8>,
        _tokens: &Tokens,
    ) -> Option<Vec<u8>> {
        let mut mutation = seed.bytes.clone();
        mutation.reserve(token.len());
        // Insert token
        let idx = self.rand.gen_range(0..=mutation.len());
        mutation.splice(idx..idx, token.iter().cloned());
        Some(mutation)
    }

    fn mutate_seed_by_replacing_bytes(
        &mut self,
        seed: &MsaSeed,
        token: &Vec<u8>,
        _tokens: &Tokens,
    ) -> Option<Vec<u8>> {
        let mut mutation = seed.bytes.clone();
        mutation.reserve(token.len());
        // Replace token
        let idx = self.rand.gen_range(0..mutation.len());
        if idx + token.len() > mutation.len() {
            mutation.resize(idx + token.len(), 0);
        }
        mutation[idx..idx + token.len()].copy_from_slice(&token);
        Some(mutation)
    }

    fn mutate_seed_by_replacing_token(
        &mut self,
        seed: &MsaSeed,
        token: &Vec<u8>,
        tokens: &Tokens,
    ) -> Option<Vec<u8>> {
        let mut mutation = seed.bytes.clone();
        mutation.reserve(token.len());
        // XXX: redundant call to find_subsequence
        if let Some(pos) = self.find_subsequence(&seed.bytes, token) {
            let start = pos;
            let end = pos + token.len();
            if end <= mutation.len() {
                mutation.drain(start..end);
                let new_token = tokens.iter().choose(&mut self.rand)?;
                mutation.splice(start..start, new_token.iter().cloned());
            }
            Some(mutation)
        } else {
            #[cfg(feature = "log")]
            println!("Token not found in seed. This shouldn't happen.");
            None
        }
    }

    fn find_match<'a>(&self, data: &'a Vec<u8>, tokens: &'a Tokens) -> Option<&'a Vec<u8>> {
        for token in tokens {
            if let Some(_pos) = self.find_subsequence(data, token) {
                return Some(token);
            }
        }
        None
    }

    fn find_subsequence(&self, data: &[u8], token: &[u8]) -> Option<usize> {
        data.windows(token.len()).position(|window| window == token)
    }

    fn _mutate_seed_fallback(&mut self, seed: &MsaSeed, output: &mut Output<()>) {
        self.log("Failed to mutate seed with tokens. Fallback to basic mutation.");
        let mutation = self.fallback_mutation(seed);
        let out_buf = &mut output.buf;
        let remaining = out_buf.capacity() - out_buf.len();
        if mutation.len() > remaining {
            out_buf.extend(&mutation[..remaining]);
        } else {
            out_buf.extend(&mutation);
        }
    }

    fn fallback_mutation(&mut self, seed: &MsaSeed) -> Vec<u8> {
        let mut fallback = seed.bytes.clone();
        let fallback_choice = self.rand.gen_range(0..3);

        let max_mutation = self.rand.gen_range(1..5);
        for _i in 0..max_mutation {
            match fallback_choice {
                0 => {
                    if !fallback.is_empty() {
                        let idx = self.rand.gen_range(0..fallback.len());
                        let bit = 1 << self.rand.gen_range(0..8);
                        fallback[idx] ^= bit;
                    }
                }
                1 => {
                    if fallback.len() > 1 {
                        let new_len = self.rand.gen_range(1..fallback.len());
                        fallback.truncate(new_len);
                    }
                }
                2 => {
                    let idx = self.rand.gen_range(0..=fallback.len());
                    let byte = self.rand.gen::<u8>();
                    fallback.insert(idx, byte);
                }
                _ => unreachable!(),
            }
        }
        fallback
    }

    fn log(&mut self, msg: &str) {
        #[cfg(feature = "log")]
        {
            if self.log_count % 1000 == 0 {
                println!("[DictMutator] {}", msg);
            }
        }
    }

    fn log_new_seed(&mut self, msg: &str) {
        #[cfg(feature = "log")]
        {
            self.log_count += 1;
            if self.log_count % 1000 == 0 {
                println!("[DictMutator] {}", msg);
            }
        }
    }
}
