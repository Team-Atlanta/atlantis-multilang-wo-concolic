use super::db::FuzzDB;
use std::path::PathBuf;

#[test]
fn test_parse_bcda_output() {
    let result_path = PathBuf::from("src/tests/jenkins_bcda.json");
    let dummy_cov_dir = PathBuf::from("src/tests/");
    let diff = Some("src/tests/jenkins.ref.diff.json".to_string());
    let db = FuzzDB::new_for_test(dummy_cov_dir, "JenkinsThree".to_string(), diff);
    db.load_bcda_result(&result_path);
    let matched = db.match_interesting_cov(&"jenkins".to_string());
    println!("JenkinsThree > ");
    for m in &matched {
        println!("{:?}", m);
    }
    let diff_match_result = db.match_diff_info(&"jenkins".to_string());
    println!("diff_match_result: {:?}", diff_match_result);
    let cov = db.load_cov(&"jenkins".to_string()).unwrap();
    let harness = "/src/fuzz/jenkins-harness-three/src/main/java/com/aixcc/jenkins/harnesses/three/JenkinsThree.java".to_string();
    assert!(cov.has_src_cov_in_range(&harness, 51, 53));
    assert!(cov.has_src_cov_in_range(&harness, 52, 52));
    assert!(!cov.has_src_cov_in_range(&harness, 48, 50));
    let tmp = "no".to_string();
    assert!(!cov.has_src_cov_in_range(&tmp, 10, 20));
}
