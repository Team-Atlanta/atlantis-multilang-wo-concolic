use fuzzdb::{FuzzDB, MatchResult};
use libafl::{corpus::CorpusId, random_corpus_id, Error};
use libafl_bolts::rands::{Rand, StdRand};
use std::path::PathBuf;

use super::{
    corpus::{UniCorpus, WeigtedCorpus},
    manager::CovAddr,
};

struct CorpusInScheduler {
    weighted_corpus: WeigtedCorpus,
    interesting_corpus: WeigtedCorpus,
}

pub struct UniScheduler {
    pub db: FuzzDB,
    normal_corpus: CorpusInScheduler,
    testlang_corpus: CorpusInScheduler,
}

const VULN_WEIGHT: usize = 8;
const KEY_WEIGHT: usize = 4;
const SHOULD_BE_TAKEN_WEIGHT: usize = 1;

const DIFF_LINE_RANGE_WEIGHT: usize = 4;
const DIFF_FILE_WEIGHT: usize = 2;

impl CorpusInScheduler {
    fn empty() -> Self {
        Self {
            weighted_corpus: WeigtedCorpus::empty(),
            interesting_corpus: WeigtedCorpus::empty(),
        }
    }

    fn calculate_score_one(match_cov: &MatchResult) -> (usize, bool) {
        if match_cov.deprioritized {
            (1, false)
        } else {
            let mut score = 0;
            let mut is_vuln = false;
            if match_cov.vuln.func_name {
                score += VULN_WEIGHT;
                is_vuln = true;
            }
            if match_cov.vuln.line {
                score += VULN_WEIGHT;
            }
            for m in &match_cov.keys {
                if m.func_name {
                    score += KEY_WEIGHT * m.weight;
                }
                if m.line {
                    score += KEY_WEIGHT * m.weight;
                }
            }
            for m in &match_cov.should_be_taken {
                if m.func_name {
                    score += SHOULD_BE_TAKEN_WEIGHT * m.weight;
                }
                if m.line {
                    score += SHOULD_BE_TAKEN_WEIGHT * m.weight;
                }
            }
            (score, is_vuln)
        }
    }

    fn eval_seed(db: &FuzzDB, fname: &String) -> (usize, bool) {
        let mut score = 1;
        let mut is_vuln = false;
        let diff_match_result = db.match_diff_info(fname);
        score += DIFF_FILE_WEIGHT * diff_match_result.num_file_matched;
        score += DIFF_LINE_RANGE_WEIGHT * diff_match_result.num_line_range_matched;
        for match_cov in db.match_interesting_cov(fname) {
            let (this_score, this_is_vuln) = Self::calculate_score_one(&match_cov);
            score += this_score;
            is_vuln = is_vuln || this_is_vuln;
        }
        (score, is_vuln)
    }

    fn add_new_seed(
        db: &FuzzDB,
        weighted_corpus: &mut WeigtedCorpus,
        interesting_corpus: &mut WeigtedCorpus,
        corpus_id: CorpusId,
        fname: &String,
    ) {
        let (weight, is_interesting) = Self::eval_seed(db, fname);
        weighted_corpus.add(corpus_id, fname.clone(), weight);
        if is_interesting {
            interesting_corpus.add(corpus_id, fname.clone(), weight);
        }
    }

    pub fn rebalance(&mut self, db: &FuzzDB) {
        let mut new_weighted = WeigtedCorpus::empty();
        let mut new_interesting = WeigtedCorpus::empty();
        for seed in self.weighted_corpus.seeds() {
            Self::add_new_seed(
                db,
                &mut new_weighted,
                &mut new_interesting,
                seed.id,
                &seed.fname,
            );
        }
        self.weighted_corpus = new_weighted;
        self.interesting_corpus = new_interesting;
    }

    pub fn add_seed(&mut self, db: &FuzzDB, corpus_id: CorpusId, fname: &String) {
        Self::add_new_seed(
            db,
            &mut self.weighted_corpus,
            &mut self.interesting_corpus,
            corpus_id,
            fname,
        );
    }

    pub fn next(&self, rand: &mut StdRand) -> Option<CorpusId> {
        let prob = rand.below(100);
        if prob < 75 {
            if prob < 25 {
                if let Some(id) = self.interesting_corpus.rand_next(rand) {
                    return Some(id);
                }
            }
            if let Some(id) = self.weighted_corpus.rand_next(rand) {
                return Some(id);
            }
        }
        self.weighted_corpus.no_weight_rand_next(rand)
    }
}

impl UniScheduler {
    pub fn new(config_path: &PathBuf) -> Self {
        Self {
            db: FuzzDB::new(config_path),
            normal_corpus: CorpusInScheduler::empty(),
            testlang_corpus: CorpusInScheduler::empty(),
        }
    }

    fn rebalance(&mut self) {
        self.normal_corpus.rebalance(&self.db);
        self.testlang_corpus.rebalance(&self.db);
    }

    pub fn notify_cpv_found(&mut self, pov_path: &PathBuf, crash_log: &[u8]) {
        if self.db.notify_cpv_found(pov_path, crash_log) {
            self.rebalance();
        }
    }

    pub fn load_bcda_result(&mut self, path: &PathBuf) -> Option<bool> {
        let ret = self.db.load_bcda_result(path);
        if ret == Some(true) {
            self.rebalance();
        }
        ret
    }

    pub fn update(
        &mut self,
        corpus_id: CorpusId,
        fname: &String,
        _covs: &[CovAddr],
        is_testlang_stage: bool,
    ) {
        if is_testlang_stage {
            self.testlang_corpus.add_seed(&self.db, corpus_id, fname);
        } else {
            self.normal_corpus.add_seed(&self.db, corpus_id, fname);
        }
    }

    pub fn next(
        &self,
        _corpus: &UniCorpus,
        rand: &mut StdRand,
        is_testlang_stage: bool,
    ) -> Result<CorpusId, Error> {
        if is_testlang_stage {
            let prob = rand.below(100);
            if prob > 10 {
                if let Some(id) = self.testlang_corpus.next(rand) {
                    return Ok(id);
                }
            }
            if let Some(id) = self.normal_corpus.next(rand) {
                return Ok(id);
            }
        } else {
            if let Some(id) = self.normal_corpus.next(rand) {
                return Ok(id);
            }
        }
        Err(Error::empty("No entries in testlang corpus".to_owned()))
    }
}
