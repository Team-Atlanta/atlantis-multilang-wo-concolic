#![allow(unused)]

use hex;
use libafl::executors::ExitKind;
use serde::{Deserialize, Serialize};
use sha1::{Digest, Sha1};
use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Command;

use crate::common::utils;

pub enum PL {
    C,
    Java,
    Other,
}

pub struct CP {
    path: PathBuf,
    yaml_path: PathBuf,

    name: String,
    lang: PL,
    srcs: HashMap<String, Src>,
    harnesses: HashMap<String, Harness>,
    sanitizers: HashMap<String, Vec<u8>>,
}

struct Src {
    address: String,
    r#ref: String,
    artifacts: Vec<PathBuf>,
}

struct Harness {
    name: String,
    source: PathBuf,
    binary: PathBuf,
}

#[derive(Serialize, Deserialize, Debug)]
struct CpYaml {
    cp_name: String,
    language: String,
    cp_sources: HashMap<String, SrcYaml>,
    sanitizers: HashMap<String, String>,
    harnesses: HashMap<String, HarnessYaml>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
struct SrcYaml {
    address: String,
    r#ref: String,
    artifacts: Vec<String>,
}

#[derive(Serialize, Deserialize, Debug, Clone)]
struct HarnessYaml {
    name: String,
    source: String,
    binary: String,
}

pub fn sha1hash(data: &[u8]) -> String {
    let mut hasher = Sha1::new();
    hasher.update(data);
    hex::encode(hasher.finalize())
}

impl CP {
    pub fn from_path(path: PathBuf) -> CP {
        let yaml_path = path.join("project.yaml");
        if !yaml_path.exists() {
            panic!("{} does not have project.yaml", path.display());
        }
        let yaml = utils::load_yaml::<CpYaml>(&yaml_path).expect("Invalid project.yaml");
        CP {
            path: path.clone(),
            yaml_path: yaml_path,
            name: yaml.cp_name,
            lang: PL::from_str(yaml.language.as_str()),
            srcs: yaml
                .cp_sources
                .iter()
                .map(|(k, v)| (k.clone(), Src::from_yaml(&path, v.clone())))
                .collect(),
            harnesses: yaml
                .harnesses
                .iter()
                .map(|(k, v)| (k.clone(), Harness::from_yaml(&path, v.clone())))
                .collect(),
            sanitizers: yaml
                .sanitizers
                .iter()
                .map(|(k, v)| (k.clone(), v.clone().into_bytes()))
                .collect(),
        }
    }

    pub fn check_sanitizer(&self, output: &[u8]) -> Option<(String, &[u8])> {
        for (s_id, s_str) in &self.sanitizers {
            if output.windows(s_str.len()).any(|w| w == s_str) {
                return Some((s_id.clone(), s_str));
            }
        }
        None
    }

    pub fn run_pov(&self, harness_name: &str, input_path: &PathBuf) -> ExitKind {
        let output = Command::new("./run.sh")
            .current_dir(&self.path)
            .args(["run_pov", input_path.to_str().unwrap(), harness_name])
            .output()
            .expect("Fail to run_pov");
        if !output.status.success() {
            ExitKind::Timeout
        } else if self.check_sanitizer(&output.stdout).is_some()
            || self.check_sanitizer(&output.stderr).is_some()
        {
            ExitKind::Crash
        } else {
            ExitKind::Ok
        }
    }

    pub fn submit_pov(&self, harness_id: &str, input_path: &PathBuf, uniq: Option<&[u8]>) {
        let hash = uniq.map(sha1hash).unwrap_or_default();
        let args = vec![
            "-m",
            "libCRS.submit",
            "submit_vd",
            "--finder",
            "UniAFL",
            "--harness",
            harness_id,
            "--pov",
            input_path.to_str().unwrap(),
            "--sanitizer-output-hash",
            &hash,
        ];
        Command::new("python3")
            .args(args)
            .output()
            .expect("Fail to submit_pov");
    }

    pub fn basic_uniqueness<'a>(output: &'a [u8], sanitizer_str: &'a [u8]) -> &'a [u8] {
        let last = output
            .windows(sanitizer_str.len())
            .rposition(|w| w == sanitizer_str)
            .expect("sanitizer_str must be in output");
        let sub_output = &output[last..];
        if let Some(ret) = sub_output.splitn(2, |&byte| byte == b'\n').next() {
            ret
        } else {
            sub_output
        }
    }
}

impl PL {
    pub fn from_str(s: &str) -> PL {
        match s {
            "c" => PL::C,
            "java" => PL::Java,
            _ => PL::Other,
        }
    }
}

impl Src {
    pub fn from_yaml(base: &PathBuf, yaml: SrcYaml) -> Src {
        Src {
            address: yaml.address,
            r#ref: yaml.r#ref,
            artifacts: yaml.artifacts.iter().map(|v| base.join(v)).collect(),
        }
    }
}

impl Harness {
    pub fn from_yaml(base: &PathBuf, yaml: HarnessYaml) -> Harness {
        Harness {
            name: yaml.name,
            source: base.join(yaml.source),
            binary: base.join(yaml.binary),
        }
    }
}
