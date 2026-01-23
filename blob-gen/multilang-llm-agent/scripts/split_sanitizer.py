import os

from ruamel.yaml import YAML


def split_sanitizer_yaml():
    # Initialize YAML with better formatting
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 88  # PEP8
    yaml.indent(mapping=2, sequence=4, offset=2)

    # Read the main YAML file
    with open(
        "mlla/modules/sanitizer_info/JazzerSanitizer_with_exploit.yaml", "r"
    ) as f:
        data = yaml.load(f)

    # Create output directory if it doesn't exist
    output_dir = "mlla/modules/sanitizer_info/JazzerSanitizer_with_exploit"
    os.makedirs(output_dir, exist_ok=True)

    # Split each top-level key into its own file
    for key, value in data.items():
        output_file = os.path.join(output_dir, f"{key}.yaml")

        # Write to individual YAML file, preserving the exact structure
        with open(output_file, "w") as f:
            yaml.dump(value, f)

        print(f"Created {output_file}")


if __name__ == "__main__":
    split_sanitizer_yaml()
