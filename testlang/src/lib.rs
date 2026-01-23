use std::{
    cmp,
    collections::{HashMap, HashSet},
    fmt::{self, Display},
    fs, io,
    path::Path,
    str::FromStr,
};

pub use ast::*;
pub use schema::*;

use rangemap::RangeInclusiveSet;
use regex::Regex;
use serde::Deserialize;
use thiserror::Error;

pub mod ast;
pub mod blob;
pub mod custom;
pub mod postprocess;
pub mod schema;
pub mod utils;

mod api;
mod hash;

#[derive(Debug, Error)]
pub enum TestLangError {
    #[error("Testlang has invalid syntax: {0}")]
    InvalidSyntax(#[from] serde_json5::Error),
    #[error("Testlang has invalid semantics{}{}: {error}", field.as_deref().map(|name| format!(" in field \"{name}\"")).unwrap_or_default(), record.as_deref().map(|name| format!(" in record \"{name}\"")).unwrap_or_default())]
    InvalidSemantics {
        error: String,
        record: Option<String>,
        field: Option<String>,
    },
    #[error("TestLangNode has invalid status: {0}")]
    InvalidNode(String),
    #[error("IO error occurred during parsing testlang: {0}")]
    Io(#[from] io::Error),
}

#[derive(Clone, Debug, Error)]
pub enum TestLangWarning {
    #[error(
        "Using unexpected attribute \"{attribute}\" in field \"{field}\" of record \"{record}\""
    )]
    UnexpectedAttribute {
        record: String,
        field: String,
        attribute: String,
    },
    #[error("Analyze the endian of field \"{field}\" of record \"{record}\". Specially, for `INPUT` record, THIS IS MANDATORY. For other records, if it is not specified, it will be assumed to be `default_endian` of the testlang.")]
    MissingEndian { record: String, field: String },
    #[error("Are there any known, but missed `possible_values` (or [] if there is no) for field \"{field}\" of record \"{record}\"? Put ONLY EXPLICITLY SEEN values from codes. If it's hard to calculate or generate this field, you SHOULD use python `encoder` or `generator`.")]
    MissingPossibleValues { record: String, field: String },
    #[error("Are there any known, but missed `terminator` for field \"{field}\" of record \"{record}\"?")]
    MissingTerminator { record: String, field: String },
    #[error("Do you have any analyzed, but missed inner structure details for field \"{field}\" of record \"{record}\"? (You SHOULD analyze further usages of this field and search ALL missing related function definitions that were used in the given codes. If you are SURE that the field has no further inner structure EVEN AFTER you've analyzed the inner structure, you can ignore this warning by adding `possible_values: []` to the field.)")]
    JustRandomBytes { record: String, field: String },
    #[error("Check if int, string or bytes data in the harness are being read using custom unusual methods. If so, you SHOULD set `mode` as `Custom` `FuzzedDataProvider`, and search implementations and express them in python `encoder`s bofore you analyze any codes further.")]
    MaybeCustomFDP,
    #[error("Check if byte_size or len of field \"{field}\" of record \"{record}\" is correct. This field's FDP call has intention to produce value with fixed-size. This warning can be ignored when you are sure that the value's size can definitely be variable.")]
    MaybeWrongFDPRangedSizeDescriptor { record: String, field: String },
    #[error("If `possible_values` for the field `{field}` of the record `{record}` represents diffrent structures, split them using union record.")]
    MaybeSelector { record: String, field: String },
    #[error(
        "If field `{field}` of record `{record}` was read using string format like `%d`, it should be `string` fields with `string_format`."
    )]
    MaybeIntString { record: String, field: String },
    #[error(
        "Does `{generator}` target critical security vulnerabilties that `AddressSanitizer` can detect (i.e. `heap-use-after-free`, `heap-buffer-overflow`, `stack-buffer-overflow`, `global-buffer-overflow`, `stack-use-after-return`, `stack-use-after-scope`, `initialization-order-fiasco`, or `detected memory leaks`) or `Jazzer` sanitizers can detect? If not, you SHOULD search code deeper and wider to find them. DO NOT just target tedious bugs or issues. Can generator `{generator}` of field `{field}` of record `{record}` REALLY effectively generate blobs that can trigger the bug? Reason about how to trigger the bug better and update `{generator}` accordingly. Make sure that `{generator}` NEVER generate malformed blobs by parsing them before returning at the end and DO NOT catch the exception from parsing. Refactor `{generator}` and keep it concise. Make sure `{generator}` DO NOT generate overlapping parts with the existing testlang. REMOVE overlapping parts from either `generator` or testlang records/fields."
    )]
    TediousGenerator {
        record: String,
        field: String,
        generator: String,
    },
    #[error("Are there more `callee_within_location` other than {callee_within_location:?} for record `{record}`?")]
    MissingCallee {
        record: String,
        callee_within_location: Vec<String>,
    },
    #[error("Invalid location for record `{record}`: {location:?}. {detail}")]
    InvalidLocation {
        record: String,
        location: Location,
        detail: String,
    },
    #[error("`{callee}` was not found within location `{location:?}` for record `{record}`. If this `callee` was called in other location, you SHOULD add another new separate `analysis` for it.")]
    MissingCalleeInLocation {
        record: String,
        location: Location,
        callee: String,
    },
    #[error("Is {callee} a recursive function? It's both of `location.func_name` and `callee_in_location` in record `{record}`.")]
    RecursiveFunction { record: String, callee: String },
}

