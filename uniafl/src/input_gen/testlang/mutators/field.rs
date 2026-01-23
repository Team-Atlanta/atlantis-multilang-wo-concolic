use std::collections::HashSet;

use bytes::BytesMut;
use libafl::{
    inputs::{BytesInput, HasMutatorBytes},
    mutators::{
        mutations::{
            BitFlipMutator, ByteAddMutator, ByteDecMutator, ByteFlipMutator, ByteIncMutator,
            ByteInterestingMutator, ByteNegMutator, ByteRandMutator, BytesCopyMutator,
            BytesDeleteMutator, BytesExpandMutator, BytesInsertCopyMutator, BytesInsertMutator,
            BytesRandInsertMutator, BytesRandSetMutator, BytesSetMutator, BytesSwapMutator,
            DwordAddMutator, DwordInterestingMutator, QwordAddMutator, WordAddMutator,
            WordInterestingMutator,
        },
        MutationResult, Mutator,
    },
    state::HasMaxSize,
};
use libafl_bolts::{rands::Rand, Named};
use memchr::memmem::find_iter;
use rand::Rng;
use testlang::{
    FieldKind, FieldValue, NumValue, RangeInclusive, StringFormat, Terminator, TestLangAst,
    TestLangInt, TestLangNodeValue, ValOrRef,
};

use crate::{
    common::Error,
    input_gen::testlang::{
        bytes_to_float, bytes_to_int, float_to_bytes, int_to_bytes,
        mutators::update_fmt_string_size_dep, node_to_bytes, service::worker::TestLangState,
        TestLangInputMutator,
    },
};

pub struct FieldChooser;

impl TestLangInputMutator for FieldChooser {
    #[cfg(feature = "log")]
    fn name(&self) -> &str {
        "FieldChooser"
    }

