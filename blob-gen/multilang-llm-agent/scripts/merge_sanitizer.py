from pathlib import Path

from ruamel.yaml import YAML


def merge_sanitizer_yaml():
    # Initialize YAML with better formatting
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 88  # PEP8
    yaml.indent(mapping=2, sequence=4, offset=2)

    # Directory containing the split YAML files
    input_dir = Path("mlla/modules/sanitizer_info/JazzerSanitizer_with_exploit")

    # Output file path
    output_file = Path("mlla/modules/sanitizer_info/JazzerSanitizer_with_exploit.yaml")

    # Dictionary to store merged data
    merged_data = {}

    # Read and merge all YAML files
    for yaml_file in sorted(input_dir.glob("*.yaml")):
        sanitizer_type = yaml_file.stem
        with open(yaml_file) as f:
            data = yaml.load(f)
            merged_data[sanitizer_type] = data

    # Write merged data to output file
    with open(output_file, "w") as f:
        yaml.dump(merged_data, f)

    print(f"Merged {len(merged_data)} sanitizer types into {output_file}")


if __name__ == "__main__":
    merge_sanitizer_yaml()
