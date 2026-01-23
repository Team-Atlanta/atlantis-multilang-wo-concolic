#!/usr/bin/env python3

from pathlib import Path
import sys
import subprocess
import os
import testlang
from zipfile import ZipFile
import datetime
import json
from time import sleep
import argparse  # Added import

def testlang_compare(tl1: str, tl2: str) -> bool:
    hash1 = testlang.hash(testlang.normalize(tl1))
    hash2 = testlang.hash(testlang.normalize(tl2))
    return hash1 == hash2

def check_difference(one, two):
    one, two = open(one).read(), open(two).read()

    for gen in [one, two]:
        try:
            testlang.validate(gen)
        except Exception as e:
            print(e)
            return False

    return testlang_compare(one, two)

def main():
    parser = argparse.ArgumentParser(description='Evaluate harnesses.')
    parser.add_argument('--harness_dir', type=str, default='./test_harnesses', help='Directory containing harness files')
    parser.add_argument('--answers_dir', type=str, default='./answers', help='Directory containing answer files')
    parser.add_argument('--num_tries', type=int, default=1, help='Number of tries for each harness')
    parser.add_argument('--model', type=str, default='gpt-4o', help='Model to use')
    args = parser.parse_args()

    harness_dir = args.harness_dir
    answers_dir = args.answers_dir
    num_tries = args.num_tries
    model = args.model

    print('harness             ,  err count,  avg. time,  avg. cost,  num equal, num subset')

    glob = Path(harness_dir).glob('*.c')
    # handpicked = ['CROMU-00001']
    # glob = [Path(harness_dir) / f'{x}.c' for x in handpicked]
    # options = "--majority 5 --few-shot --few-shot-ratio 0.3"
    options = "--majority 5"
    # options = "--majority 9 --few-shot --few-shot-ratio 1.0 --model claude-3-haiku"
    output_files = []

    time = datetime.datetime.now().isoformat()
    # log_file = os.path.abspath(f'./workdir/log-{time}.json')
    workdir = Path(f'./workdir/eval/{model}/')
    Path.mkdir(workdir, exist_ok=True)

    for harness in glob:
        error_count = 0
        times = []
        equal_count = 0
        subset_count = 0
        total_cost = 0.0

        for i in range(num_tries):
            if num_tries > 1:
                output_file = workdir / f'{harness.stem}_{i}.json'
                log_file = workdir / f'{harness.stem}_{i}.log'
            else:
                output_file = workdir / f'{harness.stem}.json'
                log_file = workdir / f'{harness.stem}.log'
            output_files.append(output_file)
            command = f'LOG_FILE="{log_file}" time -f %e python ./reverser.py --model {model} --workdir ./workdir --target {harness} --output {output_file} {options}; exit 0'
            output = subprocess.check_output(command, shell=True, stderr=subprocess.STDOUT)
            # print(output.decode())

            times.append(float(output.decode().splitlines()[-1]))
            for line in output.splitlines():
                if line.startswith(b'$'):
                    total_cost += float(line[1:])
            answer_path = Path(answers_dir) / f'{harness.stem}.json'
            equal = check_difference(output_file, answer_path)
            equal_count += 1 if equal else 0
            # subset_count += 1 if subset else 0
        
        avg_time = (sum(times) / len(times)) if len(times) else 999
        avg_cost = total_cost / num_tries if num_tries else 0
        
        print(f'{harness.stem:20s}, {error_count:10d}, {avg_time:10.5f}, {avg_cost:10.5f}, {equal_count:10d}, {subset_count:10d}')

        # Hopefully helps with rate limit
        sleep(3)
    
    # with ZipFile(f'./workdir/evaluation-{time}.zip', 'w') as output_zip:
    #     for file in output_files:
    #         output_zip.write(file)

if __name__ == "__main__":
    main()