    fn mutate(
        &mut self,
        state: &mut TestLangState,
        input: &TestLangAst,
        input_size: usize,
        bytes_output: &mut Vec<u8>,
        metadata_output: &mut Option<TestLangAst>,
    ) -> Result<MutationResult, Error> {
        let input_metadata = &input.metadata;
        let Some(field_id) = state
            .rand
            .choose(input_metadata.choosable_fields.iter())
            .copied()
        else {
            return Ok(MutationResult::Skipped);
        };

        let mut output_node = input.root.clone();
        let Some(field_node) = output_node.find_by_id_mut(field_id) else {
            return Err(Error::testlang_error("AST metadata is broken"));
        };

        let testlang_arc = state.testlang.clone();
        let testlang = testlang_arc.as_ref();

        let Some(field) = testlang.find_field_by_id(field_node.type_id) else {
            return Err(Error::testlang_error("AST metadata is broken"));
        };
        let Some(ref possible_values) = field.possible_values else {
            return Err(Error::testlang_error("AST metadata is broken"));
        };
        let Some(choosable) = state.rand.choose(possible_values) else {
            return Ok(MutationResult::Skipped);
        };

        // Pick choosable
        let size_diff = match choosable {
            FieldValue::Int(value) => {
                let value = match value {
                    NumValue::Single(ValOrRef::Val(value)) => *value,
                    NumValue::Range(RangeInclusive {
                        start: ValOrRef::Val(start),
                        end: ValOrRef::Val(end),
                    }) => {
                        let gen_range = (*end - *start + 1) as usize;
                        start + state.rand.below(gen_range) as TestLangInt
                    }
                    _ => return Ok(MutationResult::Skipped),
                };
                let TestLangNodeValue::Int(node_value) = &mut field_node.value else {
                    return Err(Error::testlang_error("AST metadata is broken"));
                };
                *node_value = value;
                0
            }
            FieldValue::Float(value) => {
                let value = match value {
                    NumValue::Single(ValOrRef::Val(value)) => *value,
                    NumValue::Range(RangeInclusive {
                        start: ValOrRef::Val(start),
                        end: ValOrRef::Val(end),
                    }) => state.rand.gen_range(*start..=*end),
                    _ => return Ok(MutationResult::Skipped),
                };
                let TestLangNodeValue::Float(node_value) = &mut field_node.value else {
                    return Err(Error::testlang_error("AST metadata is broken"));
                };
                *node_value = value;
                0
            }
            FieldValue::String(ValOrRef::Val(value)) => {
                let TestLangNodeValue::String(node_value) = &mut field_node.value else {
                    return Err(Error::testlang_error("AST metadata is broken"));
                };
                let size_diff = value.len() as i128 - node_value.len() as i128;
                *node_value = value.to_owned();
                size_diff
            }
            FieldValue::Bytes(ValOrRef::Val(value)) => {
                let TestLangNodeValue::Bytes(node_value) = &mut field_node.value else {
                    return Err(Error::testlang_error("AST metadata is broken"));
                };
                let size_diff = value.len() as i128 - node_value.len() as i128;
                *node_value = value.to_owned();
                size_diff
            }
            _ => return Ok(MutationResult::Skipped),
        };

        let max_extension = state.max_size().saturating_sub(input_size);
        if size_diff != 0 {
            if (max_extension as i128) < size_diff {
                return Ok(MutationResult::Skipped);
            }
            if let Some(associated_size_id) = input_metadata.size_ref.get(&field_id).cloned() {
                let Some(associated_size_node) = output_node.find_by_id_mut(associated_size_id)
                else {
                    return Err(Error::testlang_error("AST metadata is broken"));
                };
                match &mut associated_size_node.value {
                    TestLangNodeValue::Int(size_field_value) => {
                        *size_field_value =
                            size_field_value.saturating_add(size_diff as TestLangInt);
                    }
                    TestLangNodeValue::String(fmt_string) => {
                        let Some(field) = testlang.find_field_by_id(associated_size_node.type_id)
                        else {
                            return Err(Error::testlang_error("AST metadata is broken"));
                        };
                        update_fmt_string_size_dep(fmt_string, field, size_diff as TestLangInt, 0)?;
                    }
                    _ => {
                        return Err(Error::testlang_error(
                            "Other than integer-like type is rejected for size reference for now",
                        ));
                    }
                }
            }
        }

        let mut output = node_to_bytes(testlang, &state.codegen_path, &output_node)?;
        if output.len() > state.max_size() {
            return Ok(MutationResult::Skipped);
        }
        bytes_output.append(&mut output);
        let new_ast = TestLangAst::new(output_node, testlang)?;
        *metadata_output = Some(new_ast);
        Ok(MutationResult::Mutated)
    }
}

pub struct FieldMutator<BM> {
    #[cfg(feature = "log")]
    name: String,
    mutator: BM,
}

