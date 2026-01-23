use std::fmt::Display;
use std::path::Path;

use byteorder::{BigEndian, ByteOrder, LittleEndian};
use bytes::BytesMut;
use libafl::mutators::MutationResult;
use libfdp::{FdpEncoderChoice, JazzerFdpEncoder, LlvmFdpEncoder};
use processing::run_encoding_processor;
use serde::{Deserialize, Serialize};
use service::worker::TestLangState;
use testlang::{
    Endianness, FieldKind, FuzzedDataProviderArg, FuzzedDataProviderCall, FuzzedDataProviderKind,
    FuzzedDataProviderMethod, JazzerFuzzedDataProviderMethod, LLVMFuzzedDataProviderMethod,
    ModeKind, Ref, RefKind, TestLang, TestLangAst, TestLangInt, TestLangNode, TestLangNodeValue,
};

use crate::common::Error;

mod generators;
mod mutators;
mod processing;
pub mod service;
#[cfg(test)]
mod tests;

#[derive(Serialize, Deserialize)]
pub struct TestLangInputGenConfig {
    workdir: String,
    core_ids: Vec<usize>,
    harness_src_path: String,
    project_src_dir: Option<String>,
    diff_path: Option<String>,
    reverser_path: String,
    pov_dir: Option<String>,
    max_len: usize,
}

pub trait TestLangInputGenerator {
    #[cfg(feature = "log")]
    fn name(&self) -> &str;
    fn generate(
        &mut self,
        state: &mut TestLangState,
        bytes_output: &mut Vec<u8>,
        metadata_output: &mut Option<TestLangAst>,
    ) -> Result<(), Error>;
}

pub trait TestLangInputMutator {
    #[cfg(feature = "log")]
    fn name(&self) -> &str;
    fn mutate(
        &mut self,
        state: &mut TestLangState,
        input: &TestLangAst,
        input_size: usize,
        bytes_output: &mut Vec<u8>,
        metadata_output: &mut Option<TestLangAst>,
    ) -> Result<MutationResult, Error>;
}

pub trait TestLangAstFreeInputMutator {
    #[cfg(feature = "log")]
    fn name(&self) -> &str;
    fn mutate(
        &mut self,
        state: &mut TestLangState,
        input: &[u8],
        bytes_output: &mut Vec<u8>,
    ) -> Result<MutationResult, Error>;
}

pub fn fit_integral_bytes(
    buf: &mut BytesMut,
    target_size: Option<usize>,
    extension: u8,
    is_big_endian: bool,
) {
    let Some(target_size) = target_size else {
        return;
    };

    if is_big_endian {
        buf.reverse();
    }
    buf.resize(target_size, extension);
    if is_big_endian {
        buf.reverse();
    }
}

pub fn float_to_bytes(
    value: f64,
    size_hint: Option<usize>,
    endianness: Endianness,
) -> Result<Vec<u8>, Error> {
    let mut out_buf = BytesMut::new();
    match size_hint {
        Some(4) => {
            out_buf.resize(4, 0);
            match endianness {
                Endianness::Little => {
                    LittleEndian::write_f32(&mut out_buf, value as f32);
                }
                Endianness::Big => {
                    BigEndian::write_f32(&mut out_buf, value as f32);
                }
            }
        }
        Some(8) | None => {
            out_buf.resize(8, 0);
            match endianness {
                Endianness::Little => {
                    LittleEndian::write_f64(&mut out_buf, value);
                }
                Endianness::Big => {
                    BigEndian::write_f64(&mut out_buf, value);
                }
            }
        }
        Some(other) => {
            return Err(Error::testlang_error(format!(
                "{other} is not a regular floating point size"
            )))
        }
    }
    Ok(out_buf.to_vec())
}

pub fn bytes_to_float(buffer: &[u8], endianness: Endianness) -> Result<f64, Error> {
    match buffer.len() {
        4 => match endianness {
            Endianness::Little => Ok(LittleEndian::read_f32(buffer) as f64),
            Endianness::Big => Ok(BigEndian::read_f32(buffer) as f64),
        },
        8 => match endianness {
            Endianness::Little => Ok(LittleEndian::read_f64(buffer)),
            Endianness::Big => Ok(BigEndian::read_f64(buffer)),
        },
        other => Err(Error::testlang_error(format!(
            "{other} is not a regular floating point size"
        ))),
    }
}

pub fn int_to_bytes(
    value: TestLangInt,
    size_hint: Option<usize>,
    endianness: Endianness,
) -> Vec<u8> {
    let mut out_buf = BytesMut::new();
    let extension = if value < 0 { 0xFF } else { 0 };
    out_buf.resize(8, 0);
    match endianness {
        Endianness::Little => {
            LittleEndian::write_i64(&mut out_buf, value);
            fit_integral_bytes(&mut out_buf, size_hint, extension, false);
        }
        Endianness::Big => {
            BigEndian::write_i64(&mut out_buf, value);
            fit_integral_bytes(&mut out_buf, size_hint, extension, true);
        }
    }
    out_buf.to_vec()
}

pub fn bytes_to_int(b: &[u8], endian: Endianness) -> Result<TestLangInt, Error> {
    match endian {
        Endianness::Little => match b.len() {
            1..=8 => Ok(LittleEndian::read_int(b, b.len())),
            9..=16 => Ok(LittleEndian::read_int128(b, b.len()).try_into()?),
            _ => Err(Error::testlang_error(
                "Not supporting big byte sizes for now",
            )),
        },
        Endianness::Big => match b.len() {
            1..=8 => Ok(BigEndian::read_int(b, b.len())),
            9..=16 => Ok(BigEndian::read_int128(b, b.len()).try_into()?),
            _ => Err(Error::testlang_error(
                "Not supporting big byte sizes for now",
            )),
        },
    }
}

