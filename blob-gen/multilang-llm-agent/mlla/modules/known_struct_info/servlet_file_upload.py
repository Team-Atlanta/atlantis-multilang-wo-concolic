# flake8: noqa: E501
SERVLET_FILE_UPLOAD_PROMPT = """
<ServletFileUpload>
  <description>
    ServletFileUpload parses HTTP requests with Content-Type: multipart/form-data, extracting parts into FileItem objects.
  </description>

  <core_principles>
    <principle>Parses multipart/form-data HTTP requests</principle>
    <principle>Uses DiskFileItemFactory for temporary storage management</principle>
  </core_principles>

  <example>
    <code language="java">
      ServletFileUpload upload = new ServletFileUpload(new DiskFileItemFactory(DiskFileItemFactory.DEFAULT_SIZE_THRESHOLD, tmpDir));
      List<FileItem> items = upload.parseRequest(request);
    </code>
  </example>
</ServletFileUpload>
""".strip()
