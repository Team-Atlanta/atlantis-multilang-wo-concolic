mod db;
mod utils;
pub use db::{Cov, CovItem, FuncName, FuzzDB, Language, LinePos, MatchResult};

#[cfg(test)]
mod tests;
