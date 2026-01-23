use std::{
    collections::{HashMap, HashSet, TryReserveError},
    mem,
    num::{ParseIntError, TryFromIntError},
};

use bytes::BufMut;
pub use parser::SliceId;
use thiserror::Error;

use crate::{Endianness, StringFormat, TestLang};

mod parser;

#[derive(Clone, Debug, PartialEq)]
pub struct TestLangBlob {
    pub slices: Vec<(SliceId, Vec<u8>)>,
    pub metadata: TestLangBlobMetadata,
}

#[derive(Clone, Debug, PartialEq)]
pub enum StagedMetadata {
    Immutable(SliceId, SliceId, Option<SliceId>),
    Terminator(SliceId, SliceId),
    // Immutable strings doesn't need to be distinguished from immutable fields.
    Mutable(SliceId, SliceId, Option<Endianness>, Option<SliceId>),
    MutableString(SliceId, SliceId, Option<StringFormat>, Option<SliceId>),
    Array(SliceId, SliceId, String, Vec<SliceId>, Option<SliceId>),
    RecordField(SliceId, SliceId, String, SliceId, Option<SliceId>),
    Struct(SliceId, SliceId, String, Vec<SliceId>),
    Union(SliceId, SliceId, String, SliceId),
    Choosable(SliceId, SliceId, Option<SliceId>, Vec<Vec<u8>>),
}

#[derive(Clone, Debug, PartialEq)]
pub struct TestLangBlobMetadata {
    pub children: HashMap<SliceId, Vec<SliceId>>,
    pub parent: HashMap<SliceId, SliceId>,
    pub record_ref_name: HashMap<SliceId, String>,
    // TODO: Add references, reference_of
    pub size: HashMap<SliceId, SliceId>,
    pub size_of: HashMap<SliceId, HashSet<SliceId>>,
    pub terminator: HashMap<SliceId, SliceId>,
    pub mutable_fields: Vec<SliceId>,
    pub default_endian: Endianness,
    pub endianness: HashMap<SliceId, Option<Endianness>>,
    pub mutable_strings: HashSet<SliceId>,
    pub string_format: HashMap<SliceId, Option<StringFormat>>,
    pub choosable_fields: HashMap<SliceId, Vec<Vec<u8>>>,
    pub arrays: Vec<SliceId>,
    pub unions: Vec<SliceId>,
    pub cluster: HashMap<SliceId, SliceId>,
    pub next_id: SliceId,
    pub blob_size: usize,
    staged: Vec<StagedMetadata>,
}

#[derive(Debug, Error)]
pub enum ParseTestLangBlobError {
    #[error(
        "Tried impossible dereference: {0} This shouldn't happen, likely bug in testlang parser."
    )]
    DereferenceError(String),
    #[error("Blob contains unsupported/invalid data: {0}")]
    InvalidData(String),
    #[error("Testlang contains unsupported/invalid format: {0}")]
    InvalidTestlang(String),
    #[error("No matching const value constraint exists.")]
    ValueUnmatched,
}

impl From<TryFromIntError> for ParseTestLangBlobError {
    fn from(value: TryFromIntError) -> Self {
        Self::InvalidData(value.to_string())
    }
}

impl From<ParseIntError> for ParseTestLangBlobError {
    fn from(value: ParseIntError) -> Self {
        Self::InvalidData(value.to_string())
    }
}

impl TestLangBlob {
    pub fn get_bytes(&self, output: &mut Vec<u8>) -> Result<(), TryReserveError> {
        output.try_reserve(self.metadata.blob_size)?;
        self.slices.iter().for_each(|x| output.put(x.1.as_slice()));
        Ok(())
    }

    pub fn from_bytes(input: &[u8], testlang: &TestLang) -> Result<Self, ParseTestLangBlobError> {
        parser::parse_blob(input, testlang)
    }
}

impl From<TestLangBlob> for Vec<u8> {
    fn from(value: TestLangBlob) -> Self {
        value.slices.into_iter().flat_map(|x| x.1).collect()
    }
}

impl TestLangBlobMetadata {
    pub fn new(blob_size: usize, default_endian: Endianness) -> Self {
        Self {
            children: HashMap::new(),
            parent: HashMap::new(),
            record_ref_name: HashMap::new(),
            size: HashMap::new(),
            size_of: HashMap::new(),
            terminator: HashMap::new(),
            mutable_fields: Vec::new(),
            mutable_strings: HashSet::new(),
            string_format: HashMap::new(),
            default_endian,
            endianness: HashMap::new(),
            choosable_fields: HashMap::new(),
            arrays: Vec::new(),
            unions: Vec::new(),
            cluster: HashMap::new(),
            next_id: 0,
            blob_size,
            staged: Vec::new(),
        }
    }

    pub fn get_endian(&self, id: SliceId) -> Endianness {
        self.endianness
            .get(&id)
            .copied()
            .flatten()
            .unwrap_or(self.default_endian)
    }

    pub fn take_id(&mut self) -> SliceId {
        let id = self.next_id;
        self.next_id += 1;
        id
    }

