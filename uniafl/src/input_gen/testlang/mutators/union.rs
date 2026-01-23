use libafl::{mutators::MutationResult, state::HasMaxSize};
use libafl_bolts::rands::Rand;
use testlang::{RefKind, TestLangAst, TestLangNodeValue};

use crate::{
    common::Error,
    input_gen::testlang::{
        generators::TestLangGenerator, node_to_bytes, service::worker::TestLangState,
        TestLangInputMutator,
    },
};

pub struct UnionMutator {
    generator: TestLangGenerator,
}

impl TestLangInputMutator for UnionMutator {
    #[cfg(feature = "log")]
    fn name(&self) -> &str {
        "UnionMutator"
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
        let Some(union_id) = state.rand.choose(input_metadata.unions.iter()).copied() else {
            return Ok(MutationResult::Skipped);
        };
        let mut output_node = input.root.clone();
        let Some(union_node) = output_node.find_by_id_mut(union_id) else {
            return Err(Error::testlang_error("AST metadata is broken"));
        };

        // Generate new union element
        let testlang_arc = state.testlang.clone();
        let testlang = testlang_arc.as_ref();

        let Some(union_record) = testlang.find_record_by_id(union_node.type_id) else {
            return Err(Error::testlang_error("AST metadata is broken"));
        };
        let union_components: Vec<_> = union_record
            .fields
            .iter()
            .enumerate()
            .filter_map(|(i, x)| {
                x.items.as_ref().and_then(|component_ref| {
                    if component_ref.kind == RefKind::Record {
                        Some((i, &component_ref.name))
                    } else {
                        None
                    }
                })
            })
            .collect();

        let TestLangNodeValue::Union(union_idx, union_choice) = &mut union_node.value else {
            return Err(Error::testlang_error("AST metadata is broken"));
        };

        if union_components.len() < 2 {
            return Ok(MutationResult::Skipped);
        }

        let idx = state.rand.below(union_components.len() - 1);
        let idx = if idx >= *union_idx {
            idx + 1 // Ensure we don't select the current index
        } else {
            idx
        };
        let Some((idx, new_component_name)) = union_components.get(idx) else {
            return Ok(MutationResult::Skipped);
        };

        let node_to_replace = self.generator.generate_record(
            testlang,
            new_component_name,
            0..=state.max_size(),
            state,
        )?;
        #[cfg(feature = "log")]
        state.log(format!(
            "[AST][Before][{}]\n{:#?}",
            *union_idx, union_choice
        ));
        #[cfg(feature = "log")]
        state.log(format!("[AST][After][{}]\n{:#?}", *idx, &node_to_replace));
        *union_idx = *idx;
        *union_choice = Box::new(node_to_replace);

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

impl UnionMutator {
    #[must_use]
    pub fn new() -> Self {
        Self {
            generator: TestLangGenerator::new(),
        }
    }
}
