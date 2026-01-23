use std::cmp;
use std::collections::{HashMap, HashSet};
use std::fs;

use rangemap::RangeInclusiveSet;

use crate::custom::TYPE_IDS;
use crate::{
    Field, FieldKind, FieldValue, FuzzedDataProviderArg, FuzzedDataProviderKind,
    FuzzedDataProviderMethod, JazzerFuzzedDataProviderMethod, LLVMFuzzedDataProviderMethod,
    ModeKind, NumValue, RangeInclusive, RecordKind, Ref, RefKind, SizeDescriptor, Terminator,
    TestLang, TestLangError, TestLangInt, TestLangWarning, ValOrRef, RECORD_INPUT,
};

type FieldEntry = HashMap<String, Field>;
type ValidationResult = Result<Vec<TestLangWarning>, TestLangError>;

impl TestLang {
    pub fn validate(&mut self) -> ValidationResult {
        let mut record_names = HashSet::new();
        for record in self.records.iter() {
            if !record_names.insert(&record.name) {
                return Err(TestLangError::InvalidSemantics {
                    error: "Records should have unique name within TestLang".to_owned(),
                    record: Some(record.name.clone()),
                    field: None,
                });
            }
        }
        let mut record_history = HashMap::new();
        let mut warnings = Vec::new();
        if self.mode != ModeKind::FuzzedDataProvider(FuzzedDataProviderKind::Custom) {
            warnings.push(TestLangWarning::MaybeCustomFDP);
        }
        warnings.extend(check_record(
            self,
            RECORD_INPUT,
            &mut record_history,
            &mut Vec::new(),
            // TODO: Get max_size from the config?
            None,
            false,
        )?);
        for record in self.records.iter() {
            if !record_history.contains_key(&record.name) {
                return Err(TestLangError::InvalidSemantics {
                    error: format!("Record `{}` is not referenced. Try to merge it in appropriate place if this record is needed.", record.name),
                    record: Some(record.name.clone()),
                    field: None,
                });
            }
        }
        Ok(warnings)
    }

    pub fn validate_python_codes(&self, python_codes: &HashSet<String>) -> ValidationResult {
        let mut unvisited_python_codes = python_codes.clone();
        for record in self.records.iter() {
            for field in record.fields.iter() {
                if let Some(encoder) = &field.encoder {
                    if !unvisited_python_codes.remove(encoder) {
                        return Err(TestLangError::InvalidSemantics {
                            error: format!(
                                "Encoder `{encoder}` is not defined in the Python codes.",
                            ),
                            record: Some(record.name.clone()),
                            field: Some(field.name.clone()),
                        });
                    }
                }
                if let Some(generator) = &field.generator {
                    if !unvisited_python_codes.remove(generator) {
                        return Err(TestLangError::InvalidSemantics {
                            error: format!(
                                "Generator `{generator}` is not defined in the Python codes.",
                            ),
                            record: Some(record.name.clone()),
                            field: Some(field.name.clone()),
                        });
                    }
                }
            }
        }
        if !unvisited_python_codes.is_empty() {
            return Err(TestLangError::InvalidSemantics {
                error: format!(
                    "Unused Python codes: {}. If you just forgot to add this, just add it. Or, if you have already updated this and forgotten to remove, use the updated version and remove old versions. If not... `generator` is for complex structure or logic. `generator` is dedicated for generating SINGLE FIELD in testlang and don't have access to other fields. Thus, you SHOULD MERGE all testlang fields that `generator` generates into ONE SINGLE FIELD. Otherwise, they will duplicate and overall structure will be wrong. If you don't need it, merge logic in it into testlang fields as much as possible and remove it.", unvisited_python_codes
                        .iter()
                        .map(|s| format!("`{s}`"))
                        .collect::<Vec<_>>()
                        .join(", ")
                ),
                record: None,
                field: None,
            });
        }
        Ok(vec![])
    }
}

fn find_field(field_history: &mut [FieldEntry], reference: &Ref) -> Option<Field> {
    let Ref {
        kind: RefKind::Field,
        ref name,
    } = reference
    else {
        return None;
    };

    field_history
        .iter()
        .rev()
        .find_map(|x| x.get(name).cloned())
}

macro_rules! warn_unexpected_attrs {
    ($warnings_vec:expr, $record_name:expr, $field:expr, []) => {};
    ($warnings_vec:expr, $record_name:expr, $field:expr, [$attribute:ident $(, $tail:ident)*]) => {
        if $field.$attribute.is_some() {
            $warnings_vec.push(TestLangWarning::UnexpectedAttribute {
                record: $record_name.to_owned(),
                field: $field.name.to_owned(),
                attribute: stringify!($attribute).to_owned(),
            });
        }
        warn_unexpected_attrs!($warnings_vec, $record_name, $field, [ $($tail),* ]);
    };
}

fn check_field(
    testlang: &TestLang,
    record_name: impl AsRef<str>,
    field: &Field,
    record_history: &mut HashMap<String, bool>,
    field_history: &mut Vec<FieldEntry>,
    max_size: Option<usize>,
    has_fdp_called: bool,
) -> ValidationResult {
    let mut warnings = check_fdp_call(testlang, &record_name, field, has_fdp_called)?;
    let has_fdp_called = has_fdp_called || field.fuzzed_data_provider_call.is_some();
    let result = match &field.kind {
        FieldKind::Int => check_int_field(testlang, &record_name, field, field_history, max_size),
        FieldKind::Float => {
            check_float_field(testlang, &record_name, field, field_history, max_size)
        }
        FieldKind::Bytes => check_bytes_field(&record_name, field, field_history, max_size),
        FieldKind::String => check_string_field(&record_name, field, field_history, max_size),
        FieldKind::Array => check_array_field(
            testlang,
            &record_name,
            field,
            record_history,
            field_history,
            max_size,
            has_fdp_called,
        ),
        FieldKind::Record => check_record_field(
            testlang,
            &record_name,
            field,
            record_history,
            field_history,
            max_size,
            has_fdp_called,
        ),
        FieldKind::Custom(type_id) => {
            check_custom_field(&record_name, field, field_history, max_size, type_id)
        }
    };
    if let Some(field_history) = field_history.last_mut() {
        field_history.insert(field.name.clone(), field.clone());
    } else {
        return Err(TestLangError::InvalidSemantics {
            error: "Field history is empty while trying to insert field".to_owned(),
            record: Some(record_name.as_ref().to_owned()),
            field: Some(field.name.clone()),
        });
    }
    let new_warnings = result?;
    warnings.extend(new_warnings);
    Ok(warnings)
}

