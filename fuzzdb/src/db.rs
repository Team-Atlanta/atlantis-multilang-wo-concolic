use dashmap::{DashMap, DashSet};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::{BufReader, Read, Write};
use std::path::PathBuf;
use std::sync::Arc;

use super::utils;

#[derive(Deserialize, Debug)]
pub struct CovItem {
    pub src: String,
    pub lines: Vec<u32>,
}

pub type FuncName = String;
pub type SrcPath = String;

#[derive(Debug)]
pub struct Cov {
    func_map: HashMap<FuncName, CovItem>, // func name -> cov item
    src_map: HashMap<SrcPath, Vec<u32>>,  // src path -> lines (sorted)
}

#[derive(Serialize, Deserialize)]
pub struct FuzzDbConfig {
    harness_name: String,
    harness_path: String,
    cov_dir: String,
    workdir: String,
    processed_diff_path: Option<String>,
}

#[derive(Clone)]
pub enum Language {
    C,
    Cpp,
    Go,
    Rust,
    Python,
    Jvm,
    Swift,
    JavaScript,
}

#[derive(Clone, Debug, Hash, Eq, PartialEq)]
struct PovInfo {
    pov_name: String,
    callstack: Vec<String>,
}
#[derive(Clone, Debug)]
pub struct FuzzDB {
    cov_dir: PathBuf,
    harness_name: String,
    cov_cache: DashMap<String, Arc<Cov>>,
    bug_candidates: DashSet<Arc<BugCandidate>>,
    pov_infos: DashSet<Arc<PovInfo>>,
    mlla_workdir: PathBuf,
    diff_info: DashMap<String, Vec<(u32, u32)>>, // file_name -> (start_line, end_line)
    match_diff_info: DashMap<String, DiffMatchResult>, // seed_name -> DiffMatchResult
    acc_src_cov_map: DashMap<String, Vec<u32>>,  // src_path -> lines (sorted)
}

#[derive(Debug, Hash, Eq, PartialEq, Clone)]
pub struct LinePos {
    func_name: String,
    pub path: String,
    pub start: u32,
    pub end: u32,
}

#[derive(Debug, Eq)]
pub struct BugCandidate {
    harness_name: String,
    vuln: LinePos,
    keys: HashMap<LinePos, usize>,            // line pos -> weight
    should_be_taken: HashMap<LinePos, usize>, // line pos -> weight
    deprioritized: bool,
    json_str: String,
}

#[derive(Debug)]
pub struct MatchItem {
    pub func_name: bool,
    pub line: bool,
    pub pos: LinePos,
    pub weight: usize,
}

#[derive(Debug)]
pub struct MatchResult {
    pub vuln: MatchItem,
    pub keys: Vec<MatchItem>,
    pub should_be_taken: Vec<MatchItem>,
    pub deprioritized: bool,
}

#[derive(Debug, Clone)]
pub struct DiffMatchResult {
    pub num_file_matched: usize,
    pub num_line_range_matched: usize,
}

#[allow(dead_code)]
impl FuzzDB {
    fn parse_diff_info(diff_path: Option<String>) -> DashMap<String, Vec<(u32, u32)>> {
        if let Some(diff_path) = diff_path {
            if let Some(file) = File::open(diff_path).ok() {
                let reader = BufReader::new(file);
                let diff_info: Option<HashMap<String, Vec<(u32, u32)>>> =
                    serde_json::from_reader(reader).ok();
                if let Some(diff_info) = diff_info {
                    return DashMap::from_iter(diff_info.into_iter());
                }
            }
        }
        DashMap::new()
    }

    pub fn new(config_path: &PathBuf) -> Self {
        let config = utils::load_json::<FuzzDbConfig>(config_path)
            .unwrap_or_else(|e| panic!("Error in loading FuzzDbConfig: {}", e));
        Self {
            cov_dir: PathBuf::from(config.cov_dir),
            harness_name: config.harness_name,
            cov_cache: DashMap::new(),
            bug_candidates: DashSet::new(),
            pov_infos: DashSet::new(),
            mlla_workdir: PathBuf::from(config.workdir).join("mlla").join("workdir"),
            diff_info: Self::parse_diff_info(config.processed_diff_path),
            match_diff_info: DashMap::new(),
            acc_src_cov_map: DashMap::new(),
        }
    }

