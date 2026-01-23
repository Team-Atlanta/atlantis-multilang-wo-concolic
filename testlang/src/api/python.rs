use std::collections::{HashMap, HashSet};
use std::str::FromStr;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::wrap_pyfunction;

use crate::get_testlang_schema;
use crate::{TestLang, TestLangError, TestLangWarning, RECORD_INPUT};

#[pyclass]
struct PyTestLangWarning {
    #[pyo3(get)]
    kind: String,
    #[pyo3(get)]
    message: String,
    #[pyo3(get)]
    record: Option<String>,
    #[pyo3(get)]
    field: Option<String>,
}

#[pymethods]
impl PyTestLangWarning {
    #[new]
    #[pyo3(signature = (kind, message, record=None, field=None))]
    fn new(kind: String, message: String, record: Option<String>, field: Option<String>) -> Self {
        PyTestLangWarning {
            kind,
            message,
            record,
            field,
        }
    }

    fn __str__(&self) -> String {
        format!("[{}] {}", self.kind, self.message)
    }
}

impl From<TestLangWarning> for PyTestLangWarning {
    fn from(warning: TestLangWarning) -> Self {
        match &warning {
            TestLangWarning::UnexpectedAttribute { record, field, .. } => PyTestLangWarning {
                kind: "UnexpectedAttribute".to_owned(),
                message: warning.to_string(),
                record: Some(record.clone()),
                field: Some(field.clone()),
            },
            TestLangWarning::MissingEndian { record, field } => PyTestLangWarning {
                kind: "MissingEndian".to_owned(),
                message: warning.to_string(),
                record: Some(record.clone()),
                field: Some(field.clone()),
            },
            TestLangWarning::MissingPossibleValues { record, field } => PyTestLangWarning {
                kind: "MissingPossibleValues".to_owned(),
                message: warning.to_string(),
                record: Some(record.clone()),
                field: Some(field.clone()),
            },
            TestLangWarning::MissingTerminator { record, field } => PyTestLangWarning {
                kind: "MissingTerminator".to_owned(),
                message: warning.to_string(),
                record: Some(record.clone()),
                field: Some(field.clone()),
            },
            TestLangWarning::JustRandomBytes { record, field } => PyTestLangWarning {
                kind: "JustRandomBytes".to_owned(),
                message: warning.to_string(),
                record: Some(record.clone()),
                field: Some(field.clone()),
            },
            TestLangWarning::MaybeCustomFDP => PyTestLangWarning {
                kind: "MaybeCustomFDP".to_owned(),
                message: warning.to_string(),
                record: None,
                field: None,
            },
            TestLangWarning::MaybeSelector { record, field } => PyTestLangWarning {
                kind: "MaybeSelector".to_owned(),
                message: warning.to_string(),
                record: Some(record.clone()),
                field: Some(field.clone()),
            },
            TestLangWarning::MaybeIntString { record, field } => PyTestLangWarning {
                kind: "MaybeIntString".to_owned(),
                message: warning.to_string(),
                record: Some(record.clone()),
                field: Some(field.clone()),
            },
            TestLangWarning::TediousGenerator { record, field, .. } => PyTestLangWarning {
                kind: "TediousGenerator".to_owned(),
                message: warning.to_string(),
                record: Some(record.clone()),
                field: Some(field.clone()),
            },
            TestLangWarning::MissingCallee { record, .. } => PyTestLangWarning {
                kind: "MissingCallee".to_owned(),
                message: warning.to_string(),
                record: Some(record.clone()),
                field: None,
            },
            TestLangWarning::InvalidLocation { record, .. } => PyTestLangWarning {
                kind: "InvalidLocation".to_owned(),
                message: warning.to_string(),
                record: Some(record.clone()),
                field: None,
            },
            TestLangWarning::MissingCalleeInLocation { record, .. } => PyTestLangWarning {
                kind: "MissingCalleeInLocation".to_owned(),
                message: warning.to_string(),
                record: Some(record.clone()),
                field: None,
            },
            TestLangWarning::RecursiveFunction { record, .. } => PyTestLangWarning {
                kind: "RecursiveFunction".to_owned(),
                message: warning.to_string(),
                record: Some(record.clone()),
                field: None,
            },
            TestLangWarning::MaybeWrongFDPRangedSizeDescriptor { record, field } => {
                PyTestLangWarning {
                    kind: "MaybeWrongFDPRangedSizeDescriptor".to_owned(),
                    message: warning.to_string(),
                    record: Some(record.clone()),
                    field: Some(field.clone()),
                }
            }
        }
    }
}

#[pymodule]
fn testlang(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyTestLangWarning>()?;
    m.add_function(wrap_pyfunction!(schema, m)?)?;
    m.add_function(wrap_pyfunction!(validate, m)?)?;
    m.add_function(wrap_pyfunction!(normalize, m)?)?;
    m.add_function(wrap_pyfunction!(hash, m)?)?;
    m.add_function(wrap_pyfunction!(update, m)?)?;
    m.add_function(wrap_pyfunction!(visualize, m)?)?;
    Ok(())
}

#[pyfunction]
fn schema() -> PyResult<String> {
    Ok(get_testlang_schema().to_owned())
}

#[pyfunction]
fn validate(testlang: String, python_codes: HashSet<String>) -> PyResult<Vec<PyTestLangWarning>> {
    let mut testlang = TestLang::from_str(testlang.as_str())?;
    testlang
        .warnings
        .extend(testlang.validate_python_codes(&python_codes)?);
    Ok(testlang
        .warnings
        .iter()
        .map(|warning| PyTestLangWarning::from(warning.clone()))
        .collect())
}

#[pyfunction]
fn normalize(testlang: String) -> PyResult<String> {
    let testlang = TestLang::from_str(testlang.as_str())?
        .unroll()?
        .flatten()?
        .normalize()?;
    Ok(testlang.to_string())
}

#[pyfunction]
fn hash(testlang: String) -> PyResult<u64> {
    let testlang = TestLang::from_str(testlang.as_str())?;
    let hashes = testlang.hash()?;
    let hash = hashes
        .get(RECORD_INPUT)
        .ok_or_else(|| TestLangError::InvalidSemantics {
            error: "Failed to hash.".to_owned(),
            record: Some(RECORD_INPUT.to_owned()),
            field: None,
        })?;
    Ok(*hash)
}

#[pyfunction]
fn update(
    testlang: String,
    partial_testlang: String,
    records_to_remove: HashSet<String>,
) -> PyResult<String> {
    let testlang = TestLang::from_str(testlang.as_str())?;
    let partial_testlang = TestLang::from_str(partial_testlang.as_str())?;
    Ok(testlang
        .update(&partial_testlang, &records_to_remove)?
        .to_string())
}

#[pyfunction]
fn visualize(
    testlang: String,
    python_codes: HashMap<String, String>,
    output_path: String,
) -> PyResult<()> {
    TestLang::from_str(testlang.as_str())?.visualize(&python_codes, &output_path)?;
    Ok(())
}

impl From<TestLangError> for PyErr {
    fn from(err: TestLangError) -> PyErr {
        PyValueError::new_err(err.to_string())
    }
}