fn check_int_field(
    testlang: &TestLang,
    record_name: impl AsRef<str>,
    field: &Field,
    field_history: &mut [FieldEntry],
    max_size: Option<usize>,
) -> ValidationResult {
    let new_sem_err = |error: String| {
        Err(TestLangError::InvalidSemantics {
            error,
            record: Some(record_name.as_ref().to_owned()),
            field: Some(field.name.clone()),
        })
    };

    if field.string_format.is_some() {
        return new_sem_err(
            "`int` field cannot have `string_format`. (Hint: Try `string` kind to use `string_format`.)".to_owned(),
        );
    }

    if let ModeKind::FuzzedDataProvider(_) = testlang.mode {
        if field.fuzzed_data_provider_call.is_some()
            && (field.len.is_some() || field.byte_size.is_some())
        {
            return new_sem_err(
                "In `FuzzedDataProvider` `mode`, `int` field with `fuzzed_data_provider_call` cannot have `len` and `byte_size`."
                    .to_owned(),
            );
        }
    } else if field.byte_size.is_none() && field.len.is_none() {
        return new_sem_err(
            "In `Bytes` `mode`, `int` field must have `byte_size` or `len`.".to_owned(),
        );
    }
    for (size_name, size_descriptor) in [
        ("byte_size", field.byte_size.as_ref()),
        ("len", field.len.as_ref()),
    ] {
        check_size_descriptor(
            record_name.as_ref(),
            &field.name,
            field_history,
            size_name,
            size_descriptor,
        )?;
    }
    if field.byte_size.is_some() && field.len.is_some() && field.byte_size != field.len {
        return new_sem_err("`byte_size` and `len` don't have the same value.".to_owned());
    }
    let size_descriptor = field.byte_size.as_ref().or(field.len.as_ref());
    if let Some(NumValue::Single(ValOrRef::Val(val))) = size_descriptor {
        if !(1..=8).contains(val) {
            return new_sem_err(
                "`byte_size` or `len` has out of bounds value for `int` type. \
                    It should be in range of 1 to 8 (both ends inclusive). \
                    If the analysis made is considered correct and the length should be bigger than 8, \
                    try instead adding a separate `bytes` field at the highest order byte position for padding."
                    .to_owned(),
            );
        }
    };
    check_min_size(
        record_name.as_ref(),
        field,
        field_history,
        size_descriptor,
        max_size,
    )?;

    if let Some(ref possible_values) = field.possible_values {
        for val in possible_values.iter() {
            let is_int = match val {
                FieldValue::Int(num) => match num {
                    NumValue::Single(val_or_ref) => {
                        check_ref_field_kind(field_history, val_or_ref, field.kind.clone())
                    }
                    NumValue::Range(range) => {
                        check_ref_field_kind(field_history, &range.start, field.kind.clone())
                            && check_ref_field_kind(field_history, &range.end, field.kind.clone())
                    }
                },
                _ => false,
            };
            if !is_int {
                return new_sem_err(
                "`int` field can only have integer value or reference to `int` field in `possible_values`.".to_owned());
            }
            if let FieldValue::Int(NumValue::Range(range)) = val {
                if let (ValOrRef::Val(start), ValOrRef::Val(end)) = (&range.start, &range.end) {
                    if *start > *end {
                        return new_sem_err(format!(
                            "The `start` of the range is greater than `end`. (i.e. {} > {})",
                            start, end
                        ));
                    }
                }
            }
        }
    }

    let mut warnings = Vec::new();
    if field.endianness.is_none() && field.fuzzed_data_provider_call.is_none() {
        warnings.push(TestLangWarning::MissingEndian {
            record: record_name.as_ref().to_string(),
            field: field.name.clone(),
        });
    }
    if field.possible_values.is_none() && field.generator.is_none() {
        let warning = TestLangWarning::MissingPossibleValues {
            record: record_name.as_ref().to_string(),
            field: field.name.clone(),
        };
        warnings.push(warning);
    }
    if let Some(generator) = &field.generator {
        warnings.push(TestLangWarning::TediousGenerator {
            record: record_name.as_ref().to_string(),
            field: field.name.clone(),
            generator: generator.clone(),
        });
    }
    warnings.push(TestLangWarning::MaybeIntString {
        record: record_name.as_ref().to_string(),
        field: field.name.clone(),
    });
    warn_unexpected_attrs!(warnings, record_name.as_ref(), field, [items, terminator]);
    Ok(warnings)
}

fn check_float_field(
    testlang: &TestLang,
    record_name: impl AsRef<str>,
    field: &Field,
    field_history: &mut [FieldEntry],
    max_size: Option<usize>,
) -> ValidationResult {
    let new_sem_err = |error: String| {
        Err(TestLangError::InvalidSemantics {
            error,
            record: Some(record_name.as_ref().to_owned()),
            field: Some(field.name.clone()),
        })
    };

    if field.string_format.is_some() {
        return new_sem_err(
            "`float` field cannot have `string_format`. (Hint: Try `string` kind to use `string_format`.)".to_owned(),
        );
    }

    if let ModeKind::FuzzedDataProvider(_) = testlang.mode {
        if field.fuzzed_data_provider_call.is_some()
            && (field.len.is_some() || field.byte_size.is_some())
        {
            return new_sem_err(
                    "In `FuzzedDataProvider` `mode`, `float` field with `fuzzed_data_provider_call` cannot have `len` and `byte_size`."
                    .to_owned(),
            );
        }
    } else if field.byte_size.is_none() && field.len.is_none() {
        return new_sem_err(
            "In `Bytes` `mode`, `float` field must have `byte_size` or `len`.".to_owned(),
        );
    }
    for (size_name, size_descriptor) in [
        ("byte_size", field.byte_size.as_ref()),
        ("len", field.len.as_ref()),
    ] {
        check_size_descriptor(
            record_name.as_ref(),
            &field.name,
            field_history,
            size_name,
            size_descriptor,
        )?;
    }
    if field.byte_size.is_some() && field.len.is_some() && field.byte_size != field.len {
        return new_sem_err("`byte_size` and `len` don't have the same value.".to_owned());
    }
    let size_descriptor = field.byte_size.as_ref().or(field.len.as_ref());
    if let Some(NumValue::Single(ValOrRef::Val(val))) = size_descriptor {
        if !(4..=8).contains(val) {
            return new_sem_err(
                "`byte_size` or `len` has out of bounds value for `float` type. \
                    It should be in range of 4 to 8 (both ends inclusive). \
                    If the analysis made is considered correct and the length should be bigger than 8, \
                    try instead adding a separate `bytes` field for padding."
                    .to_owned(),
            );
        }
    };
    check_min_size(
        record_name.as_ref(),
        field,
        field_history,
        size_descriptor,
        max_size,
    )?;

    if let Some(ref possible_values) = field.possible_values {
        for val in possible_values.iter() {
            let is_float = match val {
                FieldValue::Float(num) => match num {
                    NumValue::Single(val_or_ref) => {
                        check_ref_field_kind(field_history, val_or_ref, field.kind.clone())
                    }
                    NumValue::Range(range) => {
                        check_ref_field_kind(field_history, &range.start, field.kind.clone())
                            && check_ref_field_kind(field_history, &range.end, field.kind.clone())
                    }
                },
                _ => false,
            };
            if !is_float {
                return new_sem_err(
                "`float` field can only have integer value or reference to `float` field in `possible_values`.".to_owned());
            }
            if let FieldValue::Float(NumValue::Range(range)) = val {
                if let (ValOrRef::Val(start), ValOrRef::Val(end)) = (&range.start, &range.end) {
                    if *start > *end {
                        return new_sem_err(format!(
                            "The `start` of the range is greater than `end`. (i.e. {} > {})",
                            start, end
                        ));
                    }
                }
            }
        }
    }

    let mut warnings = Vec::new();
    if field.endianness.is_none() && field.fuzzed_data_provider_call.is_none() {
        warnings.push(TestLangWarning::MissingEndian {
            record: record_name.as_ref().to_string(),
            field: field.name.clone(),
        });
    }
    if field.possible_values.is_none() && field.generator.is_none() {
        let warning = TestLangWarning::MissingPossibleValues {
            record: record_name.as_ref().to_string(),
            field: field.name.clone(),
        };
        warnings.push(warning);
    }
    if let Some(generator) = &field.generator {
        warnings.push(TestLangWarning::TediousGenerator {
            record: record_name.as_ref().to_string(),
            field: field.name.clone(),
            generator: generator.clone(),
        });
    }
    warn_unexpected_attrs!(warnings, record_name.as_ref(), field, [items, terminator]);
    Ok(warnings)
}