    fn set_size_constraint(&mut self, target: SliceId, constraint: SliceId) {
        self.size.insert(target, constraint);
        self.size_of.entry(constraint).or_default().insert(target);
    }

    fn record_immutable(
        &mut self,
        id: SliceId,
        cluster: SliceId,
        size_constraint: Option<SliceId>,
    ) {
        if let Some(size_constraint) = size_constraint {
            self.set_size_constraint(id, size_constraint);
        }
        self.cluster.insert(id, cluster);
    }

    fn record_terminator(&mut self, id: SliceId, terminator_of: SliceId) {
        self.terminator.insert(terminator_of, id);
    }

    fn record_mutable(
        &mut self,
        id: SliceId,
        cluster: SliceId,
        endianness: Option<Endianness>,
        size_constraint: Option<SliceId>,
    ) {
        self.mutable_fields.push(id);
        self.endianness.insert(id, endianness);
        self.record_immutable(id, cluster, size_constraint);
    }

    fn record_mutable_string(
        &mut self,
        id: SliceId,
        cluster: SliceId,
        string_format: Option<StringFormat>,
        size_constraint: Option<SliceId>,
    ) {
        self.mutable_strings.insert(id);
        self.string_format.insert(id, string_format);
        self.record_mutable(id, cluster, None, size_constraint);
    }

    fn record_array(
        &mut self,
        id: SliceId,
        cluster: SliceId,
        items: impl AsRef<str>,
        children: Vec<SliceId>,
        size_constraint: Option<SliceId>,
    ) {
        self.arrays.push(id);
        self.record_ref_name.insert(id, items.as_ref().to_owned());
        for child in &children {
            self.parent.insert(*child, id);
        }
        self.children.insert(id, children);
        if let Some(size_constraint) = size_constraint {
            self.set_size_constraint(id, size_constraint);
        }
        self.cluster.insert(id, cluster);
    }

    fn record_record_field(
        &mut self,
        id: SliceId,
        cluster: SliceId,
        inner_record_name: impl AsRef<str>,
        child: SliceId,
        size_constraint: Option<SliceId>,
    ) {
        self.record_ref_name
            .insert(id, inner_record_name.as_ref().to_owned());
        self.parent.insert(child, id);
        self.children.insert(id, vec![child]);
        if let Some(size_constraint) = size_constraint {
            self.set_size_constraint(id, size_constraint);
        }
        self.cluster.insert(id, cluster);
    }

    fn record_struct(
        &mut self,
        id: SliceId,
        cluster: SliceId,
        record_name: impl AsRef<str>,
        children: Vec<SliceId>,
    ) {
        for child in &children {
            self.parent.insert(*child, id);
        }
        self.record_ref_name
            .insert(id, record_name.as_ref().to_owned());
        self.children.insert(id, children);
        self.cluster.insert(id, cluster);
    }

    fn record_union(
        &mut self,
        id: SliceId,
        cluster: SliceId,
        record_name: impl AsRef<str>,
        child: SliceId,
    ) {
        self.unions.push(id);
        self.record_ref_name
            .insert(id, record_name.as_ref().to_owned());
        self.parent.insert(child, id);
        self.children.insert(id, vec![child]);
        self.cluster.insert(id, cluster);
    }

    fn record_choosable(
        &mut self,
        id: SliceId,
        cluster: SliceId,
        size_constraint: Option<SliceId>,
        choosables: Vec<Vec<u8>>,
    ) {
        self.choosable_fields.insert(id, choosables);
        self.record_immutable(id, cluster, size_constraint);
    }

    pub fn stage_immutable(&mut self, id: SliceId, size_constraint: Option<SliceId>) {
        self.staged.push(StagedMetadata::Immutable(
            id,
            self.next_id - 1,
            size_constraint,
        ));
    }

    pub fn stage_terminator(&mut self, id: SliceId, terminator_of: SliceId) {
        self.staged
            .push(StagedMetadata::Terminator(id, terminator_of));
    }

    pub fn stage_mutable(
        &mut self,
        id: SliceId,
        endianness: Option<Endianness>,
        size_constraint: Option<SliceId>,
    ) {
        self.staged.push(StagedMetadata::Mutable(
            id,
            self.next_id - 1,
            endianness,
            size_constraint,
        ));
    }

    pub fn stage_mutable_string(
        &mut self,
        id: SliceId,
        string_format: Option<StringFormat>,
        size_constraint: Option<SliceId>,
    ) {
        self.staged.push(StagedMetadata::MutableString(
            id,
            self.next_id - 1,
            string_format,
            size_constraint,
        ));
    }

    pub fn stage_array(
        &mut self,
        id: SliceId,
        name: impl AsRef<str>,
        children: Vec<SliceId>,
        size_constraint: Option<SliceId>,
    ) {
        self.staged.push(StagedMetadata::Array(
            id,
            self.next_id - 1,
            name.as_ref().to_owned(),
            children,
            size_constraint,
        ));
    }