impl<BM> TestLangInputMutator for FieldMutator<BM>
where
    BM: Mutator<BytesInput, TestLangState>,
{
    #[cfg(feature = "log")]
    fn name(&self) -> &str {
        &self.name
    }

    fn mutate(
        &mut self,
        state: &mut TestLangState,
        input: &TestLangAst,
        input_size: usize,
        bytes_output: &mut Vec<u8>,
        metadata_output: &mut Option<TestLangAst>,
    ) -> Result<MutationResult, Error> {
        let input_metadata = &input.metadata;
        let Some(field_id) = state
            .rand
            .choose(input_metadata.mutable_fields.iter())
            .copied()
        else {
            return Ok(MutationResult::Skipped);
        };

        let mut output_node = input.root.clone();
        let Some(field_node) = output_node.find_by_id_mut(field_id) else {
            return Err(Error::testlang_error("AST metadata is broken"));
        };

        let testlang_arc = state.testlang.clone();
        let testlang = testlang_arc.as_ref();

        let Some(field) = testlang.find_field_by_id(field_node.type_id) else {
            return Err(Error::testlang_error("AST metadata is broken"));
        };

        let mut original_bytes = BytesMut::new();
        let fixed_size = match field.kind {
            FieldKind::Int => {
                let TestLangNodeValue::Int(value) = &field_node.value else {
                    return Err(Error::testlang_error("AST metadata is broken"));
                };
                let endian = field.endianness.unwrap_or(testlang.default_endian);
                original_bytes.extend_from_slice(
                    int_to_bytes(*value, field_node.byte_size, endian).as_slice(),
                );
                true
            }
            FieldKind::Bytes | FieldKind::Custom(_) => {
                let TestLangNodeValue::Bytes(value) = &field_node.value else {
                    return Err(Error::testlang_error("AST metadata is broken"));
                };
                original_bytes.extend_from_slice(value.as_slice());
                false
            }
            FieldKind::String => {
                let TestLangNodeValue::String(value) = &field_node.value else {
                    return Err(Error::testlang_error("AST metadata is broken"));
                };
                original_bytes.extend_from_slice(value.as_bytes());
                false
            }
            FieldKind::Float => {
                let TestLangNodeValue::Float(value) = &field_node.value else {
                    return Err(Error::testlang_error("AST metadata is broken"));
                };
                let endian = field.endianness.unwrap_or(testlang.default_endian);
                original_bytes.extend_from_slice(
                    float_to_bytes(*value, field_node.byte_size, endian)?.as_slice(),
                );
                true
            }
            FieldKind::Array | FieldKind::Record => {
                return Err(Error::testlang_error("AST metadata is broken"));
            }
        };

        let original_size = original_bytes.len();
        let mut bytes_to_mutate = BytesInput::new(original_bytes.into());
        if self.mutator.mutate(state, &mut bytes_to_mutate)? == MutationResult::Skipped {
            return Ok(MutationResult::Skipped);
        };

        let mut bytes_to_mutate = BytesMut::from(bytes_to_mutate.bytes());

        if fixed_size {
            bytes_to_mutate.resize(original_size, 0);
        }

        if FieldKind::String == field.kind {
            let allowed_chars = match field.string_format {
                Some(StringFormat::BinInt) => b"01".to_vec(),
                Some(StringFormat::OctInt) => b"01234567".to_vec(),
                Some(StringFormat::DecInt) => b"0123456789".to_vec(),
                Some(StringFormat::HexInt) => b"0123456789abcedf".to_vec(),
                None => (1..=127).collect(),
            };

            let allowed_charset: HashSet<u8> = HashSet::from_iter(allowed_chars.iter().cloned());
            for byte in bytes_to_mutate.iter_mut() {
                if !allowed_charset.contains(byte) {
                    let new_byte_idx = state.rand.below(allowed_charset.len());
                    *byte = allowed_chars[new_byte_idx];
                }
            }
        }

        if let Some(terminator) = &field.terminator {
            let terminator_bytes = match terminator {
                Terminator::ByteSequence(seq) => seq.as_slice(),
                Terminator::CharSequence(seq) => seq.as_bytes(),
            };
            if terminator_bytes.len() > bytes_to_mutate.len() {
                return Ok(MutationResult::Skipped);
            }
            if !terminator_bytes.is_empty() {
                let mut replace_to = terminator_bytes.to_vec();
                // TODO: Potential bug here -> this doesn't guarantee clearance
                replace_to[0] = if replace_to[0] == u8::MAX {
                    1
                } else {
                    replace_to[0] + 1
                };
                let mut cursor = 0;
                let mut replaced = BytesMut::with_capacity(bytes_to_mutate.len());
                for pos in find_iter(&bytes_to_mutate, terminator_bytes) {
                    replaced.extend_from_slice(&bytes_to_mutate[cursor..pos]);
                    replaced.extend_from_slice(&replace_to);
                    cursor = pos + terminator_bytes.len();
                }
                replaced.extend_from_slice(&bytes_to_mutate[cursor..]);
                replaced.truncate(replaced.len() - terminator_bytes.len());
                replaced.extend_from_slice(terminator_bytes);
                bytes_to_mutate = replaced;
            }
        }

        match field.kind {
            FieldKind::Int => {
                let TestLangNodeValue::Int(value) = &mut field_node.value else {
                    return Err(Error::testlang_error("AST metadata is broken"));
                };
                let endian = field.endianness.unwrap_or(testlang.default_endian);
                let new_value = bytes_to_int(&bytes_to_mutate, endian)?;
                *value = new_value;
            }
            FieldKind::Bytes | FieldKind::Custom(_) => {
                let TestLangNodeValue::Bytes(value) = &mut field_node.value else {
                    return Err(Error::testlang_error("AST metadata is broken"));
                };
                *value = bytes_to_mutate.to_vec();
            }
            FieldKind::String => {
                let TestLangNodeValue::String(value) = &mut field_node.value else {
                    return Err(Error::testlang_error("AST metadata is broken"));
                };
                let converted = String::from_utf8_lossy(&bytes_to_mutate).into_owned();
                if converted.len() != bytes_to_mutate.len() {
                    return Err(Error::testlang_error(
                        "Mutated bytes containing invalid char",
                    ));
                }
                *value = converted;
            }
            FieldKind::Float => {
                let TestLangNodeValue::Float(value) = &mut field_node.value else {
                    return Err(Error::testlang_error("AST metadata is broken"));
                };
                let endian = field.endianness.unwrap_or(testlang.default_endian);
                let new_value = bytes_to_float(&bytes_to_mutate, endian)?;
                *value = new_value;
            }
            _ => unreachable!(),
        };

        let max_extension = state.max_size().saturating_sub(input_size);
        let new_size = bytes_to_mutate.len();
        if original_size != new_size {
            let size_diff = new_size as i128 - original_size as i128;
            if (max_extension as i128) < size_diff {
                return Ok(MutationResult::Skipped);
            }
            if let Some(associated_size_id) = input_metadata.size_ref.get(&field_id).cloned() {
                let Some(associated_size_node) = output_node.find_by_id_mut(associated_size_id)
                else {
                    return Err(Error::testlang_error("AST metadata is broken"));
                };
                match &mut associated_size_node.value {
                    TestLangNodeValue::Int(size_field_value) => {
                        *size_field_value =
                            size_field_value.saturating_add(size_diff as TestLangInt);
                    }
                    TestLangNodeValue::String(fmt_string) => {
                        let Some(field) = testlang.find_field_by_id(associated_size_node.type_id)
                        else {
                            return Err(Error::testlang_error("AST metadata is broken"));
                        };
                        update_fmt_string_size_dep(
                            fmt_string,
                            field,
                            size_diff as TestLangInt,
                            original_size as TestLangInt,
                        )?;
                    }
                    _ => {
                        return Err(Error::testlang_error(
                            "Other than integer-like type is rejected for size reference for now",
                        ));
                    }
                }
            }
        }

        let mut output = node_to_bytes(testlang, &state.codegen_path, &output_node)?;
        if output.len() > state.max_size() {
            return Ok(MutationResult::Skipped);
        }
        bytes_output.append(&mut output);
        let new_ast = TestLangAst::new(output_node, testlang)?;
        *metadata_output = Some(new_ast);
        Ok(MutationResult::Mutated)
    }
}

