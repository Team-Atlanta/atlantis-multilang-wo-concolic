use chrono::Utc;
use libafl_bolts::core_affinity::Cores;
use shared_memory::{Shmem, ShmemConf};
use std::sync::RwLock;

use crate::common::sem_lock::SemLock;

pub struct InputGenManager {
    sem_locks: Vec<SemLock>,
    pub core_cnt: usize,
    shmem: Shmem,
    process_start_time: Vec<RwLock<i64>>,
}

pub enum InputGenCmd {
    Mutate = 0,
    Generate = 1,
    GetRemain = 2,
    ExecCB = 3,
}

#[derive(Debug, PartialEq, Eq)]
pub enum InputGenResult {
    Empty = 0,
    Done = 1,
    Remain = 2,
}

#[allow(clippy::upper_case_acronyms)]
pub enum InputGenStatus {
    BOOTED = 0,
    READY = 1,
    WIP = 2,
}

#[repr(C)]
#[derive(Debug, Copy, Clone)]
struct SharedData {
    status: u32,
    cmd: u32,
    result: u32,
}

const TIMEOUT: i64 = 600;
impl InputGenManager {
    pub fn new(name: &str, harness_name: String, cores: &Cores, client: bool) -> Self {
        let shm_name = format!("{}_{}", harness_name, name);
        let core_cnt = cores.ids.len();
        let shmem_conf = ShmemConf::new()
            .os_id(&shm_name)
            .size(std::mem::size_of::<SharedData>() * core_cnt);
        let shmem = if client {
            shmem_conf.create().expect("Failed to create shared memory")
        } else {
            shmem_conf.open().expect("Fail to open shared memory")
        };
        let sem_locks = (0..core_cnt)
            .map(|worker_idx| {
                SemLock::new(format!("{}_{}_{}", harness_name, name, worker_idx), client)
            })
            .collect();
        let process_start_time = (0..core_cnt).map(|_| RwLock::new(0)).collect();
        Self {
            sem_locks,
            shmem,
            core_cnt,
            process_start_time,
        }
    }

    pub fn reset(&self) {
        for worker_idx in 0..self.core_cnt {
            self.set_status(worker_idx, InputGenStatus::BOOTED);
            self.set_result(worker_idx as usize, InputGenResult::Empty);
            for _ in 0..100 {
                self.sem_locks[worker_idx as usize].post_end();
            }
        }
    }

    fn set_cmd(&self, worker_idx: usize, cmd: InputGenCmd) {
        unsafe {
            let ptr = self.shmem.as_ptr() as *mut SharedData;
            let shared_array = std::slice::from_raw_parts_mut(ptr, self.core_cnt);
            shared_array[worker_idx].cmd = cmd as u32;
        }
    }

    fn get_cmd(&self, worker_idx: usize) -> InputGenCmd {
        unsafe {
            let ptr = self.shmem.as_ptr() as *mut SharedData;
            let shared_array = std::slice::from_raw_parts_mut(ptr, self.core_cnt);
            InputGenCmd::try_from(shared_array[worker_idx].cmd).expect("Unknown cmd")
        }
    }

    pub fn set_status(&self, worker_idx: usize, status: InputGenStatus) {
        unsafe {
            let ptr = self.shmem.as_ptr() as *mut SharedData;
            let shared_array = std::slice::from_raw_parts_mut(ptr, self.core_cnt);
            shared_array[worker_idx].status = status as u32;
        }
    }

    fn get_status(&self, worker_idx: usize) -> InputGenStatus {
        unsafe {
            let ptr = self.shmem.as_ptr() as *mut SharedData;
            let shared_array = std::slice::from_raw_parts_mut(ptr, self.core_cnt);
            InputGenStatus::try_from(shared_array[worker_idx].status).expect("Unknown cmd")
        }
    }

    pub fn set_result(&self, worker_idx: usize, result: InputGenResult) {
        unsafe {
            let ptr = self.shmem.as_ptr() as *mut SharedData;
            let shared_array = std::slice::from_raw_parts_mut(ptr, self.core_cnt);
            shared_array[worker_idx].result = result as u32;
        }
    }

    fn get_result(&self, worker_idx: usize) -> InputGenResult {
        unsafe {
            let ptr = self.shmem.as_ptr() as *mut SharedData;
            let shared_array = std::slice::from_raw_parts_mut(ptr, self.core_cnt);
            InputGenResult::try_from(shared_array[worker_idx].result).expect("Unknown result")
        }
    }

    pub fn is_ready(&self, worker_idx: i32) -> bool {
        matches!(self.get_status(worker_idx as usize), InputGenStatus::READY)
    }

    pub fn run_cmd(&self, worker_idx: i32, cmd: InputGenCmd) -> InputGenResult {
        let worker_idx = worker_idx as usize;
        self.set_cmd(worker_idx, cmd);
        self.sem_locks[worker_idx].post_start();
        self.sem_locks[worker_idx].wait_end();
        self.get_result(worker_idx)
    }

    pub fn wait_cmd(&self, worker_idx: i32) -> InputGenCmd {
        let worker_idx = worker_idx as usize;
        self.sem_locks[worker_idx].wait_start();
        self.set_status(worker_idx, InputGenStatus::WIP);
        let ret = self.get_cmd(worker_idx);
        *self.process_start_time[worker_idx].write().unwrap() = Utc::now().timestamp();
        ret
    }

    pub fn done_cmd(&self, worker_idx: i32, result: InputGenResult) {
        let worker_idx = worker_idx as usize;
        self.set_status(worker_idx, InputGenStatus::READY);
        self.set_result(worker_idx, result);
        *self.process_start_time[worker_idx].write().unwrap() = 0;
        self.sem_locks[worker_idx].post_end();
    }

    fn is_timeout(&self) -> bool {
        let now = Utc::now().timestamp();
        for t in &self.process_start_time {
            let t = *(t.read().unwrap());
            if t == 0 {
                continue;
            }
            if now - t > TIMEOUT {
                return true;
            }
        }
        false
    }

    pub fn check_timeout_loop(&self) {
        loop {
            std::thread::sleep(std::time::Duration::from_secs((TIMEOUT / 2) as u64));
            if self.is_timeout() {
                std::process::exit(1);
            }
        }
    }
}

impl TryFrom<u32> for InputGenCmd {
    type Error = &'static str;

    fn try_from(value: u32) -> Result<Self, Self::Error> {
        match value {
            0 => Ok(InputGenCmd::Mutate),
            1 => Ok(InputGenCmd::Generate),
            2 => Ok(InputGenCmd::GetRemain),
            3 => Ok(InputGenCmd::ExecCB),
            _ => Err("Invalid value for Status"),
        }
    }
}

impl TryFrom<u32> for InputGenStatus {
    type Error = &'static str;

    fn try_from(value: u32) -> Result<Self, Self::Error> {
        match value {
            0 => Ok(InputGenStatus::BOOTED),
            1 => Ok(InputGenStatus::READY),
            2 => Ok(InputGenStatus::WIP),
            _ => Err("Invalid value for Status"),
        }
    }
}

impl TryFrom<u32> for InputGenResult {
    type Error = &'static str;

    fn try_from(value: u32) -> Result<Self, Self::Error> {
        match value {
            0 => Ok(InputGenResult::Empty),
            1 => Ok(InputGenResult::Done),
            2 => Ok(InputGenResult::Remain),
            _ => Err("Invalid value for Status"),
        }
    }
}
