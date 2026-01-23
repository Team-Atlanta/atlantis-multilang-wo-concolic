use crate::{
    schema::{
        Field, FieldKind, Record, RecordKind, Ref, RefKind, SizeDescriptor, TestLang, ValOrRef,
    },
    TestLangError,
};

impl TestLang {
    pub fn unroll(&self) -> Result<Self, TestLangError> {
        Self::new(
            self.mode,
            self.default_endian,
            self.records.iter().map(|record| record.unroll()).collect(),
        )
    }
}

impl Record {
    fn unroll(&self) -> Self {
        match self.kind {
            RecordKind::Struct => Self {
                fields: self
                    .fields
                    .iter()
                    .flat_map(|field| field.unroll())
                    .collect(),
                ..self.clone()
            },
            RecordKind::Union => self.clone(),
        }
    }
}

impl Field {
    fn unroll(&self) -> Vec<Self> {
        if self.kind != FieldKind::Array || self.byte_size.is_some() {
            return vec![self.clone()];
        }
        let Some(SizeDescriptor::Single(ValOrRef::Val(len))) = self.len else {
            return vec![self.clone()];
        };
        let Some(record_name) = self.get_record_ref() else {
            return vec![self.clone()];
        };
        let mut unrolled = Vec::with_capacity(len);
        for i in 0..len {
            let field = Field {
                type_id: None,
                name: format!("{}_unrolled_{}", self.name, i),
                kind: FieldKind::Record,
                len: None,
                byte_size: None,
                possible_values: None,
                items: Some(Ref {
                    kind: RefKind::Record,
                    name: record_name.to_owned(),
                }),
                terminator: None,
                string_format: None,
                endianness: None,
                fuzzed_data_provider_call: None,
                encoder: None,
                generator: None,
                note: None,
            };
            unrolled.push(field);
        }
        unrolled
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use std::{env, path::Path};

    #[test]
    fn test_unroll() {
        let cargo_manifest_dir = env::var("CARGO_MANIFEST_DIR").unwrap();

        let answers_glob = Path::new(&cargo_manifest_dir)
            .join("../reverser/harness-reverser/answers/*.json")
            .to_string_lossy()
            .into_owned();
        for entry in glob::glob(&answers_glob).expect("Failed to list up sample files") {
            let entry = entry.unwrap();
            TestLang::from_file(&entry).unwrap().unroll().unwrap();
        }
    }
}
