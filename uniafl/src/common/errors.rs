use backtrace::Backtrace;
use cgroups_rs;
use nix::errno::Errno;
use postcard;
use serde_cbor;
use serde_json;
use std::{collections::TryReserveError, fmt, io, path::PathBuf, sync::Arc, time::Duration};

#[macro_export]
macro_rules! symbolic_computation_tree_error {
    ($($arg:tt)*) => {
        // Invokes the associated constructor, which captures a backtrace.
        $crate::common::Error::symbolic_computation_tree_error(format!($($arg)*))
    };
}

#[derive(Debug)]
pub struct Error {
    pub kind: ErrorKind,
    backtrace: Backtrace,
}

#[derive(Debug)]
pub enum ErrorKind {
    ExecutableDoesNotExist {
        path: String,
        harness_type: ExecutableType,
    },
    DirCreationFailed {
        path: String,
        reason: io::Error,
    },
    IOError(io::Error),
    JsonError(serde_json::Error),
    CborError(serde_cbor::Error),
    PostcardError(postcard::Error),
    ExecutionFailed {
        cmd: String,
        returncode: i32,
    },
    MissingTrace {
        harness: String,
        executable_type: ExecutableType,
    },
    InvalidTraceGeneration,
    InvalidData {
        reason: String,
    },
    SymbolicComputationTreeError {
        inner: String,
    },
    LibAFLError {
        inner: libafl::Error,
    },
    ElfError {
        inner: object::Error,
    },
    TryReserveError(TryReserveError),
    TestLangError {
        inner: String,
    },
    CustomGenError(customgen::CustomGenError),
    FuzzedDataProviderError(libfdp::EncoderError),
    TimeoutError {
        cmd: String,
        timeout: Duration,
    },
    SelfCorrectionLogicError {
        reason: String,
    },
    CGroupsError(cgroups_rs::error::Error),
    Other(String),
}

#[derive(Debug)]
pub enum ExecutableType {
    SymCCHarness,
    SymQEMUHarness,
    SymQEMU,
}

impl Error {
    fn new(kind: ErrorKind) -> Self {
        Error {
            kind,
            backtrace: Backtrace::new(),
        }
    }

    pub fn executable_does_not_exist(path: &PathBuf, harness_type: ExecutableType) -> Self {
        Error::new(ErrorKind::ExecutableDoesNotExist {
            path: path.as_os_str().to_str().unwrap().to_owned(),
            harness_type,
        })
    }

    pub fn directory_creation_failed(path: &PathBuf, reason: io::Error) -> Self {
        Error::new(ErrorKind::DirCreationFailed {
            path: path.as_os_str().to_str().unwrap().to_owned(),
            reason,
        })
    }

    pub fn execution_failed(cmd: &str, returncode: i32) -> Self {
        Error::new(ErrorKind::ExecutionFailed {
            cmd: cmd.to_owned(),
            returncode,
        })
    }

    pub fn missing_trace(harness: &PathBuf, executable_type: ExecutableType) -> Self {
        Error::new(ErrorKind::MissingTrace {
            harness: harness.as_os_str().to_str().unwrap().to_owned(),
            executable_type,
        })
    }

    pub fn invalid_trace_generation() -> Self {
        Error::new(ErrorKind::InvalidTraceGeneration)
    }

    pub fn invalid_data<T: AsRef<str>>(reason: T) -> Self {
        Error::new(ErrorKind::InvalidData {
            reason: reason.as_ref().to_owned(),
        })
    }

    pub fn testlang_error<T: AsRef<str>>(reason: T) -> Self {
        Error::new(ErrorKind::TestLangError {
            inner: reason.as_ref().to_owned(),
        })
    }

    pub fn timeout_error(cmd: &str, timeout: Duration) -> Self {
        Error::new(ErrorKind::TimeoutError {
            cmd: cmd.to_owned(),
            timeout,
        })
    }

    pub fn symbolic_computation_tree_error<T: AsRef<str>>(reason: T) -> Self {
        Error::new(ErrorKind::SymbolicComputationTreeError {
            inner: reason.as_ref().to_owned(),
        })
    }

    pub fn self_correction_logic_error<T: AsRef<str>>(reason: T) -> Self {
        Error::new(ErrorKind::SelfCorrectionLogicError {
            reason: reason.as_ref().to_owned(),
        })
    }

    pub fn other<T: AsRef<str>>(reason: T) -> Self {
        Error::new(ErrorKind::Other(reason.as_ref().to_owned()))
    }
}

impl From<io::Error> for Error {
    fn from(err: io::Error) -> Self {
        Error::new(ErrorKind::IOError(err))
    }
}

impl From<serde_json::Error> for Error {
    fn from(err: serde_json::Error) -> Self {
        Error::new(ErrorKind::JsonError(err))
    }
}

impl From<serde_cbor::Error> for Error {
    fn from(err: serde_cbor::Error) -> Self {
        Error::new(ErrorKind::CborError(err))
    }
}

impl From<anyhow::Error> for Error {
    fn from(err: anyhow::Error) -> Self {
        Error::new(ErrorKind::Other(err.to_string()))
    }
}

impl From<libafl::Error> for Error {
    fn from(err: libafl::Error) -> Self {
        Error::new(ErrorKind::LibAFLError { inner: err })
    }
}

impl From<object::Error> for Error {
    fn from(err: object::Error) -> Self {
        Error::new(ErrorKind::ElfError { inner: err })
    }
}