impl<BM> FieldMutator<BM>
where
    BM: Named,
{
    #[must_use]
    pub fn new(mutator: BM) -> Self {
        #[cfg(feature = "log")]
        let name = format!("FieldMutator({})", mutator.name());
        Self {
            #[cfg(feature = "log")]
            name,
            mutator,
        }
    }
}

#[must_use]
pub fn new_bit_flip() -> FieldMutator<BitFlipMutator> {
    FieldMutator::new(BitFlipMutator::new())
}

#[must_use]
pub fn new_byte_add() -> FieldMutator<ByteAddMutator> {
    FieldMutator::new(ByteAddMutator::new())
}

#[must_use]
pub fn new_byte_dec() -> FieldMutator<ByteDecMutator> {
    FieldMutator::new(ByteDecMutator::new())
}

#[must_use]
pub fn new_byte_flip() -> FieldMutator<ByteFlipMutator> {
    FieldMutator::new(ByteFlipMutator::new())
}

#[must_use]
pub fn new_byte_inc() -> FieldMutator<ByteIncMutator> {
    FieldMutator::new(ByteIncMutator::new())
}

#[must_use]
pub fn new_byte_interesting() -> FieldMutator<ByteInterestingMutator> {
    FieldMutator::new(ByteInterestingMutator::new())
}

