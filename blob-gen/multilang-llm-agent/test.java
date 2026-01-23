public class TarFile implements Closeable {
  public TarFile(final byte[] content) throws IOException {
        this(new SeekableInMemoryByteChannel(content));
    }
}
