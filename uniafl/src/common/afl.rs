#![allow(unused)]

use libafl::{
    corpus::testcase::Testcase,
    inputs::{HasMutatorBytes, Input},
    Error,
};
use libafl_bolts::HasLen;
use md5;

pub type InputID = u128;
pub type BlockAddr = u64;

#[allow(dead_code)]
pub fn testcase_mut_len<I>(tc: &mut Testcase<I>) -> usize
where
    I: Input + HasLen,
{
    tc.input()
        .as_ref()
        .expect("Fail to get input from tc")
        .len()
}

pub fn new_err(s: &str) -> Error {
    std::io::Error::new(std::io::ErrorKind::Other, s).into()
}

pub fn get_input_id<I: Input + HasMutatorBytes>(input: &I) -> InputID {
    let digest = md5::compute(input.bytes());
    InputID::from_be_bytes(digest.0)
}
