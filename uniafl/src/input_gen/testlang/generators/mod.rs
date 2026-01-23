use std::collections::HashMap;
use std::ops::RangeInclusive;

use libafl::{state::HasMaxSize, Error};
use libafl_bolts::rands::Rand;
use memchr::memmem::Finder;
use rand::Rng;
use rangemap::RangeInclusiveSet;
use testlang::{
    Field, FieldKind, FieldValue, NumValue, Record, RecordKind, Ref, RefKind, SizeDescriptor,
    StringFormat, Terminator, TestLang, TestLangAst, TestLangFloat, TestLangInt, TestLangNode,
    TestLangNodeValue, ValOrRef, RECORD_INPUT,
};

use crate::common::Error as UniaflError;

use super::{node_to_bytes, processing, service::worker::TestLangState, TestLangInputGenerator};

pub mod custom;

pub type TestLangGenerators = Vec<Box<dyn TestLangInputGenerator>>;

const ARRAY_BOUND: usize = 10;
const DATA_BOUND: usize = 100;

fn get_int_range_from_string_length_range(
    radix: TestLangInt,
    str_len: &RangeInclusive<usize>,
) -> RangeInclusiveSet<TestLangInt> {
    if str_len.is_empty() {
        return RangeInclusiveSet::new();
    }
    let min_pos = radix
        .checked_pow(str_len.start().saturating_sub(1) as u32)
        .unwrap_or(1);
    let max_pos = radix
        .checked_pow(*str_len.end() as u32)
        .map(|x| x - 1)
        .unwrap_or(TestLangInt::MAX);
    let min_neg = -(radix
        .checked_pow(str_len.end().saturating_sub(1) as u32)
        .map(|x| x - 1)
        .unwrap_or(TestLangInt::MAX));
    let max_neg = -(radix
        .checked_pow(str_len.start().saturating_sub(2) as u32)
        .unwrap_or(1));

    let pos_range = min_pos..=max_pos;
    let neg_range = min_neg..=max_neg;
    let mut ranges = RangeInclusiveSet::new();
    if !pos_range.is_empty() {
        ranges.insert(pos_range);
    }
    if !neg_range.is_empty() {
        ranges.insert(neg_range);
    }
    if ranges.contains(&1) {
        ranges.insert(0..=0);
    }
    ranges
}

pub struct TestLangGenerator;

impl TestLangGenerator {
    pub fn new() -> Self {
        Self
    }

    pub fn coinflip(&self, state: &mut TestLangState, success_prob: f64) -> bool {
        #[cfg(debug_assertions)]
        if cfg!(feature = "testlang-debug") {
            return true;
        }
        state.gen_only_valid || state.rand.coinflip(success_prob)
    }

