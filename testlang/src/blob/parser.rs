use std::{collections::HashMap, str::from_utf8};

use byteorder::{BigEndian, ByteOrder, LittleEndian};
use memchr::memmem::Finder;

use crate::{
    Endianness, Field, FieldKind, FieldValue, NumValue, Record, RecordKind, Ref, RefKind,
    SizeDescriptor, StringFormat, Terminator, TestLang, TestLangInt, ValOrRef, RECORD_INPUT,
};

use super::{ParseTestLangBlobError, TestLangBlob, TestLangBlobMetadata};

pub type SliceId = usize;

type BlobParseResult<T> = Result<T, ParseTestLangBlobError>;
type DereferenceMap = HashMap<String, (SliceId, DereferenceMetadata, Vec<u8>)>;

#[derive(Clone, Copy)]
struct DereferenceMetadata {
    string_format: Option<StringFormat>,
    endianness: Endianness,
}

fn bytes_to_int(b: &[u8], endian: Endianness) -> BlobParseResult<TestLangInt> {
    match endian {
        Endianness::Little => match b.len() {
            1..=8 => Ok(LittleEndian::read_int(b, b.len())),
            9..=16 => Ok(LittleEndian::read_int128(b, b.len()).try_into()?),
            _ => Err(ParseTestLangBlobError::InvalidData(
                "Not supporting big byte sizes for now".to_owned(),
            )),
        },
        Endianness::Big => match b.len() {
            1..=8 => Ok(BigEndian::read_int(b, b.len())),
            9..=16 => Ok(BigEndian::read_int128(b, b.len()).try_into()?),
            _ => Err(ParseTestLangBlobError::InvalidData(
                "Not supporting big byte sizes for now".to_owned(),
            )),
        },
    }
}

fn bytes_to_usize(b: &[u8], endian: Endianness) -> BlobParseResult<usize> {
    match endian {
        Endianness::Little => match b.len() {
            1..=8 => Ok(LittleEndian::read_uint(b, b.len()).try_into()?),
            9..=16 => Ok(LittleEndian::read_uint128(b, b.len()).try_into()?),
            _ => Err(ParseTestLangBlobError::InvalidData(
                "Not supporting big byte sizes for now".to_owned(),
            )),
        },
        Endianness::Big => match b.len() {
            1..=8 => Ok(BigEndian::read_uint(b, b.len()).try_into()?),
            9..=16 => Ok(BigEndian::read_uint128(b, b.len()).try_into()?),
            _ => Err(ParseTestLangBlobError::InvalidData(
                "Not supporting big byte sizes for now".to_owned(),
            )),
        },
    }
}

fn string_bytes_to_usize(b: &[u8], string_format: StringFormat) -> BlobParseResult<usize> {
    let Ok(decoded) = from_utf8(b) else {
        return Err(ParseTestLangBlobError::InvalidData(
            "String containing invalid bytes".to_owned(),
        ));
    };
    let radix = match string_format {
        StringFormat::BinInt => 2,
        StringFormat::DecInt => 10,
        StringFormat::HexInt => 16,
        StringFormat::OctInt => 8,
    };
    let size = usize::from_str_radix(decoded, radix)?;
    Ok(size)
}

fn find_record(testlang: &TestLang, record_name: impl AsRef<str>) -> BlobParseResult<&Record> {
    if !testlang.record_index.contains_key(record_name.as_ref()) {
        return Err(ParseTestLangBlobError::InvalidTestlang(format!(
            "{RECORD_INPUT} not found"
        )));
    }
    Ok(&testlang.records[testlang.record_index[record_name.as_ref()]])
}

fn process_size(
    size_descriptor: &SizeDescriptor,
    dereference_map: &DereferenceMap,
) -> BlobParseResult<(Option<SliceId>, usize)> {
    match size_descriptor {
        SizeDescriptor::Single(val_or_ref) => match val_or_ref {
            ValOrRef::Val(val) => Ok((None, *val)),
            ValOrRef::Ref(reference) => {
                let (id, metadata, value) =
                    dereference_map.get(&reference.name).ok_or_else(|| {
                        ParseTestLangBlobError::DereferenceError(format!(
                            "No reference to: {}",
                            reference.name
                        ))
                    })?;
                match metadata.string_format {
                    Some(string_format) => {
                        Ok((Some(*id), string_bytes_to_usize(value, string_format)?))
                    }
                    None => Ok((Some(*id), bytes_to_usize(value, metadata.endianness)?)),
                }
            }
        },
        SizeDescriptor::Range(_size_range_inclusive) => todo!(),
    }
}