pub fn fit_numeric_value_in_range<T>(val: T, min: T, max: T) -> Result<T, Error>
where
    T: Copy + Ord + Display,
{
    if (min..=max).is_empty() {
        return Err(Error::testlang_error(format!(
            "Wrong range value: {}..={}",
            min, max
        )));
    }
    Ok(val.clamp(min, max))
}

pub fn fit_f32_value_in_range(val: f32, min: f32, max: f32) -> Result<f32, Error> {
    if min > max || min.is_nan() || max.is_nan() {
        return Err(Error::testlang_error(format!(
            "Wrong range value: {}..={}",
            min, max
        )));
    }
    Ok(val.clamp(min, max))
}

pub fn fit_f64_value_in_range(val: f64, min: f64, max: f64) -> Result<f64, Error> {
    if min > max || min.is_nan() || max.is_nan() {
        return Err(Error::testlang_error(format!(
            "Wrong range value: {}..={}",
            min, max
        )));
    }
    Ok(val.clamp(min, max))
}

pub fn node_to_bytes<T: AsRef<TestLangNode>>(
    testlang: &TestLang,
    codegen_path: &Path,
    node: T,
) -> Result<Vec<u8>, Error> {
    let mut fdp_enc = match testlang.mode {
        ModeKind::Bytes => None,
        ModeKind::FuzzedDataProvider(fdp_kind) => match fdp_kind {
            FuzzedDataProviderKind::LLVM => Some(FdpEncoderChoice::Llvm(LlvmFdpEncoder::new())),
            FuzzedDataProviderKind::Jazzer => {
                Some(FdpEncoderChoice::Jazzer(JazzerFdpEncoder::new()))
            }
            FuzzedDataProviderKind::Custom => {
                return Err(Error::testlang_error("Custom FDP is not supported."))
            }
        },
    };
    let bytes = record_node_to_bytes(testlang, node, &mut fdp_enc, codegen_path)?;
    match fdp_enc {
        None => Ok(bytes),
        Some(FdpEncoderChoice::Plain(_)) => {
            Err(Error::testlang_error("Invalid FDP encoder choice"))
        }
        Some(FdpEncoderChoice::Llvm(fdp_enc)) => Ok(fdp_enc.finalize()?),
        Some(FdpEncoderChoice::Jazzer(fdp_enc)) => Ok(fdp_enc.finalize()?),
    }
}

fn record_node_to_bytes<T: AsRef<TestLangNode>>(
    testlang: &TestLang,
    node: T,
    fdp_enc: &mut Option<FdpEncoderChoice>,
    codegen_path: &Path,
) -> Result<Vec<u8>, Error> {
    let type_id = node.as_ref().type_id;
    let Some(_record) = testlang.find_record_by_id(type_id) else {
        return Err(Error::testlang_error(format!(
            "Record type {} is not found.",
            type_id
        )));
    };
    let bytes = match &node.as_ref().value {
        // For `RecordKind::Struct`
        TestLangNodeValue::Group(field_nodes) => {
            let mut buf = BytesMut::new();
            for field_node in field_nodes {
                let bytes = field_node_to_bytes(
                    testlang,
                    node.as_ref(),
                    field_node,
                    fdp_enc,
                    codegen_path,
                )?;
                buf.extend_from_slice(&bytes);
            }
            buf.to_vec()
        }
        TestLangNodeValue::Union(_, node) => {
            record_node_to_bytes(testlang, node, fdp_enc, codegen_path)?
        }
        _ => return Err(Error::testlang_error("Invalid node value for record")),
    };
    Ok(bytes)
}

