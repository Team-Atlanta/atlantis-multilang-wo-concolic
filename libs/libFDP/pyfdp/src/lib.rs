use pyo3::{exceptions::PyValueError, prelude::*};
use rlibfdp::EncoderError;

pub use jazzer::JazzerFdpEncoder;
pub use llvm::LlvmFdpEncoder;

pub mod jazzer;
pub mod llvm;

fn convert_error(err: EncoderError) -> PyErr {
    PyValueError::new_err(err.to_string())
}

#[pymodule]
fn libfdp(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<LlvmFdpEncoder>()?;
    m.add_class::<JazzerFdpEncoder>()?;
    Ok(())
}
