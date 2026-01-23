class Commit:
    hash: str
    message: str

    def __init__(self, hash: str, message: str):
        self.hash = hash
        self.message = message

    def __str__(self):
        return f"{self.hash}: {self.message}"
