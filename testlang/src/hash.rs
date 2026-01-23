use rangemap::RangeInclusiveSet;

use std::{
    collections::{
        hash_map::{DefaultHasher, Entry},
        HashMap,
    },
    hash::{Hash, Hasher},
};

use crate::{
    schema::{
        Endianness, Field, FieldKind, FieldValue, FuzzedDataProviderArg, FuzzedDataProviderCall,
        FuzzedDataProviderMethod, NumValue, RangeInclusive, Record, RecordKind, Ref, RefKind,
        Terminator, TestLang, ValOrRef,
    },
    TestLangError,
};

impl TestLang {
    pub fn hash(&self) -> Result<HashMap<String, u64>, TestLangError> {
        TestLangHasher::new(self).hash()
    }
}

struct TestLangHasher<'a> {
    default_endian: Endianness,
    records: HashMap<&'a str, &'a Record>,
}

impl<'a> TestLangHasher<'a> {
    fn new(testlang: &'a TestLang) -> Self {
        Self {
            default_endian: testlang.default_endian,
            records: testlang
                .records
                .iter()
                .map(|record| (record.name.as_str(), record))
                .collect(),
        }
    }

    fn calc_hash<T: Hash>(data: &T) -> u64 {
        let mut hasher = DefaultHasher::new();
        data.hash(&mut hasher);
        hasher.finish()
    }

    fn hash(&mut self) -> Result<HashMap<String, u64>, TestLangError> {
        let mut record_hashes = HashMap::new();
        let mut field_hashes = HashMap::new();
        for record_name in self.records.keys() {
            self.hash_record(record_name, &mut record_hashes, &mut field_hashes)?;
        }
        let mut ret = HashMap::new();
        for (name, hash) in record_hashes {
            let Some(hash) = hash else {
                return Err(TestLangError::InvalidSemantics {
                    error: "Failed to hash.".to_owned(),
                    record: Some(name.to_owned()),
                    field: None,
                });
            };
            ret.insert(name.to_owned(), hash);
        }
        Ok(ret)
    }

    fn hash_record(
        &self,
        record_name: &str,
        record_hashes: &mut HashMap<String, Option<u64>>,
        field_hashes: &mut HashMap<String, HashMap<String, Option<u64>>>,
    ) -> Result<u64, TestLangError> {
        match record_hashes.entry(record_name.to_owned()) {
            Entry::Occupied(e) => match e.get() {
                Some(hash) => return Ok(*hash),
                None => {
                    return Err(TestLangError::InvalidSemantics {
                        error: "Found cycle.".to_owned(),
                        record: Some(record_name.to_owned()),
                        field: None,
                    })
                }
            },
            Entry::Vacant(e) => {
                e.insert(None);
                if let Entry::Vacant(e) = field_hashes.entry(record_name.to_owned()) {
                    e.insert(HashMap::new());
                }
            }
        };
        let Some(record) = self.records.get(record_name) else {
            return Err(TestLangError::InvalidSemantics {
                error: "Failed to find record.".to_owned(),
                record: Some(record_name.to_owned()),
                field: None,
            });
        };
        let mut field_hashes = record
            .fields
            .iter()
            .map(|field| self.hash_field(field, record, record_hashes, field_hashes))
            .collect::<Result<Vec<_>, _>>()?;
        if let RecordKind::Union = record.kind {
            field_hashes.sort();
        }
        let mut data = vec![record.kind as u64];
        data.append(&mut field_hashes);
        let hash = Self::calc_hash(&data);
        record_hashes.insert(record.name.to_owned(), Some(hash));
        Ok(hash)
    }

