use dashmap::{DashMap, DashSet};
use fuzzdb::{Cov, CovItem, FuncName};
use rand::seq::SliceRandom;
use rand::thread_rng;
use rand::Rng;
use std::collections::{HashMap, HashSet};
use std::env;
use std::fs::File;
use std::io::{self, BufRead};
use std::path::Path;
use std::process::Command;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

pub type Tokens = HashSet<Vec<u8>>;
pub struct DictGen {
    dictgen_path: String,
    workdir: String,
    pub dict: DashMap<FuncName, Tokens>,
    dict_by_seed: DashMap<String, Tokens>,
    running: AtomicBool,
    max_function_count: usize,
    function_count: AtomicUsize,
    failure_count_per_function: DashMap<FuncName, usize>,
    use_bcda_result: bool,
}

enum FilterType {
    RandomHalf,
    All,
    None,
}

impl DictGen {
    pub fn new(
        dictgen_path: &String,
        workdir: &String,
        max_function_count: usize,
        use_bcda_result: bool,
    ) -> Self {
        Self {
            dictgen_path: dictgen_path.clone(),
            workdir: workdir.clone(),
            dict: DashMap::new(),
            dict_by_seed: DashMap::new(),
            running: AtomicBool::new(false),
            max_function_count,
            function_count: AtomicUsize::new(0),
            failure_count_per_function: DashMap::new(),
            use_bcda_result,
        }
    }

    fn filter_coverage(&self, cov: &Cov, filter_type: FilterType) -> Cov {
        let mut rng = thread_rng();
        let keys: Vec<&FuncName> = cov.func_names().collect();

        // XXX: This part can be extended.
        let selected_keys: Vec<&FuncName> = match filter_type {
            FilterType::RandomHalf => keys
                .choose_multiple(&mut rng, keys.len() / 2)
                .cloned()
                .collect(),
            FilterType::All => keys.clone(),
            FilterType::None => vec![],
        };

        let mut filtered_cov = HashMap::new();
        for key in selected_keys {
            if let Some(value) = cov.get_func_cov(key) {
                // XXX: need to confirm it is safe
                let new_cov_item = CovItem {
                    src: value.src.clone(),
                    lines: value.lines.clone(),
                };
                filtered_cov.insert(key.clone(), new_cov_item);
            }
        }
        Cov::new_with_func_map(filtered_cov)
    }

    fn is_interesting_function(
        &self,
        func: &String,
        bcda_targets: Option<&HashSet<String>>,
    ) -> bool {
        return !self.is_blacklisted_function(func)
            && self.is_interesting_function_by_bcda(func, bcda_targets);
    }

    pub fn is_blacklisted_function(&self, func: &String) -> bool {
        // case-insensitive keywords
        let BLACKLISTED_KEYWORDS: [&str; 10] = [
            "llvm", "malloc", "getName", "printf", "asInt", "asFloat", "asString", "asBool",
            "asBytes", "calloc",
        ];
        for keyword in BLACKLISTED_KEYWORDS.iter() {
            if func
                .to_lowercase()
                .contains(keyword.to_lowercase().as_str())
            {
                return true;
            }
        }
        return false;
    }

    fn is_interesting_function_by_bcda(
        &self,
        func: &String,
        bcda_targes: Option<&HashSet<String>>,
    ) -> bool {
        if !self.use_bcda_result {
            return true;
        }
        if let Some(bcda_targes) = bcda_targes {
            if bcda_targes.contains(func) {
                return true;
            }
        }
        let mut rng = rand::thread_rng();
        rng.gen_range(0..10) == 0
    }

    fn function_failed_too_many_times(&self, func: &String) -> bool {
        let failure_threshold = 3;
        if let Some(count) = self.failure_count_per_function.get(func) {
            return *count >= failure_threshold;
        }
        false
    }