fn match_field_value(
    match_target: &[u8],
    field_value: &FieldValue,
    dereference_map: &DereferenceMap,
    endianness: Endianness,
    size_hint: Option<usize>,
) -> BlobParseResult<bool> {
    Ok(match field_value {
        FieldValue::Int(value) => match value {
            NumValue::Single(ValOrRef::Val(val)) => {
                let target_size = size_hint.unwrap_or(4);
                let mut value_bytes = vec![0; target_size];
                LittleEndian::write_int(&mut value_bytes, *val, target_size);
                value_bytes == match_target
            }
            NumValue::Single(ValOrRef::Ref(reference)) => {
                let (_, _, value_bytes) =
                    dereference_map.get(&reference.name).ok_or_else(|| {
                        ParseTestLangBlobError::DereferenceError(format!(
                            "No reference to: {}",
                            reference.name
                        ))
                    })?;
                value_bytes == match_target
            }
            NumValue::Range(range) => {
                if let (ValOrRef::Val(start), ValOrRef::Val(end)) = (&range.start, &range.end) {
                    let match_target_int = bytes_to_int(match_target, endianness)?;
                    *start <= match_target_int && match_target_int <= *end
                } else {
                    todo!("src/blob/parser.rs match_field_value: Int(Range(Ref))")
                }
            }
        },
        FieldValue::Float(_) => todo!("src/blob/parser.rs match_field_value: Float"),
        FieldValue::String(string) => match string {
            ValOrRef::Val(string) => match_target == string.as_bytes(),
            ValOrRef::Ref(_) => todo!("src/blob/parser.rs match_field_value: String(Ref)"),
        },
        FieldValue::Bytes(bytes) => match bytes {
            ValOrRef::Val(bytes) => match_target == bytes,
            ValOrRef::Ref(reference) => {
                let (_, _, value_bytes) =
                    dereference_map.get(&reference.name).ok_or_else(|| {
                        ParseTestLangBlobError::DereferenceError(format!(
                            "No reference to: {}",
                            reference.name
                        ))
                    })?;
                value_bytes == match_target
            }
        },
    })
}

type BlobParseFieldState<'a> = (&'a [u8], SliceId, Vec<(SliceId, Vec<u8>)>);

