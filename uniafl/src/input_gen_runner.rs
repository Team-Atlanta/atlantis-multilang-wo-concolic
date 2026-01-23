use clap::Parser;
use std::path::PathBuf;

mod common;
#[allow(dead_code)]
mod executor;
mod input_gen;
#[allow(dead_code)]
mod msa;

use input_gen::{
    client::InputGenClient,
    dict::service::DictPool,
    mock_service::MockPool,
    server::{run_server, InputGenPool},
    testlang::service::pool::TestLangPool,
};

#[derive(Parser)]
#[command(author = "Multilang team @Team Atlanta")]
struct Args {
    #[arg(short = 'c', long = "config")]
    config: String,

    #[arg(short = 's', long = "service")]
    service: String,

    #[arg(short = 'D', long = "debug")]
    debug: bool,

    #[arg(short = 'S', long = "seed")]
    seed: Option<String>,

    #[arg(short = 'r', long = "reset")]
    reset: bool,

    #[arg(short = 'w', long = "worker-idx")]
    worker_idx: Option<u32>,

    #[arg(short = 'e', long = "end-worker-idx")]
    end_worker_idx: Option<u32>,
}

macro_rules! match_and_run {
    ($name: expr,$pool: ty, $args: expr) => {
        if $name == <$pool>::name() {
            run_server_or_debug::<$pool>($args)
        }
    };
}

fn run_server_or_debug<I: InputGenPool>(args: &Args) {
    let conf = PathBuf::from(&args.config);
    if !conf.exists() {
        panic!("{} does not exist", args.config);
    }

    if args.debug {
        println!("Running in debug mode");
        let seed_bytes = if let Some(seed) = &args.seed {
            std::fs::read(seed).expect("Failed to read seed file")
        } else {
            panic!("Seed file is required for debug mode");
        };
        InputGenClient::debug::<I>(&conf, &seed_bytes);
    } else {
        run_server::<I>(&conf, args.worker_idx, args.end_worker_idx, args.reset);
    }
}

fn main() {
    let args = Args::parse();
    match_and_run!(args.service, MockPool, &args);
    match_and_run!(args.service, TestLangPool, &args);
    match_and_run!(args.service, DictPool, &args);
}