impl From<std::string::FromUtf8Error> for Error {
    fn from(err: std::string::FromUtf8Error) -> Self {
        Error::new(ErrorKind::InvalidData {
            reason: err.to_string(),
        })
    }
}

impl From<std::string::FromUtf16Error> for Error {
    fn from(err: std::string::FromUtf16Error) -> Self {
        Error::new(ErrorKind::InvalidData {
            reason: err.to_string(),
        })
    }
}

impl From<std::num::TryFromIntError> for Error {
    fn from(err: std::num::TryFromIntError) -> Self {
        Error::new(ErrorKind::InvalidData {
            reason: err.to_string(),
        })
    }
}

impl From<std::array::TryFromSliceError> for Error {
    fn from(err: std::array::TryFromSliceError) -> Self {
        Error::new(ErrorKind::InvalidData {
            reason: err.to_string(),
        })
    }
}

impl From<std::num::ParseIntError> for Error {
    fn from(err: std::num::ParseIntError) -> Self {
        Error::new(ErrorKind::InvalidData {
            reason: err.to_string(),
        })
    }
}

impl From<libfdp::EncoderError> for Error {
    fn from(err: libfdp::EncoderError) -> Self {
        Error::new(ErrorKind::FuzzedDataProviderError(err))
    }
}

impl From<customgen::CustomGenError> for Error {
    fn from(err: customgen::CustomGenError) -> Self {
        Error::new(ErrorKind::CustomGenError(err))
    }
}

impl From<testlang::TestLangError> for Error {
    fn from(err: testlang::TestLangError) -> Self {
        Error::new(ErrorKind::TestLangError {
            inner: err.to_string(),
        })
    }
}

impl From<Errno> for Error {
    fn from(err: Errno) -> Self {
        Error::new(ErrorKind::IOError(io::Error::from_raw_os_error(err as i32)))
    }
}

impl From<postcard::Error> for Error {
    fn from(err: postcard::Error) -> Self {
        Error::new(ErrorKind::PostcardError(err))
    }
}

impl From<gimli::Error> for Error {
    fn from(err: gimli::Error) -> Self {
        Error::new(ErrorKind::InvalidData {
            reason: format!("DWARF parsing error: {}", err),
        })
    }
}

impl<T> From<Arc<T>> for Error
where
    T: fmt::Debug,
{
    fn from(err: Arc<T>) -> Self {
        Error::new(ErrorKind::Other(format!("{:?}", err)))
    }
}

impl From<TryReserveError> for Error {
    fn from(err: TryReserveError) -> Self {
        Error::new(ErrorKind::TryReserveError(err))
    }
}

impl From<cgroups_rs::error::Error> for Error {
    fn from(err: cgroups_rs::error::Error) -> Self {
        Error::new(ErrorKind::CGroupsError(err))
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        // first print a human‐friendly message
        match &self.kind {
            ErrorKind::ExecutableDoesNotExist { path, .. } => {
                write!(f, "Harness does not exist at {}", path)?
            }
            ErrorKind::DirCreationFailed { path, reason } => {
                write!(f, "Failed to create work dir at {}: {}", path, reason)?
            }
            ErrorKind::ExecutionFailed { cmd, returncode } => write!(
                f,
                "Execution of \"{}\" failed with code {}",
                cmd, returncode
            )?,
            ErrorKind::MissingTrace { harness, .. } => {
                write!(f, "Missing trace for harness at {}", harness)?
            }
            ErrorKind::InvalidTraceGeneration => write!(f, "Invalid trace generation")?,
            ErrorKind::IOError(err) => write!(f, "IO error: {}", err)?,
            ErrorKind::JsonError(err) => write!(f, "JSON error: {}", err)?,
            ErrorKind::CborError(err) => write!(f, "CBOR error: {}", err)?,
            ErrorKind::PostcardError(err) => write!(f, "Postcard error: {}", err)?,
            ErrorKind::InvalidData { reason } => write!(f, "Invalid data: {}", reason)?,
            ErrorKind::LibAFLError { inner } => write!(f, "LibAFL error: {}", inner)?,
            ErrorKind::ElfError { inner } => write!(f, "ELF error: {}", inner)?,
            ErrorKind::TryReserveError(err) => write!(f, "TryReserve error: {}", err)?,
            ErrorKind::TestLangError { inner } => write!(f, "TestLang error: {}", inner)?,
            ErrorKind::CustomGenError(err) => write!(f, "CustomGen error: {}", err)?,
            ErrorKind::TimeoutError { cmd, timeout } => {
                write!(f, "Execution of \"{}\" timed out after {:?}", cmd, timeout)?
            }
            ErrorKind::FuzzedDataProviderError(err) => {
                write!(f, "FuzzedDataProvider error: {}", err)?
            }
            ErrorKind::SymbolicComputationTreeError { inner } => {
                write!(f, "Symbolic computation tree error: {}", inner)?
            }
            ErrorKind::SelfCorrectionLogicError { reason } => {
                write!(f, "Self-correction logic error: {}", reason)?
            }
            ErrorKind::CGroupsError(err) => write!(f, "cgroups error: {}", err)?,
            ErrorKind::Other(msg) => write!(f, "{}", msg)?,
        };

        // then always append the backtrace
        write!(f, "\n\nBacktrace:\n{:?}", self.backtrace)
    }
}