    #[cfg(test)]
    pub fn new_for_test(cov_dir: PathBuf, harness_name: String, diff_path: Option<String>) -> Self {
        Self {
            cov_dir,
            harness_name,
            cov_cache: DashMap::new(),
            bug_candidates: DashSet::new(),
            pov_infos: DashSet::new(),
            mlla_workdir: PathBuf::from("mlla"),
            diff_info: Self::parse_diff_info(diff_path),
            match_diff_info: DashMap::new(),
            acc_src_cov_map: DashMap::new(),
        }
    }

    pub fn load_cov(&self, seed_name: &String) -> Option<Arc<Cov>> {
        self.cov_cache
            .entry(seed_name.clone())
            .or_try_insert_with(|| {
                let json_file = self.cov_dir.join(format!("{}.cov", seed_name));
                if let Some(cov) = json_to_cov(&json_file) {
                    for (src_path, lines) in cov.src_map.iter() {
                        let mut cov_lines = self
                            .acc_src_cov_map
                            .entry(src_path.clone())
                            .or_insert_with(|| Vec::new());
                        cov_lines.extend(lines.iter().cloned());
                        cov_lines.sort();
                    }
                    Ok(cov)
                } else {
                    Err(())
                }
            })
            .ok()
            .map(|r| r.value().clone())
    }

    pub fn has_diff_info(&self) -> bool {
        self.diff_info.len() > 0
    }

    fn impl_match_diff_info(&self, seed_name: &String) -> DiffMatchResult {
        if let Some(cov) = self.load_cov(seed_name) {
            let mut num_file_matched = 0;
            let mut num_line_range_matched = 0;
            for cov_item in cov.func_map.values() {
                if let Some(diff_lines) = self.diff_info.get(&cov_item.src) {
                    num_file_matched += 1;
                    let mut matched_line_ranges = HashSet::new();
                    for line in &cov_item.lines {
                        for (start, end) in diff_lines.iter() {
                            if line >= start && line <= end {
                                matched_line_ranges.insert((start, end));
                                break;
                            }
                        }
                    }
                    num_line_range_matched += matched_line_ranges.len();
                }
            }
            DiffMatchResult {
                num_file_matched,
                num_line_range_matched,
            }
        } else {
            DiffMatchResult {
                num_file_matched: 0,
                num_line_range_matched: 0,
            }
        }
    }

    pub fn match_diff_info(&self, seed_name: &String) -> DiffMatchResult {
        if !self.has_diff_info() {
            return DiffMatchResult {
                num_file_matched: 0,
                num_line_range_matched: 0,
            };
        }
        if let Some(result) = self.match_diff_info.get(seed_name) {
            result.clone()
        } else {
            let result = self.impl_match_diff_info(seed_name);
            self.match_diff_info
                .insert(seed_name.clone(), result.clone());
            result
        }
    }

    // return (vulnerable_cov, key_cov)
    pub fn match_interesting_cov(&self, seed_name: &String) -> Vec<MatchResult> {
        if let Some(cov) = self.load_cov(seed_name) {
            let mut result = Vec::new();
            for candidate in self.bug_candidates.iter() {
                if let Some(m) = candidate.match_cov(&cov) {
                    result.push(m);
                }
            }
            result
        } else {
            Vec::new()
        }
    }

    fn add_pov_info(&self, pov_path: &PathBuf, crash_log: &[u8]) -> Arc<PovInfo> {
        let callstack = Self::extract_callstack_from_log(crash_log);
        let pov_name = pov_path.file_name().unwrap().to_str().unwrap();
        let pov_info = Arc::new(PovInfo {
            pov_name: pov_name.to_string(),
            callstack,
        });
        self.pov_infos.insert(pov_info.clone());
        pov_info
    }

