use std::{fs, path::Path, process::Command};

use tempfile::tempdir;

use crate::common::Error;

pub fn run_encoding_processor(
    generated_processor_dir: impl AsRef<Path>,
    encoder_id: &str,
    bytes: &[u8],
) -> Result<Vec<u8>, Error> {
    if encoder_id.contains(".") {
        return Err(Error::testlang_error(format!(
            "Encoder class containing invalid character `.`: {}",
            encoder_id
        )));
    }
    let tmp_dir = tempdir()?;
    let tmp_path = tmp_dir.path();
    let input_file = tmp_path.join("input");
    fs::write(&input_file, bytes)?;
    let output_file = tmp_path.join("output");
    let cmd_output = Command::new("python")
        .args([
            "-m",
            "testlang.processing.run",
            "-i",
            &input_file.to_string_lossy(),
            "-o",
            &output_file.to_string_lossy(),
            "-p",
            &generated_processor_dir.as_ref().to_string_lossy(),
            &format!("{}.{}", encoder_id, encoder_id),
        ])
        .output()?;
    if cmd_output.status.success() {
        Ok(fs::read(output_file)?)
    } else {
        Err(Error::testlang_error(format!(
            "Failed to process encoding.\nSTDOUT:\n{}\nSTDERR:\n{}\n",
            String::from_utf8_lossy(&cmd_output.stdout),
            String::from_utf8_lossy(&cmd_output.stderr),
        )))
    }
}
