use libafl::{
    corpus::{CorpusId, Testcase},
    inputs::Input,
    Error,
};
use libafl_bolts::rands::{Rand, StdRand};
use std::{collections::HashMap, path::PathBuf};

use super::state::UniInput;

pub struct UniCorpus {
    dir_path: PathBuf,
    testcases: Vec<Testcase<UniInput>>,
    file_name_to_id: HashMap<String, CorpusId>,
}

impl UniCorpus {
    pub fn new(dir_path: &PathBuf) -> Self {
        Self {
            dir_path: dir_path.clone(),
            testcases: Vec::new(),
            file_name_to_id: HashMap::new(),
        }
    }

    pub fn count(&self) -> usize {
        self.testcases.len()
    }

    pub fn nth(&self, idx: usize) -> CorpusId {
        CorpusId(idx)
    }

    pub fn add(
        &mut self,
        stage_name: &str,
        mut new_tc: Testcase<UniInput>,
    ) -> Result<(CorpusId, bool), Error> {
        let file_name = new_tc
            .filename_mut()
            .take()
            .unwrap_or_else(|| new_tc.input().as_ref().unwrap().generate_name(None));
        if let Some(corpus_id) = self.file_name_to_id.get(&file_name) {
            self.save_testcase(stage_name, &mut new_tc, &file_name, false);
            Ok((*corpus_id, false))
        } else {
            let id = CorpusId(self.testcases.len());
            self.save_testcase(stage_name, &mut new_tc, &file_name, true);
            self.testcases.push(new_tc);
            self.file_name_to_id.insert(file_name.clone(), id);
            Ok((id, true))
        }
    }

    pub fn has_filename(&self, path: &PathBuf) -> bool {
        if let Some(file_name) = path.file_name() {
            self.file_name_to_id
                .contains_key(file_name.to_str().unwrap())
        } else {
            false
        }
    }

    pub fn get(&self, id: CorpusId) -> Result<&Testcase<UniInput>, Error> {
        if self.testcases.len() > id.0 {
            Ok(&self.testcases[id.0])
        } else {
            Err(Error::empty("No such corpus".to_owned()))
        }
    }

    fn save_testcase(
        &self,
        stage_name: &str,
        tc: &mut Testcase<UniInput>,
        file_name: &String,
        is_new: bool,
    ) {
        if is_new {
            let metadata_path = self.dir_path.join(format!(".{}.metadata", file_name));
            std::fs::write(
                &metadata_path,
                format!("{{\"finder\": \"{}\"}}", stage_name),
            )
            .ok();
        }
        let file_path = self.dir_path.join(&file_name);
        tc.input().as_ref().unwrap().to_file(&file_path).ok();
        *tc.file_path_mut() = Some(file_path);
        *tc.filename_mut() = Some(file_name.clone());
    }

    #[allow(dead_code)]
    pub fn testcases(&self) -> &[Testcase<UniInput>] {
        &self.testcases
    }
}

#[derive(Debug)]
pub struct WeightedSeed {
    pub id: CorpusId,
    pub fname: String,
    weight_idx: usize,
    weight: usize,
}

pub struct WeigtedCorpus {
    seeds: Vec<WeightedSeed>,
    last_weight: usize,
}

impl WeightedSeed {
    pub fn new(id: CorpusId, fname: String, weight_idx: usize, weight: usize) -> Self {
        Self {
            id,
            fname,
            weight_idx,
            weight,
        }
    }
}

impl WeigtedCorpus {
    pub fn empty() -> Self {
        Self {
            seeds: Vec::new(),
            last_weight: 0,
        }
    }

    pub fn seeds(&self) -> &[WeightedSeed] {
        &self.seeds
    }

    pub fn add(&mut self, id: CorpusId, fname: String, weight: usize) {
        let weight_idx = self.last_weight;
        self.seeds
            .push(WeightedSeed::new(id, fname, weight_idx, weight));
        self.last_weight += weight;
    }

    pub fn rand_next(&self, rand: &mut StdRand) -> Option<CorpusId> {
        if self.is_empty() {
            None
        } else {
            let idx = rand.below(self.last_weight);
            self.seeds
                .binary_search_by(|seed| {
                    if idx < seed.weight_idx {
                        std::cmp::Ordering::Greater
                    } else if idx >= seed.weight_idx + seed.weight {
                        std::cmp::Ordering::Less
                    } else {
                        std::cmp::Ordering::Equal
                    }
                })
                .ok()
                .map(|idx| self.seeds[idx].id)
        }
    }

    pub fn is_empty(&self) -> bool {
        self.seeds.is_empty()
    }

    pub fn no_weight_rand_next(&self, rand: &mut StdRand) -> Option<CorpusId> {
        if self.is_empty() {
            None
        } else {
            let idx = rand.below(self.seeds.len() as usize);
            Some(self.seeds[idx].id)
        }
    }
}
