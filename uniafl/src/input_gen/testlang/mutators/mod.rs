use std::num::TryFromIntError;

use array::{ArrayElementInsertMutator, ArrayElementRemoveMutator};
use testlang::{Field, FieldKind, StringFormat, TestLangInt};
use thiserror::Error;
use union::UnionMutator;

use super::{TestLangAstFreeInputMutator, TestLangInputMutator};
use crate::common::Error as UniaflError;

pub mod array;
pub mod ast_free;
pub mod field;
pub mod union;

pub type TestLangMutator = dyn TestLangInputMutator;
pub type TestLangAstFreeMutator = dyn TestLangAstFreeInputMutator;
pub type TestLangMutators = Vec<Box<TestLangMutator>>;
pub type TestLangAstFreeMutators = Vec<Box<TestLangAstFreeMutator>>;

#[derive(Debug, Error)]
pub enum TestLangBlobMutationError {
    #[error("Data too big for usize")]
    IntegerOverflow(#[from] TryFromIntError),
}

macro_rules! to_dyn_mutator {
    ($mt_mut:expr) => {
        Box::new($mt_mut)
    };
}

#[must_use]
pub fn testlang_mutators() -> TestLangMutators {
    let mut mutators = Vec::new();
    let mut field_mutators = testlang_field_mutators();
    let mut havoc_mutators = testlang_havoc_mutators();
    for _ in 0..field_mutators.len() + havoc_mutators.len() {
        mutators.append(&mut testlang_array_mutators());
        mutators.append(&mut testlang_union_mutators());
    }
    mutators.append(&mut field_mutators);
    mutators.append(&mut havoc_mutators);
    mutators
}

#[must_use]
pub fn testlang_ast_free_mutators() -> TestLangAstFreeMutators {
    vec![
        to_dyn_mutator!(ast_free::new_record_insert_front()),
        to_dyn_mutator!(ast_free::new_record_insert_middle()),
        to_dyn_mutator!(ast_free::new_record_insert_end()),
        to_dyn_mutator!(ast_free::new_record_replace_front()),
        to_dyn_mutator!(ast_free::new_record_replace_middle()),
        to_dyn_mutator!(ast_free::new_record_replace_end()),
    ]
}

#[cfg(test)]
pub fn testlang_normal_mutators() -> TestLangMutators {
    testlang_array_mutators()
        .into_iter()
        .chain(testlang_union_mutators())
        .chain(testlang_field_mutators())
        .collect()
}

pub fn testlang_array_mutators() -> TestLangMutators {
    vec![
        to_dyn_mutator!(ArrayElementInsertMutator::new()),
        to_dyn_mutator!(ArrayElementRemoveMutator::new()),
    ]
}

pub fn testlang_union_mutators() -> TestLangMutators {
    vec![to_dyn_mutator!(UnionMutator::new())]
}

pub fn testlang_field_mutators() -> TestLangMutators {
    vec![
        to_dyn_mutator!(field::new_bit_flip()),
        to_dyn_mutator!(field::new_byte_add()),
        to_dyn_mutator!(field::new_byte_dec()),
        to_dyn_mutator!(field::new_byte_flip()),
        to_dyn_mutator!(field::new_byte_inc()),
        to_dyn_mutator!(field::new_byte_interesting()),
        to_dyn_mutator!(field::new_byte_neg()),
        to_dyn_mutator!(field::new_byte_rand()),
        to_dyn_mutator!(field::new_bytes_copy()),
        to_dyn_mutator!(field::new_bytes_delete()),
        to_dyn_mutator!(field::new_bytes_expand()),
        to_dyn_mutator!(field::new_bytes_insert()),
        to_dyn_mutator!(field::new_bytes_insert_copy()),
        to_dyn_mutator!(field::new_bytes_rand_insert()),
        to_dyn_mutator!(field::new_bytes_rand_set()),
        to_dyn_mutator!(field::new_bytes_set()),
        to_dyn_mutator!(field::new_bytes_swap()),
        to_dyn_mutator!(field::new_dword_add()),
        to_dyn_mutator!(field::new_dword_interesting()),
        to_dyn_mutator!(field::new_qword_add()),
        to_dyn_mutator!(field::new_word_add()),
        to_dyn_mutator!(field::new_word_interesting()),
        to_dyn_mutator!(field::new_chooser()),
    ]
}

#[must_use]
pub fn testlang_havoc_mutators() -> TestLangMutators {
    vec![
        to_dyn_mutator!(field::new_havoc_bit_flip()),
        to_dyn_mutator!(field::new_havoc_byte_add()),
        to_dyn_mutator!(field::new_havoc_byte_dec()),
        to_dyn_mutator!(field::new_havoc_byte_flip()),
        to_dyn_mutator!(field::new_havoc_byte_inc()),
        to_dyn_mutator!(field::new_havoc_byte_interesting()),
        to_dyn_mutator!(field::new_havoc_byte_neg()),
        to_dyn_mutator!(field::new_havoc_byte_rand()),
        to_dyn_mutator!(field::new_havoc_bytes_copy()),
        to_dyn_mutator!(field::new_havoc_bytes_delete()),
        to_dyn_mutator!(field::new_havoc_bytes_expand()),
        to_dyn_mutator!(field::new_havoc_bytes_insert()),
        to_dyn_mutator!(field::new_havoc_bytes_insert_copy()),
        to_dyn_mutator!(field::new_havoc_bytes_rand_insert()),
        to_dyn_mutator!(field::new_havoc_bytes_rand_set()),
        to_dyn_mutator!(field::new_havoc_bytes_set()),
        to_dyn_mutator!(field::new_havoc_bytes_swap()),
        to_dyn_mutator!(field::new_havoc_dword_add()),
        to_dyn_mutator!(field::new_havoc_dword_interesting()),
        to_dyn_mutator!(field::new_havoc_qword_add()),
        to_dyn_mutator!(field::new_havoc_word_add()),
        to_dyn_mutator!(field::new_havoc_word_interesting()),
    ]
}

fn update_fmt_string_size_dep(
    fmt_string: &mut String,
    field: &Field,
    size_diff: TestLangInt,
    original_on_broken: TestLangInt,
) -> Result<(), UniaflError> {
    if field.kind != FieldKind::String {
        return Err(UniaflError::testlang_error("AST metadata is broken"));
    }
    let Some(format) = field.string_format else {
        return Err(UniaflError::testlang_error(
            "Non-integer like string field was chosen as size reference",
        ));
    };
    let radix = match format {
        StringFormat::BinInt => 2,
        StringFormat::OctInt => 8,
        StringFormat::DecInt => 10,
        StringFormat::HexInt => 16,
    };
    // Format string field is prone to havoc field mutation. Try fix here.
    let original_size =
        TestLangInt::from_str_radix(fmt_string, radix).unwrap_or(original_on_broken);
    let new_size = original_size.saturating_add(size_diff);
    let new_fmt_string = match format {
        StringFormat::BinInt => format!("{:b}", new_size),
        StringFormat::OctInt => format!("{:o}", new_size),
        StringFormat::DecInt => format!("{}", new_size),
        StringFormat::HexInt => format!("{:x}", new_size),
    };
    *fmt_string = new_fmt_string;
    Ok(())
}