    fn hash_field(
        &self,
        field: &Field,
        ctx_record: &Record,
        record_hashes: &mut HashMap<String, Option<u64>>,
        field_hashes: &mut HashMap<String, HashMap<String, Option<u64>>>,
    ) -> Result<u64, TestLangError> {
        match field_hashes.get_mut(ctx_record.name.as_str()) {
            None => {
                return Err(TestLangError::InvalidSemantics {
                    error: "Failed to find record.".to_owned(),
                    record: Some(ctx_record.name.clone()),
                    field: None,
                })
            }
            Some(field_hashes) => match field_hashes.entry(field.name.clone()) {
                Entry::Occupied(e) => match e.get() {
                    Some(hash) => return Ok(*hash),
                    None => {
                        return Err(TestLangError::InvalidSemantics {
                            error: "Found cycle on field.".to_owned(),
                            record: Some(ctx_record.name.clone()),
                            field: Some(field.name.clone()),
                        })
                    }
                },
                Entry::Vacant(e) => {
                    e.insert(None);
                }
            },
        };
        let field_kind = match field.kind {
            FieldKind::Int | FieldKind::Bytes | FieldKind::String | FieldKind::Custom(_) => 0,
            FieldKind::Float => 1,
            FieldKind::Array => 2,
            FieldKind::Record => 3,
        };
        let data = [
            field_kind,
            match &field.len {
                None => 0,
                Some(size_desc) => self.hash_num_value(
                    size_desc,
                    ctx_record,
                    record_hashes,
                    field_hashes,
                    |val| *val as u64,
                )?,
            },
            match &field.byte_size {
                None => 0,
                Some(size_desc) => self.hash_num_value(
                    size_desc,
                    ctx_record,
                    record_hashes,
                    field_hashes,
                    |val| *val as u64,
                )?,
            },
            match &field.possible_values {
                None => 0,
                Some(possible_values) => self.hash_possible_values(
                    possible_values,
                    field,
                    ctx_record,
                    record_hashes,
                    field_hashes,
                )?,
            },
            match &field.items {
                None => 0,
                Some(items_ref) => {
                    self.hash_ref(items_ref, ctx_record, record_hashes, field_hashes)?
                }
            },
            match &field.terminator {
                None => 0,
                Some(Terminator::ByteSequence(seq)) => Self::calc_hash(&seq),
                Some(Terminator::CharSequence(seq)) => Self::calc_hash(&seq.as_bytes()),
            },
            match &field.string_format {
                None => 0,
                Some(string_format) => *string_format as u64 + 1,
            },
            match &field.endianness {
                None => self.default_endian as u64,
                Some(endianness) => *endianness as u64,
            },
            match &field.fuzzed_data_provider_call {
                None => 0,
                Some(fdp_call) => {
                    self.hash_fdp_call(fdp_call, ctx_record, record_hashes, field_hashes)?
                }
            },
        ];
        let hash = Self::calc_hash(&data);
        match field_hashes.get_mut(ctx_record.name.as_str()) {
            None => {
                return Err(TestLangError::InvalidSemantics {
                    error: "Failed to find record.".to_owned(),
                    record: Some(ctx_record.name.clone()),
                    field: None,
                })
            }
            Some(field_hashes) => match field_hashes.entry(field.name.clone()) {
                Entry::Occupied(mut e) => match e.get() {
                    Some(other_hash) if hash != *other_hash => {
                        return Err(TestLangError::InvalidSemantics {
                            error: "Conflict in hash on field.".to_owned(),
                            record: Some(ctx_record.name.clone()),
                            field: Some(field.name.clone()),
                        })
                    }
                    _ => e.insert(Some(hash)),
                },
                Entry::Vacant(_) => {
                    return Err(TestLangError::InvalidSemantics {
                        error: "Failed to find field.".to_owned(),
                        record: Some(ctx_record.name.clone()),
                        field: Some(field.name.clone()),
                    })
                }
            },
        };
        Ok(hash)
    }