    fn deprioritize_bug_candidates(&self, pov_info: Arc<PovInfo>) -> bool {
        let mut changed = false;
        let candidates = DashSet::new();
        for candidate in self.bug_candidates.iter() {
            let mut candidate: Arc<BugCandidate> = candidate.clone();
            if !candidate.deprioritized && self.is_related_to_pov(&candidate, &pov_info) {
                if let Some(mut_ref) = Arc::get_mut(&mut candidate) {
                    mut_ref.deprioritized = true;
                }
                changed = true;
            }
            candidates.insert(candidate);
        }
        if changed {
            self.bug_candidates.clear();
            for candidate in candidates.iter() {
                self.bug_candidates.insert(candidate.clone());
            }
        }
        changed
    }

    fn __is_related_to_pov(&self, cand: &BugCandidate, pov_info: &PovInfo) -> bool {
        if pov_info.callstack.contains(&cand.vuln.func_name) {
            return true;
        }
        if let Some(cov) = self.load_cov(&pov_info.pov_name) {
            if let Some(m) = cand.match_cov(&cov) {
                if m.vuln.func_name && m.vuln.line {
                    return true;
                }
                if m.keys.iter().all(|m| m.func_name && m.line) {
                    return true;
                }
                if m.should_be_taken.iter().all(|m| m.func_name && m.line) {
                    return true;
                }
            }
        }
        false
    }

    fn save_matched_bug_candidate(&self, cand: &BugCandidate) {
        if self.mlla_workdir.exists() {
            let bit_result_dir = self.mlla_workdir.join("found_BITs");
            std::fs::create_dir_all(&bit_result_dir).ok();
            let hash = md5::compute(cand.json_str.as_bytes());
            let path = bit_result_dir.join(format!("{:x}.json", hash));
            if !path.exists() {
                if let Ok(mut file) = File::create(path) {
                    file.write_all(cand.json_str.as_bytes()).ok();
                }
            }
        }
    }

    fn is_related_to_pov(&self, cand: &BugCandidate, pov_info: &PovInfo) -> bool {
        if self.__is_related_to_pov(cand, pov_info) {
            self.save_matched_bug_candidate(cand);
            true
        } else {
            false
        }
    }

    fn is_related_to_povs(&self, cand: &BugCandidate) -> bool {
        for pov_info in self.pov_infos.iter() {
            if self.is_related_to_pov(cand, &pov_info) {
                return true;
            }
        }
        false
    }

    // return true if something changed
    pub fn notify_cpv_found(&self, pov_path: &PathBuf, crash_log: &[u8]) -> bool {
        let pov_info = self.add_pov_info(pov_path, crash_log);
        self.deprioritize_bug_candidates(pov_info)
    }

    pub fn load_bcda_result(&self, result_path: &PathBuf) -> Option<bool> {
        let mut result = false;
        let file = File::open(result_path).ok()?;
        let reader = BufReader::new(file);
        let json: Value = serde_json::from_reader(reader).ok()?;
        for bit in json.get("BITs")?.as_array()? {
            if let Some(mut cand) = BugCandidate::from_json(bit) {
                if cand.harness_name == self.harness_name {
                    result = true;
                    if let Some(mut old) = self.bug_candidates.remove(&cand) {
                        if let Some(old) = Arc::get_mut(&mut old) {
                            old.merge(&cand);
                        }
                        self.bug_candidates.insert(old);
                    } else {
                        if self.is_related_to_povs(&cand) {
                            cand.deprioritized = true;
                        }
                        self.bug_candidates.insert(Arc::new(cand));
                    }
                }
            }
        }
        Some(result)
    }

