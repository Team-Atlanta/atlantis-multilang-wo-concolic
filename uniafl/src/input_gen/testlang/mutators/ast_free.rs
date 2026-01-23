use std::fmt;

use libafl::{mutators::MutationResult, state::HasMaxSize};
use libafl_bolts::rands::Rand;

use crate::{
    common::Error,
    input_gen::testlang::{
        generators::TestLangGenerator, node_to_bytes, service::worker::TestLangState,
        TestLangAstFreeInputMutator,
    },
};

enum MutationPosition {
    Front,
    Middle,
    End,
}

impl fmt::Display for MutationPosition {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let enum_variant = match self {
            MutationPosition::Front => "Front",
            MutationPosition::Middle => "Middle",
            MutationPosition::End => "End",
        };
        write!(f, "{}", enum_variant)
    }
}

pub struct RecordInsertMutator {
    #[cfg(feature = "log")]
    name: String,
    position: MutationPosition,
    generator: TestLangGenerator,
}

impl TestLangAstFreeInputMutator for RecordInsertMutator {
    #[cfg(feature = "log")]
    fn name(&self) -> &str {
        &self.name
    }

    fn mutate(
        &mut self,
        state: &mut TestLangState,
        input: &[u8],
        bytes_output: &mut Vec<u8>,
    ) -> Result<MutationResult, Error> {
        let testlang_arc = state.testlang.clone();
        let testlang = testlang_arc.as_ref();
        let Some(record) = state.rand.choose(&testlang.records) else {
            return Ok(MutationResult::Skipped);
        };

        if input.len() >= state.max_size() {
            return Ok(MutationResult::Skipped);
        }

        let max_size = state.max_size() - input.len();
        let node_to_insert =
            match self
                .generator
                .generate_record(testlang, &record.name, 0..=max_size, state)
            {
                Ok(v) => v,
                Err(_) => return Ok(MutationResult::Skipped),
            };

        let mut output = node_to_bytes(testlang, &state.codegen_path, &node_to_insert)?;
        if output.len() > max_size {
            return Ok(MutationResult::Skipped);
        }

        match self.position {
            MutationPosition::Front => {
                bytes_output.append(&mut output);
                bytes_output.extend_from_slice(input);
            }
            MutationPosition::Middle => {
                let Some(insert_pos) = state.rand.choose(0..=input.len()) else {
                    return Ok(MutationResult::Skipped);
                };
                bytes_output.extend_from_slice(&input[..insert_pos]);
                bytes_output.append(&mut output);
                bytes_output.extend_from_slice(&input[insert_pos..]);
            }
            MutationPosition::End => {
                bytes_output.extend_from_slice(input);
                bytes_output.append(&mut output);
            }
        }
        Ok(MutationResult::Mutated)
    }
}

impl RecordInsertMutator {
    fn new(position: MutationPosition) -> Self {
        #[cfg(feature = "log")]
        let name = format!("RecordInsertMutator({})", position.to_string());
        Self {
            #[cfg(feature = "log")]
            name,
            position,
            generator: TestLangGenerator::new(),
        }
    }
}

pub struct RecordReplaceMutator {
    #[cfg(feature = "log")]
    name: String,
    position: MutationPosition,
    generator: TestLangGenerator,
}

impl TestLangAstFreeInputMutator for RecordReplaceMutator {
    #[cfg(feature = "log")]
    fn name(&self) -> &str {
        &self.name
    }

    fn mutate(
        &mut self,
        state: &mut TestLangState,
        input: &[u8],
        bytes_output: &mut Vec<u8>,
    ) -> Result<MutationResult, Error> {
        let testlang_arc = state.testlang.clone();
        let testlang = testlang_arc.as_ref();
        let Some(record) = state.rand.choose(&testlang.records) else {
            return Ok(MutationResult::Skipped);
        };

        let replacement_range = match self.position {
            MutationPosition::Front => {
                let Some(end_pos) = state.rand.choose(0..=input.len()) else {
                    return Ok(MutationResult::Skipped);
                };
                0..end_pos
            }
            MutationPosition::Middle => {
                let (Some(a), Some(b)) = (
                    state.rand.choose(0..=input.len()),
                    state.rand.choose(0..=input.len()),
                ) else {
                    return Ok(MutationResult::Skipped);
                };
                let mut pos = [a, b];
                pos.sort();
                pos[0]..pos[1]
            }
            MutationPosition::End => {
                let Some(start_pos) = state.rand.choose(0..=input.len()) else {
                    return Ok(MutationResult::Skipped);
                };
                start_pos..input.len()
            }
        };
        let freed_size = replacement_range.end - replacement_range.start;

        if (input.len() - freed_size) > state.max_size() {
            return Ok(MutationResult::Skipped);
        }

        let max_size = state.max_size() - (input.len() - freed_size);
        let node_to_insert =
            self.generator
                .generate_record(testlang, &record.name, 0..=max_size, state)?;

        let mut output = node_to_bytes(testlang, &state.codegen_path, &node_to_insert)?;
        if output.len() > max_size {
            return Ok(MutationResult::Skipped);
        }

        match self.position {
            MutationPosition::Front => {
                bytes_output.append(&mut output);
                bytes_output.extend_from_slice(&input[replacement_range.end..]);
            }
            MutationPosition::Middle => {
                bytes_output.extend_from_slice(&input[..replacement_range.start]);
                bytes_output.append(&mut output);
                bytes_output.extend_from_slice(&input[replacement_range.end..]);
            }
            MutationPosition::End => {
                bytes_output.extend_from_slice(&input[..replacement_range.start]);
                bytes_output.append(&mut output);
            }
        }
        Ok(MutationResult::Mutated)
    }
}

impl RecordReplaceMutator {
    fn new(position: MutationPosition) -> Self {
        #[cfg(feature = "log")]
        let name = format!("RecordReplaceMutator({})", position.to_string());
        Self {
            #[cfg(feature = "log")]
            name,
            position,
            generator: TestLangGenerator::new(),
        }
    }
}

#[must_use]
pub fn new_record_insert_front() -> RecordInsertMutator {
    RecordInsertMutator::new(MutationPosition::Front)
}

#[must_use]
pub fn new_record_insert_middle() -> RecordInsertMutator {
    RecordInsertMutator::new(MutationPosition::Middle)
}

#[must_use]
pub fn new_record_insert_end() -> RecordInsertMutator {
    RecordInsertMutator::new(MutationPosition::End)
}

#[must_use]
pub fn new_record_replace_front() -> RecordReplaceMutator {
    RecordReplaceMutator::new(MutationPosition::Front)
}

#[must_use]
pub fn new_record_replace_middle() -> RecordReplaceMutator {
    RecordReplaceMutator::new(MutationPosition::Middle)
}

#[must_use]
pub fn new_record_replace_end() -> RecordReplaceMutator {
    RecordReplaceMutator::new(MutationPosition::End)
}