    // Generate a record AST that follows the grammar given by the TestLang
    pub fn generate_record(
        &self,
        testlang: &TestLang,
        record_name: &str,
        size_range: RangeInclusive<usize>,
        state: &mut TestLangState,
    ) -> Result<TestLangNode, Error> {
        if let Some(record_idx) = testlang.record_index.get(record_name) {
            let record = &testlang.records[*record_idx];
            let Some(record_type_id) = record.type_id else {
                return Err(Error::illegal_state("Record has no Type ID.".to_string()));
            };
            let mut dependencies = HashMap::new();
            let mut nodes: Vec<Option<TestLangNode>> = Vec::new();
            let fields = &record.fields;

            let size_range = self.generate_size_range(
                testlang,
                record,
                record.byte_size.as_ref(),
                size_range,
                &mut dependencies,
                state,
            );

            match record.kind {
                RecordKind::Struct => {
                    let Some(size_deref_map) = testlang.size_deref_map.get(record_name) else {
                        return Err(Error::illegal_argument(
                            "Record has no size deref map".to_string(),
                        ));
                    };
                    let mut left_size = *size_range.end();
                    let min_record_size =
                        testlang.get_min_byte_size_of_record(record, &dependencies);
                    if min_record_size > left_size {
                        return Err(Error::illegal_argument(format!(
                            "Record minimum size ({min_record_size}) is larger than the given size range ({size_range:?})"
                        )));
                    }
                    left_size -= min_record_size;
                    // First try to generate all fields
                    // Size fields will be generated later after data fields are generated
                    for field in fields.iter() {
                        let field_output = if size_deref_map.get(&field.name).is_some() {
                            // Cannot generate size field if dependency is not resolved
                            None
                        } else {
                            let min_field_size =
                                testlang.get_min_byte_size_of_field(record, field, &dependencies);
                            // If it is not a size field, generate field
                            let generated = self.generate_field(
                                testlang,
                                record,
                                field,
                                min_field_size..=min_field_size + left_size,
                                &mut dependencies,
                                state,
                            )?;
                            match left_size.checked_sub(
                                generated
                                    .byte_size()
                                    .map(|x| x.saturating_sub(min_field_size))
                                    .unwrap_or_default(),
                            ) {
                                Some(new_left_size) => left_size = new_left_size,
                                None => {
                                    return Err(libafl::Error::illegal_state(
                                        "Generated bigger field than expected",
                                    ));
                                }
                            }
                            Some(generated)
                        };
                        nodes.push(field_output);
                    }

                    // Try generate_field again to resolve dependencies
                    for (idx, field) in fields.iter().enumerate() {
                        if nodes[idx].is_none() {
                            let min_field_size =
                                testlang.get_min_byte_size_of_field(record, field, &dependencies);
                            let field_output = self.generate_field(
                                testlang,
                                record,
                                field,
                                min_field_size..=min_field_size + left_size,
                                &mut dependencies,
                                state,
                            )?;
                            match left_size.checked_sub(
                                field_output
                                    .byte_size()
                                    .map(|x| x.saturating_sub(min_field_size))
                                    .unwrap_or_default(),
                            ) {
                                Some(new_left_size) => left_size = new_left_size,
                                None => {
                                    return Err(libafl::Error::illegal_state(
                                        "Generated bigger field than expected",
                                    ));
                                }
                            }
                            nodes[idx] = Some(field_output);
                        }
                    }
                    let nodes = nodes
                        .into_iter()
                        .map(|node| {
                            node.ok_or_else(|| {
                                Error::illegal_argument("Failed to generate all fields".to_string())
                            })
                        })
                        .collect::<Result<Vec<_>, _>>()?;
                    Ok(TestLangNode::new(
                        record_type_id,
                        None,
                        TestLangNodeValue::Group(nodes),
                    ))
                }
                RecordKind::Union => {
                    //randomly select one of the union
                    let compatible_records: Vec<(usize, &String)> = fields
                        .iter()
                        .enumerate()
                        .filter_map(|x| {
                            let (idx, field) = x;
                            field.items.as_ref().and_then(|component_ref| {
                                if component_ref.kind == RefKind::Record {
                                    let record_name = &component_ref.name;
                                    let record_idx = testlang.record_index.get(record_name)?;
                                    let record = &testlang.records[*record_idx];
                                    let left_size = *size_range.end();
                                    let min_element_size =
                                        testlang.get_min_byte_size_of_record(record, &dependencies);
                                    if left_size < min_element_size {
                                        return None;
                                    }
                                    Some((idx, record_name))
                                } else {
                                    None
                                }
                            })
                        })
                        .collect();
                    let Some((union_idx, union_name)) = state.rand.choose(compatible_records)
                    else {
                        return Err(Error::illegal_argument(
                            "No union variants supporting current size requirements.".to_string(),
                        ));
                    };
                    let node = self.generate_record(testlang, union_name, size_range, state)?;
                    Ok(TestLangNode::new(
                        record_type_id,
                        None,
                        TestLangNodeValue::Union(union_idx, node.into()),
                    ))
                }
            }
        } else {
            Err(Error::illegal_argument("Record not found".to_string()))
        }
    }

    fn generate_format_string(
        &self,
        size: usize,
        state: &mut TestLangState,
        field: &Field,
    ) -> Result<Vec<u8>, Error> {
        let Some(string_format) = field.string_format else {
            return Err(Error::illegal_argument(format!(
                "Field `{}` has no string format",
                field.name
            )));
        };

        let mut allowed_chars: Vec<u8> = Vec::new();
        allowed_chars.extend_from_slice(b"01");
        if string_format != StringFormat::BinInt {
            allowed_chars.extend_from_slice(b"234567");
            if string_format != StringFormat::OctInt {
                allowed_chars.extend_from_slice(b"89");
                if string_format != StringFormat::DecInt {
                    allowed_chars.extend_from_slice(b"abcdef");
                }
            }
        }

        let mut bytes = Vec::new();
        if size != 0 && state.rand.coinflip(0.5) {
            bytes.push(b'-');
        }
        bytes.extend_from_slice(&self.generate_random_bytes(
            Some(&allowed_chars),
            size - bytes.len(),
            state,
            field,
        ));
        Ok(bytes)
    }

    fn generate_random_bytes(
        &self,
        allowed_chars: Option<&[u8]>,
        size: usize,
        state: &mut TestLangState,
        field: &Field,
    ) -> Vec<u8> {
        let mut allowed_chars: Vec<u8> = if let Some(allowed_chars) = allowed_chars {
            allowed_chars.to_vec()
        } else if field.kind == FieldKind::String {
            (1..=255).collect()
        } else {
            (0..=255).collect()
        };

        if size == 0 {
            Vec::new()
        } else {
            // Valid bytes
            let mut bytes: Vec<u8> = (0..size)
                .map(|_| *state.rand.choose(allowed_chars.iter()).unwrap_or(&0u8))
                .collect();

            // Remove unintended terminator match
            if let Some(Terminator::ByteSequence(seq)) = &field.terminator {
                if let Some(pos) = allowed_chars.iter().position(|x| *x == seq[0]) {
                    allowed_chars.swap_remove(pos);
                }
                let finder = Finder::new(seq);
                while let Some(pos) = finder.find(&bytes) {
                    bytes[pos] = *state.rand.choose(allowed_chars.iter()).unwrap_or(&0u8);
                }
            } else if let Some(Terminator::CharSequence(seq)) = &field.terminator {
                if let Some(pos) = allowed_chars.iter().position(|x| *x == seq.as_bytes()[0]) {
                    allowed_chars.swap_remove(pos);
                }
                let finder = Finder::new(seq);
                while let Some(pos) = finder.find(&bytes) {
                    bytes[pos] = *state.rand.choose(allowed_chars.iter()).unwrap_or(&0u8);
                }
            }
            bytes
        }
    }

