# Native PDF Watermark Remover

[![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Engine](https://img.shields.io/badge/Engine-PyMuPDF-009688)](https://pymupdf.readthedocs.io/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Professional-grade watermark detection and removal for PDF files using **native PDF object editing**.

- No image conversion
- No OCR pipeline
- No quality loss from rasterization

## Why This Project

Most watermark removers convert PDF pages to images and rebuild the document, which reduces quality and breaks selectable text.

This project works directly on PDF content streams, preserving:

- Vector quality
- Text selectability
- Original document layout

## Features

- Smart multi-signal watermark detection
- Confidence-based scoring (`0.1` to `1.0`)
- Detection for text, image, vector, and notice/footer patterns
- Native content-stream watermark removal
- Interactive and command-line modes
- Optional visualization mode for manual review

## How It Works

1. Builds a body-text baseline (font sizes, colors, common fonts).
2. Detects repeated or suspicious elements across pages.
3. Scores candidates using multiple heuristics:
   - page coverage
   - consistent positioning
   - edge/diagonal placement
   - color/font anomalies
   - watermark keywords and timestamps
4. Removes likely watermarks by editing PDF content stream patterns (Form XObject and rotated text patterns).

## Installation

```bash
git clone <your-repo-url>
cd Watermark
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
# source .venv/bin/activate
pip install -r requirements.txt
```

Optional local package install:

```bash
pip install .
```

## Usage

### Interactive Mode

```bash
python watermark_remover.py
```

### Basic Removal

```bash
python watermark_remover.py input.pdf
```

### Preview Candidates Only

```bash
python watermark_remover.py input.pdf -p
```

### Tune Confidence

```bash
python watermark_remover.py input.pdf -c 0.4
```

### Visualization Output

```bash
python watermark_remover.py input.pdf -v
python watermark_remover.py input.pdf -v marked.pdf
```

### Custom Output Name

```bash
python watermark_remover.py input.pdf -o output_clean.pdf
```

## Confidence Guide

| Confidence | Behavior | Recommended For |
|---|---|---|
| `0.3` | Aggressive detection | Heavy watermark documents |
| `0.4` | Balanced (default) | Most mixed PDFs |
| `0.6+` | Conservative detection | Sensitive/legal documents |

## Python API

```python
from watermark_remover import WatermarkRemover, DetectionConfig

with WatermarkRemover("input.pdf", DetectionConfig(min_confidence=0.4)) as remover:
    candidates = remover.detect()
    result = remover.remove(candidates, "output_clean.pdf")
    print(result)
```

## Project Structure

```text
.
├── watermark_remover.py
├── requirements.txt
├── pyproject.toml
├── README.md
├── CONTRIBUTING.md
├── CHANGELOG.md
└── LICENSE
```

## Limitations

- Highly customized watermark encodings may need additional patterns.
- Some image/vector watermark variants are intentionally handled conservatively to avoid damaging overlapping content.
- Always validate output on critical legal/financial documents.

## Roadmap

- Better pattern coverage for uncommon PDF generators
- Safer optional image watermark pass
- Benchmark suite with sample PDFs
- CI checks and regression test corpus

## Contributing

Pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Community

- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security Policy](SECURITY.md)

## License

Licensed under the [MIT License](LICENSE).