    fn get_normal_tokens(
        &self,
        seed_name: &String,
        cov: &Cov,
        target_funcs: Option<HashSet<String>>,
        run_dictgen: bool,
    ) -> Tokens {
        if let Some(tokens) = self.dict_by_seed.get(seed_name) {
            return tokens.clone();
        }

        let mut ret_tokens = Tokens::new();
        let mut store_seed_dict = true;
        let mut found_bof_token = false;

        for func in cov.func_names() {
            if !self.is_interesting_function(func, target_funcs.as_ref()) {
                continue;
            }

            if let Some(tokens) = self.dict.get(func) {
                ret_tokens.extend(tokens.clone());
            } else if run_dictgen {
                if self.function_failed_too_many_times(func) {
                    #[cfg(feature = "log")]
                    println!("Skipping function {} due to too many failures", func);
                    continue;
                }

                if self.function_count.load(Ordering::SeqCst) >= self.max_function_count {
                    #[cfg(feature = "log")]
                    println!("Reached max function count. Skipping the function {}", func);
                    continue;
                }

                if let Some(mut tokens) = self.generate_tokens(func) {
                    (tokens, found_bof_token) = self.filter_bof_token(tokens, found_bof_token);
                    tokens = self.filter_one_byte_token(tokens);
                    ret_tokens.extend(tokens.clone());
                    let tokens = tokens.clone();
                    self.dict.insert(func.clone(), tokens);
                    self.function_count.fetch_add(1, Ordering::SeqCst);
                    #[cfg(feature = "log")]
                    println!(
                        "Incremented analyzed function count {}",
                        self.function_count.load(Ordering::SeqCst)
                    );
                } else {
                    self.failure_count_per_function
                        .entry(func.clone())
                        .and_modify(|count| *count += 1)
                        .or_insert(1);
                    store_seed_dict = false;
                }
            }
        }

        if run_dictgen && store_seed_dict {
            self.dict_by_seed
                .insert(seed_name.clone(), ret_tokens.clone());
        }
        ret_tokens
    }

    fn filter_bof_token(&self, mut tokens: Tokens, found_bof_token: bool) -> (Tokens, bool) {
        let is_bof = |token: &Vec<u8>| token.len() >= 128 && token.iter().all(|&b| b == b'A');
        if found_bof_token {
            tokens.retain(|t| !is_bof(t));
            return (tokens, true);
        }

        let mut found_one = false;
        tokens.retain(|t| {
            if is_bof(t) {
                if !found_one {
                    found_one = true;
                    true
                } else {
                    false
                }
            } else {
                true
            }
        });
        (tokens, found_one)
    }

    fn filter_one_byte_token(&self, mut tokens: Tokens) -> Tokens {
        tokens.retain(|token| token.len() > 1);
        tokens
    }

    fn get_tokens_from_diff(
        &self,
        diff_path: Option<String>,
        cov: &Cov,
        run_dictgen: bool,
    ) -> Tokens {
        let mut ret_tokens = Tokens::new();
        if diff_path.is_none() {
            return ret_tokens;
        }

        let fake_seed_name = "<DIFF>".to_string();

        let mut rng = thread_rng();
        // diff tokens are not stable. generate new ones 2% of the time.
        if !run_dictgen || rng.gen_bool(0.98) {
            if let Some(tokens) = self.dict_by_seed.get(&fake_seed_name) {
                return tokens.clone();
            }
        }

        let diff_path = diff_path.as_ref().unwrap();
        if run_dictgen {
            self.generate_tokens_from_diff(diff_path, &cov)
                .map(|tokens| ret_tokens.extend(tokens));
            self.dict_by_seed
                .insert(fake_seed_name.clone(), ret_tokens.clone());
        }
        ret_tokens
    }

    pub fn get_tokens(
        &self,
        seed_name: &String,
        cov: &Cov,
        target_funcs: Option<HashSet<String>>,
        diff_path: Option<String>,
    ) -> (Tokens, Tokens) {
        let cov = self.filter_coverage(cov, FilterType::All);
        let run_dictgen = self
            .running
            .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .is_ok();
        let diff_tokens = self.get_tokens_from_diff(diff_path, &cov, run_dictgen);
        let normal_tokens = self.get_normal_tokens(seed_name, &cov, target_funcs, run_dictgen);
        if run_dictgen {
            self.running.store(false, Ordering::SeqCst);
        }
        (normal_tokens, diff_tokens)
    }

    pub fn generate_tokens(&self, func: &String) -> Option<Tokens> {
        let output = self.invoke_dictgen(func)?;
        if output.status.success() {
            Some(self.parse_output_to_tokens(&output.stdout))
        } else {
            None
        }
    }

