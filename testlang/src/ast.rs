use std::{
    cmp::Ordering,
    collections::{HashMap, HashSet},
};

use crate::{
    FieldKind, Ref, RefKind, SizeDescriptor, TestLang, TestLangError, TestLangInt, ValOrRef,
};

type TestLangNodeId = usize;
type ReferenceMap = HashMap<String, TestLangNodeId>;

#[derive(Clone, Debug)]
pub enum TestLangTreeError {
    InvalidRecord(String),
    InvalidField(String),
    InvalidReference(usize),
}

#[derive(Clone, Debug)]
pub struct TestLangNode {
    pub id: TestLangNodeId,
    pub type_id: usize,
    pub byte_size: Option<usize>,
    pub value: TestLangNodeValue,
}

impl TestLangNode {
    pub fn new(type_id: usize, byte_size: Option<usize>, value: TestLangNodeValue) -> Self {
        Self {
            id: 0,
            type_id,
            byte_size,
            value,
        }
    }

    pub fn byte_size(&self) -> Option<usize> {
        match &self.value {
            TestLangNodeValue::Int(_) => self.byte_size,
            TestLangNodeValue::Float(_) => self.byte_size,
            TestLangNodeValue::Bytes(b) => Some(b.len()),
            TestLangNodeValue::String(s) => Some(s.len()),
            TestLangNodeValue::Group(g) => {
                let mut size = 0;
                for node in g {
                    size += node.byte_size()?;
                }
                Some(size)
            }
            TestLangNodeValue::Record(r) => r.byte_size(),
            TestLangNodeValue::Union(_, node) => node.byte_size(),
            TestLangNodeValue::Ref(_) => self.byte_size,
        }
    }

    pub fn find_by_id(&self, id: TestLangNodeId) -> Option<&TestLangNode> {
        match self.id.cmp(&id) {
            Ordering::Less => match &self.value {
                TestLangNodeValue::Group(g) => g.iter().find_map(|node| node.find_by_id(id)),
                TestLangNodeValue::Union(_, node) => node.find_by_id(id),
                TestLangNodeValue::Record(node) => node.find_by_id(id),
                _ => None,
            },
            Ordering::Equal => Some(self),
            Ordering::Greater => None,
        }
    }

    pub fn find_by_id_mut(&mut self, id: TestLangNodeId) -> Option<&mut TestLangNode> {
        match self.id.cmp(&id) {
            Ordering::Less => match &mut self.value {
                TestLangNodeValue::Group(g) => {
                    g.iter_mut().rev().find_map(|node| node.find_by_id_mut(id))
                }
                TestLangNodeValue::Union(_, node) => node.find_by_id_mut(id),
                TestLangNodeValue::Record(node) => node.find_by_id_mut(id),
                _ => None,
            },
            Ordering::Equal => Some(self),
            Ordering::Greater => None,
        }
    }