    fn update_size_dependency(
        &self,
        size_descriptor: Option<&SizeDescriptor>,
        dependencies: &mut HashMap<String, TestLangInt>,
        size: usize,
    ) {
        if let Some(SizeDescriptor::Single(ValOrRef::Ref(Ref {
            kind: RefKind::Field,
            name,
        }))) = size_descriptor
        {
            dependencies.insert(name.clone(), size as TestLangInt);
        };
    }

    #[allow(clippy::too_many_arguments)]
    fn fit_to_size(
        &self,
        testlang: &TestLang,
        record: &Record,
        size_descriptor: Option<&SizeDescriptor>,
        range: RangeInclusive<usize>,
        dependencies: &mut HashMap<String, TestLangInt>,
        state: &mut TestLangState,
        bytes: &mut Vec<u8>,
    ) {
        let size_range = self.generate_size_range(
            testlang,
            record,
            size_descriptor,
            range,
            dependencies,
            state,
        );
        if !size_range.contains(&bytes.len()) {
            if size_range.start() > &bytes.len() {
                bytes.resize(*size_range.start(), 0);
            } else {
                bytes.resize(*size_range.end(), 0);
            }
        }
        self.update_size_dependency(size_descriptor, dependencies, bytes.len());
    }

    // Generate size and update dependencies
    fn generate_size(
        &self,
        testlang: &TestLang,
        record: &Record,
        size_descriptor: Option<&SizeDescriptor>,
        range: RangeInclusive<usize>,
        dependencies: &mut HashMap<String, TestLangInt>,
        state: &mut TestLangState,
    ) -> usize {
        let size_range = self.generate_size_range(
            testlang,
            record,
            size_descriptor,
            range,
            dependencies,
            state,
        );
        let size = state.rand.gen_range(size_range);
        self.update_size_dependency(size_descriptor, dependencies, size);
        size
    }

    fn generate_size_range(
        &self,
        testlang: &TestLang,
        record: &Record,
        size_descriptor: Option<&SizeDescriptor>,
        range: RangeInclusive<usize>,
        deps: &mut HashMap<String, TestLangInt>,
        state: &mut TestLangState,
    ) -> RangeInclusive<usize> {
        let size_range_set = testlang
            .get_size_range_set(record, size_descriptor, deps)
            .iter()
            .map(|range| {
                let start = if self.coinflip(state, 0.9) {
                    *range.start()
                } else {
                    if state.rand.coinflip(0.5) {
                        range.start().checked_div(2)
                    } else {
                        range.start().checked_sub(0x1000)
                    }
                    .unwrap_or(*range.start())
                };
                let end = if self.coinflip(state, 0.9) {
                    *range.end()
                } else {
                    if state.rand.coinflip(0.5) {
                        range.end().checked_mul(2)
                    } else {
                        range.end().checked_add(0x1000)
                    }
                    .unwrap_or(*range.end())
                };
                start..=end
            })
            .collect();
        let mut range_set = RangeInclusiveSet::new();
        range_set.insert(range.clone());
        state
            .rand
            .choose(range_set.intersection(&size_range_set))
            .unwrap_or(range)
    }

    fn get_size_range_intersect(
        &self,
        range1: &RangeInclusive<usize>,
        range2: &RangeInclusive<usize>,
    ) -> Option<RangeInclusive<usize>> {
        let start = *range1.start().max(range2.start());
        let end = *range1.end().min(range2.end());

        if start <= end {
            Some(start..=end)
        } else {
            None
        }
    }