fn check_bytes_field(
    record_name: impl AsRef<str>,
    field: &Field,
    field_history: &mut [FieldEntry],
    max_size: Option<usize>,
) -> ValidationResult {
    let new_sem_err = |error: String| {
        Err(TestLangError::InvalidSemantics {
            error,
            record: Some(record_name.as_ref().to_owned()),
            field: Some(field.name.clone()),
        })
    };

    if field.string_format.is_some() {
        return new_sem_err(
            "`bytes` field cannot have `string_format`. (Hint: Try `string` kind to use `string_format`.)".to_owned(),
        );
    }

    for (size_name, size_descriptor) in [
        ("byte_size", field.byte_size.as_ref()),
        ("len", field.len.as_ref()),
    ] {
        check_size_descriptor(
            record_name.as_ref(),
            &field.name,
            field_history,
            size_name,
            size_descriptor,
        )?;
    }
    if field.byte_size.is_some() && field.len.is_some() && field.byte_size != field.len {
        return new_sem_err("`byte_size` and `len` don't have the same value.".to_owned());
    }
    let size_descriptor = field.byte_size.as_ref().or(field.len.as_ref());
    let max_concrete_size = get_max_concrete_size(size_descriptor);
    check_min_size(
        record_name.as_ref(),
        field,
        field_history,
        size_descriptor,
        max_size,
    )?;

    if let Some(ref possible_values) = field.possible_values {
        if !possible_values.iter().all(|x| match x {
            FieldValue::Bytes(val_or_ref) => {
                check_ref_field_kind(field_history, val_or_ref, field.kind.clone())
            }
            _ => false,
        }) {
            return new_sem_err("`bytes` field can only have bytes value or reference to `bytes` field in `possible_values`.".to_owned());
        }
    }

    match &field.terminator {
        Some(Terminator::ByteSequence(seq)) if seq.is_empty() => {
            return new_sem_err("`terminator` attribute shouldn't be empty.".to_owned());
        }
        Some(Terminator::ByteSequence(seq)) => {
            if let Some(max_size) = max_concrete_size {
                if max_size < seq.len() {
                    return new_sem_err(
                        "`terminator` is longer than maximum concrete value of its size descriptor. Check if you made size descriptor with `terminator` length in account."
                            .to_owned(),
                    );
                }
            }
        }
        Some(Terminator::CharSequence(_)) => {
            return new_sem_err(
                "`bytes` field can only have bytes value for `terminator`.".to_owned(),
            );
        }
        _ => (),
    }

    let mut warnings = Vec::new();
    if field.possible_values.is_none() && field.generator.is_none() {
        warnings.push(TestLangWarning::JustRandomBytes {
            record: record_name.as_ref().to_string(),
            field: field.name.clone(),
        });
    }
    if let Some(possible_values) = &field.possible_values {
        if possible_values.len() > 1 {
            warnings.push(TestLangWarning::MaybeSelector {
                record: record_name.as_ref().to_string(),
                field: field.name.clone(),
            });
        }
    }
    if let Some(generator) = &field.generator {
        warnings.push(TestLangWarning::TediousGenerator {
            record: record_name.as_ref().to_string(),
            field: field.name.clone(),
            generator: generator.clone(),
        });
    }
    warn_unexpected_attrs!(warnings, record_name.as_ref(), field, [items]);
    Ok(warnings)
}

fn check_custom_field(
    record_name: impl AsRef<str>,
    field: &Field,
    field_history: &mut [FieldEntry],
    max_size: Option<usize>,
    type_id: &str,
) -> ValidationResult {
    let new_sem_err = |error: String| {
        Err(TestLangError::InvalidSemantics {
            error,
            record: Some(record_name.as_ref().to_owned()),
            field: Some(field.name.clone()),
        })
    };

    if field.string_format.is_some() {
        return new_sem_err(
            "`custom` field cannot have `string_format`. (Hint: Try `string` kind to use `string_format`.)".to_owned(),
        );
    }

    if !TYPE_IDS.contains(&type_id) {
        return new_sem_err(
            format!("`custom` field is containing unsupported type id: `{}`. Please refer to `Supported custom type string IDs` section of testlang grammar to see supported ids.", type_id)
        );
    }

    if let Some(generator) = &field.generator {
        return new_sem_err(
            format!("`custom` field will use built-in generator instead of `{generator}`. If you want to use `{generator}`, you should make or merge another union record with this field."),
        );
    }

    for (size_name, size_descriptor) in [
        ("byte_size", field.byte_size.as_ref()),
        ("len", field.len.as_ref()),
    ] {
        check_size_descriptor(
            record_name.as_ref(),
            &field.name,
            field_history,
            size_name,
            size_descriptor,
        )?;
    }
    if field.byte_size.is_some() && field.len.is_some() && field.byte_size != field.len {
        return new_sem_err("`byte_size` and `len` don't have the same value.".to_owned());
    }
    let size_descriptor = field.byte_size.as_ref().or(field.len.as_ref());
    let max_concrete_size = get_max_concrete_size(size_descriptor);
    check_min_size(
        record_name.as_ref(),
        field,
        field_history,
        size_descriptor,
        max_size,
    )?;

    if field.possible_values.is_some() {
        return new_sem_err("`custom` field cannot have `possible_values`.".to_owned());
    }

    match &field.terminator {
        Some(Terminator::ByteSequence(seq)) if seq.is_empty() => {
            return new_sem_err("`terminator` attribute shouldn't be empty.".to_owned());
        }
        Some(Terminator::ByteSequence(seq)) => {
            if let Some(max_size) = max_concrete_size {
                if max_size < seq.len() {
                    return new_sem_err(
                        "`terminator` is longer than maximum concrete value of its size descriptor. Check if you made size descriptor with `terminator` length in account."
                            .to_owned(),
                    );
                }
            }
        }
        Some(Terminator::CharSequence(_)) => {
            return new_sem_err(
                "`custom` field can only have bytes value for `terminator`.".to_owned(),
            );
        }
        _ => (),
    }

    let mut warnings = Vec::new();
    warn_unexpected_attrs!(warnings, record_name.as_ref(), field, [items]);
    Ok(warnings)
}

