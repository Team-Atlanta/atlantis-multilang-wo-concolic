use std::collections::{HashMap, HashSet, VecDeque};

use crate::{
    schema::{
        Endianness, Field, FieldKind, FieldValue, ModeKind, NumValue, Record, RecordKind, Ref,
        RefKind, TestLang, ValOrRef, RECORD_INPUT,
    },
    TestLangError,
};

impl TestLang {
    pub fn flatten(&self) -> Result<Self, TestLangError> {
        TestLangFlattener::new(self).flatten()
    }
}

struct TestLangFlattener {
    mode: ModeKind,
    default_endian: Endianness,
    records: HashMap<String, Record>,
}

impl TestLangFlattener {
    fn new(testlang: &TestLang) -> Self {
        Self {
            mode: testlang.mode,
            default_endian: testlang.default_endian,
            records: testlang
                .records
                .iter()
                .map(|record| (record.name.clone(), record.clone()))
                .collect(),
        }
    }

    fn flatten(&mut self) -> Result<TestLang, TestLangError> {
        let mut usages = HashMap::new();
        let mut done = HashSet::new();
        let mut q: VecDeque<_> = self.records.keys().cloned().collect();
        while let Some(record_name) = q.pop_front() {
            let is_done = match self.records.get(record_name.as_str()) {
                None => continue,
                Some(record) if record.kind == RecordKind::Union && record.fields.len() == 1 => {
                    if let Some(field) = record.fields.first() {
                        if let Some(ref_record) = field
                            .get_record_ref()
                            .and_then(|ref_name| self.records.get(ref_name))
                        {
                            let record = Record {
                                name: record_name.clone(),
                                ..ref_record.clone()
                            };
                            self.records.insert(record.name.clone(), record);
                            false
                        } else {
                            true
                        }
                    } else {
                        true
                    }
                }
                Some(record) if record.kind == RecordKind::Union => true,
                Some(record) => record
                    .fields
                    .iter()
                    .filter(|field| field.kind == FieldKind::Record)
                    .all(|field| {
                        field
                            .get_record_ref()
                            .map(|name| done.contains(name))
                            .unwrap_or(true)
                    }),
            };
            if !is_done {
                q.push_back(record_name);
                continue;
            }
            if let Some(record) = self.records.get(record_name.as_str()) {
                if record.kind == RecordKind::Union {
                    done.insert(record_name);
                    continue;
                }
            }
            if let Some(record) = self.records.remove(record_name.as_str()) {
                let (_, record) = record
                    .fields
                    .iter()
                    .enumerate()
                    .filter(|(_, field)| field.kind == FieldKind::Record)
                    .fold(
                        (0isize, record.clone()),
                        |(i_base, record), (i_field, field)| {
                            field
                                .get_record_ref()
                                .and_then(|ref_name| self.records.get(ref_name))
                                .map(|ref_record| {
                                    if ref_record.kind == RecordKind::Union
                                        || field.fuzzed_data_provider_call.is_some()
                                    {
                                        return (i_base, record.clone());
                                    }
                                    let usage = usages.entry(ref_record.name.clone()).or_insert(0);
                                    *usage += 1;
                                    let mut fields = ref_record.fields.clone();
                                    fields.iter_mut().for_each(|field| {
                                        let rename = |name: &mut String| {
                                            *name =
                                                format!("{}_{}_{}", ref_record.name, usage, name)
                                        };
                                        rename(&mut field.name);
                                        if let Some(size) = &mut field.len {
                                            self.rename_num_value(&rename, size);
                                        }
                                        if let Some(size) = &mut field.byte_size {
                                            self.rename_num_value(&rename, size);
                                        }
                                        if let Some(possible_values) = &mut field.possible_values {
                                            for possible_value in possible_values.iter_mut() {
                                                match possible_value {
                                                    FieldValue::Int(num) => {
                                                        self.rename_num_value(&rename, num);
                                                    }
                                                    FieldValue::Float(num) => {
                                                        self.rename_num_value(&rename, num);
                                                    }
                                                    FieldValue::String(val_or_ref) => {
                                                        self.rename_val_or_ref(&rename, val_or_ref);
                                                    }
                                                    FieldValue::Bytes(val_or_ref) => {
                                                        self.rename_val_or_ref(&rename, val_or_ref);
                                                    }
                                                }
                                            }
                                        }
                                        if let Some(items) = &mut field.items {
                                            self.rename_ref(&rename, items);
                                        }
                                    });
                                    (
                                        i_base - 1 + ref_record.fields.len() as isize,
                                        record.replace_field(
                                            (i_base + i_field as isize) as usize,
                                            fields,
                                        ),
                                    )
                                })
                                .unwrap_or((i_base, record))
                        },
                    );
                self.records.insert(record.name.clone(), record);
            }
            done.insert(record_name);
        }
        let mut records = HashMap::new();
        let mut q: VecDeque<_> = vec![RECORD_INPUT].into();
        while let Some(record_name) = q.pop_front() {
            if let Some(record) = self.records.get(record_name) {
                records.insert(record.name.clone(), record.clone());
                for field in record.fields.iter() {
                    if let Some(ref_name) = field.get_record_ref() {
                        q.push_back(ref_name);
                    }
                }
            }
        }
        TestLang::new(
            self.mode,
            self.default_endian,
            records.values().cloned().collect(),
        )
    }

    fn rename_num_value<T>(&self, rename: &impl Fn(&mut String), num_val: &mut NumValue<T>) {
        match num_val {
            NumValue::Single(val_or_ref) => {
                self.rename_val_or_ref(rename, val_or_ref);
            }
            NumValue::Range(range) => {
                self.rename_val_or_ref(rename, &mut range.start);
                self.rename_val_or_ref(rename, &mut range.end);
            }
        }
    }

    fn rename_val_or_ref<T>(&self, rename: &impl Fn(&mut String), val_or_ref: &mut ValOrRef<T>) {
        if let ValOrRef::Ref(reference) = val_or_ref {
            self.rename_ref(rename, reference);
        }
    }

    fn rename_ref(&self, rename: &impl Fn(&mut String), reference: &mut Ref) {
        if let RefKind::Field = reference.kind {
            rename(&mut reference.name);
        }
    }
}

impl Record {
    fn replace_field(&self, idx: usize, fields: Vec<Field>) -> Self {
        let mut ret = self.clone();
        if idx >= self.fields.len() {
            return ret;
        }
        ret.fields.splice(idx..idx + 1, fields);
        ret
    }
}

impl Field {
    pub fn get_record_ref(&self) -> Option<&str> {
        match self.kind {
            FieldKind::Record | FieldKind::Array => match &self.items {
                Some(Ref {
                    kind: RefKind::Record,
                    name,
                }) => Some(name.as_str()),
                _ => None,
            },
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use std::{env, path::Path};

    #[test]
    fn test_flatten() {
        let cargo_manifest_dir = env::var("CARGO_MANIFEST_DIR").unwrap();

        let answers_glob = Path::new(&cargo_manifest_dir)
            .join("../reverser/harness-reverser/answers/*.json")
            .to_string_lossy()
            .into_owned();
        for entry in glob::glob(&answers_glob).expect("Failed to list up sample files") {
            let entry = entry.unwrap();
            TestLang::from_file(&entry).unwrap().flatten().unwrap();
            TestLang::from_file(&entry)
                .unwrap()
                .unroll()
                .unwrap()
                .flatten()
                .unwrap();
        }
    }
}
