import glob
import os
import hashlib

from .model import Corpus

searched_corpus = list()


class CorpusSearcher:
    def __init__(self, corpus_directory: str):
        # Assume shared directory structure:
        # root - cp - harness_name - corpuses
        # Start with cp scope
        self.corpus_directory = corpus_directory

    def search_new_corpus(self) -> list[Corpus]:
        # Dynamically discover all CRS directories except our own
        our_crs_name = os.environ.get('CRS_NAME', 'crs-multilang')
        corpuses = list()
        if not os.path.isdir(self.corpus_directory):
            return []
        for entry in os.listdir(self.corpus_directory):
            corpus_directory_path = os.path.join(self.corpus_directory, entry)
            # Skip our own CRS directory and non-directories
            if entry == our_crs_name or not os.path.isdir(corpus_directory_path):
                continue
            glob_result = glob.glob(
                f"{corpus_directory_path}{os.sep}**", recursive=True
            )
            corpuses.extend(
                [corpus_f for corpus_f in glob_result
                 if os.path.isfile(corpus_f)
                 and not os.path.basename(corpus_f).startswith('.')
                 and not corpus_f.endswith('.cov')]
            )

        new_corpuses = list()
        for corpus_path in corpuses:
            if corpus_path not in searched_corpus:
                harness_name = os.path.basename(os.path.dirname(corpus_path))
                with open(corpus_path, "rb") as f:
                    corpus_data = f.read()

                hasher = hashlib.sha256()
                hasher.update(corpus_data + harness_name.encode())

                corpus_id = hasher.hexdigest()
                new_corpus = Corpus(
                    harness=harness_name,
                    data=corpus_data,
                    path=corpus_path,
                    corpus_id=corpus_id,
                )

                searched_corpus.append(corpus_path)
                new_corpuses.append(new_corpus)

        return new_corpuses