fn process_normal_field<'a>(
    input: &'a [u8],
    field: &Field,
    dereference_map: &mut DereferenceMap,
    metadata: &mut TestLangBlobMetadata,
) -> BlobParseResult<BlobParseFieldState<'a>> {
    let my_id = metadata.take_id();
    let is_string = field.kind == FieldKind::String;
    let is_bytes = field.kind == FieldKind::Bytes;
    let (field_size_id, field_size_except_terminator, terminator) =
        if let Some(size_descriptor) = field.len.as_ref().or(field.byte_size.as_ref()) {
            let (field_size_id, field_size) = process_size(size_descriptor, dereference_map)?;
            match &field.terminator {
                Some(Terminator::ByteSequence(seq)) => {
                    (field_size_id, field_size - seq.len(), Some(seq.as_slice()))
                }
                Some(Terminator::CharSequence(seq)) => {
                    (field_size_id, field_size - seq.len(), Some(seq.as_bytes()))
                }
                None => (field_size_id, field_size, None),
            }
        } else {
            match &field.terminator {
                Some(Terminator::CharSequence(seq)) if is_string => {
                    let finder = Finder::new(&seq);
                    if let Some(pos) = finder.find(input) {
                        (None, pos, Some(seq.as_bytes()))
                    } else {
                        return Err(ParseTestLangBlobError::InvalidData(
                            "Terminator not found in remaining data".to_owned(),
                        ));
                    }
                }
                Some(Terminator::ByteSequence(seq)) if is_bytes => {
                    let finder = Finder::new(&seq);
                    if let Some(pos) = finder.find(input) {
                        (None, pos, Some(seq.as_slice()))
                    } else {
                        return Err(ParseTestLangBlobError::InvalidData(
                            "Terminator not found in remaining data".to_owned(),
                        ));
                    }
                }
                None if is_string => {
                    let finder = Finder::new(b"\0");
                    if let Some(pos) = finder.find(input) {
                        (None, pos, Some(b"\0".as_slice()))
                    } else {
                        (None, input.len(), None)
                    }
                }
                None if is_bytes => (None, input.len(), None),
                _ => {
                    return Err(ParseTestLangBlobError::InvalidData(
                        "Normal field expected but doesn't contain size or proper terminator."
                            .to_owned(),
                    ));
                }
            }
        };

    let remaining_len = input.len();
    let terminator_len = terminator.map(|x| x.len()).unwrap_or(0);
    let full_field_size = field_size_except_terminator + terminator_len;

    if remaining_len < full_field_size {
        return Err(ParseTestLangBlobError::InvalidData(format!(
            "Required field size {full_field_size} bigger than remaining input size {remaining_len}."
        )));
    }

    if let Some(terminator) = terminator {
        let terminator_input = &input[field_size_except_terminator..][..terminator_len];
        if terminator_input != terminator {
            return Err(ParseTestLangBlobError::ValueUnmatched);
        }
    }

    let field_bytes = &input[..field_size_except_terminator];
    if let Some([field_value]) = field.possible_values.as_deref() {
        if !match_field_value(
            field_bytes,
            field_value,
            dereference_map,
            field.endianness.unwrap_or(metadata.default_endian),
            Some(field_size_except_terminator),
        )? {
            return Err(ParseTestLangBlobError::ValueUnmatched);
        }
        metadata.stage_immutable(my_id, field_size_id);
        // TODO: Limit possible values to be in format -> Do this in testlang parser
    } else if let Some(field_values) = &field.possible_values {
        let mut choosables = Vec::new();
        let mut current_choice = None;
        for field_value in field_values {
            if match_field_value(
                field_bytes,
                field_value,
                dereference_map,
                field.endianness.unwrap_or(metadata.default_endian),
                Some(field_size_except_terminator),
            )? {
                current_choice = Some(field_value.clone());
            }
            choosables.push(field_bytes.to_vec());
        }
        if current_choice.is_none() {
            return Err(ParseTestLangBlobError::ValueUnmatched);
        }
        metadata.stage_choosable(my_id, field_size_id, choosables);
        // TODO: Limit possible values to be in format -> Do this in testlang parser
    } else if is_string {
        metadata.stage_mutable_string(my_id, field.string_format, field_size_id);
    } else {
        metadata.stage_mutable(my_id, field.endianness, field_size_id);
    }

    dereference_map.insert(
        field.name.to_owned(),
        (
            my_id,
            DereferenceMetadata {
                string_format: field.string_format,
                endianness: field.endianness.unwrap_or(metadata.default_endian),
            },
            field_bytes.to_vec(),
        ),
    );

    if let Some(string_format) = field.string_format {
        if string_bytes_to_usize(field_bytes, string_format).is_err() {
            return Err(ParseTestLangBlobError::ValueUnmatched);
        }
    }

    if let Some(terminator) = terminator {
        let terminator_id = metadata.take_id();
        metadata.stage_terminator(terminator_id, my_id);
        Ok((
            &input[full_field_size..],
            my_id,
            // Terminator slice doesn't need extra processing for now.
            // But it may break some future implementations. Take it into consideration.
            vec![
                (my_id, field_bytes.to_vec()),
                (terminator_id, terminator.to_vec()),
            ],
        ))
    } else {
        Ok((
            &input[field_size_except_terminator..],
            my_id,
            vec![(my_id, field_bytes.to_vec())],
        ))
    }
}

fn process_array_field<'a>(
    input: &'a [u8],
    field: &Field,
    testlang: &TestLang,
    dereference_map: &mut DereferenceMap,
    metadata: &mut TestLangBlobMetadata,
) -> BlobParseResult<BlobParseFieldState<'a>> {
    let my_id = metadata.take_id();
    let size_descriptor = field.len.as_ref().ok_or_else(|| {
        ParseTestLangBlobError::InvalidTestlang(format!(
            "{} doesn't specifies array length",
            &field.name
        ))
    })?;
    let (len_id, len) = process_size(size_descriptor, dereference_map)?;
    let mut children = Vec::new();
    let mut slices = Vec::new();
    let mut remainder = input;
    let Some(Ref {
        kind: RefKind::Record,
        name: ref ref_name,
    }) = field.items
    else {
        return Err(ParseTestLangBlobError::InvalidTestlang(format!(
            "Array field {} doesn't have record reference",
            field.name
        )));
    };
    let record = find_record(testlang, ref_name)?;

    for _ in 0..len {
        let (advanced, id, slice) =
            process_record(remainder, record, testlang, dereference_map, metadata)?;
        children.push(id);
        slices.extend(slice);
        remainder = advanced;
    }

    metadata.stage_array(my_id, ref_name, children, len_id);
    Ok((remainder, my_id, slices))
}