fn field_node_to_bytes<T: AsRef<TestLangNode>>(
    testlang: &TestLang,
    record_node: T,
    node: T,
    fdp_enc: &mut Option<FdpEncoderChoice>,
    codegen_path: &Path,
) -> Result<Vec<u8>, Error> {
    let type_id = node.as_ref().type_id;
    let Some(field) = testlang.find_field_by_id(type_id) else {
        return Err(Error::testlang_error(format!(
            "Field type {} is not found.",
            type_id
        )));
    };
    let ser_result = match &field.fuzzed_data_provider_call {
        None => match &node.as_ref().value {
            TestLangNodeValue::Int(i) => {
                let endian = field.endianness.unwrap_or(testlang.default_endian);
                Ok(int_to_bytes(*i, node.as_ref().byte_size(), endian))
            }
            TestLangNodeValue::Float(f) => {
                let endian = field.endianness.unwrap_or(testlang.default_endian);
                float_to_bytes(*f, node.as_ref().byte_size(), endian)
            }
            TestLangNodeValue::Bytes(b) => Ok(b.clone()),
            TestLangNodeValue::String(s) => Ok(s.as_bytes().to_vec()),
            // For `FieldKind::Array`
            TestLangNodeValue::Group(field_nodes) => {
                let mut buf = BytesMut::new();
                for node in field_nodes {
                    let bytes = record_node_to_bytes(testlang, node, fdp_enc, codegen_path)?;
                    buf.extend_from_slice(&bytes);
                }
                Ok(buf.to_vec())
            }
            // For `FieldKind::Record`
            TestLangNodeValue::Record(record_node) => {
                record_node_to_bytes(testlang, record_node, fdp_enc, codegen_path)
            },
            TestLangNodeValue::Ref(_) => Err(Error::testlang_error("TODO: field_node_to_bytes() for TestLangNodeValue::Ref")),
            TestLangNodeValue::Union(_, _) => Err(Error::testlang_error(format!(
                "Invalid node value for field\n[TestLang]\n{testlang}\n[Field]\n{field:#?}\n[Node]\n{:#?}",
                node.as_ref()
            ))),
        },
        Some(fdp_call) => {
            let Some(fdp_enc) = fdp_enc else {
                return Err(Error::testlang_error("FDP encoder is not initialized"));
            };
            let fdp_call_args = fdp_call.args.iter().map(|arg| {
                find_fdp_arg_ref(testlang, record_node.as_ref(), arg)
            }).collect::<Result<Vec<_>, _>>()?;
            let fdp_call = FuzzedDataProviderCall {
                args: fdp_call_args,
                ..fdp_call.clone()
            };
            match fdp_call.method {
                FuzzedDataProviderMethod::LLVM(method) => match method {
                    LLVMFuzzedDataProviderMethod::ConsumeIntegralInRange |
                    LLVMFuzzedDataProviderMethod::ConsumeIntegral |
                    LLVMFuzzedDataProviderMethod::ConsumeBool |
                    LLVMFuzzedDataProviderMethod::ConsumeEnum |
                    LLVMFuzzedDataProviderMethod::PickValueInArray => {
                        fdp_int_node_to_bytes(node, &fdp_call, fdp_enc)?
                    }
                    LLVMFuzzedDataProviderMethod::ConsumeProbability |
                    LLVMFuzzedDataProviderMethod::ConsumeFloatingPointInRange |
                    LLVMFuzzedDataProviderMethod::ConsumeFloatingPoint =>  {
                        fdp_float_node_to_bytes(node, &fdp_call, fdp_enc)?
                    }
                    LLVMFuzzedDataProviderMethod::ConsumeBytes |
                    LLVMFuzzedDataProviderMethod::ConsumeRemainingBytes |
                    LLVMFuzzedDataProviderMethod::ConsumeBytesWithTerminator |
                    LLVMFuzzedDataProviderMethod::ConsumeData => {
                        fdp_bytes_node_to_bytes(testlang, node, &fdp_call, fdp_enc, codegen_path)?
                    }
                    LLVMFuzzedDataProviderMethod::ConsumeBytesAsString |
                    LLVMFuzzedDataProviderMethod::ConsumeRemainingBytesAsString |
                    LLVMFuzzedDataProviderMethod::ConsumeRandomLengthString => {
                        fdp_string_node_to_bytes(node, &fdp_call, fdp_enc)?
                    }
                    LLVMFuzzedDataProviderMethod::remaining_bytes => (),
                }
                FuzzedDataProviderMethod::Jazzer(method) => match method {
                    JazzerFuzzedDataProviderMethod::consumeBoolean |
                    JazzerFuzzedDataProviderMethod::consumeByte |
                    JazzerFuzzedDataProviderMethod::consumeShort |
                    JazzerFuzzedDataProviderMethod::consumeChar |
                    JazzerFuzzedDataProviderMethod::consumeCharNoSurrogates |
                    JazzerFuzzedDataProviderMethod::consumeInt |
                    JazzerFuzzedDataProviderMethod::consumeLong |
                    JazzerFuzzedDataProviderMethod::pickValue => {
                        fdp_int_node_to_bytes(node, &fdp_call, fdp_enc)?
                    }
                    JazzerFuzzedDataProviderMethod::consumeProbabilityFloat |
                    JazzerFuzzedDataProviderMethod::consumeProbabilityDouble |
                    JazzerFuzzedDataProviderMethod::consumeRegularFloat |
                    JazzerFuzzedDataProviderMethod::consumeRegularDouble |
                    JazzerFuzzedDataProviderMethod::consumeFloat |
                    JazzerFuzzedDataProviderMethod::consumeDouble => {
                        fdp_float_node_to_bytes(node, &fdp_call, fdp_enc)?
                    }
                    JazzerFuzzedDataProviderMethod::consumeBytes |
                    JazzerFuzzedDataProviderMethod::consumeRemainingAsBytes => {
                        fdp_bytes_node_to_bytes(testlang, node, &fdp_call, fdp_enc, codegen_path)?
                    }
                    JazzerFuzzedDataProviderMethod::consumeAsciiString |
                    JazzerFuzzedDataProviderMethod::consumeRemainingAsAsciiString |
                    JazzerFuzzedDataProviderMethod::consumeString |
                    JazzerFuzzedDataProviderMethod::consumeRemainingAsString => {
                        fdp_string_node_to_bytes(node, &fdp_call, fdp_enc)?
                    }
                    JazzerFuzzedDataProviderMethod::consumeBooleans |
                    JazzerFuzzedDataProviderMethod::consumeShorts |
                    JazzerFuzzedDataProviderMethod::consumeInts |
                    JazzerFuzzedDataProviderMethod::consumeLongs |
                    JazzerFuzzedDataProviderMethod::pickValues => {
                        fdp_group_node_to_bytes(node, &fdp_call, fdp_enc)?
                    }
                    JazzerFuzzedDataProviderMethod::remainingBytes => (),
                },
            };
            Ok(vec![])
        }
    }?;

    if let Some(encoder) = &field.encoder {
        // TODO: maybe just pass encoding on failure?
        run_encoding_processor(codegen_path, encoder, &ser_result)
    } else {
        Ok(ser_result)
    }
}