    fn fill_metadata(
        &mut self,
        metadata: &mut AstMetadata,
        testlang: &TestLang,
        ref_map: &mut ReferenceMap,
    ) -> Result<(), TestLangError> {
        self.id = metadata.take_id();

        fn process_normal_field(
            node: &TestLangNode,
            metadata: &mut AstMetadata,
            testlang: &TestLang,
            ref_map: &mut ReferenceMap,
        ) -> Result<(), TestLangError> {
            let testlang_field = testlang
                .find_field_by_id(node.type_id)
                .ok_or_else(|| TestLangError::InvalidNode("Type ID not found".to_owned()))?;
            ref_map.insert(testlang_field.name.to_owned(), node.id);
            if let Some(SizeDescriptor::Single(ValOrRef::Ref(Ref {
                kind: RefKind::Field,
                name,
            }))) = testlang_field
                .byte_size
                .as_ref()
                .or(testlang_field.len.as_ref())
            {
                let size_id = ref_map.get(name).ok_or_else(|| {
                    TestLangError::InvalidNode(format!("Undefined reference {}", name))
                })?;
                metadata.size_ref.insert(node.id, *size_id);
                metadata
                    .size_of
                    .entry(*size_id)
                    .or_default()
                    .insert(node.id);
            }

            match testlang_field.possible_values.as_deref() {
                Some([_, _, ..]) => {
                    metadata.choosable_fields.push(node.id);
                }
                Some([]) | None => match testlang_field.kind {
                    FieldKind::Int | FieldKind::Float | FieldKind::Bytes | FieldKind::Custom(_) => {
                        metadata.mutable_fields.push(node.id);
                    }
                    FieldKind::String => {
                        metadata.mutable_fields.push(node.id);
                        metadata.mutable_strings.push(node.id);
                    }
                    _ => {
                        return Err(TestLangError::InvalidNode(
                            "AST type_id seems to be broken. This branch should be unreachable."
                                .to_owned(),
                        ));
                    }
                },
                Some([_]) => {}
            }
            metadata.normal_fields.push(node.id);
            Ok(())
        }

        match &mut self.value {
            TestLangNodeValue::Int(_)
            | TestLangNodeValue::Float(_)
            | TestLangNodeValue::Bytes(_)
            | TestLangNodeValue::String(_) => {
                process_normal_field(self, metadata, testlang, ref_map)?;
            }
            TestLangNodeValue::Group(inner_nodes) => {
                if let Some(testlang_field) = testlang.find_field_by_id(self.type_id) {
                    if FieldKind::Array == testlang_field.kind {
                        let Some(Ref {
                            kind: RefKind::Record,
                            name,
                        }) = &testlang_field.items
                        else {
                            return Err(TestLangError::InvalidNode(format!(
                                "Array field not containing valid item reference {:?}",
                                testlang_field.items
                            )));
                        };
                        metadata.arrays.push(self.id);
                        metadata.record_ref_name.insert(self.id, name.to_owned());
                    }
                }
                // ELSE: record field
                let mut local_ref_map = ref_map.clone();
                for node in inner_nodes.iter_mut() {
                    node.fill_metadata(metadata, testlang, &mut local_ref_map)?;
                }
            }
            TestLangNodeValue::Record(inner_node) => {
                // This is OK because ref_map will be cloned in inner loop
                inner_node.fill_metadata(metadata, testlang, ref_map)?;
            }
            TestLangNodeValue::Union(_, inner_node) => {
                let testlang_record =
                    testlang.find_record_by_id(self.type_id).ok_or_else(|| {
                        TestLangError::InvalidNode("Record type ID not found".to_owned())
                    })?;
                metadata.unions.push(self.id);
                metadata
                    .record_ref_name
                    .insert(self.id, testlang_record.name.to_owned());
                inner_node.fill_metadata(metadata, testlang, &mut ref_map.clone())?;
            }
            TestLangNodeValue::Ref(value_id) => {
                metadata.value_ref.insert(self.id, *value_id);
                metadata
                    .value_of
                    .entry(*value_id)
                    .or_default()
                    .insert(self.id);
            }
        }
        Ok(())
    }
}

impl AsRef<TestLangNode> for TestLangNode {
    fn as_ref(&self) -> &TestLangNode {
        self
    }
}

#[derive(Clone, Debug)]
pub enum TestLangNodeValue {
    Int(TestLangInt),
    Float(f64),
    Bytes(Vec<u8>),
    String(String),
    // For FieldKind::Array and RecordKind::Struct
    Group(Vec<TestLangNode>),
    // For FieldKind::Record
    Record(Box<TestLangNode>),
    Union(usize, Box<TestLangNode>),
    Ref(TestLangNodeId),
}

#[derive(Clone, Debug)]
pub struct TestLangAst {
    pub root: TestLangNode,
    pub metadata: AstMetadata,
}

impl TestLangAst {
    pub fn new(mut root: TestLangNode, testlang: &TestLang) -> Result<Self, TestLangError> {
        let mut metadata = AstMetadata::new();
        root.fill_metadata(&mut metadata, testlang, &mut HashMap::new())?;
        Ok(Self { root, metadata })
    }
}

#[derive(Clone, Debug, Default)]
pub struct AstMetadata {
    pub local_testlang_id: usize,

    // Fast reference lookup to prevent tree traversal
    pub value_ref: HashMap<TestLangNodeId, TestLangNodeId>,
    pub value_of: HashMap<TestLangNodeId, HashSet<TestLangNodeId>>,
    pub size_ref: HashMap<TestLangNodeId, TestLangNodeId>,
    pub size_of: HashMap<TestLangNodeId, HashSet<TestLangNodeId>>,
    pub record_ref_name: HashMap<TestLangNodeId, String>,

    // Mutation choosers
    pub normal_fields: Vec<TestLangNodeId>,
    pub mutable_fields: Vec<TestLangNodeId>,
    pub mutable_strings: Vec<TestLangNodeId>,
    pub choosable_fields: Vec<TestLangNodeId>,
    pub arrays: Vec<TestLangNodeId>,
    pub unions: Vec<TestLangNodeId>,

    pub next_id: TestLangNodeId,
}

impl AstMetadata {
    pub fn new() -> Self {
        Default::default()
    }

    pub fn take_id(&mut self) -> TestLangNodeId {
        let id = self.next_id;
        self.next_id += 1;
        id
    }
}