fn process_record_field<'a>(
    input: &'a [u8],
    field: &Field,
    testlang: &TestLang,
    dereference_map: &mut DereferenceMap,
    metadata: &mut TestLangBlobMetadata,
) -> BlobParseResult<BlobParseFieldState<'a>> {
    let my_id = metadata.take_id();
    let Some(Ref {
        kind: RefKind::Record,
        name: ref ref_name,
    }) = field.items
    else {
        return Err(ParseTestLangBlobError::InvalidTestlang(format!(
            "Record field {} doesn't have record reference",
            field.name
        )));
    };
    let record = find_record(testlang, ref_name)?;

    let (result, size_constraint) =
        if let Some(size_descriptor) = field.len.as_ref().or(field.byte_size.as_ref()) {
            let (size_id, size) = process_size(size_descriptor, dereference_map)?;

            let (_, id, slices) =
                process_record(&input[..size], record, testlang, dereference_map, metadata)?;
            // WARN if remainder above has contents

            ((&input[size..], id, slices), size_id)
        } else {
            let result = process_record(input, record, testlang, dereference_map, metadata)?;
            (result, None)
        };
    metadata.stage_record_field(my_id, ref_name, result.1, size_constraint);
    Ok(result)
}

fn process_field<'a>(
    input: &'a [u8],
    field: &Field,
    testlang: &TestLang,
    dereference_map: &mut DereferenceMap,
    metadata: &mut TestLangBlobMetadata,
) -> BlobParseResult<BlobParseFieldState<'a>> {
    match field.kind {
        FieldKind::Int
        | FieldKind::Float
        | FieldKind::Bytes
        | FieldKind::String
        | FieldKind::Custom(_) => process_normal_field(input, field, dereference_map, metadata),
        FieldKind::Array => process_array_field(input, field, testlang, dereference_map, metadata),
        FieldKind::Record => {
            process_record_field(input, field, testlang, dereference_map, metadata)
        }
    }
}

type BlobParseRecordState<'a> = (&'a [u8], SliceId, Vec<(SliceId, Vec<u8>)>);

fn process_record<'a>(
    input: &'a [u8],
    record: &Record,
    testlang: &TestLang,
    dereference_map: &DereferenceMap,
    metadata: &mut TestLangBlobMetadata,
) -> BlobParseResult<BlobParseRecordState<'a>> {
    let my_id = metadata.take_id();

    match record.kind {
        RecordKind::Struct => {
            let mut slices = Vec::new();
            let mut children = Vec::new();
            let mut local_deref_map = dereference_map.clone();
            let mut remainder = input;

            for field in &record.fields {
                let (advanced, id, slice) =
                    process_field(remainder, field, testlang, &mut local_deref_map, metadata)?;
                remainder = advanced;
                children.push(id);
                slices.extend(slice);
            }
            metadata.stage_struct(my_id, &record.name, children);
            Ok((remainder, my_id, slices))
        }
        RecordKind::Union => {
            let snapshot = metadata.get_stage_snapshot();
            let found = record
                .fields
                .iter()
                .find_map(|record_field| {
                    let Some(Ref {
                        kind: RefKind::Record,
                        name: ref ref_name,
                    }) = record_field.items
                    else {
                        return None;
                    };

                    find_record(testlang, ref_name)
                        .and_then(|record| {
                            process_record(input, record, testlang, dereference_map, metadata)
                                .inspect_err(|_| {
                                    metadata.discard_staged(snapshot);
                                })
                        })
                        .ok()
                })
                .ok_or(ParseTestLangBlobError::ValueUnmatched)?;
            metadata.stage_union(my_id, &record.name, found.1);
            metadata.record_staged();

            Ok(found)
        }
    }
}

pub fn parse_blob(input: &[u8], testlang: &TestLang) -> BlobParseResult<TestLangBlob> {
    let mut metadata = TestLangBlobMetadata::new(input.len(), testlang.default_endian);
    let input_record = find_record(testlang, RECORD_INPUT)?;
    let (_, _, flattened) = process_record(
        input,
        input_record,
        testlang,
        &HashMap::new(),
        &mut metadata,
    )?;

    metadata.record_staged();
    metadata.finalize_mutables();

    Ok(TestLangBlob {
        metadata,
        slices: flattened,
    })
}