    fn hash_field_value(
        &self,
        field_val: &FieldValue,
        ctx_record: &Record,
        record_hashes: &mut HashMap<String, Option<u64>>,
        field_hashes: &mut HashMap<String, HashMap<String, Option<u64>>>,
    ) -> Result<u64, TestLangError> {
        let data = match field_val {
            FieldValue::Int(num) => [
                0,
                self.hash_num_value(num, ctx_record, record_hashes, field_hashes, |val| {
                    *val as u64
                })?,
            ],
            FieldValue::Float(num) => [
                1,
                self.hash_num_value(num, ctx_record, record_hashes, field_hashes, |val| {
                    val.to_bits()
                })?,
            ],
            FieldValue::String(val_or_ref) => [
                2,
                self.hash_val_or_ref(val_or_ref, ctx_record, record_hashes, field_hashes, |val| {
                    Self::calc_hash(val)
                })?,
            ],
            FieldValue::Bytes(val_or_ref) => [
                3,
                self.hash_val_or_ref(val_or_ref, ctx_record, record_hashes, field_hashes, |val| {
                    Self::calc_hash(val)
                })?,
            ],
        };
        Ok(Self::calc_hash(&data))
    }

    fn hash_possible_values(
        &self,
        possible_values: &[FieldValue],
        field: &Field,
        ctx_record: &Record,
        record_hashes: &mut HashMap<String, Option<u64>>,
        field_hashes: &mut HashMap<String, HashMap<String, Option<u64>>>,
    ) -> Result<u64, TestLangError> {
        let possible_values = match field.kind {
            FieldKind::Int => {
                let mut normalized_possible_values = vec![];
                let mut ranges = RangeInclusiveSet::new();
                for val in possible_values {
                    match val {
                        FieldValue::Int(NumValue::Single(ValOrRef::Val(val))) => {
                            ranges.insert(*val..=*val)
                        }
                        FieldValue::Int(NumValue::Range(RangeInclusive {
                            start: ValOrRef::Val(start),
                            end: ValOrRef::Val(end),
                        })) => ranges.insert(*start..=*end),
                        FieldValue::Int(_) => normalized_possible_values.push(val.clone()),
                        _ => {
                            return Err(TestLangError::InvalidSemantics {
                                error: format!(
                                    "`int` field has non-integer `possible_values`: {:?}.",
                                    val
                                ),
                                record: Some(ctx_record.name.clone()),
                                field: Some(field.name.clone()),
                            })
                        }
                    }
                }
                ranges.iter().for_each(|range| {
                    normalized_possible_values.push(FieldValue::Int(NumValue::Range(
                        RangeInclusive {
                            start: ValOrRef::Val(*range.start()),
                            end: ValOrRef::Val(*range.end()),
                        },
                    )));
                });
                normalized_possible_values
            }
            _ => possible_values.to_vec(),
        };
        Ok(Self::calc_hash(
            &possible_values
                .iter()
                .map(|field_val| {
                    self.hash_field_value(field_val, ctx_record, record_hashes, field_hashes)
                })
                .collect::<Result<Vec<_>, _>>()?,
        ))
    }

    fn hash_num_value<T>(
        &self,
        num: &NumValue<T>,
        ctx_record: &Record,
        record_hashes: &mut HashMap<String, Option<u64>>,
        field_hashes: &mut HashMap<String, HashMap<String, Option<u64>>>,
        hash_val: fn(&T) -> u64,
    ) -> Result<u64, TestLangError> {
        let data = match num {
            NumValue::Single(val_or_ref) => [
                0,
                self.hash_val_or_ref(
                    val_or_ref,
                    ctx_record,
                    record_hashes,
                    field_hashes,
                    hash_val,
                )?,
            ],
            NumValue::Range(range) => [
                1,
                Self::calc_hash(&[
                    self.hash_val_or_ref(
                        &range.start,
                        ctx_record,
                        record_hashes,
                        field_hashes,
                        hash_val,
                    )?,
                    self.hash_val_or_ref(
                        &range.end,
                        ctx_record,
                        record_hashes,
                        field_hashes,
                        hash_val,
                    )?,
                ]),
            ],
        };
        Ok(Self::calc_hash(&data))
    }

