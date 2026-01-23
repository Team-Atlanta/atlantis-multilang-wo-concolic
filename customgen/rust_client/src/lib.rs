use std::{io, path::Path};

use customgen_rpc::{
    CustomGenRequest, custom_gen_response::Result as ResponseResult,
    custom_gen_service_client::CustomGenServiceClient,
};
use thiserror::Error;
use tonic::transport::Channel;

pub mod customgen_rpc {
    tonic::include_proto!("customgen");
}

#[derive(Error, Debug)]
pub enum CustomGenError {
    #[error("failed to connect customgen daemon: {0}")]
    Connection(#[from] tonic::transport::Error),
    #[error("failed processing service request: {0}")]
    Service(#[from] tonic::Status),
    #[error("failed processing customgen: {0}")]
    Generation(String),
    #[error("failed due to invalid endpoint")]
    InvalidEndpoint(),
    #[error("failed due to IO error: {0}")]
    Io(#[from] io::Error),
}

type CustomGenResult<T> = Result<T, CustomGenError>;

pub struct CustomGenerator {
    client: CustomGenServiceClient<Channel>,
}

impl CustomGenerator {
    pub async fn new(endpoint_socket: impl AsRef<Path>) -> CustomGenResult<Self> {
        static SIZE_512MB: usize = 512 * 1024 * 1024;
        let endpoint_resolved = endpoint_socket
            .as_ref()
            .canonicalize()?
            .to_str()
            .ok_or_else(CustomGenError::InvalidEndpoint)
            .map(|p| format!("unix://{}", p))?;
        let client = CustomGenServiceClient::connect(endpoint_resolved)
            .await?
            .max_decoding_message_size(SIZE_512MB);
        Ok(CustomGenerator { client })
    }

    pub async fn generate(
        &mut self,
        generator_id: &str,
        count: usize,
    ) -> CustomGenResult<Vec<Vec<u8>>> {
        let request = CustomGenRequest {
            generator_id: generator_id.to_owned(),
            count: count as u32,
        };
        let response = self.client.generate(request).await?.into_inner();
        let Some(result) = response.result else {
            return Err(CustomGenError::Generation(
                "No response received.".to_owned(),
            ));
        };
        match result {
            ResponseResult::Generated(generation_result) => Ok(generation_result.output),
            ResponseResult::Failed(generation_error) => {
                Err(CustomGenError::Generation(generation_error.message))
            }
        }
    }
}
