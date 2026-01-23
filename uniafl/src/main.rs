use clap::Parser;
use std::path::PathBuf;

mod common;
mod executor;
mod input_gen;
mod msa;

#[derive(Parser)]
#[command(author = "Multilang team @Team Atlanta")]
struct Args {
    #[arg(short = 'c', long = "config")]
    config: String,

    #[arg(short, long)]
    executor_mode: bool,
}

fn main() {
    let args = Args::parse();
    let conf = PathBuf::from(&args.config);
    if !conf.exists() {
        panic!("{} does not exist", args.config);
    }
    if args.executor_mode {
        msa::execute_one_by_one(&conf);
    } else {
        msa::start_fuzz_loop(&conf);
    }
}
