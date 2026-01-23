#[cfg(test)]
mod tests {
    use crate::input_gen::dict::{dictgen::DictGen, service::DictMutator};
    use std::collections::{HashMap, HashSet};
    use std::env;
    use std::fs::canonicalize;
    use std::path::PathBuf;
    use std::str;
    use std::sync::{Arc, Mutex};

    // fn mock_c_check(out_buf: &ManuallyDrop<Vec<u8>>) -> Result<(), anyhow::Error> {
    //     if out_buf.len() > 0x40 && out_buf[0] == b'A' {
    //         Ok(())
    //     } else {
    //         Err(anyhow::Error::msg("Failed to trigger mock_c's target_1"))
    //     }
    // }

    // #[test]
    // fn test_dict_input_mut() {
    //     assert!(false);
    //     match env::var("DICTGEN_PATH") {
    //         Ok(_) => {}
    //         Err(_) => {
    //             eprintln!("DICTGEN_PATH environment variable is not set.");
    //             return;
    //         }
    //     }
    //     let dictgen_path = env::var("DICTGEN_PATH").unwrap();
    //     let workdir = "/tmp/workdir/dictgen".to_string();
    //     let mut dictgen = DictGen::new(&dictgen_path, &workdir);
    //     let mut mutator = DictMutator::new();

    //     let cov = Cov::new();
    //     cov.insert(
    //         "target_1".to_string(),
    //         CovItem {
    //             src: "src/input_gen/dict/tests".to_string(),
    //             lines: Vec::new(),
    //         },
    //     );

    //     for func in cov.keys() {
    //         match canonicalize(func) {
    //             Ok(absolute_path) => {
    //                 env::set_var("CP_SRC_PATH", absolute_path);
    //             }
    //             Err(e) => {
    //                 eprintln!("Failed to convert to absolute path: {}", e);
    //                 return;
    //             }
    //         }

    //         if let Some(tokens) = dictgen.generate_tokens(func) {
    //             assert!(tokens.len() > 0);

    //             let initial_input = MsaSeed {
    //                 bytes: vec![0x00, 0x01, 0x02],
    //                 fname: "foo".to_string(),
    //                 id: 0,
    //             };

    //             let mut rand = StdRand::with_seed(0);
    //             let mut found = false;
    //             for _ in 0..1000 {
    //                 let out_buf = ManuallyDrop::new(Vec::new());
    //                 mutator.__mutate_seed(&initial_input, &tokens, &mut out_buf);
    //                 eprintln!("{:?}", out_buf);
    //                 match mock_c_check(&out_buf) {
    //                     Ok(()) => {
    //                         found = true;
    //                         break;
    //                     }
    //                     Err(_) => {}
    //                 }
    //                 unsafe {
    //                     ManuallyDrop::drop(&mut out_buf);
    //                 }
    //             }
    //             assert!(found);
    //         }
    //     }
    // }

    #[derive(Debug)]
    struct TestCase {
        directory: PathBuf,
        functions: Vec<String>,
        answers: HashSet<Vec<u8>>,
    }

    fn get_basic_tests(test_directory: &PathBuf) -> HashMap<String, TestCase> {
        let mut tests = HashMap::new();
        tests.insert(
            "c".to_string(),
            TestCase {
                directory: test_directory.join("c"),
                functions: vec!["foo".to_string()],
                answers: ["42", "66", "67", "token", "token2"]
                    .iter()
                    .map(|s| s.as_bytes().to_vec())
                    .collect(),
            },
        );
        tests.insert(
            "java".to_string(),
            TestCase {
                directory: test_directory.join("java"),
                functions: vec!["foo".to_string()],
                answers: ["42", "66", "67", "token", "token2", "magic"]
                    .iter()
                    .map(|s| s.as_bytes().to_vec())
                    .collect(),
            },
        );
        tests.insert(
            "python".to_string(),
            TestCase {
                directory: test_directory.join("python"),
                functions: vec!["foo".to_string()],
                answers: ["0xbeaf", "token1", "token2", "token3"]
                    .iter()
                    .map(|s| s.as_bytes().to_vec())
                    .collect(),
            },
        );
        tests.insert(
            "go".to_string(),
            TestCase {
                directory: test_directory.join("go"),
                functions: vec!["foo".to_string()],
                answers: ["42", "43", "token", "token2", "token3", "t", "66", "67"]
                    .iter()
                    .map(|s| s.as_bytes().to_vec())
                    .collect(),
            },
        );
        tests
    }

