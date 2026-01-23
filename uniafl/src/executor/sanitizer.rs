use std::collections::HashSet;

pub struct CrashObserver {
    crash_logs: HashSet<Vec<u8>>,
}

impl CrashObserver {
    pub fn new() -> Self {
        Self {
            crash_logs: HashSet::new(),
        }
    }

    pub fn is_interesting(&self, log: &[u8]) -> bool {
        !self.crash_logs.contains(log)
    }
    pub fn add_log(&mut self, log: &[u8]) -> bool {
        self.crash_logs.insert(log.to_vec())
    }
}