    pub fn extract_callstack_from_log(log: &[u8]) -> Vec<String> {
        let mut callstack = Vec::new();
        for line in log.split(|&b| b == b'\n') {
            if line.starts_with(b"\tat ") {
                // Jazzer
                let line = &line[4..];
                if let Some(end) = utils::find_subarr(line, b"(") {
                    let func_name = &line[..end];
                    let func_name = String::from_utf8_lossy(func_name);
                    callstack.push(func_name.to_string());
                }
            } else if line.starts_with(b"    #") {
                // libFuzzer
                let line = &line[5..];
                if let Some(end) = utils::find_subarr(line, b" in ") {
                    let line = &line[end + 4..];
                    if let Some(end) = utils::find_subarr(line, b" ") {
                        let func_name = &line[..end];
                        let func_name = String::from_utf8_lossy(func_name);
                        callstack.push(func_name.to_string());
                    }
                }
            }
        }
        callstack
    }

    pub fn collect_interesting_functions(&self) -> HashSet<String> {
        let mut interesting_functions = HashSet::new();

        for bug_candidate in self.bug_candidates.iter() {
            for line_pos in bug_candidate.keys.keys() {
                interesting_functions.insert(line_pos.func_name.clone());
            }
        }

        interesting_functions
    }

    pub fn has_acc_src_cov_in_range(&self, src_path: &SrcPath, start: u32, end: u32) -> bool {
        if let Some(lines) = self.acc_src_cov_map.get(src_path) {
            match lines.binary_search(&start) {
                Ok(_) => true,
                Err(idx) => idx < lines.len() && lines[idx] <= end,
            }
        } else {
            false
        }
    }
}

fn json_to_cov(json_file: &PathBuf) -> Option<Arc<Cov>> {
    let mut file = File::open(&json_file).ok()?;
    let mut contents = String::new();
    file.read_to_string(&mut contents).ok()?;
    let json_value: Value = serde_json::from_str(&contents).ok()?;
    let mut cov = Cov::new();
    for (k, v) in json_value.as_object()?.iter() {
        if let Ok(item) = serde_json::from_value::<CovItem>(v.clone()) {
            if let Some(lines) = cov.src_map.get_mut(&item.src) {
                lines.extend(&item.lines);
            } else {
                cov.src_map.insert(item.src.clone(), item.lines.clone());
            }
            cov.func_map.insert(k.clone(), item);
        }
    }
    for (_, lines) in cov.src_map.iter_mut() {
        lines.sort();
    }
    Some(Arc::new(cov))
}

impl From<&str> for Language {
    fn from(s: &str) -> Self {
        match s {
            "c" => Self::C,
            "c++" => Self::Cpp,
            "go" => Self::Go,
            "rust" => Self::Rust,
            "python" => Self::Python,
            "jvm" => Self::Jvm,
            "swift" => Self::Swift,
            "javascript" => Self::JavaScript,
            _ => unreachable!(),
        }
    }
}

impl From<String> for Language {
    fn from(s: String) -> Self {
        Language::from(s.as_str())
    }
}

impl From<&String> for Language {
    fn from(s: &String) -> Self {
        Language::from(s.as_str())
    }
}

impl BugCandidate {
    pub fn from_json(value: &Value) -> Option<Self> {
        let harness_name = value.get("harness_name")?.as_str()?.to_string();
        let vuln = LinePos::from_json(value.get("func_location")?)?;
        let keys = value
            .get("key_conditions")?
            .as_array()?
            .iter()
            .map(LinePos::from_json)
            .flatten()
            .map(|pos| (pos, 1))
            .collect();
        let should_be_taken = value
            .get("should_be_taken_lines")?
            .as_array()?
            .iter()
            .map(LinePos::from_json)
            .flatten()
            .map(|pos| (pos, 1))
            .collect();
        Some(Self {
            harness_name,
            vuln,
            keys,
            should_be_taken,
            deprioritized: false,
            json_str: value.to_string(),
        })
    }

    pub fn merge(&mut self, other: &Self) {
        for (pos, weight) in other.keys.iter() {
            *self.keys.entry(pos.clone()).or_insert(0) += weight;
        }
        for (pos, weight) in other.should_be_taken.iter() {
            *self.should_be_taken.entry(pos.clone()).or_insert(0) += weight;
        }
    }