impl FromStr for TestLang {
    type Err = TestLangError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        let mut track = serde_path_to_error::Track::new();
        let mut deserializer = serde_json5::Deserializer::from_str(s)?;
        let deserializer = serde_path_to_error::Deserializer::new(&mut deserializer, &mut track);
        let mut testlang = TestLang::deserialize(deserializer).map_err(|err| match err {
            serde_json5::Error::Message { msg, location } => {
                let path = track.path();
                let mut path_str = path.clone().to_string();
                if let Ok(json_value) = serde_json5::from_str::<serde_json::Value>(s) {
                    // Find records[\d+]
                    if let Ok(re) = Regex::new(r"records\[(\d+)\]") {
                        if let Some(caps) = re.captures(&path_str) {
                            if let Some(index) = caps.get(1) {
                                if let Ok(index) = index.as_str().parse::<usize>() {
                                    if let Some(record) =
                                        json_value.get("records").and_then(|r| r.get(index))
                                    {
                                        if let Some(name) =
                                            record.get("name").and_then(|n| n.as_str())
                                        {
                                            path_str = path_str.replace(&caps[0], name);
                                        }
                                        // Find fields\[\d+\]
                                        if let Ok(re) = Regex::new(r"fields\[(\d+)\]") {
                                            if let Some(caps) = re.captures(&path_str) {
                                                if let Some(index) = caps.get(1) {
                                                    if let Ok(index) =
                                                        index.as_str().parse::<usize>()
                                                    {
                                                        if let Some(field) = record
                                                            .get("fields")
                                                            .and_then(|f| f.get(index))
                                                        {
                                                            if let Some(name) = field
                                                                .get("name")
                                                                .and_then(|n| n.as_str())
                                                            {
                                                                path_str = path_str
                                                                    .replace(&caps[0], name);
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                };

                let msg = if msg == "invalid type: map, expected a sequence"
                    && path
                        .iter()
                        .next_back()
                        .map(|seg| seg.to_string())
                        .unwrap_or_default()
                        == "possible_values"
                {
                    format!("{msg} (e.g. possible_values: [{{\"start\": 0, \"end\": 0x500}}] )")
                } else if msg.starts_with(
                    "data did not match any variant of untagged enum FuzzedDataProviderMethod",
                ) {
                    format!("{msg} (Did you specify wrong `FuzzedDataProvider` for `mode`?)")
                } else {
                    msg
                };
                serde_json5::Error::Message {
                    msg: format!("{msg} (at {})", path_str),
                    location,
                }
            }
        })?;
        if let Some(true) = testlang.is_partial {
        } else {
            testlang.postprocess()?;
        }
        Ok(testlang)
    }
}

impl Display for TestLang {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let json_string = serde_json::to_string_pretty(self)
            .unwrap_or_else(|e| format!("ERROR serializing: {}", e));
        write!(f, "{}", json_string)
    }
}

impl TestLang {
    fn postprocess(&mut self) -> Result<(), TestLangError> {
        let mut type_id = 0;
        for (idx, record) in self.records.iter_mut().enumerate() {
            self.record_index.insert(record.name.clone(), idx);
            type_id += 1;
            record.type_id = Some(type_id);
            self.type_map.insert(
                type_id,
                TestLangType::RecordType {
                    record: record.name.clone(),
                },
            );
            for field in record.fields.iter_mut() {
                type_id += 1;
                field.type_id = Some(type_id);
                self.type_map.insert(
                    type_id,
                    TestLangType::FieldType {
                        record: record.name.clone(),
                        field: field.name.clone(),
                    },
                );
            }
        }
        let warnings = self.validate()?;
        self.warnings.extend(warnings);
        for record in self.records.iter() {
            self.int_ref_map.insert(
                record.name.clone(),
                record
                    .fields
                    .iter()
                    .map(|field| {
                        let mut ref_map = HashSet::new();
                        let mut add_ref = |reference: &Ref| {
                            if reference.kind == RefKind::Field {
                                ref_map.insert(reference.name.clone());
                            }
                        };
                        if let Some(values) = field.possible_values.as_ref() {
                            values.iter().for_each(|value| {
                                if let FieldValue::Int(num) = value {
                                    match num {
                                        NumValue::Single(val_or_ref) => {
                                            if let ValOrRef::Ref(reference) = val_or_ref {
                                                add_ref(reference);
                                            }
                                        }
                                        NumValue::Range(range) => {
                                            if let ValOrRef::Ref(reference) = &range.start {
                                                add_ref(reference);
                                            }
                                            if let ValOrRef::Ref(reference) = &range.end {
                                                add_ref(reference);
                                            }
                                        }
                                    }
                                }
                            })
                        }
                        (field.name.clone(), ref_map)
                    })
                    .collect(),
            );
            self.size_deref_map.insert(
                record.name.clone(),
                record
                    .fields
                    .iter()
                    .filter_map(|field| match field.get_byte_size() {
                        Some(SizeDescriptor::Single(ValOrRef::Ref(Ref {
                            kind: RefKind::Field,
                            name,
                        }))) => Some((name.clone(), field.name.clone())),
                        _ => None,
                    })
                    .collect(),
            );
        }
        Ok(())
    }

    pub fn new(
        mode: ModeKind,
        default_endian: Endianness,
        records: Vec<Record>,
    ) -> Result<Self, TestLangError> {
        let mut testlang = Self {
            is_partial: Some(false),
            mode,
            default_endian,
            records,
            record_index: HashMap::new(),
            type_map: HashMap::new(),
            warnings: Vec::new(),
            int_ref_map: HashMap::new(),
            size_deref_map: HashMap::new(),
        };
        testlang.postprocess()?;
        Ok(testlang)
    }

    pub fn update(
        &self,
        other: &Self,
        records_to_remove: &HashSet<String>,
    ) -> Result<Self, TestLangError> {
        if records_to_remove.contains(RECORD_INPUT) {
            return Err(TestLangError::InvalidSemantics {
                error: format!("Record \"{}\" should not be removed.", RECORD_INPUT),
                record: Some(RECORD_INPUT.to_owned()),
                field: None,
            });
        }
        let mut record_map = self
            .records
            .iter()
            .map(|record| (record.name.clone(), record.clone()))
            .collect::<HashMap<_, _>>();
        for new_record in other.records.iter() {
            if records_to_remove.contains(&new_record.name) {
                return Err(TestLangError::InvalidSemantics {
                    error: format!(
                        "Record \"{}\" is to be removed and updated at the same time.",
                        new_record.name
                    ),
                    record: Some(new_record.name.clone()),
                    field: None,
                });
            }
            if let Some(record) = record_map.get_mut(&new_record.name) {
                *record = new_record.clone();
            } else {
                record_map.insert(new_record.name.clone(), new_record.clone());
            }
        }
        for record_name in records_to_remove.iter() {
            record_map.remove(record_name);
        }
        Self::new(
            self.mode,
            self.default_endian,
            record_map.values().cloned().collect(),
        )
    }

    pub fn from_file(path: impl AsRef<Path>) -> Result<Self, TestLangError> {
        let file_contents = fs::read_to_string(&path)?;
        let testlang = TestLang::from_str(&file_contents)?;
        Ok(testlang)
    }

    pub fn find_record_by_name(&self, name: &str) -> Option<&Record> {
        self.record_index
            .get(name)
            .and_then(|i| self.records.get(*i))
    }

    pub fn find_field_by_name(&self, record_name: &str, field_name: &str) -> Option<&Field> {
        self.find_record_by_name(record_name)
            .and_then(|record| record.fields.iter().find(|field| field.name == field_name))
    }

    pub fn find_record_by_id(&self, type_id: usize) -> Option<&Record> {
        match self.type_map.get(&type_id)? {
            TestLangType::RecordType { record } => self.find_record_by_name(record),
            _ => None,
        }
    }

    pub fn find_field_by_id(&self, type_id: usize) -> Option<&Field> {
        match self.type_map.get(&type_id)? {
            TestLangType::FieldType { record, field } => self
                .record_index
                .get(record)
                .and_then(|i| self.records.get(*i))
                .and_then(|r| r.fields.iter().find(|f| f.name == *field)),
            _ => None,
        }
    }

    pub fn get_size_range_set(
        &self,
        // FIXME: Remove this parameter after adding `record_name` to `Ref`
        record: &Record,
        size_desc: Option<&SizeDescriptor>,
        deps: &HashMap<String, TestLangInt>,
    ) -> RangeInclusiveSet<usize> {
        let mut range_set = RangeInclusiveSet::new();
        let Some(size_desc) = size_desc else {
            return range_set;
        };
        let to_int_val_or_ref = |val_or_ref: &ValOrRef<usize>| match val_or_ref {
            ValOrRef::Val(val) => {
                // FIXME: Use `i128` for `TestLangInt` if `json5` crate is ready
                ValOrRef::Val(cmp::min(TestLangInt::MAX as usize, *val) as TestLangInt)
            }
            ValOrRef::Ref(reference) => ValOrRef::Ref(reference.clone()),
        };
        let mut size_ranges =
            self.get_int_num_value_ranges(record, size_desc, to_int_val_or_ref, deps);
        // Filter negative ranges out for size
        size_ranges.remove(TestLangInt::MIN..=-1);
        for range in size_ranges {
            range_set.insert(*range.start() as usize..=*range.end() as usize);
        }
        range_set
    }

    pub fn get_possible_int_ranges(
        &self,
        record: &Record,
        field: &Field,
        deps: &HashMap<String, TestLangInt>,
    ) -> RangeInclusiveSet<TestLangInt> {
        let mut ranges = RangeInclusiveSet::new();
        let Some(possible_values) = &field.possible_values else {
            return ranges;
        };
        for value in possible_values {
            if let FieldValue::Int(num) = value {
                for value in self.get_int_num_value_ranges(
                    record,
                    num,
                    |val_or_ref| val_or_ref.clone(),
                    deps,
                ) {
                    ranges.insert(value);
                }
            }
        }
        ranges
    }

    pub fn get_int_num_value_ranges<T>(
        &self,
        record: &Record,
        num: &NumValue<T>,
        to_int_val_or_ref: impl Fn(&ValOrRef<T>) -> ValOrRef<TestLangInt>,
        deps: &HashMap<String, TestLangInt>,
    ) -> RangeInclusiveSet<TestLangInt> {
        match num {
            NumValue::Single(val_or_ref) => {
                self.get_int_val_or_ref_ranges(record, &to_int_val_or_ref(val_or_ref), deps)
            }
            NumValue::Range(range) => {
                let start_ranges =
                    self.get_int_val_or_ref_ranges(record, &to_int_val_or_ref(&range.start), deps);
                let end_ranges =
                    self.get_int_val_or_ref_ranges(record, &to_int_val_or_ref(&range.end), deps);
                let start = start_ranges
                    .first()
                    .map(|range| *range.start())
                    .unwrap_or(TestLangInt::MIN);
                let end = end_ranges
                    .last()
                    .map(|range| *range.end())
                    .unwrap_or(TestLangInt::MAX);
                let mut ranges = RangeInclusiveSet::new();
                let range = start..=end;
                // There is no guarantee that range is not empty if start or end is reference
                if !range.is_empty() {
                    ranges.insert(range);
                }
                ranges
            }
        }
    }

    pub fn get_int_val_or_ref_ranges(
        &self,
        record: &Record,
        val_or_ref: &ValOrRef<TestLangInt>,
        deps: &HashMap<String, TestLangInt>,
    ) -> RangeInclusiveSet<TestLangInt> {
        let mut ranges = RangeInclusiveSet::new();
        match val_or_ref {
            ValOrRef::Val(val) => ranges.insert(*val..=*val),
            ValOrRef::Ref(reference) => {
                if let Some(val) = deps.get(&reference.name) {
                    ranges.insert(*val..=*val);
                } else if let Some(field) = self.find_field_by_name(&record.name, &reference.name) {
                    return self.get_possible_int_ranges(record, field, deps);
                }
            }
        }
        ranges
    }

    pub fn get_min_byte_size_of_record(
        &self,
        record: &Record,
        deps: &HashMap<String, TestLangInt>,
    ) -> usize {
        match record.kind {
            RecordKind::Struct => record
                .fields
                .iter()
                .map(|field| self.get_min_byte_size_of_field(record, field, deps))
                .sum::<usize>(),
            RecordKind::Union => record
                .fields
                .iter()
                .map(|field| {
                    field
                        .get_record_ref()
                        .and_then(|ref_record_name| self.find_record_by_name(ref_record_name))
                        .map(|ref_record| self.get_min_byte_size_of_record(ref_record, deps))
                        .unwrap_or_default()
                })
                .min()
                .unwrap_or_default(),
        }
    }

    pub fn get_min_byte_size_of_field(
        &self,
        record: &Record,
        field: &Field,
        deps: &HashMap<String, TestLangInt>,
    ) -> usize {
        match field.kind {
            FieldKind::Int |
            // FIXME: Fix to only 4 or 8
            FieldKind::Float |
            FieldKind::Bytes |
            FieldKind::Custom(_) |
            FieldKind::String => {
                self
                    .get_size_range_set(record, field.get_byte_size(), deps)
                    .iter()
                    .next()
                    .map(|range| *range.start())
                    .unwrap_or_default()
            }
            FieldKind::Array => {
                let Some(Ref {kind: RefKind::Record, name}) = field.items.as_ref() else {
                    return 0;
                };
                let Some(record) = self.find_record_by_name(name) else {
                    return 0;
                };
                let min_record_byte_size = self.get_min_byte_size_of_record(record, deps);
                if min_record_byte_size == 0 {
                    return 0;
                }
                let min_len = self
                    .get_size_range_set(record, field.get_len(), deps)
                    .iter()
                    .next()
                    .map(|range| *range.start())
                    .unwrap_or_default();
                min_len * min_record_byte_size
            }
            FieldKind::Record => {
                let Some(Ref {kind: RefKind::Record, name}) = field.items.as_ref() else {
                    return 0;
                };
                let Some(record) = self.find_record_by_name(name) else {
                    return 0;
                };
                self.get_min_byte_size_of_record(record, deps)
            }
        }
    }
}

impl Field {
    pub fn get_byte_size(&self) -> Option<&SizeDescriptor> {
        match self.kind {
            FieldKind::Int => self.byte_size.as_ref().or(self.len.as_ref()),
            FieldKind::Float => self.byte_size.as_ref().or(self.len.as_ref()),
            FieldKind::Bytes => self.byte_size.as_ref().or(self.len.as_ref()),
            FieldKind::String => self.byte_size.as_ref(),
            FieldKind::Array => self.byte_size.as_ref(),
            FieldKind::Record => self.byte_size.as_ref().or(self.len.as_ref()),
            FieldKind::Custom(_) => self.byte_size.as_ref().or(self.len.as_ref()),
        }
    }

    pub fn get_len(&self) -> Option<&SizeDescriptor> {
        match self.kind {
            FieldKind::Int => self.len.as_ref().or(self.byte_size.as_ref()),
            FieldKind::Float => self.len.as_ref().or(self.byte_size.as_ref()),
            FieldKind::Bytes => self.len.as_ref().or(self.byte_size.as_ref()),
            FieldKind::String => self.len.as_ref(),
            FieldKind::Array => self.len.as_ref(),
            FieldKind::Record => self.len.as_ref().or(self.byte_size.as_ref()),
            FieldKind::Custom(_) => self.len.as_ref().or(self.byte_size.as_ref()),
        }
    }
}

#[cfg(test)]
mod tests {
    use std::{env, path::Path};

    use glob::glob;
    use rand::{
        rng,
        seq::{IndexedMutRandom, IndexedRandom},
    };

    use super::*;

    #[test]
    fn parse_schema() {
        let workspace_env = env::var("CARGO_MANIFEST_DIR").unwrap();
        let testlang_samples_dir = Path::new(&workspace_env)
            .join("../reverser/harness-reverser/answers")
            .to_string_lossy()
            .into_owned();
        let glob_pattern = format!("{testlang_samples_dir}/*.json");
        for entry in glob(&glob_pattern).expect("Failed to listup sample files") {
            match entry {
                Ok(path) => {
                    let _ = TestLang::from_file(&path).unwrap();
                }
                Err(e) => eprintln!("{:?}", e),
            }
        }
    }

    #[test]
    fn update() {
        let mut rng = rng();
        let workspace_env = env::var("CARGO_MANIFEST_DIR").unwrap();
        let testlang_samples_dir = Path::new(&workspace_env)
            .join("../reverser/harness-reverser/answers")
            .to_string_lossy()
            .into_owned();
        let glob_pattern = format!("{testlang_samples_dir}/*.json");
        for entry in glob(&glob_pattern).expect("Failed to listup sample files") {
            match entry {
                Ok(path) => {
                    let testlang = TestLang::from_file(&path).unwrap();
                    let record_to_update = testlang.records.choose(&mut rng).unwrap();
                    let new_record = {
                        let mut new_record = record_to_update.clone();
                        if let Some(field) = new_record.fields.choose_mut(&mut rng) {
                            let field_name = field.name.clone();
                            let new_field_name = format!("new_{}", field.name);
                            *field = Field {
                                name: new_field_name.clone(),
                                ..field.clone()
                            };
                            Some((new_record, field_name, new_field_name))
                        } else {
                            None
                        }
                    };
                    let record_to_remove = testlang.records.choose(&mut rng).unwrap();
                    if let Ok(testlang) = testlang.update(
                        &TestLang {
                            is_partial: Some(true),
                            mode: testlang.mode,
                            default_endian: testlang.default_endian,
                            records: new_record
                                .as_ref()
                                .map(|x| vec![x.0.clone()])
                                .unwrap_or_default(),
                            record_index: HashMap::new(),
                            type_map: HashMap::new(),
                            warnings: Vec::new(),
                            int_ref_map: HashMap::new(),
                            size_deref_map: HashMap::new(),
                        },
                        &[record_to_remove.name.clone()].into_iter().collect(),
                    ) {
                        if let Some(x) = new_record {
                            assert!(testlang
                                .find_field_by_name(&record_to_update.name, &x.1)
                                .is_none());
                            assert!(testlang
                                .find_field_by_name(&record_to_update.name, &x.2)
                                .is_some());
                        }
                        assert!(testlang
                            .find_record_by_name(&record_to_remove.name)
                            .is_none());
                    }
                }
                Err(e) => eprintln!("{:?}", e),
            }
        }
    }
}