fn check_string_field(
    record_name: impl AsRef<str>,
    field: &Field,
    field_history: &mut [FieldEntry],
    max_size: Option<usize>,
) -> ValidationResult {
    let new_sem_err = |error: String| {
        Err(TestLangError::InvalidSemantics {
            error,
            record: Some(record_name.as_ref().to_owned()),
            field: Some(field.name.clone()),
        })
    };

    for (size_name, size_descriptor) in [
        ("byte_size", field.byte_size.as_ref()),
        ("len", field.len.as_ref()),
    ] {
        check_size_descriptor(
            record_name.as_ref(),
            &field.name,
            field_history,
            size_name,
            size_descriptor,
        )?;
    }
    // Remove this check for `string` field
    if field.byte_size.is_some() && field.len.is_some() && field.byte_size != field.len {
        return new_sem_err("`byte_size` and `len` don't have the same value.".to_owned());
    }
    let size_descriptor = field.byte_size.as_ref().or(field.len.as_ref());
    let max_concrete_size = get_max_concrete_size(size_descriptor);
    check_min_size(
        record_name.as_ref(),
        field,
        field_history,
        size_descriptor,
        max_size,
    )?;

    if let Some(ref possible_values) = field.possible_values {
        if !possible_values.iter().all(|x| match x {
            FieldValue::String(val_or_ref) => {
                check_ref_field_kind(field_history, val_or_ref, field.kind.clone())
            }
            FieldValue::Int(num) if field.string_format.is_some() => match num {
                NumValue::Single(val_or_ref) => {
                    check_ref_field_kind(field_history, val_or_ref, FieldKind::Int)
                }
                NumValue::Range(range) => {
                    check_ref_field_kind(field_history, &range.start, FieldKind::Int)
                        && check_ref_field_kind(field_history, &range.end, FieldKind::Int)
                }
            },
            _ => false,
        }) {
            return new_sem_err(
                "`string` field can only have `possible_values` with string value or reference to `string` field (or integer value or range if `string_format` is set).".to_owned());
        }
    }

    match &field.terminator {
        Some(Terminator::CharSequence(seq)) if seq.is_empty() => {
            return new_sem_err("`terminator` attribute shouldn't be empty.".to_owned());
        }
        Some(Terminator::CharSequence(seq)) => {
            if let Some(max_size) = max_concrete_size {
                if max_size < seq.len() {
                    return new_sem_err(
                        "`terminator` is longer than maximum concrete value of its size descriptor. Check if you made size descriptor with `terminator` length in account."
                            .to_owned(),
                    );
                }
            }
        }
        Some(Terminator::ByteSequence(_)) => {
            return new_sem_err(
                "`string` field can only have string value for `terminator`.".to_owned(),
            );
        }
        _ => (),
    }

    let mut warnings = Vec::new();
    if field.possible_values.is_none() && field.generator.is_none() {
        let warning = TestLangWarning::MissingPossibleValues {
            record: record_name.as_ref().to_string(),
            field: field.name.clone(),
        };
        warnings.push(warning);
    }
    if let Some(generator) = &field.generator {
        warnings.push(TestLangWarning::TediousGenerator {
            record: record_name.as_ref().to_string(),
            field: field.name.clone(),
            generator: generator.clone(),
        });
    }
    if field.string_format.is_some() && field.terminator.is_none() {
        warnings.push(TestLangWarning::MissingTerminator {
            record: record_name.as_ref().to_string(),
            field: field.name.clone(),
        });
    }
    warn_unexpected_attrs!(warnings, record_name.as_ref(), field, [items]);
    Ok(warnings)
}

fn check_array_field(
    testlang: &TestLang,
    record_name: impl AsRef<str>,
    field: &Field,
    record_history: &mut HashMap<String, bool>,
    field_history: &mut Vec<FieldEntry>,
    max_size: Option<usize>,
    has_fdp_called: bool,
) -> ValidationResult {
    let new_sem_err = |error: String| {
        Err(TestLangError::InvalidSemantics {
            error,
            record: Some(record_name.as_ref().to_owned()),
            field: Some(field.name.clone()),
        })
    };

    if field.string_format.is_some() {
        return new_sem_err(
            "`array` field cannot have `string_format`. (Hint: Try `string` kind to use `string_format`.)".to_owned(),
        );
    }

    if let Some(generator) = &field.generator {
        return new_sem_err(
            format!("`array` field cannot have `generator`. If you want to use `{generator}`, you should make another union record with this field."),
        );
    }

    check_size_descriptor(
        record_name.as_ref(),
        &field.name,
        field_history,
        "len",
        field.len.as_ref(),
    )?;
    let size_descriptor = field.len.as_ref();
    let min_len = get_min_size(record_name.as_ref(), field, field_history, size_descriptor)?;

    let Some(Ref {
        kind: RefKind::Record,
        ref name,
    }) = field.items
    else {
        return new_sem_err("`array` field must have a reference to record in `items`.".to_owned());
    };

    let mut warnings = Vec::new();
    warn_unexpected_attrs!(
        warnings,
        record_name.as_ref(),
        field,
        [byte_size, possible_values, terminator]
    );
    warnings.extend(check_record(
        testlang,
        name,
        record_history,
        field_history,
        max_size.map(|max_size| max_size / min_len),
        has_fdp_called,
    )?);
    Ok(warnings)
}

