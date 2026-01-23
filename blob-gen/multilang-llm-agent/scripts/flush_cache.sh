#!/bin/bash

# Delete all keys starting with mcga::
redis-cli --raw keys "mcga::*" | xargs -r -I{} redis-cli del "{}"

# Delete all keys starting with cgpa::
redis-cli --raw keys "cgpa::*" | xargs -r -I{} redis-cli del "{}"
