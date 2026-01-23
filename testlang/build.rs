use std::{
    fs::OpenOptions,
    io::{self, Read, Write},
    process::{Command, Stdio},
};

fn rustfmt_generated_string(source: &str) -> String {
    let rustfmt = "rustfmt";
    let mut cmd = Command::new(rustfmt);

    cmd.stdin(Stdio::piped()).stdout(Stdio::piped());
    let mut child = cmd.spawn().unwrap();
    let mut child_stdin = child.stdin.take().unwrap();
    let mut child_stdout = child.stdout.take().unwrap();

    let source = source.to_owned();

    // Write to stdin in a new thread, so that we can read from stdout on this
    // thread. This keeps the child from blocking on writing to its stdout which
    // might block us from writing to its stdin.
    let stdin_handle = ::std::thread::spawn(move || {
        let _ = child_stdin.write_all(source.as_bytes());
        source
    });

    let mut output = vec![];
    io::copy(&mut child_stdout, &mut output).unwrap();

    let status = child.wait().unwrap();
    let source = stdin_handle.join().expect(
        "The thread writing to rustfmt's stdin doesn't do \
            anything that could panic",
    );

    match String::from_utf8(output) {
        Ok(bindings) => match status.code() {
            Some(0) => bindings,
            Some(2) => panic!("Rustfmt parse error"),
            Some(3) => bindings,
            _ => panic!("Rustfmt internal error"),
        },
        _ => source,
    }
}

fn main() {
    const CUSTOM_IDS: &str = "res/custom_ids.json";
    const CUSTOM_IDS_CODE: &str = "src/custom.rs";
    println!("cargo::rerun-if-changed={}", CUSTOM_IDS);
    let mut custom_id_file = OpenOptions::new().read(true).open(CUSTOM_IDS).unwrap();
    let mut custom_id_contents = String::new();
    custom_id_file
        .read_to_string(&mut custom_id_contents)
        .unwrap();
    let _values: Vec<String> = serde_json::from_str(&custom_id_contents).unwrap();

    let mut custom_id_codegen = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(CUSTOM_IDS_CODE)
        .unwrap();
    let code = rustfmt_generated_string(&format!(
        "pub static TYPE_IDS: &[&str] = &{};\n",
        custom_id_contents
    ));
    custom_id_codegen.write_all(code.as_bytes()).unwrap();
}