fn check_record_field(
    testlang: &TestLang,
    record_name: impl AsRef<str>,
    field: &Field,
    record_history: &mut HashMap<String, bool>,
    field_history: &mut Vec<FieldEntry>,
    max_size: Option<usize>,
    has_fdp_called: bool,
) -> ValidationResult {
    let new_sem_err = |error: String| {
        Err(TestLangError::InvalidSemantics {
            error,
            record: Some(record_name.as_ref().to_owned()),
            field: Some(field.name.clone()),
        })
    };

    if field.string_format.is_some() {
        return new_sem_err(
            "`record` field cannot have `string_format`. (Hint: Try `string` kind to use `string_format`.)".to_owned(),
        );
    }

    if let Some(generator) = &field.generator {
        return new_sem_err(
            format!("`record` field cannot have `generator`. If you want to use `{generator}`, you should make another union record with this field."),
        );
    }

    check_size_descriptor(
        record_name.as_ref(),
        &field.name,
        field_history,
        "byte_size",
        field.byte_size.as_ref(),
    )?;

    let Some(Ref {
        kind: RefKind::Record,
        ref name,
    }) = field.items
    else {
        return new_sem_err(
            "`record` field must have a reference to record in `items`.".to_owned(),
        );
    };

    let mut warnings = Vec::new();
    warn_unexpected_attrs!(
        warnings,
        record_name.as_ref(),
        field,
        [possible_values, terminator]
    );
    warnings.extend(check_record(
        testlang,
        name,
        record_history,
        field_history,
        max_size,
        has_fdp_called || field.fuzzed_data_provider_call.is_some(),
    )?);
    Ok(warnings)
}

