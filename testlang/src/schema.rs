use std::{
    collections::{HashMap, HashSet},
    sync::LazyLock,
};

use serde::{Deserialize, Serialize};

use schemars::{schema_for, JsonSchema};

use crate::TestLangWarning;

pub const RECORD_INPUT: &str = "INPUT";

pub type TestLangInt = i64;
pub type TestLangFloat = f64;
pub type TestLangString = String;
pub type TestLangBytes = Vec<u8>;

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TestLang {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub is_partial: Option<bool>,
    pub mode: ModeKind,
    pub default_endian: Endianness,
    pub records: Vec<Record>,
    #[serde(skip)]
    pub record_index: HashMap<String, usize>,
    #[serde(skip)]
    pub type_map: HashMap<usize, TestLangType>,
    #[serde(skip)]
    pub warnings: Vec<TestLangWarning>,
    #[serde(skip)]
    pub int_ref_map: HashMap<String, HashMap<String, HashSet<String>>>,
    #[serde(skip)]
    pub size_deref_map: HashMap<String, HashMap<String, String>>,
}

#[derive(Clone, Debug)]
pub enum TestLangType {
    RecordType { record: String },
    FieldType { record: String, field: String },
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Record {
    #[serde(skip)]
    pub type_id: Option<usize>,
    pub name: String,
    pub kind: RecordKind,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub byte_size: Option<SizeDescriptor>,
    pub fields: Vec<Field>,
    pub analysis: Vec<Analysis>,
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq, Copy)]
pub enum ModeKind {
    Bytes,
    FuzzedDataProvider(FuzzedDataProviderKind),
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq, Copy)]
pub enum FuzzedDataProviderKind {
    LLVM,
    Jazzer,
    Custom,
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq, Copy)]
#[serde(rename_all = "snake_case")]
pub enum RecordKind {
    Struct,
    Union,
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Field {
    #[serde(skip)]
    pub type_id: Option<usize>,
    pub name: String,
    pub kind: FieldKind,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub len: Option<SizeDescriptor>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub byte_size: Option<SizeDescriptor>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub possible_values: Option<Vec<FieldValue>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub items: Option<Ref>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub terminator: Option<Terminator>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub string_format: Option<StringFormat>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub endianness: Option<Endianness>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fuzzed_data_provider_call: Option<FuzzedDataProviderCall>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub encoder: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub generator: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Analysis {
    pub location: Location,
    pub callee_within_location: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq)]
pub struct Location {
    pub file_path: String,
    pub func_name: String,
    pub start_line_num: usize,
    pub end_line_num: usize,
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum FieldKind {
    Int,
    Float,
    Bytes,
    String,
    Array,
    Record,
    Custom(String),
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Ref {
    pub kind: RefKind,
    pub name: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq, Copy)]
#[serde(rename_all = "snake_case")]
pub enum RefKind {
    Field,
    Record,
}

pub type SizeDescriptor = NumValue<usize>;

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq)]
#[serde(untagged)]
pub enum FieldValue {
    // `Bytes` is chosen as a first variant of this enum with consideration.
    // Integer array with two elements should be parsed into `Vec<u8>`, rather than into `RangeInclusive<T>`.
    // Related tracking issue: https://github.com/serde-rs/json/issues/1059
    Bytes(ValOrRef<TestLangBytes>),
    Int(NumValue<TestLangInt>),
    Float(NumValue<TestLangFloat>),
    String(ValOrRef<TestLangString>),
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq)]
#[serde(untagged)]
pub enum NumValue<T> {
    Single(ValOrRef<T>),
    Range(RangeInclusive<T>),
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct RangeInclusive<T> {
    pub start: ValOrRef<T>,
    pub end: ValOrRef<T>,
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq)]
#[serde(untagged)]
pub enum ValOrRef<T> {
    Val(T),
    Ref(Ref),
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq)]
#[serde(untagged)]
pub enum Terminator {
    ByteSequence(Vec<u8>),
    CharSequence(String),
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq, Copy)]
#[serde(rename_all = "snake_case")]
pub enum StringFormat {
    BinInt,
    DecInt,
    HexInt,
    OctInt,
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq, Copy)]
#[serde(rename_all = "snake_case")]
pub enum Endianness {
    Little,
    Big,
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq)]
pub struct FuzzedDataProviderCall {
    pub method: FuzzedDataProviderMethod,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub type_size: Option<u8>,
    pub args: Vec<FuzzedDataProviderArg>,
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq)]
pub enum FuzzedDataProviderArg {
    Int(u64),
    Float(f64),
    Ref(Ref),
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq, Copy)]
#[serde(untagged)]
pub enum FuzzedDataProviderMethod {
    LLVM(LLVMFuzzedDataProviderMethod),
    Jazzer(JazzerFuzzedDataProviderMethod),
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq, Copy)]
#[allow(non_camel_case_types)]
pub enum LLVMFuzzedDataProviderMethod {
    ConsumeIntegralInRange,
    ConsumeIntegral,
    ConsumeBool,
    ConsumeProbability,
    ConsumeFloatingPointInRange,
    ConsumeFloatingPoint,
    ConsumeEnum,
    PickValueInArray,
    ConsumeBytes,
    ConsumeRemainingBytes,
    ConsumeBytesWithTerminator,
    ConsumeBytesAsString,
    ConsumeRemainingBytesAsString,
    ConsumeRandomLengthString,
    ConsumeData,
    remaining_bytes,
}

#[derive(Clone, Debug, Deserialize, Serialize, JsonSchema, PartialEq, Eq, Copy)]
#[allow(non_camel_case_types)]
pub enum JazzerFuzzedDataProviderMethod {
    consumeBoolean,
    consumeByte,
    consumeShort,
    consumeChar,
    consumeCharNoSurrogates,
    consumeInt,
    consumeLong,
    consumeProbabilityFloat,
    consumeProbabilityDouble,
    consumeRegularFloat,
    consumeRegularDouble,
    consumeFloat,
    consumeDouble,
    consumeBooleans,
    consumeBytes,
    consumeShorts,
    consumeInts,
    consumeLongs,
    consumeRemainingAsBytes,
    consumeAsciiString,
    consumeRemainingAsAsciiString,
    consumeString,
    consumeRemainingAsString,
    pickValue,
    pickValues,
    remainingBytes,
}

pub fn get_testlang_schema() -> &'static str {
    static SCHEMA: LazyLock<String> = LazyLock::new(|| {
        let schema = schema_for!(TestLang);
        serde_json::to_string_pretty(&schema).unwrap()
    });
    SCHEMA.as_str()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn check_schema() {
        get_testlang_schema();
    }
}