    fn hash_val_or_ref<T>(
        &self,
        val_or_ref: &ValOrRef<T>,
        ctx_record: &Record,
        record_hashes: &mut HashMap<String, Option<u64>>,
        field_hashes: &mut HashMap<String, HashMap<String, Option<u64>>>,
        hash_val: fn(&T) -> u64,
    ) -> Result<u64, TestLangError> {
        let data = match val_or_ref {
            ValOrRef::Val(val) => [0, hash_val(val)],
            ValOrRef::Ref(reference) => [
                1,
                self.hash_ref(reference, ctx_record, record_hashes, field_hashes)?,
            ],
        };
        Ok(Self::calc_hash(&data))
    }

    fn hash_ref(
        &self,
        reference: &Ref,
        ctx_record: &Record,
        record_hashes: &mut HashMap<String, Option<u64>>,
        field_hashes: &mut HashMap<String, HashMap<String, Option<u64>>>,
    ) -> Result<u64, TestLangError> {
        let data = [
            reference.kind as u64,
            match reference.kind {
                RefKind::Field => {
                    let field = ctx_record
                        .fields
                        .iter()
                        .find(|field| field.name == reference.name)
                        .ok_or_else(|| TestLangError::InvalidSemantics {
                            error: "Failed to find field.".to_owned(),
                            record: Some(ctx_record.name.clone()),
                            field: Some(reference.name.clone()),
                        })?;
                    self.hash_field(field, ctx_record, record_hashes, field_hashes)?
                }
                RefKind::Record => {
                    self.hash_record(reference.name.as_str(), record_hashes, field_hashes)?
                }
            },
        ];
        Ok(Self::calc_hash(&data))
    }

    fn hash_fdp_call(
        &self,
        fdp_call: &FuzzedDataProviderCall,
        ctx_record: &Record,
        record_hashes: &mut HashMap<String, Option<u64>>,
        field_hashes: &mut HashMap<String, HashMap<String, Option<u64>>>,
    ) -> Result<u64, TestLangError> {
        let data = [
            self.hash_fdp_method(&fdp_call.method)?,
            match fdp_call.type_size {
                None => 0,
                Some(size) => size as u64,
            },
            Self::calc_hash(
                &fdp_call
                    .args
                    .iter()
                    .map(|arg| self.hash_fdp_arg(arg, ctx_record, record_hashes, field_hashes))
                    .collect::<Result<Vec<_>, _>>()?,
            ),
        ];
        Ok(Self::calc_hash(&data))
    }

    fn hash_fdp_method(&self, fdp_method: &FuzzedDataProviderMethod) -> Result<u64, TestLangError> {
        let data = match fdp_method {
            FuzzedDataProviderMethod::LLVM(method) => [0, *method as u64],
            FuzzedDataProviderMethod::Jazzer(method) => [1, *method as u64],
        };
        Ok(Self::calc_hash(&data))
    }

    fn hash_fdp_arg(
        &self,
        fdp_arg: &FuzzedDataProviderArg,
        ctx_record: &Record,
        record_hashes: &mut HashMap<String, Option<u64>>,
        field_hashes: &mut HashMap<String, HashMap<String, Option<u64>>>,
    ) -> Result<u64, TestLangError> {
        let data = match fdp_arg {
            FuzzedDataProviderArg::Int(num) => [0, *num],
            FuzzedDataProviderArg::Float(num) => [1, *num as u64],
            FuzzedDataProviderArg::Ref(reference) => [
                2,
                self.hash_ref(reference, ctx_record, record_hashes, field_hashes)?,
            ],
        };
        Ok(Self::calc_hash(&data))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use std::{env, path::Path};

    use crate::RECORD_INPUT;

    #[test]
    fn test_hash() {
        let cargo_manifest_dir = env::var("CARGO_MANIFEST_DIR").unwrap();

        let answers_glob = Path::new(&cargo_manifest_dir)
            .join("../reverser/harness-reverser/answers/*.json")
            .to_string_lossy()
            .into_owned();
        for entry in glob::glob(&answers_glob).expect("Failed to list up sample files") {
            TestLang::from_file(entry.unwrap())
                .unwrap()
                .hash()
                .unwrap()
                .get(RECORD_INPUT)
                .unwrap();
        }
    }
}