#[must_use]
pub fn new_byte_neg() -> FieldMutator<ByteNegMutator> {
    FieldMutator::new(ByteNegMutator::new())
}

#[must_use]
pub fn new_byte_rand() -> FieldMutator<ByteRandMutator> {
    FieldMutator::new(ByteRandMutator::new())
}

#[must_use]
pub fn new_bytes_copy() -> FieldMutator<BytesCopyMutator> {
    FieldMutator::new(BytesCopyMutator::new())
}

#[must_use]
pub fn new_bytes_delete() -> FieldMutator<BytesDeleteMutator> {
    FieldMutator::new(BytesDeleteMutator::new())
}

#[must_use]
pub fn new_bytes_expand() -> FieldMutator<BytesExpandMutator> {
    FieldMutator::new(BytesExpandMutator::new())
}

#[must_use]
pub fn new_bytes_insert_copy() -> FieldMutator<BytesInsertCopyMutator> {
    FieldMutator::new(BytesInsertCopyMutator::new())
}

#[must_use]
pub fn new_bytes_insert() -> FieldMutator<BytesInsertMutator> {
    FieldMutator::new(BytesInsertMutator::new())
}

#[must_use]
pub fn new_bytes_rand_insert() -> FieldMutator<BytesRandInsertMutator> {
    FieldMutator::new(BytesRandInsertMutator::new())
}

#[must_use]
pub fn new_bytes_rand_set() -> FieldMutator<BytesRandSetMutator> {
    FieldMutator::new(BytesRandSetMutator::new())
}

#[must_use]
pub fn new_bytes_set() -> FieldMutator<BytesSetMutator> {
    FieldMutator::new(BytesSetMutator::new())
}

#[must_use]
pub fn new_bytes_swap() -> FieldMutator<BytesSwapMutator> {
    FieldMutator::new(BytesSwapMutator::new())
}

#[must_use]
pub fn new_dword_add() -> FieldMutator<DwordAddMutator> {
    FieldMutator::new(DwordAddMutator::new())
}

#[must_use]
pub fn new_dword_interesting() -> FieldMutator<DwordInterestingMutator> {
    FieldMutator::new(DwordInterestingMutator::new())
}

#[must_use]
pub fn new_qword_add() -> FieldMutator<QwordAddMutator> {
    FieldMutator::new(QwordAddMutator::new())
}

#[must_use]
pub fn new_word_add() -> FieldMutator<WordAddMutator> {
    FieldMutator::new(WordAddMutator::new())
}

#[must_use]
pub fn new_word_interesting() -> FieldMutator<WordInterestingMutator> {
    FieldMutator::new(WordInterestingMutator::new())
}

#[must_use]
pub fn new_chooser() -> FieldChooser {
    FieldChooser
}

pub struct FieldHavocMutator<BM> {
    #[cfg(feature = "log")]
    name: String,
    mutator: BM,
}