fn fdp_int_node_to_bytes<T: AsRef<TestLangNode>>(
    node: T,
    fdp_call: &FuzzedDataProviderCall,
    fdp_enc: &mut FdpEncoderChoice,
) -> Result<(), Error> {
    let val = match &node.as_ref().value {
        TestLangNodeValue::Int(val) => val,
        _ => return Err(Error::testlang_error("Invalid node value")),
    };
    match fdp_call.method {
        FuzzedDataProviderMethod::LLVM(method) => {
            let FdpEncoderChoice::Llvm(fdp_enc) = fdp_enc else {
                return Err(Error::testlang_error("Invalid FDP encoder choice"));
            };
            // FIXME: Save T instead of sizeof(T)? or just add signed/unsigend
            let Some(type_size) = fdp_call.type_size else {
                return Err(Error::testlang_error(
                    "FDP call for ConsumeIntegralInRange<T> does not have sizeof(T)",
                ));
            };
            match method {
                LLVMFuzzedDataProviderMethod::ConsumeIntegralInRange => match &fdp_call.args[..] {
                    [FuzzedDataProviderArg::Int(min), FuzzedDataProviderArg::Int(max)] => {
                        match type_size {
                            1 => Ok(fdp_enc.produce_char_in_range(
                                fit_numeric_value_in_range(*val as i8, *min as i8, *max as i8)?,
                                *min as i8,
                                *max as i8,
                            )?),
                            2 => Ok(fdp_enc.produce_short_in_range(
                                fit_numeric_value_in_range(*val as i16, *min as i16, *max as i16)?,
                                *min as i16,
                                *max as i16,
                            )?),
                            4 => Ok(fdp_enc.produce_int_in_range(
                                fit_numeric_value_in_range(*val as i32, *min as i32, *max as i32)?,
                                *min as i32,
                                *max as i32,
                            )?),
                            8 => Ok(fdp_enc.produce_long_long_in_range(
                                fit_numeric_value_in_range(*val, *min as i64, *max as i64)?,
                                *min as i64,
                                *max as i64,
                            )?),
                            _ => Err(Error::testlang_error(format!(
                                "Invalid FDP call sizeof(T) = {type_size} for {method:?}"
                            ))),
                        }
                    }
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                LLVMFuzzedDataProviderMethod::ConsumeIntegral => match &fdp_call.args[..] {
                    [] => match type_size {
                        1 => Ok(fdp_enc.produce_byte(*val as u8)?),
                        2 => Ok(fdp_enc.produce_unsigned_short(*val as u16)?),
                        4 => Ok(fdp_enc.produce_unsigned_int(*val as u32)?),
                        8 => Ok(fdp_enc.produce_unsigned_long_long(*val as u64)?),
                        _ => Err(Error::testlang_error(format!(
                            "Invalid FDP call sizeof(T) = {type_size} for {method:?}"
                        ))),
                    },
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                // TODO: Make FuzzedDataProviderArg::Bool
                LLVMFuzzedDataProviderMethod::ConsumeBool => match &fdp_call.args[..] {
                    [] => Ok(fdp_enc.produce_bool(*val != 0)?),
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                LLVMFuzzedDataProviderMethod::ConsumeEnum => match &fdp_call.args[..] {
                    [] => Err(Error::testlang_error(
                        "Unable to make arg `max_value` of `produce_enum`",
                    )),
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                LLVMFuzzedDataProviderMethod::PickValueInArray => match &fdp_call.args[..] {
                    [FuzzedDataProviderArg::Int(length)] => Ok(fdp_enc
                        .produce_picked_value_index_in_array(
                            fit_numeric_value_in_range(
                                *val as usize,
                                0,
                                (*length as usize).saturating_sub(1),
                            )?,
                            *length as usize,
                        )?),
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                _ => Err(Error::testlang_error(format!(
                    "Unsupported LLVM FDP method for `int`: {method:?}"
                ))),
            }
        }
        FuzzedDataProviderMethod::Jazzer(method) => {
            let FdpEncoderChoice::Jazzer(fdp_enc) = fdp_enc else {
                return Err(Error::testlang_error("Invalid FDP encoder choice"));
            };
            match method {
                JazzerFuzzedDataProviderMethod::consumeBoolean => match &fdp_call.args[..] {
                    [] => Ok(fdp_enc.produce_jbool(*val != 0)?),
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                JazzerFuzzedDataProviderMethod::consumeByte => match &fdp_call.args[..] {
                    [] => Ok(fdp_enc.produce_jbyte(*val as i8)?),
                    [FuzzedDataProviderArg::Int(min), FuzzedDataProviderArg::Int(max)] => {
                        Ok(fdp_enc.produce_jbyte_in_range(
                            fit_numeric_value_in_range(*val as i8, *min as i8, *max as i8)?,
                            *min as i8,
                            *max as i8,
                        )?)
                    }
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                JazzerFuzzedDataProviderMethod::consumeShort => match &fdp_call.args[..] {
                    [] => Ok(fdp_enc.produce_jshort(*val as i16)?),
                    [FuzzedDataProviderArg::Int(min), FuzzedDataProviderArg::Int(max)] => {
                        Ok(fdp_enc.produce_jshort_in_range(
                            fit_numeric_value_in_range(*val as i16, *min as i16, *max as i16)?,
                            *min as i16,
                            *max as i16,
                        )?)
                    }
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                JazzerFuzzedDataProviderMethod::consumeChar => match &fdp_call.args[..] {
                    [] => Ok(fdp_enc.produce_jchar(*val as u16)?),
                    [FuzzedDataProviderArg::Int(min), FuzzedDataProviderArg::Int(max)] => {
                        Ok(fdp_enc.produce_jchar_in_range(
                            fit_numeric_value_in_range(*val as u16, *min as u16, *max as u16)?,
                            *min as u16,
                            *max as u16,
                        )?)
                    }
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                JazzerFuzzedDataProviderMethod::consumeCharNoSurrogates => match &fdp_call.args[..]
                {
                    [] => Ok(fdp_enc.produce_jchar_no_surrogates(*val as u16)?),
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                JazzerFuzzedDataProviderMethod::consumeInt => match &fdp_call.args[..] {
                    [] => Ok(fdp_enc.produce_jint(*val as i32)?),
                    [FuzzedDataProviderArg::Int(min), FuzzedDataProviderArg::Int(max)] => {
                        Ok(fdp_enc.produce_jint_in_range(
                            fit_numeric_value_in_range(*val as i32, *min as i32, *max as i32)?,
                            *min as i32,
                            *max as i32,
                        )?)
                    }
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                JazzerFuzzedDataProviderMethod::consumeLong => match &fdp_call.args[..] {
                    [] => Ok(fdp_enc.produce_jlong(*val)?),
                    [FuzzedDataProviderArg::Int(min), FuzzedDataProviderArg::Int(max)] => {
                        Ok(fdp_enc.produce_jlong_in_range(
                            fit_numeric_value_in_range(*val, *min as i64, *max as i64)?,
                            *min as i64,
                            *max as i64,
                        )?)
                    }
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                JazzerFuzzedDataProviderMethod::pickValue => match &fdp_call.args[..] {
                    [FuzzedDataProviderArg::Int(length)] => Ok(fdp_enc
                        .produce_picked_value_index_in_jarray(
                            fit_numeric_value_in_range(
                                *val as usize,
                                0,
                                (*length as usize).saturating_sub(1),
                            )?,
                            *length as usize,
                        )?),
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                _ => Err(Error::testlang_error(format!(
                    "Unsupported Jazzer FDP method for `int`: {method:?}"
                ))),
            }
        }
    }
}

fn fdp_float_node_to_bytes<T: AsRef<TestLangNode>>(
    node: T,
    fdp_call: &FuzzedDataProviderCall,
    fdp_enc: &mut FdpEncoderChoice,
) -> Result<(), Error> {
    let val = match &node.as_ref().value {
        TestLangNodeValue::Float(val) => val,
        _ => return Err(Error::testlang_error("Invalid node value")),
    };
    match fdp_call.method {
        FuzzedDataProviderMethod::LLVM(method) => {
            let FdpEncoderChoice::Llvm(fdp_enc) = fdp_enc else {
                return Err(Error::testlang_error("Invalid FDP encoder choice"));
            };
            let Some(type_size) = fdp_call.type_size else {
                return Err(Error::testlang_error(
                    "FDP call for ConsumeIntegralInRange<T> does not have sizeof(T)",
                ));
            };
            match method {
                LLVMFuzzedDataProviderMethod::ConsumeProbability => match &fdp_call.args[..] {
                    [] => match type_size {
                        4 => Ok(fdp_enc.produce_probability_float(*val as f32)?),
                        8 => Ok(fdp_enc.produce_probability_double(*val)?),
                        _ => Err(Error::testlang_error(format!(
                            "Invalid FDP call sizeof(T) = {type_size} for {method:?}"
                        ))),
                    },
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                LLVMFuzzedDataProviderMethod::ConsumeFloatingPointInRange => {
                    match &fdp_call.args[..] {
                        [FuzzedDataProviderArg::Float(min), FuzzedDataProviderArg::Float(max)] => {
                            match type_size {
                                4 => Ok(fdp_enc.produce_float_in_range(
                                    fit_f32_value_in_range(*val as f32, *min as f32, *max as f32)?,
                                    *min as f32,
                                    *max as f32,
                                )?),
                                8 => Ok(fdp_enc.produce_double_in_range(
                                    fit_f64_value_in_range(*val, *min, *max)?,
                                    *min,
                                    *max,
                                )?),
                                _ => Err(Error::testlang_error(format!(
                                    "Invalid FDP call sizeof(T) = {type_size} for {method:?}"
                                ))),
                            }
                        }
                        _ => Err(Error::testlang_error(format!(
                            "Invalid FDP call args for {method:?}"
                        ))),
                    }
                }
                LLVMFuzzedDataProviderMethod::ConsumeFloatingPoint => match &fdp_call.args[..] {
                    [] => match type_size {
                        4 => Ok(fdp_enc.produce_float(*val as f32)?),
                        8 => Ok(fdp_enc.produce_double(*val)?),
                        _ => Err(Error::testlang_error(format!(
                            "Invalid FDP call sizeof(T) = {type_size} for {method:?}"
                        ))),
                    },
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                _ => Err(Error::testlang_error(format!(
                    "Unsupported LLVM FDP method for `float`: {method:?}"
                ))),
            }
        }
        FuzzedDataProviderMethod::Jazzer(method) => {
            let FdpEncoderChoice::Jazzer(fdp_enc) = fdp_enc else {
                return Err(Error::testlang_error("Invalid FDP encoder choice"));
            };
            match method {
                JazzerFuzzedDataProviderMethod::consumeProbabilityFloat => match &fdp_call.args[..]
                {
                    [] => Ok(fdp_enc.produce_probability_jfloat(fit_f32_value_in_range(
                        *val as f32,
                        0.0,
                        1.0,
                    )?)?),
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                JazzerFuzzedDataProviderMethod::consumeProbabilityDouble => {
                    match &fdp_call.args[..] {
                        [] => Ok(fdp_enc.produce_probability_jdouble(fit_f64_value_in_range(
                            *val, 0.0, 1.0,
                        )?)?),
                        _ => Err(Error::testlang_error(format!(
                            "Invalid FDP call args for {method:?}"
                        ))),
                    }
                }
                JazzerFuzzedDataProviderMethod::consumeRegularFloat => match &fdp_call.args[..] {
                    [] => Ok(fdp_enc.produce_regular_jfloat(*val as f32)?),
                    [FuzzedDataProviderArg::Float(min), FuzzedDataProviderArg::Float(max)] => {
                        Ok(fdp_enc.produce_regular_jfloat_in_range(
                            fit_f32_value_in_range(*val as f32, *min as f32, *max as f32)?,
                            *min as f32,
                            *max as f32,
                        )?)
                    }
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                JazzerFuzzedDataProviderMethod::consumeRegularDouble => match &fdp_call.args[..] {
                    [] => Ok(fdp_enc.produce_regular_jdouble(*val)?),
                    [FuzzedDataProviderArg::Float(min), FuzzedDataProviderArg::Float(max)] => {
                        Ok(fdp_enc.produce_regular_jdouble_in_range(
                            fit_f64_value_in_range(*val, *min, *max)?,
                            *min,
                            *max,
                        )?)
                    }
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                JazzerFuzzedDataProviderMethod::consumeFloat => match &fdp_call.args[..] {
                    [] => Ok(fdp_enc.produce_jfloat(*val as f32)?),
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                JazzerFuzzedDataProviderMethod::consumeDouble => match &fdp_call.args[..] {
                    [] => Ok(fdp_enc.produce_jdouble(*val)?),
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                _ => Err(Error::testlang_error(format!(
                    "Unsupported Jazzer FDP method for `float`: {method:?}"
                ))),
            }
        }
    }
}

fn fdp_bytes_node_to_bytes<T: AsRef<TestLangNode>>(
    testlang: &TestLang,
    node: T,
    fdp_call: &FuzzedDataProviderCall,
    fdp_enc: &mut FdpEncoderChoice,
    codegen_path: &Path,
) -> Result<(), Error> {
    let val = match &node.as_ref().value {
        TestLangNodeValue::Bytes(val) => val,
        TestLangNodeValue::Record(rec) => {
            // You are not allowed to call fdp methods in nested fashion.
            &record_node_to_bytes(testlang, rec, &mut None, codegen_path)?
        }
        _ => return Err(Error::testlang_error("Invalid node value")),
    };
    match fdp_call.method {
        FuzzedDataProviderMethod::LLVM(method) => {
            let FdpEncoderChoice::Llvm(fdp_enc) = fdp_enc else {
                return Err(Error::testlang_error("Invalid FDP encoder choice"));
            };
            match method {
                LLVMFuzzedDataProviderMethod::ConsumeBytes => match &fdp_call.args[..] {
                    [FuzzedDataProviderArg::Int(num_bytes)] => Ok(fdp_enc.produce_bytes(
                        &val[..val.len().min(*num_bytes as usize)],
                        *num_bytes as usize,
                    )?),
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                LLVMFuzzedDataProviderMethod::ConsumeRemainingBytes => match &fdp_call.args[..] {
                    [] => Ok(fdp_enc.produce_remaining_bytes(val)?),
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                LLVMFuzzedDataProviderMethod::ConsumeBytesWithTerminator => {
                    match &fdp_call.args[..] {
                        [FuzzedDataProviderArg::Int(num_bytes), FuzzedDataProviderArg::Int(terminator)] => {
                            Ok(fdp_enc.produce_bytes_with_terminator(
                                &val[..val.len().min(*num_bytes as usize)],
                                *num_bytes as usize,
                                *terminator as u8,
                            )?)
                        }
                        _ => Err(Error::testlang_error(format!(
                            "Invalid FDP call args for {method:?}"
                        ))),
                    }
                }
                LLVMFuzzedDataProviderMethod::ConsumeData => match &fdp_call.args[..] {
                    [_destination, FuzzedDataProviderArg::Int(num_bytes)] => Ok(fdp_enc
                        .produce_bytes(
                            &val[..val.len().min(*num_bytes as usize)],
                            *num_bytes as usize,
                        )?),
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                _ => Err(Error::testlang_error(format!(
                    "Unsupported LLVM FDP method for `bytes`: {method:?}"
                ))),
            }
        }
        FuzzedDataProviderMethod::Jazzer(method) => {
            let FdpEncoderChoice::Jazzer(fdp_enc) = fdp_enc else {
                return Err(Error::testlang_error("Invalid FDP encoder choice"));
            };
            match method {
                JazzerFuzzedDataProviderMethod::consumeBytes => match &fdp_call.args[..] {
                    [FuzzedDataProviderArg::Int(max_length)] => {
                        let mut data = val.iter().map(|b| *b as i8).collect::<Vec<i8>>();
                        data.truncate(*max_length as usize);
                        Ok(fdp_enc.produce_jbytes(&data, *max_length as i32)?)
                    }
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                JazzerFuzzedDataProviderMethod::consumeRemainingAsBytes => match &fdp_call.args[..]
                {
                    [] => Ok(fdp_enc.produce_remaining_as_jbytes(
                        &val.iter().map(|b| *b as i8).collect::<Vec<i8>>(),
                    )?),
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                _ => Err(Error::testlang_error(format!(
                    "Unsupported Jazzer FDP method for `bytes`: {method:?}"
                ))),
            }
        }
    }
}

fn fdp_string_node_to_bytes<T: AsRef<TestLangNode>>(
    node: T,
    fdp_call: &FuzzedDataProviderCall,
    fdp_enc: &mut FdpEncoderChoice,
) -> Result<(), Error> {
    let val = match &node.as_ref().value {
        TestLangNodeValue::String(val) => val,
        _ => return Err(Error::testlang_error("Invalid node value")),
    };
    match fdp_call.method {
        FuzzedDataProviderMethod::LLVM(method) => {
            let FdpEncoderChoice::Llvm(fdp_enc) = fdp_enc else {
                return Err(Error::testlang_error("Invalid FDP encoder choice"));
            };
            match method {
                LLVMFuzzedDataProviderMethod::ConsumeBytesAsString => match &fdp_call.args[..] {
                    [FuzzedDataProviderArg::Int(num_bytes)] => Ok(fdp_enc
                        .produce_bytes_as_string(
                            &val.as_bytes()[..val.len().min(*num_bytes as usize)],
                            *num_bytes as usize,
                        )?),
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                LLVMFuzzedDataProviderMethod::ConsumeRemainingBytesAsString => {
                    match &fdp_call.args[..] {
                        [] => Ok(fdp_enc.produce_remaining_bytes_as_string(val.as_bytes())?),
                        _ => Err(Error::testlang_error(format!(
                            "Invalid FDP call args for {method:?}"
                        ))),
                    }
                }
                LLVMFuzzedDataProviderMethod::ConsumeRandomLengthString => match &fdp_call.args[..]
                {
                    [] => Ok(fdp_enc.produce_random_length_string(val.as_bytes())?),
                    [FuzzedDataProviderArg::Int(max_length)] => Ok(fdp_enc
                        .produce_random_length_string_with_max_length(
                            &val.as_bytes()[..val.len().min(*max_length as usize)],
                            *max_length as usize,
                        )?),
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                _ => Err(Error::testlang_error(format!(
                    "Unsupported LLVM FDP method for `string`: {method:?}"
                ))),
            }
        }
        FuzzedDataProviderMethod::Jazzer(method) => {
            let FdpEncoderChoice::Jazzer(fdp_enc) = fdp_enc else {
                return Err(Error::testlang_error("Invalid FDP encoder choice"));
            };
            match method {
                JazzerFuzzedDataProviderMethod::consumeAsciiString => match &fdp_call.args[..] {
                    [FuzzedDataProviderArg::Int(max_length)] => Ok(fdp_enc.produce_ascii_string(
                        &String::from_utf8_lossy(
                            &val.as_bytes()[..val.len().min(*max_length as usize)],
                        ),
                        *max_length as i32,
                    )?),
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                JazzerFuzzedDataProviderMethod::consumeRemainingAsAsciiString => {
                    match &fdp_call.args[..] {
                        [] => Ok(fdp_enc.produce_remaining_as_ascii_string(val)?),
                        _ => Err(Error::testlang_error(format!(
                            "Invalid FDP call args for {method:?}"
                        ))),
                    }
                }
                JazzerFuzzedDataProviderMethod::consumeString => match &fdp_call.args[..] {
                    [FuzzedDataProviderArg::Int(max_length)] => {
                        let data = match val.char_indices().nth(*max_length as usize) {
                            Some((idx, _)) => &val[..idx],
                            None => val.as_str(),
                        };
                        Ok(fdp_enc.produce_jstring(data, *max_length as i32)?)
                    }
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                JazzerFuzzedDataProviderMethod::consumeRemainingAsString => {
                    match &fdp_call.args[..] {
                        [] => Ok(fdp_enc.produce_remaining_as_jstring(val)?),
                        _ => Err(Error::testlang_error(format!(
                            "Invalid FDP call args for {method:?}"
                        ))),
                    }
                }
                _ => Err(Error::testlang_error(format!(
                    "Unsupported Jazzer FDP method for `string`: {method:?}"
                ))),
            }
        }
    }
}

fn fdp_group_node_to_bytes<T: AsRef<TestLangNode>>(
    node: T,
    fdp_call: &FuzzedDataProviderCall,
    fdp_enc: &mut FdpEncoderChoice,
) -> Result<(), Error> {
    let val = match &node.as_ref().value {
        TestLangNodeValue::Group(val) => val,
        _ => return Err(Error::testlang_error("Invalid node value")),
    };
    match fdp_call.method {
        FuzzedDataProviderMethod::LLVM(method) => {
            let FdpEncoderChoice::Llvm(_fdp_enc) = fdp_enc else {
                return Err(Error::testlang_error("Invalid FDP encoder choice"));
            };
            Err(Error::testlang_error(format!(
                "Unsupported LLVM FDP method for `group`: {method:?}"
            )))
        }
        FuzzedDataProviderMethod::Jazzer(method) => {
            let FdpEncoderChoice::Jazzer(fdp_enc) = fdp_enc else {
                return Err(Error::testlang_error("Invalid FDP encoder choice"));
            };
            // This is rather a heuristic based decision (array element has no inner union)
            match method {
                JazzerFuzzedDataProviderMethod::consumeBooleans => match &fdp_call.args[..] {
                    [FuzzedDataProviderArg::Int(max_length)] => {
                        let mut vals = val
                            .iter()
                            .flat_map(|node| {
                                // Array node is Group(Group(_)) or Group(Union(_))
                                if let TestLangNodeValue::Group(inner_group) = &node.value {
                                    inner_group
                                        .iter()
                                        .map(|inner_node| {
                                            if let TestLangNodeValue::Int(v) = &inner_node.value {
                                                Ok(*v != 0)
                                            } else {
                                                Err(Error::testlang_error(format!(
                                                    "Invalid node value for {method:?}"
                                                )))
                                            }
                                        })
                                        .collect()
                                } else {
                                    vec![Err(Error::testlang_error(format!(
                                        "Invalid node value for {method:?}"
                                    )))]
                                }
                            })
                            .collect::<Result<Vec<_>, _>>()?;
                        vals.truncate(*max_length as usize);
                        Ok(fdp_enc.produce_jbools(&vals, *max_length as i32)?)
                    }
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                JazzerFuzzedDataProviderMethod::consumeShorts => match &fdp_call.args[..] {
                    [FuzzedDataProviderArg::Int(max_length)] => {
                        let mut vals = val
                            .iter()
                            .flat_map(|node| {
                                // Array node is Group(Group(_)) or Group(Union(_))
                                if let TestLangNodeValue::Group(inner_group) = &node.value {
                                    inner_group
                                        .iter()
                                        .map(|inner_node| {
                                            if let TestLangNodeValue::Int(v) = &inner_node.value {
                                                Ok(*v as i16)
                                            } else {
                                                Err(Error::testlang_error(format!(
                                                    "Invalid node value for {method:?}"
                                                )))
                                            }
                                        })
                                        .collect()
                                } else {
                                    vec![Err(Error::testlang_error(format!(
                                        "Invalid node value for {method:?}"
                                    )))]
                                }
                            })
                            .collect::<Result<Vec<_>, _>>()?;
                        vals.truncate(*max_length as usize);
                        Ok(fdp_enc.produce_jshorts(&vals, *max_length as i32)?)
                    }
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                JazzerFuzzedDataProviderMethod::consumeInts => match &fdp_call.args[..] {
                    [FuzzedDataProviderArg::Int(max_length)] => {
                        let mut vals = val
                            .iter()
                            .flat_map(|node| {
                                // Array node is Group(Group(_)) or Group(Union(_))
                                if let TestLangNodeValue::Group(inner_group) = &node.value {
                                    inner_group
                                        .iter()
                                        .map(|inner_node| {
                                            if let TestLangNodeValue::Int(v) = &inner_node.value {
                                                Ok(*v as i32)
                                            } else {
                                                Err(Error::testlang_error(format!(
                                                    "Invalid node value for {method:?}"
                                                )))
                                            }
                                        })
                                        .collect()
                                } else {
                                    vec![Err(Error::testlang_error(format!(
                                        "Invalid node value for {method:?}"
                                    )))]
                                }
                            })
                            .collect::<Result<Vec<_>, _>>()?;
                        vals.truncate(*max_length as usize);
                        Ok(fdp_enc.produce_jints(&vals, *max_length as i32)?)
                    }
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                JazzerFuzzedDataProviderMethod::consumeLongs => match &fdp_call.args[..] {
                    [FuzzedDataProviderArg::Int(max_length)] => {
                        let mut vals = val
                            .iter()
                            .flat_map(|node| {
                                // Array node is Group(Group(_)) or Group(Union(_))
                                if let TestLangNodeValue::Group(inner_group) = &node.value {
                                    inner_group
                                        .iter()
                                        .map(|inner_node| {
                                            if let TestLangNodeValue::Int(v) = &inner_node.value {
                                                Ok(*v)
                                            } else {
                                                Err(Error::testlang_error(format!(
                                                    "Invalid node value for {method:?}"
                                                )))
                                            }
                                        })
                                        .collect()
                                } else {
                                    vec![Err(Error::testlang_error(format!(
                                        "Invalid node value for {method:?}"
                                    )))]
                                }
                            })
                            .collect::<Result<Vec<_>, _>>()?;
                        vals.truncate(*max_length as usize);
                        Ok(fdp_enc.produce_jlongs(&vals, *max_length as i32)?)
                    }
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                JazzerFuzzedDataProviderMethod::pickValues => match &fdp_call.args[..] {
                    [FuzzedDataProviderArg::Int(length), FuzzedDataProviderArg::Int(num_of_elements)] =>
                    {
                        if val.len() != *num_of_elements as usize {
                            return Err(Error::testlang_error(format!(
                                "Invalid number of elements for {method:?}"
                            )));
                        }
                        let vals = val
                            .iter()
                            .flat_map(|node| {
                                // Array node is Group(Group(_)) or Group(Union(_))
                                if let TestLangNodeValue::Group(inner_group) = &node.value {
                                    inner_group
                                        .iter()
                                        .map(|inner_node| {
                                            if let TestLangNodeValue::Int(v) = &inner_node.value {
                                                Ok(*v as usize)
                                            } else {
                                                Err(Error::testlang_error(format!(
                                                    "Invalid node value for {method:?}"
                                                )))
                                            }
                                        })
                                        .collect()
                                } else {
                                    vec![Err(Error::testlang_error(format!(
                                        "Invalid node value for {method:?}"
                                    )))]
                                }
                            })
                            .collect::<Result<Vec<_>, _>>()?;
                        Ok(fdp_enc
                            .produce_picked_value_indexes_in_jarray(&vals, *length as usize)?)
                    }
                    _ => Err(Error::testlang_error(format!(
                        "Invalid FDP call args for {method:?}"
                    ))),
                },
                _ => Err(Error::testlang_error(format!(
                    "Unsupported Jazzer FDP method for `group`: {method:?}"
                ))),
            }
        }
    }
}

fn find_fdp_arg_ref<T: AsRef<TestLangNode>>(
    testlang: &TestLang,
    record_node: T,
    fdp_arg: &FuzzedDataProviderArg,
) -> Result<FuzzedDataProviderArg, Error> {
    let FuzzedDataProviderArg::Ref(reference) = fdp_arg else {
        return Ok(fdp_arg.clone());
    };
    let Ref {
        kind: RefKind::Field,
        name: ref_name,
    } = reference
    else {
        return Err(Error::testlang_error("FDP arg kind is not Field"));
    };
    let record_type_id = record_node.as_ref().type_id;
    let Some(record) = testlang.find_record_by_id(record_type_id) else {
        return Err(Error::testlang_error(format!(
            "Record type {} is not found.",
            record_type_id
        )));
    };
    let TestLangNodeValue::Group(field_nodes) = &record_node.as_ref().value else {
        return Err(Error::testlang_error("Record node value is not Group"));
    };
    let Some((ref_node, ref_field)) = field_nodes
        .iter()
        .zip(record.fields.iter())
        .find(|(_, field)| field.name == *ref_name)
    else {
        return Err(Error::testlang_error(format!(
            "Field {} is not found in record",
            ref_name
        )));
    };
    match ref_field.kind {
        FieldKind::Int => match &ref_node.value {
            TestLangNodeValue::Int(ref_value) => Ok(FuzzedDataProviderArg::Int(*ref_value as u64)),
            _ => Err(Error::testlang_error(format!(
                "Field {} is not Int",
                ref_name
            ))),
        },
        _ => Err(Error::testlang_error(format!(
            "Ref Field {} is not Int",
            ref_name
        ))),
    }
}