    pub fn match_cov(&self, cov: &Arc<Cov>) -> Option<MatchResult> {
        let vuln = self.vuln.match_cov(cov, 1);
        let keys: Vec<MatchItem> = self
            .keys
            .iter()
            .map(|(pos, weight)| pos.match_cov(cov, *weight))
            .flatten()
            .collect();
        let should_be_taken: Vec<MatchItem> = self
            .should_be_taken
            .iter()
            .map(|(pos, weight)| pos.match_cov(cov, *weight))
            .flatten()
            .collect();
        if keys.len() == 0 && should_be_taken.len() == 0 && vuln.is_none() {
            None
        } else {
            let vuln = vuln.unwrap_or(MatchItem::empty(self.vuln.clone()));
            Some(MatchResult {
                vuln,
                keys,
                should_be_taken,
                deprioritized: self.deprioritized,
            })
        }
    }
}

impl LinePos {
    pub fn from_json(value: &Value) -> Option<Self> {
        let func_name = Self::parse_func_name(value.get("func_name")?.as_str()?.to_string());
        let path = value.get("file_path")?.as_str()?.to_string();
        let start = value.get("start_line")?.as_number()?.as_u64()? as u32;
        let end = value.get("end_line")?.as_number()?.as_u64()? as u32;
        Some(Self {
            func_name,
            path,
            start,
            end,
        })
    }

    pub fn parse_func_name(name: String) -> String {
        let name = name.as_str();
        let name = if let Some(paren_idx) = name.find('(') {
            &name[..paren_idx]
        } else {
            name
        };
        let name = if let Some(space_idx) = name.rfind(" ") {
            &name[space_idx + 1..]
        } else {
            name
        };
        name.to_string()
    }

    pub fn match_cov(&self, cov: &Arc<Cov>, weight: usize) -> Option<MatchItem> {
        let mut match_func_name = false;
        let mut match_line = false;
        for (func_name, item) in cov.func_map.iter() {
            if item.src == self.path {
                if func_name.starts_with(&self.func_name) {
                    match_func_name = true;
                }
                for line in self.start..self.end {
                    if item.lines.contains(&line) {
                        match_line = true;
                        break;
                    }
                }
            }
            if match_func_name && match_line {
                break;
            }
        }
        if !match_func_name && !match_line {
            None
        } else {
            Some(MatchItem {
                func_name: match_func_name,
                line: match_line,
                pos: self.clone(),
                weight,
            })
        }
    }
}

impl MatchItem {
    pub fn empty(pos: LinePos) -> Self {
        Self {
            func_name: false,
            line: false,
            pos,
            weight: 0,
        }
    }
}

impl std::hash::Hash for BugCandidate {
    fn hash<H: std::hash::Hasher>(&self, state: &mut H) {
        self.harness_name.hash(state);
        self.vuln.hash(state);
    }
}

impl PartialEq for BugCandidate {
    fn eq(&self, other: &Self) -> bool {
        self.harness_name == other.harness_name && self.vuln == other.vuln
    }
}
impl Cov {
    pub fn new() -> Self {
        Self {
            func_map: HashMap::new(),
            src_map: HashMap::new(),
        }
    }

    pub fn new_with_func_map(func_map: HashMap<FuncName, CovItem>) -> Self {
        Self {
            func_map,
            src_map: HashMap::new(),
        }
    }

    pub fn func_names(&self) -> impl Iterator<Item = &FuncName> {
        self.func_map.keys()
    }

    pub fn get_func_cov(&self, func_name: &FuncName) -> Option<&CovItem> {
        self.func_map.get(func_name)
    }

    pub fn get_src_cov(&self, src_path: &SrcPath) -> Option<&Vec<u32>> {
        self.src_map.get(src_path)
    }

    pub fn has_src_cov_in_range(&self, src_path: &SrcPath, start: u32, end: u32) -> bool {
        if let Some(lines) = self.src_map.get(src_path) {
            match lines.binary_search(&start) {
                Ok(_) => true,
                Err(idx) => idx < lines.len() && lines[idx] <= end,
            }
        } else {
            false
        }
    }
}