    fn generate_int(
        &self,
        range: &RangeInclusive<TestLangInt>,
        state: &mut TestLangState,
    ) -> TestLangInt {
        // It is intended to generate a special int in valid range with 10% chance even when `testlang-debug` is enabled
        if state.rand.coinflip(0.9) {
            state.rand.gen_range(range.clone())
        } else {
            let mut range_set = RangeInclusiveSet::new();
            range_set.insert(range.clone());
            // https://github.com/google/syzkaller/blob/9882047a78fc9ecc64da219b2fb28f2708da44af/prog/rand.go#L68-L78
            let special_ints = [
                0,
                1,
                2,
                3,
                4,
                5,
                6,
                7,
                8,
                9,
                10,
                11,
                12,
                13,
                14,
                15,
                16,
                64,
                127,
                128,
                129,
                255,
                256,
                257,
                511,
                512,
                1023,
                1024,
                1025,
                2047,
                2048,
                4095,
                4096,
                (1 << 15) - 1,
                (1 << 15),
                (1 << 15) + 1,
                (1 << 16) - 1,
                (1 << 16),
                (1 << 16) + 1,
                (1 << 31) - 1,
                (1 << 31),
                (1 << 31) + 1,
                (1 << 32) - 1,
                (1 << 32),
                (1 << 32) + 1,
                (1 << 62) - 1,
                (1 << 62),
                (1 << 62) + 1,
                TestLangInt::MAX,
            ];
            let mut special_int_range_set = RangeInclusiveSet::new();
            for int in special_ints.iter() {
                special_int_range_set.insert(*int..=*int);
                special_int_range_set.insert(-1 * *int..=-1 * *int);
            }
            state
                .rand
                .choose(range_set.intersection(&special_int_range_set))
                .map(|range| state.rand.gen_range(range))
                .unwrap_or_else(|| state.rand.gen_range(range.clone()))
        }
    }

    fn generate_int_range(
        &self,
        range: &RangeInclusive<TestLangInt>,
        state: &mut TestLangState,
    ) -> RangeInclusive<TestLangInt> {
        let start = if self.coinflip(state, 0.9) {
            *range.start()
        } else {
            let start = *range.start();
            if state.rand.coinflip(0.5) {
                if start < 0 {
                    start.checked_mul(2)
                } else {
                    start.checked_div(2)
                }
            } else {
                let offset = if state.rand.coinflip(0.5) {
                    0x100
                } else {
                    0x1000
                };
                start.checked_sub(offset)
            }
            .unwrap_or(start)
        };
        let end = if self.coinflip(state, 0.9) {
            *range.end()
        } else {
            let end = *range.end();
            if state.rand.coinflip(0.5) {
                if end < 0 {
                    end.checked_div(2)
                } else {
                    end.checked_mul(2)
                }
            } else {
                let offset = if state.rand.coinflip(0.5) {
                    0x100
                } else {
                    0x1000
                };
                end.checked_add(offset)
            }
            .unwrap_or(end)
        };
        start..=end
    }

    // generate field bytes and update dependencies
    fn generate_field(
        &self,
        testlang: &TestLang,
        record: &Record,
        field: &Field,
        size_range: RangeInclusive<usize>,
        dependencies: &mut HashMap<String, TestLangInt>,
        state: &mut TestLangState,
    ) -> Result<TestLangNode, Error> {
        match field.kind {
            FieldKind::Int => {
                self.generate_int_field(testlang, record, field, size_range, dependencies, state)
            }
            FieldKind::Float => {
                self.generate_float_field(testlang, record, field, size_range, dependencies, state)
            }
            FieldKind::Bytes => {
                self.generate_bytes_field(testlang, record, field, size_range, dependencies, state)
            }
            FieldKind::Custom(_) => {
                self.generate_custom_field(testlang, record, field, size_range, dependencies, state)
            }
            FieldKind::String => {
                self.generate_string_field(testlang, record, field, size_range, dependencies, state)
            }
            FieldKind::Array => {
                self.generate_array_field(testlang, record, field, size_range, dependencies, state)
            }
            FieldKind::Record => self.generate_record_field(testlang, field, size_range, state),
        }
    }

    fn generate_int_field(
        &self,
        testlang: &TestLang,
        record: &Record,
        field: &Field,
        size_range: RangeInclusive<usize>,
        dependencies: &mut HashMap<String, TestLangInt>,
        state: &mut TestLangState,
    ) -> Result<TestLangNode, Error> {
        if field.kind != FieldKind::Int {
            return Err(Error::illegal_argument(
                "Trying to generate non-`int` field in generate_int_field()".to_string(),
            ));
        }
        let Some(type_id) = field.type_id else {
            return Err(Error::illegal_state("Field has no Type ID.".to_string()));
        };
        let (byte_size, type_size) = if let Some(fdp_call) = &field.fuzzed_data_provider_call {
            (None, fdp_call.type_size.map(|x| x as usize))
        } else {
            let size_descriptor = field.get_byte_size();
            let int_size_range = 1..=8;
            let size_range = self.get_size_range_intersect(&size_range, &int_size_range);
            let Some(size_range) = size_range else {
                return Err(Error::illegal_argument(format!(
                    "Field has invalid size range: {size_range:?} vs {int_size_range:?}"
                )));
            };
            let byte_size = self.generate_size(
                testlang,
                record,
                size_descriptor,
                size_range,
                dependencies,
                state,
            );
            (Some(byte_size), Some(byte_size))
        };
        let value = match dependencies.get(&field.name) {
            Some(value) => *value,
            None => {
                let range = field
                    .possible_values
                    .as_ref()
                    .and_then(|possible_values| {
                        state
                            .rand
                            .choose(possible_values)
                            .and_then(|val| match val {
                                FieldValue::Int(num) if self.coinflip(state, 0.9) => {
                                    state.rand.choose(testlang.get_int_num_value_ranges(
                                        record,
                                        num,
                                        |val_or_ref| val_or_ref.clone(),
                                        dependencies,
                                    ))
                                }
                                _ => None,
                            })
                    })
                    .unwrap_or(
                        type_size
                            .map(|type_size| {
                                let width = 1i128 << (type_size * 8 - 1);
                                (-width as TestLangInt)..=((width - 1) as TestLangInt)
                            })
                            .unwrap_or(TestLangInt::MIN..=TestLangInt::MAX),
                    );
                let range = if testlang
                    .size_deref_map
                    .get(&record.name)
                    .and_then(|size_deref_map| size_deref_map.get(&field.name))
                    .is_some()
                {
                    range
                } else {
                    self.generate_int_range(&range, state)
                };
                self.generate_int(&range, state)
            }
        };
        Ok(TestLangNode::new(
            type_id,
            byte_size,
            TestLangNodeValue::Int(value),
        ))
    }

