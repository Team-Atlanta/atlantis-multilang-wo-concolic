use super::executor::ExecutorConf;
use crate::{
    common::{sem_lock::SemLock, utils},
    msa::manager::MsaManager,
};
use fuzzdb::Language;
use std::io::Read;
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Child, ChildStderr, ChildStdout, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
macro_rules! read_output {
    ( $x:expr ) => {{
        let mut buf = Vec::new();
        let mut byte = [0; 1];
        let mut end = false;
        loop {
            let mut len = 0;
            loop {
                if let Ok(n) = $x.read(&mut byte) {
                    len += n;
                    buf.push(byte[0]);
                    if byte[0] == b'\n' {
                        break;
                    }
                } else {
                    break;
                }
            }
            if len == MSG_IN_WAIT_NEW_LINE.len() {
                if buf.ends_with(MSG_IN_WAIT_NEW_LINE.as_bytes()) {
                    end = false;
                    break;
                }
            }
            if len == MSG_END.len() {
                if buf.ends_with(MSG_END.as_bytes()) {
                    end = true;
                    break;
                }
            }
        }
        if end {
            (true, buf[..buf.len() - MSG_END.len()].to_vec())
        } else {
            (
                false,
                buf[..buf.len() - MSG_IN_WAIT_NEW_LINE.len()].to_vec(),
            )
        }
    }};
}

pub fn prepare_standalone(
    stdout: Arc<Mutex<ChildStdout>>,
    stderr: Arc<Mutex<ChildStderr>>,
    stdout_available: Option<bool>,
) {
    let stdout = stdout.clone();
    let stderr = stderr.clone();
    let h1 = thread::spawn(move || match stdout_available {
        Some(true) => {
            read_output!(stdout.lock().unwrap());
        }
        _ => (),
    });
    let h2 = thread::spawn(move || read_output!(stderr.lock().unwrap()));
    h1.join().ok();
    h2.join().ok();
}

pub struct ExecRunner {
    sem_lock: SemLock,
    proc: Child,
    stdout: Option<Arc<Mutex<ChildStdout>>>,
    stderr: Option<Arc<Mutex<ChildStderr>>>,
    stdout_available: Option<bool>,
}

const MSG_IN_WAIT: &str = "UNIAFL_MSG_IN_WAIT";
const MSG_IN_WAIT_NEW_LINE: &str = "UNIAFL_MSG_IN_WAIT\n";
const MSG_END: &str = "UNIAFL_MSG_END\n";
impl ExecRunner {
    pub fn new(
        msa_mgr: &MsaManager,
        conf: &ExecutorConf,
        executor_dir: &String,
        worker_idx: i32,
    ) -> Self {
        Self::new_with(msa_mgr, conf, executor_dir, worker_idx, false)
    }

    pub fn new_with(
        msa_mgr: &MsaManager,
        conf: &ExecutorConf,
        executor_dir: &String,
        worker_idx: i32,
        standalone: bool,
    ) -> Self {
        let executor_name = Path::new(executor_dir)
            .file_name()
            .unwrap()
            .to_str()
            .unwrap();
        let sem_key_name = format!("{}-{}-{}", conf.harness_name, executor_name, worker_idx);
        let sem_lock = SemLock::new(sem_key_name, true);
        let stdout_available = if standalone {
            Some(Self::check_stdout_available(
                msa_mgr,
                conf,
                executor_dir,
                &sem_lock,
                standalone,
                worker_idx,
            ))
        } else {
            None
        };
        let cmd = Self::boot_up_cmd(
            msa_mgr,
            conf,
            executor_dir,
            standalone,
            worker_idx,
            None,
            stdout_available,
        );
        let mut proc = Self::boot_up(
            msa_mgr,
            &cmd,
            &sem_lock,
            &conf.harness_name,
            executor_dir,
            worker_idx,
            standalone,
        );
        if standalone {
            let stdout = Arc::new(Mutex::new(proc.stdout.take().unwrap()));
            let stderr = Arc::new(Mutex::new(proc.stderr.take().unwrap()));
            prepare_standalone(stdout.clone(), stderr.clone(), stdout_available);
            Self {
                sem_lock,
                proc,
                stdout: Some(stdout),
                stderr: Some(stderr),
                stdout_available,
            }
        } else {
            Self {
                sem_lock,
                proc,
                stdout: None,
                stderr: None,
                stdout_available,
            }
        }
    }