impl<BM> TestLangInputMutator for FieldHavocMutator<BM>
where
    BM: Mutator<BytesInput, TestLangState>,
{
    #[cfg(feature = "log")]
    fn name(&self) -> &str {
        &self.name
    }

    fn mutate(
        &mut self,
        state: &mut TestLangState,
        input: &TestLangAst,
        input_size: usize,
        bytes_output: &mut Vec<u8>,
        metadata_output: &mut Option<TestLangAst>,
    ) -> Result<MutationResult, Error> {
        let input_metadata = &input.metadata;
        let Some(field_id) = state
            .rand
            .choose(input_metadata.normal_fields.iter())
            .copied()
        else {
            return Ok(MutationResult::Skipped);
        };

        let mut output_node = input.root.clone();
        let Some(field_node) = output_node.find_by_id_mut(field_id) else {
            return Err(Error::testlang_error("AST metadata is broken"));
        };

        let testlang_arc = state.testlang.clone();
        let testlang = testlang_arc.as_ref();

        let Some(field) = testlang.find_field_by_id(field_node.type_id) else {
            return Err(Error::testlang_error("AST metadata is broken"));
        };

        let bytes_value = match &field_node.value {
            TestLangNodeValue::Int(value) => {
                let endian = field.endianness.unwrap_or(testlang.default_endian);
                &int_to_bytes(*value, field_node.byte_size, endian)
            }
            TestLangNodeValue::Float(value) => {
                let endian = field.endianness.unwrap_or(testlang.default_endian);
                &float_to_bytes(*value, field_node.byte_size, endian)?
            }
            TestLangNodeValue::Bytes(value) => value.as_slice(),
            TestLangNodeValue::String(value) => value.as_bytes(),
            TestLangNodeValue::Group(_)
            | TestLangNodeValue::Record(_)
            | TestLangNodeValue::Union(_, _)
            | TestLangNodeValue::Ref(_) => {
                return Err(Error::testlang_error("AST metadata is broken"));
            }
        };
        let mut bytes_to_mutate = BytesInput::from(bytes_value);
        let max_mutated_len = state
            .max_size()
            .saturating_sub(input_size)
            .saturating_add(bytes_value.len());
        if self.mutator.mutate(state, &mut bytes_to_mutate)? == MutationResult::Skipped {
            return Ok(MutationResult::Skipped);
        };

        let mut bytes_to_mutate = BytesMut::from(bytes_to_mutate.bytes());
        match &mut field_node.value {
            TestLangNodeValue::Int(value) => {
                bytes_to_mutate.resize(field_node.byte_size.unwrap_or(8), 0);
                let endian = field.endianness.unwrap_or(testlang.default_endian);
                let new_value = bytes_to_int(&bytes_to_mutate, endian)?;
                *value = new_value;
            }
            TestLangNodeValue::Float(value) => {
                bytes_to_mutate.resize(field_node.byte_size.unwrap_or(8), 0);
                let endian = field.endianness.unwrap_or(testlang.default_endian);
                let new_value = bytes_to_float(&bytes_to_mutate, endian)?;
                *value = new_value;
            }
            TestLangNodeValue::Bytes(value) => {
                bytes_to_mutate.truncate(max_mutated_len);
                *value = bytes_to_mutate.to_vec()
            }
            TestLangNodeValue::String(value) => {
                bytes_to_mutate.truncate(max_mutated_len);
                *value = String::from_utf8_lossy(&bytes_to_mutate).into_owned()
            }
            TestLangNodeValue::Group(_)
            | TestLangNodeValue::Record(_)
            | TestLangNodeValue::Union(_, _)
            | TestLangNodeValue::Ref(_) => unreachable!(),
        };

        let mut output = node_to_bytes(testlang, &state.codegen_path, &output_node)?;
        if output.len() > state.max_size() {
            return Ok(MutationResult::Skipped);
        }
        bytes_output.append(&mut output);
        let new_ast = TestLangAst::new(output_node, testlang)?;
        *metadata_output = Some(new_ast);
        Ok(MutationResult::Mutated)
    }
}

impl<BM> FieldHavocMutator<BM>
where
    BM: Named,
{
    #[must_use]
    pub fn new(mutator: BM) -> Self {
        #[cfg(feature = "log")]
        let name = format!("FieldHavocMutator({})", mutator.name());
        Self {
            #[cfg(feature = "log")]
            name,
            mutator,
        }
    }
}

#[must_use]
pub fn new_havoc_bit_flip() -> FieldHavocMutator<BitFlipMutator> {
    FieldHavocMutator::new(BitFlipMutator::new())
}