    pub fn stage_record_field(
        &mut self,
        id: SliceId,
        name: impl AsRef<str>,
        child: SliceId,
        size_constraint: Option<SliceId>,
    ) {
        self.staged.push(StagedMetadata::RecordField(
            id,
            self.next_id - 1,
            name.as_ref().to_owned(),
            child,
            size_constraint,
        ));
    }

    pub fn stage_struct(&mut self, id: SliceId, name: impl AsRef<str>, children: Vec<SliceId>) {
        self.staged.push(StagedMetadata::Struct(
            id,
            self.next_id - 1,
            name.as_ref().to_owned(),
            children,
        ));
    }

    pub fn stage_union(&mut self, id: SliceId, name: impl AsRef<str>, child: SliceId) {
        self.staged.push(StagedMetadata::Union(
            id,
            self.next_id - 1,
            name.as_ref().to_owned(),
            child,
        ));
    }

    pub fn stage_choosable(
        &mut self,
        id: SliceId,
        size_constraint: Option<SliceId>,
        choosables: Vec<Vec<u8>>,
    ) {
        self.staged.push(StagedMetadata::Choosable(
            id,
            self.next_id - 1,
            size_constraint,
            choosables,
        ));
    }

    pub fn record_staged(&mut self) {
        let staged = mem::take(&mut self.staged);
        for staged_item in staged {
            match staged_item {
                StagedMetadata::Immutable(id, cluster, size_constraint) => {
                    self.record_immutable(id, cluster, size_constraint);
                }
                StagedMetadata::Terminator(id, terminator_of) => {
                    self.record_terminator(id, terminator_of);
                }
                StagedMetadata::Mutable(id, cluster, endianness, size_constraint) => {
                    self.record_mutable(id, cluster, endianness, size_constraint);
                }
                StagedMetadata::MutableString(id, cluster, string_format, size_constraint) => {
                    self.record_mutable_string(id, cluster, string_format, size_constraint);
                }
                StagedMetadata::Array(id, cluster, name, children, size_constraint) => {
                    self.record_array(id, cluster, name, children, size_constraint);
                }
                StagedMetadata::RecordField(id, cluster, name, child, size_constraint) => {
                    self.record_record_field(id, cluster, name, child, size_constraint);
                }
                StagedMetadata::Struct(id, cluster, name, children) => {
                    self.record_struct(id, cluster, name, children);
                }
                StagedMetadata::Union(id, cluster, name, child) => {
                    self.record_union(id, cluster, name, child);
                }
                StagedMetadata::Choosable(id, cluster, size_constraint, choosables) => {
                    self.record_choosable(id, cluster, size_constraint, choosables);
                }
            }
        }
    }

    pub fn get_stage_snapshot(&mut self) -> usize {
        self.staged.len()
    }

    pub fn discard_staged(&mut self, pos: usize) {
        if pos == 0 {
            self.staged.clear();
        } else {
            self.staged.truncate(pos);
        }
    }

    pub fn finalize_mutables(&mut self) {
        self.mutable_fields
            .retain(|x| !self.size_of.contains_key(x));
        self.arrays.retain(|x| self.size.contains_key(x));
    }
}

#[cfg(test)]
mod tests {
    use std::{env, fs, path::Path};

    use glob::glob;

    use super::*;

    #[test]
    fn serde_blob() {
        let workspace_env = env::var("CARGO_MANIFEST_DIR").unwrap();
        let testlang_samples_dir = Path::new(&workspace_env)
            .join("../reverser/harness-reverser/answers")
            .to_string_lossy()
            .into_owned();
        let mut testlangs: HashMap<String, TestLang> = HashMap::new();
        let glob_pattern = format!("{testlang_samples_dir}/*.json");
        for entry in glob(&glob_pattern).expect("Failed to listup sample files") {
            match entry {
                Ok(path) => {
                    testlangs.insert(
                        path.file_stem().unwrap().to_string_lossy().into_owned(),
                        TestLang::from_file(&path).unwrap(),
                    );
                }
                Err(e) => eprintln!("{:?}", e),
            }
        }

        let blob_samples_dir = Path::new(&workspace_env)
            .join("res/test-blobs")
            .to_string_lossy()
            .into_owned();
        let glob_pattern = format!("{blob_samples_dir}/*_solve.bin");
        for entry in glob(&glob_pattern).expect("Failed to listup sample files") {
            match entry {
                Ok(path) => {
                    let sample_name = path
                        .file_stem()
                        .unwrap()
                        .to_string_lossy()
                        .strip_suffix("_solve")
                        .unwrap()
                        .to_owned();
                    let testlang = &testlangs[&sample_name];
                    let file_bytes = fs::read(&path).unwrap();
                    let deserialized = TestLangBlob::from_bytes(&file_bytes, testlang).unwrap();
                    let mut serialized = Vec::new();
                    deserialized.get_bytes(&mut serialized).unwrap();
                    assert_eq!(file_bytes, serialized);
                }
                Err(e) => eprintln!("{:?}", e),
            }
        }
    }
}
