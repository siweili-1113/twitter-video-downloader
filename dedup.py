#!/usr/bin/env python3
"""Simple deduplication tool for text files (one entry per line)."""
import sys
import os


def remove_duplicates(file_path):
    with open(file_path, 'r', encoding='utf8') as f:
        lines = f.readlines()
    unique_lines = list(dict.fromkeys(line.strip() for line in lines if line.strip()))
    with open(file_path, 'w', encoding='utf8') as f:
        for line in unique_lines:
            f.write(line + '\n')


if __name__ == '__main__':
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
    else:
        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "no_media.txt")
    print(f"Dedup file: {file_path}")
    remove_duplicates(file_path)
    print(f"Dedup complete. File updated: {file_path}")