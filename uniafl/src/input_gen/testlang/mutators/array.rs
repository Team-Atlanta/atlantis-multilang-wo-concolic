use libafl::{mutators::MutationResult, state::HasMaxSize};
use libafl_bolts::rands::Rand;
use testlang::{TestLangAst, TestLangInt, TestLangNodeValue};

use crate::{
    common::Error,
    input_gen::testlang::{
        generators::TestLangGenerator, mutators::update_fmt_string_size_dep, node_to_bytes,
        service::worker::TestLangState, TestLangInputMutator,
    },
};

pub struct ArrayElementInsertMutator {
    generator: TestLangGenerator,
}

pub struct ArrayElementRemoveMutator;

impl TestLangInputMutator for ArrayElementInsertMutator {
    #[cfg(feature = "log")]
    fn name(&self) -> &str {
        "ArrayElementInsertMutator"
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
        let Some(array_id) = state.rand.choose(input_metadata.arrays.iter()).copied() else {
            return Ok(MutationResult::Skipped);
        };

        let mut output_node = input.root.clone();

        let testlang_arc = state.testlang.clone();
        let testlang = testlang_arc.as_ref();

        let mut_len = state.rand.below(20) + 1;
        #[cfg(feature = "log")]
        state.log(format!("[MutLen] {mut_len}"));

        let node = output_node
            .find_by_id_mut(array_id)
            .ok_or_else(|| Error::testlang_error("AST metadata is broken"))?;
        let TestLangNodeValue::Group(testlang_nodes) = &mut node.value else {
            return Err(Error::testlang_error("AST metadata is broken"));
        };
        let orig_testlang_nodes_len = testlang_nodes.len();

        // This is safe because mut_len >= 1
        let insert_size_hint = state.max_size().saturating_sub(input_size) / mut_len;
        // Generate new array element
        let record_name_to_generate = &input_metadata.record_ref_name[&array_id];
        for _ in 0..mut_len {
            let node_to_insert = self.generator.generate_record(
                testlang,
                record_name_to_generate,
                0..=insert_size_hint,
                state,
            )?;
            let insertion_index = state.rand.below(testlang_nodes.len() + 1);
            #[cfg(feature = "log")]
            state.log(format!(
                "Added new array element at {insertion_index} \n {:#?}",
                &node_to_insert
            ));
            testlang_nodes.insert(insertion_index, node_to_insert);
        }

        if let Some(associated_size_id) = input_metadata.size_ref.get(&array_id) {
            let Some(associated_size_node) = output_node.find_by_id_mut(*associated_size_id) else {
                return Err(Error::testlang_error("AST metadata is broken"));
            };
            match &mut associated_size_node.value {
                TestLangNodeValue::Int(array_size) => {
                    *array_size = array_size.saturating_add(mut_len as TestLangInt);
                }
                TestLangNodeValue::String(fmt_string) => {
                    let Some(field) = testlang.find_field_by_id(associated_size_node.type_id)
                    else {
                        return Err(Error::testlang_error("AST metadata is broken"));
                    };
                    update_fmt_string_size_dep(
                        fmt_string,
                        field,
                        mut_len as TestLangInt,
                        orig_testlang_nodes_len as TestLangInt,
                    )?;
                }
                _ => {
                    return Err(Error::testlang_error(
                        "Other than integer-like type is rejected for size reference for now",
                    ));
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

impl ArrayElementInsertMutator {
    #[must_use]
    pub fn new() -> Self {
        Self {
            generator: TestLangGenerator::new(),
        }
    }
}

impl TestLangInputMutator for ArrayElementRemoveMutator {
    #[cfg(feature = "log")]
    fn name(&self) -> &str {
        "ArrayElementRemoveMutator"
    }

    fn mutate(
        &mut self,
        state: &mut TestLangState,
        input: &TestLangAst,
        _input_size: usize,
        bytes_output: &mut Vec<u8>,
        metadata_output: &mut Option<TestLangAst>,
    ) -> Result<MutationResult, Error> {
        let input_metadata = &input.metadata;
        let Some(array_id) = state.rand.choose(input_metadata.arrays.iter()).copied() else {
            return Ok(MutationResult::Skipped);
        };

        let mut output_node = input.root.clone();

        let testlang_arc = state.testlang.clone();
        let testlang = testlang_arc.as_ref();
        let node = output_node
            .find_by_id_mut(array_id)
            .ok_or_else(|| Error::testlang_error("AST metadata is broken"))?;
        let TestLangNodeValue::Group(testlang_nodes) = &mut node.value else {
            return Err(Error::testlang_error("AST metadata is broken"));
        };
        if testlang_nodes.is_empty() {
            return Ok(MutationResult::Skipped);
        }

        let orig_testlang_nodes_len = testlang_nodes.len();
        let deletion_index = state.rand.below(orig_testlang_nodes_len);
        testlang_nodes.remove(deletion_index);

        if let Some(associated_size_id) = input_metadata.size_ref.get(&array_id) {
            let Some(associated_size_node) = output_node.find_by_id_mut(*associated_size_id) else {
                return Err(Error::testlang_error("AST metadata is broken"));
            };
            match &mut associated_size_node.value {
                TestLangNodeValue::Int(array_size) => {
                    *array_size = array_size.saturating_sub(1);
                }
                TestLangNodeValue::String(fmt_string) => {
                    let Some(field) = testlang.find_field_by_id(associated_size_node.type_id)
                    else {
                        return Err(Error::testlang_error("AST metadata is broken"));
                    };
                    update_fmt_string_size_dep(
                        fmt_string,
                        field,
                        -1,
                        orig_testlang_nodes_len as TestLangInt,
                    )?;
                }
                _ => {
                    return Err(Error::testlang_error(
                        "Other than integer-like type is rejected for size reference for now",
                    ));
                }
            }
        }

        let mut output = node_to_bytes(testlang, &state.codegen_path, &output_node)?;
        // It shouldn't happen but just in case.
        if output.len() > state.max_size() {
            return Ok(MutationResult::Skipped);
        }
        bytes_output.append(&mut output);
        let new_ast = TestLangAst::new(output_node, testlang)?;
        *metadata_output = Some(new_ast);

        Ok(MutationResult::Mutated)
    }
}

impl ArrayElementRemoveMutator {
    #[must_use]
    pub fn new() -> Self {
        Self
    }
}
