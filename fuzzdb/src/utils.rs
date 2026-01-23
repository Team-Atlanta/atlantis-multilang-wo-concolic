use std::fs::File;
use std::io::BufReader;
use std::path::Path;

#[allow(dead_code)]
pub fn load_json<T: serde::de::DeserializeOwned>(path: impl AsRef<Path>) -> serde_json::Result<T> {
    let file = File::open(&path)
        .unwrap_or_else(|e| panic!("{} has an error: {}", path.as_ref().display(), e));
    let reader = BufReader::new(file);
    serde_json::from_reader(reader)
}

pub fn find_subarr<T: std::cmp::PartialEq>(arr: &[T], subarr: &[T]) -> Option<usize> {
    arr.windows(subarr.len())
        .position(|window| window == subarr)
}