    fn boot_up_cmd(
        msa_mgr: &MsaManager,
        conf: &ExecutorConf,
        executor_dir: &String,
        standalone: bool,
        worker_idx: i32,
        standalone_stdout_test: Option<String>,
        stdout_available: Option<bool>,
    ) -> String {
        let harness_name = Path::new(&conf.harness_path)
            .file_name()
            .unwrap()
            .to_str()
            .unwrap();
        let corpus_dir = format!("{}/{}_{}_corpus", executor_dir, harness_name, worker_idx);
        std::fs::create_dir(&corpus_dir).ok();
        let cmd = if standalone {
            let core = msa_mgr.cores.ids[worker_idx as usize];
            let corpus_dir = format!("{}_executor_{}", corpus_dir, core.0);
            format!(
                "rm -rf '{}'; mkdir -p '{}'; run_fuzzer '{}' '{}'",
                corpus_dir, corpus_dir, harness_name, corpus_dir
            )
        } else {
            format!("run_fuzzer '{}' '{}' ", harness_name, corpus_dir)
        };
        let cmd = match Language::from(&conf.language) {
            Language::Jvm => format!(
                "{} --uniafl_coverage --redis_url={} -timeout=150 --keep_going=10000",
                cmd, conf.redis_url
            ),
            _ => cmd,
        };
        if standalone {
            if let Some(standalone_stdout_test) = standalone_stdout_test {
                format!("{} > {}", cmd, standalone_stdout_test)
            } else {
                match stdout_available {
                    Some(true) => format!(
                        "while true; do {}; echo -ne \"{}\"; echo -ne \"{}\" 1>&2; done",
                        cmd, MSG_END, MSG_END
                    ),
                    _ => format!(
                        "while true; do {} > /dev/null; echo -ne \"{}\" 1>&2; done",
                        cmd, MSG_END,
                    ),
                }
            }
        } else {
            format!("while true; do {} > /dev/null 2>&1; done", cmd)
        }
    }

    fn boot_up(
        msa_mgr: &MsaManager,
        cmd: &String,
        sem_lock: &SemLock,
        harness_name: &String,
        executor_dir: &String,
        worker_idx: i32,
        standalone: bool,
    ) -> Child {
        let core = msa_mgr.cores.ids[worker_idx as usize];
        let mut proc = unsafe {
            let mut proc = Command::new("bash");
            if standalone {
                proc.env("UNIAFL_MSG_IN_WAIT", MSG_IN_WAIT)
                    .env("ALWAYS_GET_COV", "TRUE");
            }
            proc.args(["-c", cmd.as_str()])
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .stdin(Stdio::piped())
                .pre_exec(move || {
                    core.set_affinity().ok();
                    libc::setsid();
                    // libc::setuid(1000);
                    // libc::setgid(1000);
                    Ok(())
                })
                .env("SEM_KEY", &sem_lock.key)
                .env("HARNESS_NAME", harness_name)
                .env("CUR_WORKER", format!("{}", worker_idx))
                .env("OUT", executor_dir)
                .env("SILENT_MODE", "TRUE")
                .env("SKIP_SEED_CORPUS", "1")
                .spawn()
                .expect("Fail to run_fuzzer")
        };
        proc
    }

    fn check_stdout_available(
        msa_mgr: &MsaManager,
        conf: &ExecutorConf,
        executor_dir: &String,
        sem_lock: &SemLock,
        standalone: bool,
        worker_idx: i32,
    ) -> bool {
        let stdout_test = format!("{}/stdout_test-{}", msa_mgr.workdir.display(), worker_idx);
        let cmd = Self::boot_up_cmd(
            msa_mgr,
            conf,
            executor_dir,
            standalone,
            worker_idx,
            Some(stdout_test.clone()),
            None,
        );
        let mut proc = Self::boot_up(
            msa_mgr,
            &cmd,
            &sem_lock,
            &conf.harness_name,
            executor_dir,
            worker_idx,
            standalone,
        );
        let mut stderr = proc.stderr.take().unwrap();
        read_output!(stderr);
        utils::force_kill(&mut proc);
        if let Ok(data) = std::fs::read_to_string(stdout_test) {
            data.contains(MSG_IN_WAIT)
        } else {
            false
        }
    }

    pub fn let_go(&self) {
        self.sem_lock.post_start();
        self.sem_lock.wait_end();
    }

    // stdout, stderr
    pub fn get_outputs(&mut self) -> (Vec<u8>, Vec<u8>) {
        let stdout = self.stdout.clone().unwrap();
        let stderr = self.stderr.clone().unwrap();
        let stdout_available = self.stdout_available.clone();
        let h1 = thread::spawn(move || match stdout_available {
            Some(true) => {
                let (end, out) = read_output!(stdout.lock().unwrap());
                if end {
                    read_output!(stdout.lock().unwrap());
                    read_output!(stdout.lock().unwrap());
                }
                out
            }
            _ => Vec::new(),
        });
        let h2 = thread::spawn(move || {
            let (end, err) = read_output!(stderr.lock().unwrap());
            if end {
                read_output!(stderr.lock().unwrap());
                read_output!(stderr.lock().unwrap());
            }
            err
        });
        self.let_go();
        (h1.join().unwrap(), h2.join().unwrap())
    }
}

impl Drop for ExecRunner {
    fn drop(&mut self) {
        utils::force_kill(&mut self.proc);
    }
}
