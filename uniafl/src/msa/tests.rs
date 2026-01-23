use super::start_fuzz_loop;
use std::path::PathBuf;

fn config_path() -> PathBuf {
    let path = std::env::var("UNIAFL_CONFIG").expect("Fail to get UNIAFL_CONFIG in env");
    PathBuf::from(&path)
}
#[test]
#[ignore]
fn check_fuzzer() {
    let conf = config_path();
    start_fuzz_loop(&conf);
}
