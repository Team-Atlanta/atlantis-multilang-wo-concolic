#!/bin/bash -eux
echo "ATHERIS INSTALL"
unset CFLAGS CXXFLAGS
# PYI_STATIC_ZLIB=1 is needed for installing pyinstaller 5.0
export PYI_STATIC_ZLIB=1
pip3 install -v --no-cache-dir "pyinstaller==5.0.1" "setuptools==42.0.2" "coverage==6.3.2"
mkdir /tmp/atheris && cd /tmp/atheris 
wget https://github.com/google/atheris/archive/refs/tags/2.3.0.zip -O ./atheris.zip
unzip ./atheris.zip
cp /tmp/merge_libfuzzer_sanitizer.sh /tmp/atheris/atheris-2.3.0/setup_utils/merge_libfuzzer_sanitizer.sh
LIBFUZZER_LIB=$( echo /usr/local/lib/clang/*/lib/x86_64-unknown-linux-gnu/libclang_rt.fuzzer_no_main.a ) pip3 install ./atheris-2.3.0/
rm -rf /tmp/*
