use std::{mem, ops::DerefMut};

use crate::{common::Error, input_gen::testlang::service::worker::CustomGenRuntime};

pub fn generate_one(
    customgen: &mut CustomGenRuntime,
    generator_id: &str,
) -> Result<Vec<u8>, Error> {
    let runtime = &customgen.runtime;
    let client = &mut customgen.client;
    match runtime
        .block_on(client.generate(generator_id, 1))?
        .deref_mut()
    {
        [] => Err(Error::testlang_error(
            "custom generation failed: output is empty",
        )),
        [out, ..] => Ok(mem::take(out)),
    }
}
