use std::collections::HashMap;

use crate::{
    schema::{
        Endianness, Field, FieldValue, ModeKind, NumValue, RangeInclusive, Record, Ref, RefKind,
        TestLang, ValOrRef,
    },
    TestLangError, RECORD_INPUT,
};

impl TestLang {
    pub fn normalize(&self) -> Result<Self, TestLangError> {
        TestLangNormalizer::new(self)?.normalize()
    }
}

struct TestLangNormalizer {
    mode: ModeKind,
    default_endian: Endianness,
    records: Vec<Record>,
    record_map: HashMap<String, usize>,
    field_map: HashMap<usize, HashMap<String, usize>>,
}

impl TestLangNormalizer {
    fn new(testlang: &TestLang) -> Result<Self, TestLangError> {
        let hash_map = testlang.hash()?;
        let mut records = testlang.records.clone();
        records.sort_by(|a, b| hash_map.get(&a.name).cmp(&hash_map.get(&b.name)));
        let mut record_map = HashMap::new();
        let mut field_map = HashMap::new();
        for (i_record, record) in records.iter().enumerate() {
            record_map.insert(record.name.clone(), i_record);
            field_map.insert(
                i_record,
                record
                    .fields
                    .iter()
                    .enumerate()
                    .map(|(i_field, field)| (field.name.clone(), i_field))
                    .collect(),
            );
        }
        Ok(Self {
            mode: testlang.mode,
            default_endian: testlang.default_endian,
            records,
            record_map,
            field_map,
        })
    }

    fn normalize(&self) -> Result<TestLang, TestLangError> {
        let mut records = vec![];
        for (i_record, record) in self.records.iter().enumerate() {
            records.push(self.normalize_record(record, i_record)?);
        }
        TestLang::new(self.mode, self.default_endian, records)
    }

    fn normalize_record(&self, record: &Record, i_record: usize) -> Result<Record, TestLangError> {
        let name = if record.name == RECORD_INPUT {
            RECORD_INPUT.to_string()
        } else {
            format!("record{i_record}")
        };
        Ok(Record {
            name,
            fields: record
                .fields
                .iter()
                .enumerate()
                .map(|(i_field, field)| self.normalize_field(field, i_record, i_field))
                .collect::<Result<Vec<_>, _>>()?,
            ..record.clone()
        })
    }

    fn normalize_field(
        &self,
        field: &Field,
        i_record: usize,
        i_field: usize,
    ) -> Result<Field, TestLangError> {
        Ok(Field {
            type_id: None,
            name: format!("field{i_field}"),
            kind: field.kind.clone(),
            len: field
                .len
                .as_ref()
                .map(|size_desc| self.normalize_num_value(size_desc, i_record))
                .transpose()?,
            byte_size: field
                .byte_size
                .as_ref()
                .map(|size_desc| self.normalize_num_value(size_desc, i_record))
                .transpose()?,
            possible_values: field
                .possible_values
                .as_ref()
                .map(|possible_values| {
                    possible_values
                        .iter()
                        .map(|field_val| self.normalize_field_value(field_val, i_record))
                        .collect::<Result<Vec<_>, _>>()
                })
                .transpose()?,
            items: field
                .items
                .as_ref()
                .map(|items_ref| self.normalize_ref(items_ref, i_record))
                .transpose()?,
            terminator: field.terminator.clone(),
            string_format: field.string_format,
            endianness: field.endianness,
            fuzzed_data_provider_call: field.fuzzed_data_provider_call.clone(),
            encoder: field.encoder.clone(),
            generator: field.generator.clone(),
            note: None,
        })
    }

    fn normalize_field_value(
        &self,
        field_val: &FieldValue,
        i_record: usize,
    ) -> Result<FieldValue, TestLangError> {
        Ok(match field_val {
            FieldValue::Int(num) => FieldValue::Int(self.normalize_num_value(num, i_record)?),
            FieldValue::Float(num) => FieldValue::Float(self.normalize_num_value(num, i_record)?),
            FieldValue::String(val_or_ref) => {
                FieldValue::String(self.normalize_val_or_ref(val_or_ref, i_record)?)
            }
            FieldValue::Bytes(val_or_ref) => {
                FieldValue::Bytes(self.normalize_val_or_ref(val_or_ref, i_record)?)
            }
        })
    }

    fn normalize_num_value<T: Clone>(
        &self,
        num: &NumValue<T>,
        i_record: usize,
    ) -> Result<NumValue<T>, TestLangError> {
        Ok(match num {
            NumValue::Single(val_or_ref) => {
                NumValue::Single(self.normalize_val_or_ref(val_or_ref, i_record)?)
            }
            NumValue::Range(range) => NumValue::Range(RangeInclusive {
                start: self.normalize_val_or_ref(&range.start, i_record)?,
                end: self.normalize_val_or_ref(&range.end, i_record)?,
            }),
        })
    }

    fn normalize_val_or_ref<T: Clone>(
        &self,
        val_or_ref: &ValOrRef<T>,
        i_record: usize,
    ) -> Result<ValOrRef<T>, TestLangError> {
        Ok(match val_or_ref {
            ValOrRef::Ref(reference) => ValOrRef::Ref(self.normalize_ref(reference, i_record)?),
            _ => val_or_ref.clone(),
        })
    }

    fn normalize_ref(&self, reference: &Ref, i_record: usize) -> Result<Ref, TestLangError> {
        let name = match reference.kind {
            RefKind::Field => {
                let field_map = self.field_map.get(&i_record).ok_or_else(|| {
                    TestLangError::InvalidSemantics {
                        error: "Failed to find record".to_owned(),
                        record: self.records.get(i_record).map(|record| record.name.clone()),
                        field: None,
                    }
                })?;
                let i_field = field_map.get(&reference.name).ok_or_else(|| {
                    TestLangError::InvalidSemantics {
                        error: "Failed to find field.".to_owned(),
                        record: self.records.get(i_record).map(|record| record.name.clone()),
                        field: Some(reference.name.clone()),
                    }
                })?;
                format!("field{i_field}")
            }
            RefKind::Record => {
                let i_record = self.record_map.get(&reference.name).ok_or_else(|| {
                    TestLangError::InvalidSemantics {
                        error: "Failed to find record.".to_owned(),
                        record: Some(reference.name.clone()),
                        field: None,
                    }
                })?;
                format!("record{i_record}")
            }
        };
        Ok(Ref {
            kind: reference.kind,
            name,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use std::{env, path::Path};

    #[test]
    fn test_normalize() {
        let cargo_manifest_dir = env::var("CARGO_MANIFEST_DIR").unwrap();

        let answers_glob = Path::new(&cargo_manifest_dir)
            .join("../reverser/harness-reverser/answers/*.json")
            .to_string_lossy()
            .into_owned();
        for entry in glob::glob(&answers_glob).expect("Failed to list up sample files") {
            let entry = entry.unwrap();
            TestLang::from_file(&entry).unwrap().normalize().unwrap();
            TestLang::from_file(&entry)
                .unwrap()
                .unroll()
                .unwrap()
                .flatten()
                .unwrap()
                .normalize()
                .unwrap();
        }
    }
}