    fn generate_float_field(
        &self,
        testlang: &TestLang,
        record: &Record,
        field: &Field,
        size_range: RangeInclusive<usize>,
        dependencies: &mut HashMap<String, TestLangInt>,
        state: &mut TestLangState,
    ) -> Result<TestLangNode, Error> {
        if field.kind != FieldKind::Float {
            return Err(Error::illegal_argument(
                "Trying to generate non-`float` field in generate_float_field()".to_string(),
            ));
        }
        let Some(type_id) = field.type_id else {
            return Err(Error::illegal_state("Field has no Type ID.".to_string()));
        };
        let (byte_size, type_size) = if let Some(fdp_call) = &field.fuzzed_data_provider_call {
            (None, fdp_call.type_size.map(|x| x as usize))
        } else {
            let size_descriptor = field.get_byte_size();
            let float_size_range = 4..=8;
            let size_range = self.get_size_range_intersect(&size_range, &float_size_range);
            let Some(size_range) = size_range else {
                return Err(Error::illegal_argument(format!(
                    "Field has invalid size range: {size_range:?} vs {float_size_range:?}"
                )));
            };
            let byte_size = self.generate_size(
                testlang,
                record,
                size_descriptor,
                size_range,
                dependencies,
                state,
            );
            (Some(byte_size), Some(byte_size))
        };
        let value = {
            let range = field
                .possible_values
                .as_ref()
                .and_then(|possible_values| {
                    state
                        .rand
                        .choose(possible_values)
                        .and_then(|val| match val {
                            FieldValue::Float(num) if self.coinflip(state, 0.9) => match num {
                                NumValue::Single(ValOrRef::Val(value)) => Some(*value..=*value),
                                NumValue::Range(testlang::RangeInclusive {
                                    start: ValOrRef::Val(start),
                                    end: ValOrRef::Val(end),
                                }) => {
                                    let start = if self.coinflip(state, 0.9) {
                                        *start
                                    } else {
                                        let new_start = if state.rand.coinflip(0.5) {
                                            *start / 2.0
                                        } else {
                                            *start - 1.0
                                        };
                                        if new_start.is_finite() {
                                            new_start
                                        } else {
                                            *start
                                        }
                                    };
                                    let end = if self.coinflip(state, 0.9) {
                                        *end
                                    } else {
                                        let new_end = if state.rand.coinflip(0.5) {
                                            *end * 2.0
                                        } else {
                                            *end + 1.0
                                        };
                                        if new_end.is_finite() {
                                            new_end
                                        } else {
                                            *end
                                        }
                                    };
                                    Some(start..=end)
                                }
                                _ => None,
                            },
                            _ => None,
                        })
                })
                .unwrap_or(match type_size {
                    Some(4) => f32::MIN as f64..=f32::MAX as f64,
                    Some(8) => f64::MIN..=f64::MAX,
                    _ => TestLangFloat::MIN..=TestLangFloat::MAX,
                });
            state.rand.gen_range(range)
        };
        Ok(TestLangNode::new(
            type_id,
            byte_size,
            TestLangNodeValue::Float(value),
        ))
    }

