#![allow(unused)]

use crate::common::Error;
use libc::{
    chmod, clock_gettime, sem_close, sem_getvalue, sem_open, sem_post, sem_t, sem_timedwait,
    sem_unlink, sem_wait, timespec, CLOCK_REALTIME, O_CREAT,
};
use shared_memory::{Shmem, ShmemConf};
use std::ffi::CString;

pub struct SemLock {
    pub key: String,
    sem_start: *mut sem_t,
    sem_end: *mut sem_t,
}

impl SemLock {
    fn create_key(key: &String, post: &str) -> CString {
        CString::new(format!("{}.{}", key, post)).unwrap()
    }

    fn unlink_sem(key: &String, post: &str) {
        let key = Self::create_key(key, post);
        unsafe { sem_unlink(key.as_ptr()) };
    }

    fn create_sem(key: &String, post: &str, create: bool) -> *mut sem_t {
        let key = Self::create_key(key, post);
        if create {
            let ret = unsafe { sem_open(key.as_ptr(), O_CREAT, 0o666, 0) };
            let sem_path = CString::new(format!("/dev/shm/sem.{}", key.to_string_lossy())).unwrap();
            unsafe { chmod(sem_path.as_ptr(), 0o666) };
            ret
        } else {
            unsafe { sem_open(key.as_ptr(), 0) }
        }
    }

    fn refresh_sem(sem: *mut sem_t, key: &String, post: &str) -> *mut sem_t {
        unsafe {
            sem_close(sem);
        };
        Self::unlink_sem(key, post);
        Self::create_sem(key, post, true)
    }

    pub fn new(key: String, create: bool) -> Self {
        let sem_start = Self::create_sem(&key, "start", create);
        let sem_end = Self::create_sem(&key, "end", create);
        Self {
            key,
            sem_start,
            sem_end,
        }
    }

    pub fn new2(key: String, create: bool) -> Result<Self, Error> {
        let sem_start = Self::create_sem(&key, "start", create);
        if sem_start.is_null() {
            let err = std::io::Error::last_os_error();
            return Err(err.into());
        }
        let sem_end = Self::create_sem(&key, "end", create);
        if sem_end.is_null() {
            let err = std::io::Error::last_os_error();
            unsafe { sem_close(sem_start) };
            return Err(err.into());
        }
        Ok(Self {
            key,
            sem_start,
            sem_end,
        })
    }

    #[inline]
    pub fn destroy(&self) {
        unsafe {
            sem_close(self.sem_start);
            sem_close(self.sem_end);
        }
        Self::unlink_sem(&self.key, "start");
        Self::unlink_sem(&self.key, "end");
    }

    #[inline]
    pub fn post_start(&self) {
        loop {
            if unsafe { sem_post(self.sem_start) } == 0 {
                break;
            }
        }
    }

    #[inline]
    pub fn post_end(&self) {
        loop {
            if unsafe { sem_post(self.sem_end) } == 0 {
                break;
            }
        }
    }

    #[inline]
    pub fn start_consumed(&self) -> bool {
        let mut ret = 0;
        unsafe { sem_getvalue(self.sem_start, &mut ret) };
        ret == 0
    }

    #[inline]
    pub fn end_consumed(&self) -> bool {
        let mut ret = 0;
        unsafe { sem_getvalue(self.sem_end, &mut ret) };
        ret == 0
    }

    #[inline]
    pub fn wait_start(&self) {
        loop {
            if unsafe { sem_wait(self.sem_start) } == 0 {
                break;
            }
        }
    }

    #[inline]
    pub fn wait_end(&self) {
        loop {
            if unsafe { sem_wait(self.sem_end) } == 0 {
                break;
            }
        }
    }

    pub fn refresh(&mut self) {
        self.sem_start = Self::refresh_sem(self.sem_start, &self.key, "start");
        self.sem_end = Self::refresh_sem(self.sem_end, &self.key, "end");
    }
}

pub struct SingleSem {
    key: String,
    suffix: String,
    sem: *mut sem_t,
}

impl SingleSem {
    fn create_key(key: &str, post: &str) -> CString {
        CString::new(format!("{}.{}", key, post)).unwrap()
    }

    fn unlink_sem(key: &str, post: &str) {
        let key = Self::create_key(key, post);
        unsafe { sem_unlink(key.as_ptr()) };
    }

    fn create_sem(key: &str, post: &str, create: bool) -> *mut sem_t {
        let key = Self::create_key(key, post);
        if create {
            let ret = unsafe { sem_open(key.as_ptr(), O_CREAT, 0o666, 0) };
            let sem_path = CString::new(format!("/dev/shm/sem.{}", key.to_string_lossy())).unwrap();
            unsafe { chmod(sem_path.as_ptr(), 0o666) };
            ret
        } else {
            unsafe { sem_open(key.as_ptr(), 0) }
        }
    }

    fn refresh_sem(sem: *mut sem_t, key: &str, post: &str) -> *mut sem_t {
        unsafe {
            sem_close(sem);
        };
        Self::unlink_sem(key, post);
        Self::create_sem(key, post, true)
    }

    pub fn new(key: &str, suffix: &str, create: bool) -> Self {
        let sem = Self::create_sem(key, suffix, create);
        Self {
            key: key.to_string(),
            suffix: suffix.to_string(),
            sem,
        }
    }

    pub fn new2(key: &str, suffix: &str, create: bool) -> Result<Self, Error> {
        let sem = Self::create_sem(key, suffix, create);
        if sem.is_null() {
            let err = std::io::Error::last_os_error();
            return Err(err.into());
        }
        Ok(Self {
            key: key.to_string(),
            suffix: suffix.to_string(),
            sem,
        })
    }

    #[inline]
    pub fn destroy(&self) {
        unsafe {
            sem_close(self.sem);
        }
        Self::unlink_sem(&self.key, &self.suffix);
    }

    #[inline]
    pub fn post(&self) {
        loop {
            if unsafe { sem_post(self.sem) } == 0 {
                break;
            }
        }
    }

    #[inline]
    pub fn get_consumed(&self) -> bool {
        let mut ret = 0;
        unsafe { sem_getvalue(self.sem, &mut ret) };
        ret == 0
    }

    #[inline]
    pub fn wait(&self) {
        loop {
            if unsafe { sem_wait(self.sem) } == 0 {
                break;
            }
        }
    }

    pub fn refresh(&mut self) {
        self.sem = Self::refresh_sem(self.sem, &self.key, &self.suffix);
    }
}