fn check_fdp_call(
    testlang: &TestLang,
    record_name: impl AsRef<str>,
    field: &Field,
    has_fdp_called: bool,
) -> ValidationResult {
    let new_sem_err = |error: String| {
        Err(TestLangError::InvalidSemantics {
            error,
            record: Some(record_name.as_ref().to_owned()),
            field: Some(field.name.clone()),
        })
    };
    let mut warnings = Vec::new();

    match testlang.mode {
        ModeKind::FuzzedDataProvider(fdp_kind) => {
            if has_fdp_called && field.fuzzed_data_provider_call.is_some() {
                return new_sem_err(
                    "There should be at most one relevant `fuzzed_data_provider_call` for each field. \
                        This field contains `fuzzed_data_provider_call`. \
                        However, it seems that this field's ancestor is already holding `fuzzed_data_provider_call`. \
                        Fix the nested `fuzzed_data_provider_call`.".to_owned());
            } else if !has_fdp_called
                && field.fuzzed_data_provider_call.is_none()
                && !(field.kind == FieldKind::Array || field.kind == FieldKind::Record)
            {
                return new_sem_err(
                    "Each normal field should have one and only relevant `fuzzed_data_provider_call`. \
                        It seems that this field has no relevant `fuzzed_data_provider_call. \
                        Add missing `fuzzed_data_provider_call` to this field or to an ancestor of this field, but NEVER synthesize non-existing FuzzedDataProvider calls. \
                        If it turns out that this field is not originated from a FuzzedDataProvider call, remove this field.".to_owned());
            }
            match &field.fuzzed_data_provider_call {
                None => (),
                // TODO: Valdiate `fdp_call.args.len()` per `method`.
                Some(fdp_call) => match fdp_call.method {
                    FuzzedDataProviderMethod::LLVM(method) => {
                        if fdp_kind != FuzzedDataProviderKind::LLVM {
                            return new_sem_err(
                                    "In `Original` FuzzedDataProvider `mode`, `fuzzed_data_provider_call` should have `Original` `method`."
                                    .to_owned());
                        }
                        if fdp_call.type_size.is_none() {
                            return new_sem_err(
                                    "In `Original` FuzzedDataProvider `mode`, \
                                        `fuzzed_data_provider_call` should contain `type_size` with its value as the size of corresponding template type."
                                        .to_owned());
                        }
                        match method {
                            LLVMFuzzedDataProviderMethod::ConsumeData
                            | LLVMFuzzedDataProviderMethod::ConsumeBytes
                            | LLVMFuzzedDataProviderMethod::ConsumeBytesAsString
                                if fdp_call.args.len() == 1 =>
                            {
                                let Some(byte_size) =
                                    field.byte_size.as_ref().or(field.len.as_ref())
                                else {
                                    return new_sem_err(
                                        format!("In `FuzzedDataProvider` `mode`, field with `{method:?}` should have `byte_size` or `len`."
                                        ));
                                };
                                if let FuzzedDataProviderArg::Int(val_arg) = &fdp_call.args[0] {
                                    if let NumValue::Single(ValOrRef::Val(val_field)) = byte_size {
                                        if *val_arg as usize != *val_field {
                                            return new_sem_err(
                                                format!("In `FuzzedDataProvider` `mode`, field with `{method:?}` should have its `byte_size` or `len` matching to the argument value when specified."
                                                ));
                                        }
                                    } else if let NumValue::Range(_) = byte_size {
                                        warnings.push(
                                            TestLangWarning::MaybeWrongFDPRangedSizeDescriptor {
                                                record: record_name.as_ref().to_owned(),
                                                field: field.name.to_owned(),
                                            },
                                        );
                                    }
                                }
                            }

                            LLVMFuzzedDataProviderMethod::ConsumeBytesWithTerminator
                                if fdp_call.args.len() == 1 =>
                            {
                                let Some(byte_size) =
                                    field.byte_size.as_ref().or(field.len.as_ref())
                                else {
                                    return new_sem_err(
                                            format!("In `FuzzedDataProvider` `mode`, field with `{method:?}` should have `byte_size` or `len`."
                                            ));
                                };
                                if let FuzzedDataProviderArg::Int(val_arg) = &fdp_call.args[0] {
                                    if let NumValue::Single(ValOrRef::Val(val_field)) = byte_size {
                                        if (*val_arg + 1) as usize != *val_field {
                                            return new_sem_err(
                                                    format!("In `FuzzedDataProvider` `mode`, field with `{method:?}` should have its `byte_size` or `len` equal to argument value added by one when specified."
                                                    ));
                                        }
                                    } else if let NumValue::Range(_) = byte_size {
                                        warnings.push(
                                            TestLangWarning::MaybeWrongFDPRangedSizeDescriptor {
                                                record: record_name.as_ref().to_owned(),
                                                field: field.name.to_owned(),
                                            },
                                        );
                                    }
                                }
                            }
                            LLVMFuzzedDataProviderMethod::ConsumeRandomLengthString
                                if fdp_call.args.len() == 1 =>
                            {
                                if field.byte_size.as_ref().or(field.len.as_ref()).is_none() {
                                    return new_sem_err(
                                        format!("In `FuzzedDataProvider` `mode`, field with `{method:?}` should have `byte_size` or `len`."
                                        ));
                                }
                            }
                            _ => (),
                        }
                        match method {
                            LLVMFuzzedDataProviderMethod::ConsumeIntegralInRange
                            | LLVMFuzzedDataProviderMethod::ConsumeIntegral
                            | LLVMFuzzedDataProviderMethod::ConsumeBool
                            | LLVMFuzzedDataProviderMethod::ConsumeEnum
                            | LLVMFuzzedDataProviderMethod::PickValueInArray => {
                                if field.kind != FieldKind::Int {
                                    return new_sem_err(
                                        format!("In `FuzzedDataProvider` `mode`, field with `{method:?}` should be `int` kind."
                                        ));
                                }
                            }
                            LLVMFuzzedDataProviderMethod::ConsumeProbability
                            | LLVMFuzzedDataProviderMethod::ConsumeFloatingPointInRange
                            | LLVMFuzzedDataProviderMethod::ConsumeFloatingPoint => {
                                if field.kind != FieldKind::Float {
                                    return new_sem_err(
                                        format!("In `FuzzedDataProvider` `mode`, field with `{method:?}` should be `float` kind."
                                        ));
                                }
                            }
                            LLVMFuzzedDataProviderMethod::ConsumeBytes
                            | LLVMFuzzedDataProviderMethod::ConsumeRemainingBytes
                            | LLVMFuzzedDataProviderMethod::ConsumeBytesWithTerminator
                            | LLVMFuzzedDataProviderMethod::ConsumeData => {
                                if field.kind != FieldKind::Record && field.kind != FieldKind::Bytes
                                {
                                    return new_sem_err(
                                        format!("In `FuzzedDataProvider` `mode`, field with `{method:?}` should be `record` or `bytes` kind."
                                        ));
                                }
                            }
                            LLVMFuzzedDataProviderMethod::ConsumeBytesAsString
                            | LLVMFuzzedDataProviderMethod::ConsumeRemainingBytesAsString
                            | LLVMFuzzedDataProviderMethod::ConsumeRandomLengthString => {
                                if field.kind != FieldKind::String {
                                    return new_sem_err(
                                        format!("In `FuzzedDataProvider` `mode`, field with `{method:?}` should be `string` kind."
                                        ));
                                }
                            }
                            _ => (),
                        }
                    }
                    FuzzedDataProviderMethod::Jazzer(method) => {
                        if fdp_kind != FuzzedDataProviderKind::Jazzer {
                            return new_sem_err(
                                    "In `Jazzer` FuzzedDataProvider `mode`, `fuzzed_data_provider_call` should have `Jazzer` `method`."
                                    .to_owned());
                        }
                        match method {
                            JazzerFuzzedDataProviderMethod::consumeBytes => {
                                let Some(byte_size) =
                                    field.byte_size.as_ref().or(field.len.as_ref())
                                else {
                                    return new_sem_err(
                                        format!("In `FuzzedDataProvider` `mode`, field with `{method:?}` should have `byte_size` or `len`."
                                    ));
                                };
                                if !fdp_call.args.is_empty() {
                                    if let FuzzedDataProviderArg::Int(val_arg) = &fdp_call.args[0] {
                                        if let NumValue::Single(ValOrRef::Val(val_field)) =
                                            byte_size
                                        {
                                            if *val_arg as usize != *val_field {
                                                return new_sem_err(
                                                    format!("In `FuzzedDataProvider` `mode`, field with `{method:?}` should have its `byte_size` or `len` matching to the argument value when specified."
                                                ));
                                            }
                                        } else if let NumValue::Range(_) = byte_size {
                                            warnings.push(
                                                TestLangWarning::MaybeWrongFDPRangedSizeDescriptor {
                                                    record: record_name.as_ref().to_owned(),
                                                    field: field.name.to_owned(),
                                                },
                                            );
                                        }
                                    }
                                }
                            }
                            JazzerFuzzedDataProviderMethod::consumeBooleans
                            | JazzerFuzzedDataProviderMethod::consumeShorts
                            | JazzerFuzzedDataProviderMethod::consumeInts
                            | JazzerFuzzedDataProviderMethod::consumeLongs
                            | JazzerFuzzedDataProviderMethod::consumeAsciiString
                            | JazzerFuzzedDataProviderMethod::consumeString => {
                                if field.byte_size.as_ref().or(field.len.as_ref()).is_none() {
                                    return new_sem_err(
                                        format!("In `FuzzedDataProvider` `mode`, field with `{method:?}` should have `byte_size` or `len`."
                                        ));
                                }
                            }
                            _ => (),
                        }
                        match method {
                            JazzerFuzzedDataProviderMethod::consumeBoolean
                            | JazzerFuzzedDataProviderMethod::consumeByte
                            | JazzerFuzzedDataProviderMethod::consumeShort
                            | JazzerFuzzedDataProviderMethod::consumeChar
                            | JazzerFuzzedDataProviderMethod::consumeCharNoSurrogates
                            | JazzerFuzzedDataProviderMethod::consumeInt
                            | JazzerFuzzedDataProviderMethod::consumeLong
                            | JazzerFuzzedDataProviderMethod::pickValue => {
                                if field.kind != FieldKind::Int {
                                    return new_sem_err(
                                        format!("In `FuzzedDataProvider` `mode`, field with `{method:?}` should be `int` kind."
                                        ));
                                }
                            }

                            JazzerFuzzedDataProviderMethod::consumeProbabilityFloat
                            | JazzerFuzzedDataProviderMethod::consumeProbabilityDouble
                            | JazzerFuzzedDataProviderMethod::consumeRegularFloat
                            | JazzerFuzzedDataProviderMethod::consumeRegularDouble
                            | JazzerFuzzedDataProviderMethod::consumeFloat
                            | JazzerFuzzedDataProviderMethod::consumeDouble => {
                                if field.kind != FieldKind::Float {
                                    return new_sem_err(
                                        format!("In `FuzzedDataProvider` `mode`, field with `{method:?}` should be `float` kind."
                                        ));
                                }
                            }

                            JazzerFuzzedDataProviderMethod::consumeBooleans
                            | JazzerFuzzedDataProviderMethod::consumeShorts
                            | JazzerFuzzedDataProviderMethod::consumeInts
                            | JazzerFuzzedDataProviderMethod::consumeLongs
                            | JazzerFuzzedDataProviderMethod::pickValues => {
                                if field.kind != FieldKind::Array {
                                    return new_sem_err(
                                        format!("In `FuzzedDataProvider` `mode`, field with `{method:?}` should be `array` kind."
                                        ));
                                }
                            }

                            JazzerFuzzedDataProviderMethod::consumeBytes
                            | JazzerFuzzedDataProviderMethod::consumeRemainingAsBytes => {
                                if field.kind != FieldKind::Record && field.kind != FieldKind::Bytes
                                {
                                    return new_sem_err(
                                        format!("In `FuzzedDataProvider` `mode`, field with `{method:?}` should be `record` or `bytes` kind."
                                        ));
                                }
                            }

                            JazzerFuzzedDataProviderMethod::consumeAsciiString
                            | JazzerFuzzedDataProviderMethod::consumeRemainingAsAsciiString
                            | JazzerFuzzedDataProviderMethod::consumeString
                            | JazzerFuzzedDataProviderMethod::consumeRemainingAsString => {
                                if field.kind != FieldKind::String {
                                    return new_sem_err(
                                        format!("In `FuzzedDataProvider` `mode`, field with `{method:?}` should be `string` kind."
                                        ));
                                }
                            }
                            _ => (),
                        }
                    }
                },
            }
        }
        _ => {
            if field.fuzzed_data_provider_call.is_some() {
                return new_sem_err(
                    "Field should not have `fuzzed_data_provider_call` in non-`FuzzedDataProvider` `mode`."
                    .to_owned(),
                );
            }
        }
    }

    Ok(warnings)
}