    #[test]
    fn test_dictgen() {
        match env::var("DICTGEN_PATH") {
            Ok(_) => {}
            Err(_) => {
                eprintln!("DICTGEN_PATH environment variable is not set.");
                return;
            }
        }
        let dictgen_path = env::var("DICTGEN_PATH").unwrap();
        let workdir = "/tmp/workdir/dictgen".to_string();
        let test_directory = PathBuf::from("../dictgen/tests");
        let basic_tests = get_basic_tests(&test_directory);
        for (language, test_case) in basic_tests {
            eprintln!("Language: {}", language);
            eprintln!("Directory: {:?}", test_case.directory);
            eprintln!("Functions: {:?}", test_case.functions);
            eprintln!("Answers: {:?}", test_case.answers);
            let dictgen = DictGen::new(&dictgen_path, &workdir, 1000, false);

            match canonicalize(&test_case.directory) {
                Ok(absolute_path) => {
                    env::set_var("CP_SRC_PATH", absolute_path);
                }
                Err(e) => {
                    eprintln!("Failed to convert to absolute path: {}", e);
                    continue;
                }
            }

            eprintln!("CP_SRC_PATH: {:?}", env::var("CP_SRC_PATH"));
            for func in &test_case.functions {
                let tokens = dictgen.generate_tokens(func);
                assert_eq!(tokens.is_some(), true);
                let tokens = tokens.unwrap();
                assert_ne!(tokens.len(), 0);
                check_answer(tokens, &test_case.answers);
            }
        }
    }

    fn check_answer(tokens: HashSet<Vec<u8>>, answers: &HashSet<Vec<u8>>) {
        let mut converted_tokens = HashSet::new();
        for token in &tokens {
            // Convert Vec<u8> to String
            if let Ok(token_str) = str::from_utf8(token) {
                if let Some(stripped) = token_str.strip_prefix("0x") {
                    if let Ok(decimal_value) = u64::from_str_radix(stripped, 16) {
                        converted_tokens.insert(decimal_value.to_string());
                    } else {
                        converted_tokens.insert(token_str.to_string());
                    }
                } else {
                    converted_tokens.insert(token_str.to_string());
                }
            }
        }

        for answer in answers {
            if let Ok(answer_str) = str::from_utf8(answer) {
                if let Ok(num) = answer_str.parse::<u64>() {
                    assert!(converted_tokens.contains(&num.to_string()));
                } else if let Some(hex_num) = answer_str
                    .strip_prefix("0x")
                    .and_then(|s| u64::from_str_radix(s, 16).ok())
                {
                    assert!(converted_tokens.contains(&hex_num.to_string()));
                } else {
                    assert!(converted_tokens.contains(answer_str));
                }
            }
        }
    }

    #[test]
    fn test_parse_output_to_token() {
        struct TestCase {
            output: String,
            expected: Option<Vec<u8>>,
        }

        let test_cases = vec![
            TestCase {
                output: "str0=\"\x01AAAAA\"".to_string(),
                expected: Some(vec![1, 65, 65, 65, 65, 65]),
            },
            TestCase {
                output: "int-le-0=0x1234".to_string(),
                expected: Some(vec![52, 18, 0, 0]),
            },
            TestCase {
                output: "short-be-0=0x1234".to_string(),
                expected: Some(vec![18, 52]),
            },
        ];

        let dummy_string = "".to_string();
        let dictgen = DictGen::new(&dummy_string, &dummy_string, 1000, false);

        for test_case in test_cases {
            let tokens = dictgen.parse_output_to_tokens(test_case.output.as_bytes());
            eprintln!("{:?}", tokens);
            if let Some(expected) = test_case.expected {
                assert_eq!(tokens.len(), 1);
                let token = tokens.iter().next().expect("Token should exist");
                assert_eq!(*token, expected);
            } else {
                assert_eq!(tokens.len(), 0);
            }
        }
    }

    #[test]
    fn test_compute_hash() {
        let inputs = [
            vec![0x00, 0x01, 0x02],
            vec![0x00, 0x01, 0x02, 0x03],
            vec![0x00, 0x01, 0x02, 0x03, 0x04],
            vec![0x00, 0x01, 0x02, 0x03, 0x04, 0x05],
            vec![0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06],
        ];

        let mutator = DictMutator::new(Arc::new(Mutex::new(HashSet::new())), 1024);
        let mut hashes = HashSet::new();

        for input in &inputs {
            let hash = mutator.compute_hash(input);
            println!("Hash: {}", hash);

            assert!(
                hashes.insert(hash),
                "Hash collision detected for input: {:?}",
                input
            );

            let hash_again = mutator.compute_hash(input);
            assert_eq!(
                hash, hash_again,
                "Different hash for the same input: {:?}",
                input
            );
        }
    }

    #[test]
    fn test_hash_limit() {
        let inputs = [
            vec![0x00, 0x01, 0x02, 0x03],
            vec![0x00, 0x01, 0x02, 0x03],
            vec![0x00, 0x01, 0x02, 0x03, 0x04],
            vec![0x00, 0x01, 0x02, 0x03, 0x04, 0x05],
            vec![0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06],
        ];

        let mut mutator = DictMutator::new(Arc::new(Mutex::new(HashSet::new())), 2);

        for input in &inputs {
            mutator.is_unique(input);
        }
    }

    #[test]
    fn test_is_blacklisted_function() {
        let dummy_string = "".to_string();
        let dictgen = DictGen::new(&dummy_string, &dummy_string, 1000, false);

        let blacklisted_functions = vec!["av_malloc", "asInt", "error_handler_fprintf"];
        for func in &blacklisted_functions {
            assert!(dictgen.is_blacklisted_function(&func.to_string()));
        }
        let sane_functions = vec!["foo", "bar", "baz"];
        for func in &sane_functions {
            assert!(!dictgen.is_blacklisted_function(&func.to_string()));
        }
    }
}