    fn generate_bytes_field(
        &self,
        testlang: &TestLang,
        record: &Record,
        field: &Field,
        size_range: RangeInclusive<usize>,
        dependencies: &mut HashMap<String, TestLangInt>,
        state: &mut TestLangState,
    ) -> Result<TestLangNode, Error> {
        if field.kind != FieldKind::Bytes {
            return Err(Error::illegal_argument(
                "Trying to generate non-`bytes` field in generate_bytes_field()".to_string(),
            ));
        }
        let Some(type_id) = field.type_id else {
            return Err(Error::illegal_state("Field has no Type ID.".to_string()));
        };
        let terminator_size = match &field.terminator {
            Some(Terminator::ByteSequence(seq)) => seq.len(),
            _ => 0,
        };
        let size_range_without_terminator = size_range.start().saturating_sub(terminator_size)
            ..=size_range.end().saturating_sub(terminator_size);

        if let Some(generator) = &field.generator {
            let mut bytes = processing::run_encoding_processor(&state.codegen_path, generator, &[])
                .map_err(|e| {
                    Error::invalid_corpus(format!("Failed to run processor `{generator}`: {e}"))
                })?;
            if !size_range_without_terminator.contains(&bytes.len()) {
                return Err(Error::illegal_state(
                    "Python-based processor returned size-out-of-bounds output.".to_string(),
                ));
            }
            if let Some(Terminator::ByteSequence(seq)) = &field.terminator {
                bytes.extend_from_slice(seq);
            }
            return Ok(TestLangNode::new(
                type_id,
                None,
                TestLangNodeValue::Bytes(bytes),
            ));
        }
        let size_descriptor = field.len.as_ref().or(field.byte_size.as_ref());
        let bytes_size_range = 0..=DATA_BOUND.saturating_sub(terminator_size);
        let size = self.generate_size(
            testlang,
            record,
            size_descriptor,
            self.get_size_range_intersect(&size_range_without_terminator, &bytes_size_range)
                .unwrap_or(size_range_without_terminator.clone()),
            dependencies,
            state,
        );

        let mut bytes = field
            .possible_values
            .as_ref()
            .map(|possible_values| {
                possible_values.iter().filter_map(|x| match x {
                    FieldValue::Bytes(ValOrRef::Val(bytes))
                        if size_range_without_terminator.contains(&bytes.len()) =>
                    {
                        Some(bytes)
                    }
                    _ => None,
                })
            })
            .and_then(|possible_values| {
                if self.coinflip(state, 0.9) {
                    state.rand.choose(possible_values)
                } else {
                    None
                }
            })
            .cloned()
            .unwrap_or_else(|| self.generate_random_bytes(None, size, state, field));
        if let Some(Terminator::ByteSequence(seq)) = &field.terminator {
            bytes.extend_from_slice(seq);
        }
        self.update_size_dependency(size_descriptor, dependencies, bytes.len());
        Ok(TestLangNode::new(
            type_id,
            None,
            TestLangNodeValue::Bytes(bytes),
        ))
    }

    fn generate_custom_field(
        &self,
        testlang: &TestLang,
        record: &Record,
        field: &Field,
        size_range: RangeInclusive<usize>,
        dependencies: &mut HashMap<String, TestLangInt>,
        state: &mut TestLangState,
    ) -> Result<TestLangNode, Error> {
        let FieldKind::Custom(generator_id) = &field.kind else {
            return Err(Error::illegal_argument(
                "Trying to generate non-`custom` field in generate_custom_field()".to_string(),
            ));
        };
        let Some(type_id) = field.type_id else {
            return Err(Error::illegal_state("Field has no Type ID.".to_string()));
        };
        let terminator_size = if let Some(Terminator::ByteSequence(seq)) = &field.terminator {
            seq.len()
        } else {
            0
        };
        let size_descriptor = field.len.as_ref().or(field.byte_size.as_ref());
        let size_range_without_terminator = size_range.start().saturating_sub(terminator_size)
            ..=size_range.end().saturating_sub(terminator_size);
        let mut bytes = match &mut state.customgen_runtime {
            Some(customgen_runtime) => {
                custom::generate_one(customgen_runtime, generator_id.as_ref())
                    .map_err(|e| Error::illegal_state(e.to_string()))?
            }
            None => {
                let size = if size_range_without_terminator.is_empty() {
                    0
                } else {
                    state.rand.gen_range(size_range_without_terminator.clone())
                };
                self.generate_random_bytes(None, size, state, field)
            }
        };
        self.fit_to_size(
            testlang,
            record,
            size_descriptor,
            size_range_without_terminator,
            dependencies,
            state,
            &mut bytes,
        );
        if let Some(Terminator::ByteSequence(seq)) = &field.terminator {
            bytes.extend_from_slice(seq);
        }
        self.update_size_dependency(size_descriptor, dependencies, bytes.len());
        Ok(TestLangNode::new(
            type_id,
            None,
            TestLangNodeValue::Bytes(bytes),
        ))
    }

