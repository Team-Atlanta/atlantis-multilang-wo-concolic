use crate::msa::manager::CovAddr;
use std::collections::HashSet;

pub struct CovObserver {
    total_cov: HashSet<CovAddr>,
}

impl CovObserver {
    pub fn new() -> Self {
        Self {
            total_cov: HashSet::new(),
        }
    }

    pub fn is_interesting(&self, covs: &[u64]) -> bool {
        for cov in covs {
            if !self.total_cov.contains(cov) {
                return true;
            }
        }
        false
    }

    pub fn update_cov(&mut self, covs: &[u64]) -> bool {
        let mut ret = false;
        for cov in covs {
            if self.total_cov.insert(*cov) {
                ret = true;
            }
        }
        ret
    }
}