fn check_size_descriptor(
    record_name: &str,
    field_name: &str,
    field_history: &mut [FieldEntry],
    size_name: &str,
    size_descriptor: Option<&SizeDescriptor>,
) -> Result<(), TestLangError> {
    let Some(size_descriptor) = size_descriptor else {
        return Ok(());
    };
    let new_sem_err = |error: String| {
        Err(TestLangError::InvalidSemantics {
            error,
            record: Some(record_name.to_owned()),
            field: Some(field_name.to_owned()),
        })
    };
    if let SizeDescriptor::Range(range) = size_descriptor {
        if let (ValOrRef::Val(start), ValOrRef::Val(end)) = (&range.start, &range.end) {
            if start > end {
                return new_sem_err(format!(
                    "The `start` of the range is greater than `end`. (i.e. {} > {})",
                    start, end
                ));
            }
        }
    }
    if let SizeDescriptor::Single(ValOrRef::Ref(reference)) = size_descriptor {
        if reference.kind != RefKind::Field {
            return new_sem_err(format!(
                "`{size_name}` is referring to a non-field: {}.",
                reference.name
            ));
        } else {
            let field = find_field(field_history, reference);
            match field.map(|field| (field.kind, field.string_format)) {
                None => {
                    return new_sem_err(format!(
                        "`{size_name}` is referring to a non-existing field: {}. (You can set `{size_name}` as `null` if there are no size constraints.)",
                        reference.name
                    ))
                }
                Some((field_kind, string_format))
                    if field_kind == FieldKind::String && string_format.is_some() => {}
                Some((field_kind, _)) if field_kind != FieldKind::Int => {
                    return new_sem_err(format!(
                        "`{size_name}` is referring to a non-`int` field: {}.",
                        reference.name
                    ))
                }
                _ => (),
            }
        }
    }
    Ok(())
}

fn check_ref_field_kind<T>(
    field_history: &mut [FieldEntry],
    val_or_ref: &ValOrRef<T>,
    target_field_kind: FieldKind,
) -> bool {
    match val_or_ref {
        ValOrRef::Val(_) => true,
        ValOrRef::Ref(reference) => {
            reference.kind == RefKind::Field
                && Some(target_field_kind)
                    == find_field(field_history, reference).map(|field| field.kind)
        }
    }
}

fn get_max_concrete_size(size_descriptor: Option<&SizeDescriptor>) -> Option<usize> {
    size_descriptor.and_then(|x| {
        let val_or_ref = match x {
            NumValue::Single(s) => s,
            NumValue::Range(r) => &r.end,
        };
        match val_or_ref {
            ValOrRef::Val(v) => Some(*v),
            ValOrRef::Ref(_) => None,
        }
    })
}

fn check_min_size(
    record_name: &str,
    field: &Field,
    field_history: &mut [FieldEntry],
    size_descriptor: Option<&SizeDescriptor>,
    max_size: Option<usize>,
) -> Result<(), TestLangError> {
    let Some(max_size) = max_size else {
        return Ok(());
    };
    let min_size = get_min_size(record_name, field, field_history, size_descriptor)?;
    if max_size < min_size {
        return Err(TestLangError::InvalidSemantics {
            error: format!(
                "Field's minimum size is greater than its maximum size. (i.e. {} > {})",
                min_size, max_size
            ),
            record: Some(record_name.to_owned()),
            field: Some(field.name.to_owned()),
        });
    }
    Ok(())
}

fn get_min_size(
    record_name: &str,
    field: &Field,
    field_history: &mut [FieldEntry],
    size_descriptor: Option<&SizeDescriptor>,
) -> Result<usize, TestLangError> {
    let new_sem_err = |error: String| {
        Err(TestLangError::InvalidSemantics {
            error,
            record: Some(record_name.to_owned()),
            field: Some(field.name.to_owned()),
        })
    };
    let Some(size_descriptor) = size_descriptor else {
        match field.kind {
            FieldKind::Int => return Ok(1),
            FieldKind::Float => return Ok(4),
            FieldKind::Bytes => return Ok(0),
            FieldKind::String => return Ok(0),
            FieldKind::Array => return Ok(1),
            FieldKind::Record => return Ok(0),
            FieldKind::Custom(_) => return Ok(0),
        }
    };
    match size_descriptor {
        SizeDescriptor::Single(val_or_ref) => match val_or_ref {
            ValOrRef::Val(val) => Ok(*val),
            ValOrRef::Ref(reference) => {
                if reference.kind != RefKind::Field {
                    return new_sem_err(format!(
                        "size is referring to non field: {}.",
                        reference.name
                    ));
                }
                if find_field(field_history, reference).is_none() {
                    return new_sem_err(format!(
                        "size is referring to non-existing field: {}.",
                        reference.name
                    ));
                }
                Ok(0)
            }
        },
        SizeDescriptor::Range(range) => get_min_size(
            record_name,
            field,
            field_history,
            Some(&SizeDescriptor::Single(range.start.clone())),
        ),
    }
}

fn get_size_range_estimate(
    size_descriptor: Option<&NumValue<usize>>,
) -> std::ops::RangeInclusive<usize> {
    let min = match size_descriptor {
        Some(SizeDescriptor::Single(ValOrRef::Val(val)))
        | Some(SizeDescriptor::Range(RangeInclusive {
            start: ValOrRef::Val(val),
            end: _,
        })) => Some(val),
        _ => None,
    }
    .copied()
    .unwrap_or(0);
    let max = match size_descriptor {
        Some(SizeDescriptor::Single(ValOrRef::Val(val)))
        | Some(SizeDescriptor::Range(RangeInclusive {
            start: _,
            end: ValOrRef::Val(val),
        })) => Some(val),
        _ => None,
    }
    .copied()
    .unwrap_or(TestLangInt::MAX as usize);
    min..=max
}