    fn generate_string_field(
        &self,
        testlang: &TestLang,
        record: &Record,
        field: &Field,
        size_range: RangeInclusive<usize>,
        dependencies: &mut HashMap<String, TestLangInt>,
        state: &mut TestLangState,
    ) -> Result<TestLangNode, Error> {
        if field.kind != FieldKind::String {
            return Err(Error::illegal_argument(
                "Trying to generate non-`string` field in generate_string_field()".to_string(),
            ));
        }
        let Some(type_id) = field.type_id else {
            return Err(Error::illegal_state("Field has no Type ID.".to_string()));
        };
        let terminator_size = match &field.terminator {
            Some(Terminator::CharSequence(seq)) => seq.len(),
            _ => 0,
        };

        let size_range_without_terminator = size_range.start().saturating_sub(terminator_size)
            ..=size_range.end().saturating_sub(terminator_size);
        if let Some(generator) = &field.generator {
            let bytes = processing::run_encoding_processor(&state.codegen_path, generator, &[])
                .map_err(|e| {
                    Error::invalid_corpus(format!("Failed to run processor `{generator}`: {e}"))
                })?;
            let mut string = String::from_utf8_lossy(&bytes).into_owned();
            if !size_range_without_terminator.contains(&string.len()) {
                return Err(Error::illegal_state(
                    "Python-based processor returned size-out-of-bounds output.".to_string(),
                ));
            }
            if let Some(Terminator::CharSequence(seq)) = &field.terminator {
                string.push_str(seq);
            }
            return Ok(TestLangNode::new(
                type_id,
                None,
                TestLangNodeValue::String(string),
            ));
        }
        let size_descriptor = field.len.as_ref().or(field.byte_size.as_ref());
        let max_str_size = match field.string_format {
            None => DATA_BOUND.saturating_sub(terminator_size),
            Some(str_fmt) => match str_fmt {
                StringFormat::BinInt => format!("{:b}", TestLangInt::MAX).len(),
                StringFormat::OctInt => format!("{:o}", TestLangInt::MAX).len(),
                StringFormat::DecInt => format!("{}", TestLangInt::MAX).len(),
                StringFormat::HexInt => format!("{:x}", TestLangInt::MAX).len(),
            },
        };
        let str_size_range = 0..=max_str_size;
        let size = self.generate_size(
            testlang,
            record,
            size_descriptor,
            self.get_size_range_intersect(&size_range_without_terminator, &str_size_range)
                .unwrap_or(size_range_without_terminator.clone()),
            dependencies,
            state,
        );
        let mut string = if size == 0 {
            String::new()
        } else {
            field
                .possible_values
                .as_ref()
                .and_then(|possible_values| match field.string_format {
                    None if self.coinflip(state, 0.9) => state
                        .rand
                        .choose(possible_values)
                        .and_then(|val| match val {
                            FieldValue::String(ValOrRef::Val(string))
                                if size_range_without_terminator.contains(&string.len()) =>
                            {
                                Some(string.clone())
                            }
                            _ => None,
                        }),
                    Some(str_fmt) if self.coinflip(state, 0.9) => {
                        let radix = match str_fmt {
                            StringFormat::BinInt => 2,
                            StringFormat::OctInt => 8,
                            StringFormat::DecInt => 10,
                            StringFormat::HexInt => 16,
                        };
                        state
                            .rand
                            .choose(possible_values)
                            .and_then(|val| match val {
                                FieldValue::Int(num) => Some(num.clone()),
                                FieldValue::String(ValOrRef::Val(string)) => {
                                    TestLangInt::from_str_radix(string, radix)
                                        .ok()
                                        .map(|num| NumValue::Single(ValOrRef::Val(num)))
                                }
                                _ => None,
                            })
                            .and_then(|num| {
                                let ranges = testlang.get_int_num_value_ranges(
                                    record,
                                    &num,
                                    |val_or_ref| val_or_ref.clone(),
                                    dependencies,
                                );
                                let allowed_int_range = get_int_range_from_string_length_range(
                                    radix as TestLangInt,
                                    &size_range_without_terminator,
                                );
                                state
                                    .rand
                                    .choose(ranges.intersection(&allowed_int_range))
                                    .map(|range| {
                                        let range = if testlang
                                            .size_deref_map
                                            .get(&record.name)
                                            .and_then(|size_deref_map| {
                                                size_deref_map.get(&field.name)
                                            })
                                            .is_some()
                                        {
                                            range
                                        } else {
                                            self.generate_int_range(&range, state)
                                        };
                                        let num = self.generate_int(&range, state);
                                        // We don't know the type size of actual integer,
                                        // where processed format string will be stored.
                                        // So we can't use two's complement form of format string.
                                        let (sign, num) =
                                            (if num < 0 { "-" } else { "" }, num.abs());
                                        match str_fmt {
                                            StringFormat::BinInt => format!("{sign}{num:b}"),
                                            StringFormat::OctInt => format!("{sign}{num:o}"),
                                            StringFormat::DecInt => format!("{sign}{num}"),
                                            StringFormat::HexInt => format!("{sign}{num:x}"),
                                        }
                                    })
                            })
                    }
                    _ => None,
                })
                .unwrap_or_else(|| {
                    // FIXME: DO NOT generate from bytes
                    let bytes = field
                        .string_format
                        .as_ref()
                        .and_then(|_| self.generate_format_string(size, state, field).ok())
                        .unwrap_or_else(|| {
                            // FIXME: Support all UTF-8 charset
                            self.generate_random_bytes(
                                Some(&(0u8..=0x7fu8).collect::<Vec<_>>()),
                                size,
                                state,
                                field,
                            )
                        });
                    String::from_utf8_lossy(&bytes).into_owned()
                })
        };
        if let Some(Terminator::CharSequence(seq)) = &field.terminator {
            string.push_str(seq);
        }
        // TODO: Consider encoding for FieldKind::String
        self.update_size_dependency(size_descriptor, dependencies, string.len());
        Ok(TestLangNode::new(
            type_id,
            None,
            TestLangNodeValue::String(string),
        ))
    }

