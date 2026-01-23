from mlla.modules.known_struct import SERVLET_FILE_UPLOAD_TAG, get_known_struct_prompts


class TestServletFileUpload:
    def test_servlet_file_upload_detection(self):
        # Code with ServletFileUpload
        code = """
        public class FileUploadHandler {
            public void handleUpload(HttpServletRequest request) {
                File tmpDir = new File("/tmp");
                ServletFileUpload upload = new ServletFileUpload(new DiskFileItemFactory(DiskFileItemFactory.DEFAULT_SIZE_THRESHOLD, tmpDir));
                List<FileItem> items = upload.parseRequest(request);
            }
        }
        """  # noqa: E501
        prompt = get_known_struct_prompts(code)

        # Check that the prompt contains the ServletFileUpload tag
        assert SERVLET_FILE_UPLOAD_TAG in prompt
        assert "<ServletFileUpload>" in prompt
        assert "multipart/form-data" in prompt

    def test_servlet_file_upload_with_processing(self):
        # Code with ServletFileUpload and file processing
        code = """
        public void processUpload(HttpServletRequest request) throws Exception {
            ServletFileUpload upload = new ServletFileUpload(new DiskFileItemFactory());
            List<FileItem> items = upload.parseRequest(request);

            for (FileItem item : items) {
                if (item.isFormField()) {
                    String fieldName = item.getFieldName();
                    String fieldValue = item.getString();
                } else {
                    String fileName = item.getName();
                    InputStream fileContent = item.getInputStream();
                }
            }
        }
        """
        prompt = get_known_struct_prompts(code)

        # Check that the prompt contains ServletFileUpload information
        assert SERVLET_FILE_UPLOAD_TAG in prompt
        assert "DiskFileItemFactory" in prompt

    def test_no_servlet_file_upload_detection(self):
        # Code without ServletFileUpload
        code = """
        public class RegularHandler {
            public void handleRequest(HttpServletRequest request) {
                String param = request.getParameter("test");
                System.out.println(param);
            }
        }
        """
        prompt = get_known_struct_prompts(code)

        # Check that ServletFileUpload is not detected
        assert SERVLET_FILE_UPLOAD_TAG not in prompt
        assert "<ServletFileUpload>" not in prompt

    def test_servlet_file_upload_prompt_content(self):
        # Code with ServletFileUpload
        code = """
        ServletFileUpload upload = new ServletFileUpload(new DiskFileItemFactory(DiskFileItemFactory.DEFAULT_SIZE_THRESHOLD, tmpDir));
        """  # noqa: E501
        prompt = get_known_struct_prompts(code)

        # Check specific content in the prompt
        assert "multipart/form-data" in prompt
        assert "DiskFileItemFactory" in prompt
        assert "temporary storage management" in prompt
