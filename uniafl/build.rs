fn main() {
    // Use the cc crate to compile the C code
    cc::Build::new()
        .file("src/msa/manager/manager.c") // Path to your C file
        .include("src/msa/manager") // Include directory for headers
        .compile("manager"); // Name of the output library

    let lib_path = format!(
        "{}/../../../libmanager.so",
        std::env::var("OUT_DIR").unwrap()
    );
    std::process::Command::new("gcc")
        .args(&[
            "-shared",
            "-fPIC",
            "src/msa/manager/manager.c",
            "-lrt",
            "-m64",
            "-o",
            lib_path.as_str(),
        ])
        .status()
        .expect("Failed to compile shared library");

    println!("cargo:rerun-if-changed=src/msa/manager.c");
    println!("cargo:rerun-if-changed=src/msa/manager.h");
    println!("cargo:link=static=manager");
}