    fn generate_array_field(
        &self,
        testlang: &TestLang,
        record: &Record,
        field: &Field,
        size_range: RangeInclusive<usize>,
        dependencies: &mut HashMap<String, TestLangInt>,
        state: &mut TestLangState,
    ) -> Result<TestLangNode, Error> {
        if field.kind != FieldKind::Array {
            return Err(Error::illegal_argument(
                "Trying to generate non-`array` field in generate_array_field()".to_string(),
            ));
        }
        let Some(type_id) = field.type_id else {
            return Err(Error::illegal_state("Field has no Type ID.".to_string()));
        };
        // Handle array field generation
        let Some(items) = &field.items else {
            return Err(libafl::Error::illegal_state("Invalid testlang"));
        };
        let size_descriptor = field.len.as_ref();
        let len = self.generate_size(
            testlang,
            record,
            size_descriptor,
            0..=ARRAY_BOUND,
            dependencies,
            state,
        );
        let mut nodes = Vec::new();
        let Some(record_idx) = testlang.record_index.get(&items.name) else {
            return Err(Error::illegal_argument("Record not found".to_string()));
        };
        let array_item_record = &testlang.records[*record_idx];
        let min_element_size =
            testlang.get_min_byte_size_of_record(array_item_record, dependencies);
        let min_group_size = min_element_size.saturating_mul(len);
        let mut left_size = match size_range.end().checked_sub(min_group_size) {
            Some(left_size) => left_size,
            None => {
                return Err(libafl::Error::illegal_argument(
                    "Size not enough to generate array elements",
                ));
            }
        };
        for _ in 0..len {
            let size_range = min_element_size..=min_element_size + left_size;
            let node = self.generate_record(testlang, &items.name, size_range, state)?;
            match left_size.checked_sub(
                node.byte_size()
                    .map(|x| x.saturating_sub(min_element_size))
                    .unwrap_or_default(),
            ) {
                Some(new_left_size) => left_size = new_left_size,
                None => {
                    return Err(libafl::Error::illegal_state(
                        "Generated bigger element than expected",
                    ));
                }
            }
            nodes.push(node);
        }
        Ok(TestLangNode::new(
            type_id,
            None,
            TestLangNodeValue::Group(nodes),
        ))
    }

    fn generate_record_field(
        &self,
        testlang: &TestLang,
        field: &Field,
        size_range: RangeInclusive<usize>,
        state: &mut TestLangState,
    ) -> Result<TestLangNode, Error> {
        if field.kind != FieldKind::Record {
            return Err(Error::illegal_argument(
                "Trying to generate non-`record` field in generate_record_field()".to_string(),
            ));
        }
        let Some(type_id) = field.type_id else {
            return Err(Error::illegal_state("Field has no Type ID.".to_string()));
        };
        let Some(Ref {
            kind: RefKind::Record,
            name: ref ref_name,
        }) = field.items.as_ref()
        else {
            return Err(libafl::Error::illegal_state("Invalid testlang"));
        };
        let inner_record = self.generate_record(testlang, ref_name, size_range, state)?;
        let inner_value = TestLangNodeValue::Record(inner_record.into());
        Ok(TestLangNode::new(type_id, None, inner_value))
    }
}

impl TestLangInputGenerator for TestLangGenerator {
    #[cfg(feature = "log")]
    fn name(&self) -> &str {
        "TestLangGenerator"
    }

    fn generate(
        &mut self,
        state: &mut TestLangState,
        bytes_output: &mut Vec<u8>,
        metadata_output: &mut Option<TestLangAst>,
    ) -> Result<(), UniaflError> {
        let testlang_arc = state.testlang.clone();
        let testlang = testlang_arc.as_ref();
        state.gen_only_valid = state.rand.coinflip(0.5);
        let ast_root_node =
            self.generate_record(testlang, RECORD_INPUT, 0..=state.max_size(), state)?;
        let mut bytes = node_to_bytes(testlang, &state.codegen_path, &ast_root_node)?;
        if bytes.len() > state.max_size() {
            return Err(UniaflError::testlang_error(format!(
                "Generated input is too long: {}",
                bytes.len()
            )));
        }
        bytes_output.append(&mut bytes);
        if let Ok(ast) = TestLangAst::new(ast_root_node, testlang) {
            *metadata_output = Some(ast);
        }
        Ok(())
    }
}

pub fn testlang_generators() -> TestLangGenerators {
    vec![Box::new(TestLangGenerator::new())]
}