pub(crate) fn check_record(
    testlang: &TestLang,
    record_name: impl AsRef<str>,
    record_history: &mut HashMap<String, bool>,
    field_history: &mut Vec<FieldEntry>,
    max_size: Option<usize>,
    has_fdp_called: bool,
) -> ValidationResult {
    if !testlang.record_index.contains_key(record_name.as_ref()) {
        return Err(TestLangError::InvalidSemantics {
            error: "Failed to find record.".to_owned(),
            record: Some(record_name.as_ref().to_owned()),
            field: None,
        });
    }
    if let Some(true) = record_history.get(record_name.as_ref()) {
        return Err(TestLangError::InvalidSemantics {
            error: "Record references should not form a cyclic dependency.".to_owned(),
            record: Some(record_name.as_ref().to_owned()),
            field: None,
        });
    }

    let record = &testlang.records[testlang.record_index[record_name.as_ref()]];
    let max_size = max_size.map(|max_size| match &record.byte_size {
        Some(SizeDescriptor::Single(val_or_ref)) => match val_or_ref {
            ValOrRef::Val(val) => cmp::min(max_size, *val),
            ValOrRef::Ref(_) => max_size,
        },
        Some(SizeDescriptor::Range(range)) => match &range.end {
            ValOrRef::Val(val) => cmp::min(max_size, *val),
            ValOrRef::Ref(_) => max_size,
        },
        None => max_size,
    });
    let record_size_range = get_size_range_estimate(record.byte_size.as_ref());
    if record_size_range.is_empty() {
        return Err(TestLangError::InvalidSemantics {
            error: "Record byte size `start` is bigger than `end`.".to_owned(),
            record: Some(record_name.as_ref().to_owned()),
            field: None,
        });
    }

    let mut field_names: HashSet<_> = HashSet::new();
    for field in record.fields.iter() {
        if !field_names.insert(&field.name) {
            return Err(TestLangError::InvalidSemantics {
                error: "Field names should be unique within a record.".to_owned(),
                record: Some(record_name.as_ref().to_owned()),
                field: Some(field.name.clone()),
            });
        }
    }

    record_history.insert(record.name.clone(), true);
    field_history.push(HashMap::new());
    let mut warnings = Vec::new();

    match record.kind {
        RecordKind::Struct => {
            if record.fields.is_empty() {
                return Err(TestLangError::InvalidSemantics {
                    error: "`struct` should contain at least one field.".to_owned(),
                    record: Some(record_name.as_ref().to_owned()),
                    field: None,
                });
            }
            let mut field_sizes_min: usize = 0;
            let mut field_sizes_max: usize = 0;
            for field in &record.fields {
                warnings.extend(check_field(
                    testlang,
                    &record.name,
                    field,
                    record_history,
                    field_history,
                    max_size,
                    has_fdp_called,
                )?);
                let size_descriptor = field.byte_size.as_ref().or(field.len.as_ref());
                let field_size_range = get_size_range_estimate(size_descriptor);
                // min max check should have been done in `check_field`.
                field_sizes_min = field_sizes_min.saturating_add(*field_size_range.start());
                field_sizes_max = field_sizes_max.saturating_add(*field_size_range.end());
            }
            let field_sizes_range = field_sizes_min..=field_sizes_max;
            if !field_sizes_range.is_empty() {
                let mut record_range = RangeInclusiveSet::new();
                record_range.insert(record_size_range);
                let mut field_range = RangeInclusiveSet::new();
                field_range.insert(field_sizes_range);
                if record_range.intersection(&field_range).next().is_none() {
                    return Err(TestLangError::InvalidSemantics {
                        error: "Record bytes size cannot be made up of its field's sizes."
                            .to_owned(),
                        record: Some(record_name.as_ref().to_owned()),
                        field: None,
                    });
                }
            }
            for analysis in &record.analysis {
                warnings.push(TestLangWarning::MissingCallee {
                    record: record.name.clone(),
                    callee_within_location: analysis.callee_within_location.clone(),
                });
                for callee in &analysis.callee_within_location {
                    if callee == &analysis.location.func_name {
                        warnings.push(TestLangWarning::RecursiveFunction {
                            record: record.name.clone(),
                            callee: callee.clone(),
                        });
                    }
                }
                match fs::read_to_string(&analysis.location.file_path) {
                    Ok(code) => {
                        let code_lines = code.split('\n').collect::<Vec<_>>();
                        if analysis.location.start_line_num <= analysis.location.end_line_num {
                            if analysis.location.end_line_num <= code_lines.len() {
                                if analysis.location.start_line_num >= 1 {
                                    let start_line_idx = analysis.location.start_line_num - 1;
                                    let end_line_idx = analysis.location.end_line_num - 1;
                                    let code_lines = &code_lines[start_line_idx..=end_line_idx];
                                    for callee in &analysis.callee_within_location {
                                        if !code_lines.iter().any(|line| line.contains(callee)) {
                                            warnings.push(
                                                TestLangWarning::MissingCalleeInLocation {
                                                    record: record.name.clone(),
                                                    location: analysis.location.clone(),
                                                    callee: callee.clone(),
                                                },
                                            );
                                        }
                                    }
                                } else {
                                    warnings.push(TestLangWarning::InvalidLocation {
                                        record: record.name.clone(),
                                        location: analysis.location.clone(),
                                        detail:
                                            "`start_line_num` should be greater than or equal to 1."
                                                .to_owned(),
                                    });
                                }
                            } else {
                                warnings.push(TestLangWarning::InvalidLocation {
                                    record: record.name.clone(),
                                    location: analysis.location.clone(),
                                    detail: "`end_line_num` should be less than or equal to the number of lines in the file.".to_owned(),
                                });
                            }
                        } else {
                            warnings.push(TestLangWarning::InvalidLocation {
                                record: record.name.clone(),
                                location: analysis.location.clone(),
                                detail: "`start_line_num` should be less than or equal to `end_line_num`.".to_owned(),
                            });
                        }
                    }
                    Err(err) => {
                        warnings.push(TestLangWarning::InvalidLocation {
                            record: record.name.clone(),
                            location: analysis.location.clone(),
                            detail: format!("Failed to read file: {}", err),
                        });
                    }
                }
            }
        }
        RecordKind::Union => {
            if record.fields.is_empty() {
                return Err(TestLangError::InvalidSemantics {
                    error: "`union` should contain at least one field.".to_owned(),
                    record: Some(record_name.as_ref().to_owned()),
                    field: None,
                });
            }
            for field in &record.fields {
                if field.kind != FieldKind::Record {
                    return Err(TestLangError::InvalidSemantics {
                        error: "`union` is containing a non-record field.".to_owned(),
                        record: Some(record_name.as_ref().to_owned()),
                        field: Some(field.name.clone()),
                    });
                }
                warnings.extend(check_field(
                    testlang,
                    &record.name,
                    field,
                    record_history,
                    field_history,
                    max_size,
                    has_fdp_called,
                )?);
            }
        }
    }
    field_history.pop();
    record_history.insert(record.name.clone(), false);
    Ok(warnings)
}
