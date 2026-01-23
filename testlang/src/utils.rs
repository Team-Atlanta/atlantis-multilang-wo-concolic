use std::{collections::HashMap, fs};

use graphviz_rust::{
    cmd::Format,
    dot_generator::*,
    dot_structures::*,
    printer::{DotPrinter, PrinterContext},
};

use crate::{Record, TestLang, TestLangError, RECORD_INPUT};

impl TestLang {
    pub fn visualize(
        &self,
        python_codes: &HashMap<String, String>,
        output_path: &str,
    ) -> Result<(), TestLangError> {
        if !output_path.ends_with(".png") {
            return Err(TestLangError::InvalidSemantics {
                error: "Output path must end with .png for now".to_string(),
                record: None,
                field: None,
            });
        }

        let root_record = self.find_record_by_name(RECORD_INPUT).ok_or_else(|| {
            TestLangError::InvalidSemantics {
                error: format!("Failed to find record: {RECORD_INPUT}"),
                record: Some(RECORD_INPUT.to_owned()),
                field: None,
            }
        })?;

        let mut graph = graph!(strict di id!("TestLang"));
        let metadata = TestLang {
            records: vec![],
            ..self.clone()
        }
        .to_string()
        .replace(" \"records\": []\n", "");
        let metadata = TestLang::escape_node_label(&metadata);
        graph.add_stmt(stmt!(node!("<START>"; attr!("label", metadata))));
        self.add_record_node(&mut graph, root_record)?;
        self.make_graph(&mut graph, root_record, python_codes)?;

        let dot_output = graph.print(&mut PrinterContext::default());
        let png_data = graphviz_rust::exec_dot(dot_output, vec![Format::Png.into()])?;
        fs::write(output_path, &png_data)?;

        Ok(())
    }

    fn escape_node_label(node_label: &str) -> String {
        let node_label = format!("{node_label}\n ");
        let node_label = node_label
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\"", "&quot;")
            .replace("\n", "<br align='left'/>");
        format!("<{node_label}>")
    }

    fn add_record_node(&self, graph: &mut Graph, record: &Record) -> Result<(), TestLangError> {
        let record_content =
            serde_json::to_string_pretty(record).map_err(|_| TestLangError::InvalidSemantics {
                error: format!("Failed to serialize record: {}", record.name),
                record: Some(record.name.clone()),
                field: None,
            })?;
        self.add_node(graph, &record.name, &record_content);
        Ok(())
    }

    fn add_node(&self, graph: &mut Graph, node_name: &str, node_label: &str) {
        let node_label = TestLang::escape_node_label(node_label);
        graph.add_stmt(stmt!(node!(node_name; attr!("label", node_label))));
    }

    fn make_graph(
        &self,
        graph: &mut Graph,
        record: &Record,
        python_codes: &HashMap<String, String>,
    ) -> Result<(), TestLangError> {
        for field in &record.fields {
            if let Some(ref_record_name) = field.get_record_ref() {
                if let Some(ref_record) = self.find_record_by_name(ref_record_name) {
                    self.add_record_node(graph, ref_record)?;
                    graph.add_stmt(stmt!(
                        edge!(node_id!(record.name) => node_id!(ref_record_name))
                    ));
                    self.make_graph(graph, ref_record, python_codes)?;
                }
            }
            if let Some(encoder) = &field.encoder {
                if let Some(code) = python_codes.get(encoder) {
                    self.add_node(graph, encoder, code);
                    graph.add_stmt(stmt!(edge!(node_id!(record.name) => node_id!(encoder))));
                }
            }
            if let Some(generator) = &field.generator {
                if let Some(code) = python_codes.get(generator) {
                    self.add_node(graph, generator, code);
                    graph.add_stmt(stmt!(edge!(node_id!(record.name) => node_id!(generator))));
                }
            }
        }
        Ok(())
    }
}

// test
#[cfg(test)]
mod tests {
    use std::{env, path::Path};

    use glob::glob;

    use crate::TestLang;

    use super::*;

    #[test]
    #[ignore]
    fn test_visualize() {
        let workspace_env = env::var("CARGO_MANIFEST_DIR").unwrap();
        let testlang_samples_dir = Path::new(&workspace_env)
            .join("../reverser/harness-reverser/answers")
            .to_string_lossy()
            .into_owned();
        let glob_pattern = format!("{testlang_samples_dir}/*.json");
        for entry in glob(&glob_pattern).expect("Failed to listup sample files") {
            match entry {
                Ok(path) => {
                    let testlang = TestLang::from_file(&path).unwrap();
                    let testlang_visualize_dir =
                        Path::new(&workspace_env).join("test-testlang-visualize");
                    fs::create_dir_all(&testlang_visualize_dir).unwrap();
                    let file_name = path
                        .file_name()
                        .unwrap()
                        .to_string_lossy()
                        .replace(".json", ".png");
                    let temp_file_path = testlang_visualize_dir.join(&file_name);
                    testlang
                        .visualize(&HashMap::new(), temp_file_path.to_str().unwrap())
                        .unwrap();
                    fs::remove_dir_all(&testlang_visualize_dir).unwrap();
                }
                Err(e) => eprintln!("{:?}", e),
            }
        }
    }
}