#[must_use]
pub fn new_havoc_byte_add() -> FieldHavocMutator<ByteAddMutator> {
    FieldHavocMutator::new(ByteAddMutator::new())
}

#[must_use]
pub fn new_havoc_byte_dec() -> FieldHavocMutator<ByteDecMutator> {
    FieldHavocMutator::new(ByteDecMutator::new())
}

#[must_use]
pub fn new_havoc_byte_flip() -> FieldHavocMutator<ByteFlipMutator> {
    FieldHavocMutator::new(ByteFlipMutator::new())
}

#[must_use]
pub fn new_havoc_byte_inc() -> FieldHavocMutator<ByteIncMutator> {
    FieldHavocMutator::new(ByteIncMutator::new())
}

#[must_use]
pub fn new_havoc_byte_interesting() -> FieldHavocMutator<ByteInterestingMutator> {
    FieldHavocMutator::new(ByteInterestingMutator::new())
}

#[must_use]
pub fn new_havoc_byte_neg() -> FieldHavocMutator<ByteNegMutator> {
    FieldHavocMutator::new(ByteNegMutator::new())
}

#[must_use]
pub fn new_havoc_byte_rand() -> FieldHavocMutator<ByteRandMutator> {
    FieldHavocMutator::new(ByteRandMutator::new())
}

#[must_use]
pub fn new_havoc_bytes_copy() -> FieldHavocMutator<BytesCopyMutator> {
    FieldHavocMutator::new(BytesCopyMutator::new())
}

#[must_use]
pub fn new_havoc_bytes_delete() -> FieldHavocMutator<BytesDeleteMutator> {
    FieldHavocMutator::new(BytesDeleteMutator::new())
}

#[must_use]
pub fn new_havoc_bytes_expand() -> FieldHavocMutator<BytesExpandMutator> {
    FieldHavocMutator::new(BytesExpandMutator::new())
}

#[must_use]
pub fn new_havoc_bytes_insert_copy() -> FieldHavocMutator<BytesInsertCopyMutator> {
    FieldHavocMutator::new(BytesInsertCopyMutator::new())
}

#[must_use]
pub fn new_havoc_bytes_insert() -> FieldHavocMutator<BytesInsertMutator> {
    FieldHavocMutator::new(BytesInsertMutator::new())
}

#[must_use]
pub fn new_havoc_bytes_rand_insert() -> FieldHavocMutator<BytesRandInsertMutator> {
    FieldHavocMutator::new(BytesRandInsertMutator::new())
}

#[must_use]
pub fn new_havoc_bytes_rand_set() -> FieldHavocMutator<BytesRandSetMutator> {
    FieldHavocMutator::new(BytesRandSetMutator::new())
}

#[must_use]
pub fn new_havoc_bytes_set() -> FieldHavocMutator<BytesSetMutator> {
    FieldHavocMutator::new(BytesSetMutator::new())
}

#[must_use]
pub fn new_havoc_bytes_swap() -> FieldHavocMutator<BytesSwapMutator> {
    FieldHavocMutator::new(BytesSwapMutator::new())
}

#[must_use]
pub fn new_havoc_dword_add() -> FieldHavocMutator<DwordAddMutator> {
    FieldHavocMutator::new(DwordAddMutator::new())
}

#[must_use]
pub fn new_havoc_dword_interesting() -> FieldHavocMutator<DwordInterestingMutator> {
    FieldHavocMutator::new(DwordInterestingMutator::new())
}

#[must_use]
pub fn new_havoc_qword_add() -> FieldHavocMutator<QwordAddMutator> {
    FieldHavocMutator::new(QwordAddMutator::new())
}

#[must_use]
pub fn new_havoc_word_add() -> FieldHavocMutator<WordAddMutator> {
    FieldHavocMutator::new(WordAddMutator::new())
}

#[must_use]
pub fn new_havoc_word_interesting() -> FieldHavocMutator<WordInterestingMutator> {
    FieldHavocMutator::new(WordInterestingMutator::new())
}