    fn invoke_dictgen(&self, func: &str) -> Option<std::process::Output> {
        let path = env::var("CP_SRC_PATH").ok()?;
        Command::new("python3")
            .arg(&self.dictgen_path)
            .arg("--path")
            .arg(path)
            .arg("--funcs")
            .arg(func)
            .arg("--output")
            .arg("STDOUT") // dictgen will print generate tokens into stdout
            .arg("--no-extract-parsable-string")
            .env("WORKDIR", &self.workdir)
            .output()
            .ok()
    }

    fn generate_tokens_from_diff(&self, diff_path: &String, cov: &Cov) -> Option<Tokens> {
        let output = self.invoke_dicten_with_diff(diff_path)?;
        if output.status.success() {
            Some(self.parse_output_to_tokens(&output.stdout))
        } else {
            None
        }
    }

    fn invoke_dicten_with_diff(&self, diff_path: &String) -> Option<std::process::Output> {
        let path = env::var("CP_SRC_PATH").ok()?;
        Command::new("python3")
            .arg(&self.dictgen_path)
            .arg("--path")
            .arg(path)
            .arg("--delta")
            .arg("--refdiff")
            .arg(diff_path)
            .arg("--output")
            .arg("STDOUT") // dictgen will print generate tokens into stdout
            .arg("--no-extract-parsable-string")
            .env("WORKDIR", &self.workdir)
            .output()
            .ok()
    }

    pub fn parse_output_to_tokens(&self, output: &[u8]) -> Tokens {
        output
            .split(|&byte| byte == b'\n')
            .filter_map(|line| {
                if let Some(pos) = line.iter().position(|&byte| byte == b'=') {
                    let (key, value) = line.split_at(pos);
                    if value.len() < 1 {
                        return None;
                    }
                    let value = &value[1..];
                    if value.first() == Some(&b'"') {
                        self.parse_vec_to_token(value)
                    } else {
                        self.parse_integer_to_token(key, value)
                    }
                } else {
                    None
                }
            })
            .collect()
    }

    fn parse_vec_to_token(&self, value: &[u8]) -> Option<Vec<u8>> {
        if value.len() < 2 {
            return None;
        }
        let value = &value[1..value.len() - 1];
        let mut i = 0;
        let mut token = Vec::<u8>::new();
        while i < value.len() {
            if value[i] == b'\\' && i + 3 < value.len() && value[i + 1] == b'x' {
                if let (Some(high), Some(low)) = (hex_digit(value[i + 2]), hex_digit(value[i + 3]))
                {
                    token.push((high << 4) | low);
                }
                i += 4;
            } else {
                token.push(value[i]);
                i += 1;
            }
        }
        if !token.is_empty() {
            Some(token)
        } else {
            None
        }
    }

    fn parse_integer_to_token(&self, key: &[u8], value: &[u8]) -> Option<Vec<u8>> {
        // key encodes size and endian. ex) int-le-0 -> integer
        // (4bytes), little endian.
        // XXX: Currently, this does not consider the length of value
        let parts: Vec<&str> = std::str::from_utf8(key).ok()?.split('-').collect();
        if parts.len() < 3 {
            return None;
        }

        let (int_type, endian, _index) = (parts[0], parts[1], parts[2]);
        let size = match int_type {
            "short" => 2,
            "int" | "long" => 4,
            _ => return None,
        };

        let value_str = std::str::from_utf8(value).ok()?;
        let num = if value_str.starts_with("0x") || value_str.starts_with("0X") {
            u64::from_str_radix(&value_str[2..], 16).ok()?
        } else {
            value_str.parse::<u64>().ok()?
        };

        let bytes = match endian {
            "le" => num.to_le_bytes(),
            "be" => num.to_be_bytes(),
            _ => return None,
        };

        match endian {
            "le" => Some(bytes[..size].to_vec()),
            "be" => Some(bytes[bytes.len() - size..].to_vec()),
            _ => None,
        }
    }
}

fn hex_digit(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

fn read_lines<P>(filename: P) -> io::Result<io::Lines<io::BufReader<File>>>
where
    P: AsRef<Path>,
{
    let file = File::open(filename)?;
    Ok(io::BufReader::new(file).lines())
}
